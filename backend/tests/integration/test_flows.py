"""End-to-end turns through the LangGraph with scripted MockLLM decisions.
These prove the GRAPH wiring (gate -> agent -> policy -> executor ->
grounding), with zero dependence on any real model."""
import pytest

from app.db.models import Appointment, Conversation, Handoff, ToolEvent
from app.llm.base import ToolProposal, ReplyProposal
from app.services import conversation_service as svc
from tests.conftest import free_slot


def test_full_booking_flow_scripted(db, mock_llm):
    conv = svc.create_conversation(db)
    slot = free_slot(db, doctor_id=1)
    mock_llm([
        ToolProposal("lookup_patient", {"phone": "+91-9820000003"}),
        ToolProposal("search_slots", {"date": slot.date.isoformat(),
                                      "doctor_id": 1}),
        ToolProposal("book_appointment", {"patient_id": 3, "slot_id": slot.id}),
        ReplyProposal(f"Your appointment is booked for {slot.date.isoformat()} "
                      f"at {slot.start_time}."),
    ])
    r = svc.run_turn(conv.id, "I'm Priya, 9820000003. Book me with Dr. Mehta "
                              f"on {slot.date.isoformat()} at {slot.start_time}")
    assert r["conversation_status"] == "OPEN"
    assert "booked" in r["reply"].lower()
    assert db.query(Appointment).filter_by(slot_id=slot.id, status="ACTIVE").count() == 1
    # every tool call audited
    tools_called = [e["tool"] for e in r["events"]]
    assert tools_called == ["lookup_patient", "search_slots", "book_appointment"]


def test_ambiguous_rahul_flow(db, mock_llm):
    conv = svc.create_conversation(db)
    mock_llm([
        ToolProposal("lookup_patient", {"name": "Rahul"}),
        ReplyProposal("I found multiple patients named Rahul. Could you share "
                      "your registered phone number?"),
    ])
    r = svc.run_turn(conv.id, "Book an appointment for Rahul")
    amb = [e for e in r["events"] if e["tool"] == "lookup_patient"][0]
    assert amb["result"]["status"] == "AMBIGUOUS"
    assert "multiple" in r["reply"].lower()
    assert conv.confirmed_patient_id is None
    assert db.query(Appointment).count() == 1  # seeded only -> no guessing-book


def test_emergency_mid_workflow_hard_stops(db, mock_llm):
    """Caller goes emergency WHILE a booking is in progress."""
    conv = svc.create_conversation(db)
    mock_llm([ToolProposal("lookup_patient", {"name": "Priya"})])
    svc.run_turn(conv.id, "Book me with Dr. Mehta")

    mock_llm([])  # any LLM use from here is a test failure of the gate
    r = svc.run_turn(conv.id, "Actually I'm having severe chest pain right now")
    assert r["conversation_status"] == "ESCALATED"
    assert r["safety_label"] == "EMERGENCY"
    assert "emergency" in r["reply"].lower()
    h = db.query(Handoff).filter_by(conversation_id=conv.id).first()
    assert h and h.reason == "EMERGENCY" and h.status == "OPEN"
    m = mock_llm([])
    # follow-up tries to resume booking: LLM must NOT be consulted, tools NOT run
    r2 = svc.run_turn(conv.id, "Anyway, can you still book the 4pm slot?")
    assert r2["conversation_status"] == "ESCALATED"
    assert len(m.calls) == 0


def test_audit_trail_is_persisted(db, mock_llm):
    conv = svc.create_conversation(db)
    slot = free_slot(db)
    mock_llm([
        ToolProposal("lookup_patient", {"phone": "9820000003"}),
        ToolProposal("book_appointment", {"patient_id": 3, "slot_id": slot.id}),
        ReplyProposal("All set! Your appointment is booked."),
    ])
    svc.run_turn(conv.id, "Priya here, 9820000003, book the first slot")
    events = db.query(ToolEvent).filter_by(conversation_id=conv.id).all()
    assert len(events) == 2
    import json
    book = [e for e in events if e.tool_name == "book_appointment"][0]
    res = json.loads(book.result)
    assert res["status"] == "BOOKED" and book.status == "SUCCESS"


