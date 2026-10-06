"""REST API — the complete contract is documented in README.md."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Conversation, Handoff, Patient
from app.services import conversation_service as svc

router = APIRouter(prefix="/api")


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class MessageOut(BaseModel):
    reply: str
    conversation_status: str
    safety_label: str
    reply_substituted: bool = False
    events: list[dict]
    handoff: dict | None


@router.post("/conversations")
def create_conversation(db: Session = Depends(get_db)):
    conv = svc.create_conversation(db)
    return {"conversation_id": conv.id, "status": conv.status}


@router.post("/conversations/{conversation_id}/messages")
def post_message(conversation_id: str, body: MessageIn):
    result = svc.run_turn(conversation_id, body.content)
    if result.get("error") == "CONV_NOT_FOUND":
        raise HTTPException(404, detail={"code": "CONV_NOT_FOUND",
                                         "message": "No such conversation."})
    return result


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, db: Session = Depends(get_db)):
    detail = svc.conversation_detail(db, conversation_id)
    if detail is None:
        raise HTTPException(404, detail={"code": "CONV_NOT_FOUND",
                                         "message": "No such conversation."})
    return detail


@router.get("/conversations")
def list_conversations(db: Session = Depends(get_db), limit: int = 30):
    rows = (db.query(Conversation)
            .order_by(Conversation.created_at.desc()).limit(limit).all())
    return [{"id": c.id, "status": c.status,
             "confirmed_patient_id": c.confirmed_patient_id,
             "created_at": c.created_at.isoformat()} for c in rows]


@router.get("/handoffs")
def list_handoffs(db: Session = Depends(get_db), status: str = "OPEN"):
    q = db.query(Handoff)
    if status != "ALL":
        q = q.filter(Handoff.status == status)
    out = []
    for h in q.order_by(Handoff.created_at.desc()).all():
        conv = db.get(Conversation, h.conversation_id)
        patient = db.get(Patient, conv.confirmed_patient_id) if conv and conv.confirmed_patient_id else None
        out.append({
            "id": h.id, "conversation_id": h.conversation_id,
            "reason": h.reason, "summary": h.summary, "status": h.status,
            "conversation_status": conv.status if conv else None,
            "confirmed_patient": patient.name if patient else None,
            "created_at": h.created_at.isoformat(),
            "resolved_at": h.resolved_at.isoformat() if h.resolved_at else None,
        })
    return out


@router.post("/handoffs/{handoff_id}/resolve")
def resolve_handoff(handoff_id: int, db: Session = Depends(get_db)):
    h = db.get(Handoff, handoff_id)
    if h is None:
        raise HTTPException(404, detail={"code": "HANDOFF_NOT_FOUND",
                                         "message": "No such handoff."})
    if h.status == "RESOLVED":
        return {"id": h.id, "status": h.status, "already_resolved": True}
    from datetime import datetime, timezone
    h.status = "RESOLVED"
    h.resolved_at = datetime.now(timezone.utc)
    conv = db.get(Conversation, h.conversation_id)
    if conv:
        conv.status = "CLOSED"
    db.commit()
    return {"id": h.id, "status": h.status}


@router.get("/slots/open")
def open_slots_preview(db: Session = Depends(get_db)):
    """Small read-only helper for the dashboard: counts per day."""
    from app.db.models import Slot, Appointment
    from sqlalchemy import func
    taken = select(Appointment.slot_id).where(Appointment.status == "ACTIVE")
    rows = (db.query(Slot.date, func.count(Slot.id))
            .filter(Slot.id.not_in(taken))
            .group_by(Slot.date).order_by(Slot.date).all())
    return [{"date": d.isoformat(), "open_slots": n} for d, n in rows]
