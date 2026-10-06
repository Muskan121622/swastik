"""Conversation service: the REST-facing orchestration per turn.

Graph = workflow; DB = truth; this layer persists the transcript and
returns the audit trail (events) that the frontend renders.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Conversation, Message, ToolEvent, Handoff, Appointment, Patient
from app.agent.graph import build_graph
from app.agent.runtime import get_provider
from app.db.database import SessionLocal


def create_conversation(db: Session) -> Conversation:
    conv = Conversation(status="OPEN")
    db.add(conv)
    db.commit()
    return conv


def load_history(db: Session, conversation_id: str) -> list[dict]:
    """Transcript -> openai-style messages (user/assistant only; tool
    internals are replayed inside the current turn, past ones live on as
    the audit trail + what the agent already said)."""
    rows = (db.execute(select(Message)
                       .where(Message.conversation_id == conversation_id)
                       .order_by(Message.id))
            .scalars().all())
    return [{"role": "user" if m.role == "user" else "assistant",
             "content": m.content} for m in rows]


def run_turn(conversation_id: str, user_text: str) -> dict:
    db = SessionLocal()
    try:
        conv = db.get(Conversation, conversation_id)
        if conv is None:
            return {"error": "CONV_NOT_FOUND"}
        db.add(Message(conversation_id=conversation_id, role="user", content=user_text))
        db.commit()
        history = load_history(db, conversation_id)  # includes the message just added
    finally:
        db.close()

    graph = build_graph(get_provider(), SessionLocal)
    from app.agent.state import new_turn
    state = graph.invoke(new_turn(conversation_id, user_text, history),
                         {"recursion_limit": 40})

    db = SessionLocal()
    try:
        db.add(Message(conversation_id=conversation_id, role="agent",
                       content=state["reply"]))
        db.commit()
        conv = db.get(Conversation, conversation_id)
        handoff = (db.query(Handoff)
                   .filter_by(conversation_id=conversation_id)
                   .order_by(Handoff.id.desc()).first())
        return {
            "reply": state["reply"],
            "conversation_status": conv.status,
            "safety_label": state.get("safety_label", ""),
            "reply_substituted": bool(state.get("reply_substituted", False)),
            "events": state.get("events", []),
            "handoff": {"id": handoff.id, "reason": handoff.reason,
                        "status": handoff.status} if handoff else None,
        }
    finally:
        db.close()


def conversation_detail(db: Session, conversation_id: str) -> dict | None:
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        return None
    messages = (db.execute(select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id)).scalars().all())
    events = (db.execute(select(ToolEvent)
              .where(ToolEvent.conversation_id == conversation_id)
              .order_by(ToolEvent.id)).scalars().all())
    handoffs = (db.query(Handoff)
                .filter_by(conversation_id=conversation_id).all())

    patient = db.get(Patient, conv.confirmed_patient_id) if conv.confirmed_patient_id else None
    appts = []
    if patient:
        for a in patient.appointments:
            if a.status == "ACTIVE":
                appts.append({"appointment_id": a.id, "slot_id": a.slot_id,
                              "doctor_id": a.doctor_id, "status": a.status})

    return {
        "id": conv.id,
        "status": conv.status,
        "confirmed_patient": ({"id": patient.id, "name": patient.name}
                              if patient else None),
        "active_appointments": appts,
        "messages": [{"id": m.id, "role": m.role, "content": m.content,
                      "created_at": m.created_at.isoformat()} for m in messages],
        "events": [{"id": e.id, "tool": e.tool_name, "status": e.status,
                    "arguments": _json(e.arguments), "result": _json(e.result),
                    "created_at": e.created_at.isoformat()} for e in events],
        "handoffs": [{"id": h.id, "reason": h.reason, "summary": h.summary,
                      "status": h.status,
                      "created_at": h.created_at.isoformat(),
                      "resolved_at": h.resolved_at.isoformat() if h.resolved_at else None}
                     for h in handoffs],
    }


def _json(s: str):
    import json
    try:
        return json.loads(s)
    except Exception:
        return {"raw": s}