def test_rest_api_roundtrip(client, db, mock_llm):
    c = client.post("/api/conversations")
    assert c.status_code == 200
    cid = c.json()["conversation_id"]

    mock_llm([ToolProposal("lookup_patient", {"phone": "9820000003"}),
              ReplyProposal("Thanks Priya, how can I help?")])
    r = client.post(f"/api/conversations/{cid}/messages",
                    json={"content": "Hi, it's Priya, my phone is 9820000003"})
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] and "events" in body and "safety_label" in body

    detail = client.get(f"/api/conversations/{cid}").json()
    assert detail["confirmed_patient"]["name"] == "Priya Singh"
    assert len(detail["messages"]) == 2

    assert client.get("/api/conversations/does-not-exist").status_code == 404
    assert client.get("/api/health").json() == {"ok": True}


def test_handoff_queue_and_resolve(client, db, mock_llm):
    conv = svc.create_conversation(db)
    mock_llm([])
    svc.run_turn(conv.id, "My father had an accident, he's unconscious")

    q = client.get("/api/handoffs", params={"status": "OPEN"}).json()
    assert len(q) >= 1
    hid = q[0]["id"]
    assert q[0]["reason"] == "EMERGENCY"

    res = client.post(f"/api/handoffs/{hid}/resolve")
    assert res.status_code == 200 and res.json()["status"] == "RESOLVED"
    again = client.get("/api/handoffs", params={"status": "OPEN"}).json()
    assert all(h["id"] != hid for h in again)
    assert client.post("/api/handoffs/99999/resolve").status_code == 404


def test_model_is_shown_the_current_message(db, mock_llm):
    """Regression: an off-by-one used to hide the caller's LATEST message
    from the model, so it acted on stale context (and greeted on turn 1)."""
    conv = svc.create_conversation(db)
    marker = "9820013377-please-check-this-number"
    m = mock_llm([ToolProposal("lookup_patient", {"phone": "9820013377"}),
                  ReplyProposal("That number isn't in our records.")])
    svc.run_turn(conv.id, f"Can you look me up? {marker}")
    first_call = m.calls[0]
    assert any(marker in (msg.get("content") or "") for msg in first_call)


def test_model_proposing_relative_date_does_not_kill_the_turn(db, mock_llm):
    """A real gpt-oss failure mode: date="tomorrow". Schema rejects it, the
    model gets the structured error, and the caller still receives a reply —
    never a 500 and never a hand-off for a merely confused proposal."""
    conv = svc.create_conversation(db)
    mock_llm([
        ToolProposal("search_slots", {"date": "tomorrow"}),
        ReplyProposal("Which day would you like? Please give me a date."),
    ])
    r = svc.run_turn(conv.id, "Any slot tomorrow?")
    ev = [e for e in r["events"] if e["tool"] == "search_slots"][0]
    assert ev["status"] == "BLOCKED"
    assert "VALIDATION_ERROR" in str(ev["result"])
    assert "date" in str(ev["result"]).lower()  # the model is told what is wrong
    assert r["reply"].strip()
    assert r["conversation_status"] == "OPEN"


def test_unexpected_tool_crash_is_contained_not_propagated(db, mock_llm, monkeypatch):
    """Fail-closed means "any", including a bug inside a tool body: the turn
    must end in a human handoff, the transcript must show the failed call, and
    the conversation must be locked so nothing mutates after the trust break."""
    from dataclasses import replace
    from app.tools import registry

    def explode(*_args, **_kwargs):
        raise RuntimeError("simulated tool-body defect")

    boom = replace(registry.REGISTRY["search_slots"], fn=explode)
    monkeypatch.setitem(registry.REGISTRY, "search_slots", boom)

    conv = svc.create_conversation(db)
    before = db.query(Appointment).count()
    mock_llm([ToolProposal("search_slots", {"date": "2026-10-07"})])

    r = svc.run_turn(conv.id, "Check availability for me please")

    ev = [e for e in r["events"] if e["tool"] == "search_slots"][0]
    assert ev["status"] == "ERROR" and ev["result"]["ok"] is False
    assert ev["result"]["status"] == "TOOL_EXECUTION_ERROR"
    assert "RuntimeError" in str(ev["result"]["error"]["message"])  # not swallowed
    assert r["handoff"] and r["handoff"]["reason"] == "SYSTEM_ERROR"
    assert r["conversation_status"] == "ESCALATED"
    assert db.query(Appointment).count() == before  # no half-written state
    # the conversation is now locked: a follow-up cannot mutate anything
    mock_llm([ToolProposal("book_appointment", {"patient_id": 3, "slot_id": 1})])
    r2 = svc.run_turn(conv.id, "Anyway just book me")
    assert r2["conversation_status"] == "ESCALATED"
    assert db.query(Appointment).count() == before

