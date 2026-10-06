"""The deterministic router: the graph must not depend on the LLM to take a
step the caller's own words plus committed DB state already make obvious.

A 'dumb' MockLLM (empty script -> always prose) stands in for the real gpt-oss
failure mode we logged (Tests 1/9/12/18/22): the model returned text instead of
the tool call it should have made. The router has to carry out the obvious
action anyway, and must NOT act at all when the step is genuinely ambiguous.
"""
import pytest
from sqlalchemy import select

from app.db.models import Appointment, Conversation, Patient, Slot
from app.llm.base import ToolProposal, ReplyProposal
from app.services import conversation_service as svc
from app.agent.graph import _next_obvious_action
from app.agent.intent import extract_intent
from tests.conftest import free_slot


# ---------- intent parsing --------------------------------------------------
def test_intent_extracts_explicit_ids_and_verbs():
    i = extract_intent("please cancel appointment #12 for me")
    assert i.action == "CANCEL" and i.appointment_id == 12

    i = extract_intent("my number is 9820000003, book me with Dr Mehta")
    assert i.has_phone and i.phone == "9820000003" and i.action == "BOOK"

    # a bare availability question is NOT a booking imperative
    i = extract_intent("is there any slot free on Monday?")
    assert i.action is None

    # a weekday number must not be mistaken for an appointment id
    i = extract_intent("can I come Saturday?")
    assert i.appointment_id is None


def test_intent_understands_hinglish_verbs():
    """The router must be as bilingual as the safety gate — the caller's own
    words should not decide whether the deterministic layer helps them."""
    i = extract_intent("meri appointment #7 radd kar do")
    assert i.action == "CANCEL" and i.appointment_id == 7

    i = extract_intent("Kal jo sabse jaldi wala slot hai, woh book kar do")
    assert i.action == "BOOK"

    i = extract_intent("Mujhe Dr. Rao se appointment chahiye")
    assert i.action == "BOOK"

    i = extract_intent("appointment ka time badal do")
    assert i.action == "RESCHEDULE"

    # a cancel verb must never be misread as a booking
    assert extract_intent("cancel kar do").action == "CANCEL"

    i = extract_intent("mera phone number 9820000003 hai")
    assert i.has_phone and i.phone == "9820000003"


# ---------- router decision (pure) -----------------------------------------
def _ev(tool, args, ok=True, status="OK"):
    return {"tool": tool, "arguments": args,
            "result": {"ok": ok, "status": status, "data": {}}}


def test_router_refuses_multi_slot_booking():
    """Restraint: two open slots is the caller's choice, never an auto-book."""
    intent = extract_intent("book the earliest slot please")
    events = [_ev("search_slots", {}, ok=True, status="OK")]
    events[0]["result"]["data"]["slots"] = [{"slot_id": 7}, {"slot_id": 8}]
    assert _next_obvious_action(intent, events, confirmed_pid=3) is None


def test_router_will_not_re_run_a_step_already_taken():
    intent = extract_intent("cancel #5")
    events = [_ev("cancel_appointment", {"patient_id": 3, "appointment_id": 5})]
    assert _next_obvious_action(intent, events, confirmed_pid=3) is None


def test_router_stops_after_an_escalation():
    intent = extract_intent("cancel #5, my phone 9820000003")
    events = [_ev("escalate_to_human", {"reason": "POLICY_BLOCK"},
                 ok=True, status="ESCALATED")]
    assert _next_obvious_action(intent, events, confirmed_pid=3) is None


def test_router_cancel_needs_a_confirmed_caller():
    intent = extract_intent("cancel appointment #5")
    # no confirmed identity -> cannot safely pick whose appointment it is
    assert _next_obvious_action(intent, events=[], confirmed_pid=None) is None


# ---------- end-to-end with a deliberately useless model -------------------
def test_router_looks_up_a_stated_phone_when_model_only_greets(db, mock_llm):
    conv = svc.create_conversation(db)
    mock_llm([])  # every decide() returns prose; the model proposes no tool
    r = svc.run_turn(conv.id, "Hi, this is Priya, my phone is 9820000003")

    lookups = [e for e in r["events"] if e["tool"] == "lookup_patient"]
    assert lookups, "router must resolve the identity the caller volunteered"
    assert lookups[0]["origin"] == "deterministic"
    db.expire_all()  # run_turn wrote through its own session
    fresh = db.get(Conversation, conv.id)
    assert fresh.confirmed_patient_id == 3


def test_router_cancels_explicit_id_model_dropped(db, mock_llm):
    """D5 regression: the model keeps omitting appointment_id. With an explicit
    '#id' in the text and a confirmed caller, the router executes the cancel
    itself; the tool still re-checks ownership before writing."""
    appt = db.execute(select(Appointment).where(
        Appointment.patient_id == 4, Appointment.status == "ACTIVE")).scalars().first()
    assert appt is not None
    conv = Conversation(status="OPEN", confirmed_patient_id=4)
    db.add(conv); db.commit()

    mock_llm([])  # useless model
    r = svc.run_turn(conv.id, f"Please cancel appointment #{appt.id} now")

    cancels = [e for e in r["events"] if e["tool"] == "cancel_appointment"]
    assert cancels and cancels[0]["origin"] == "deterministic"
    assert cancels[0]["result"]["status"] == "CANCELLED"
    db.expire_all()
    assert db.get(Appointment, appt.id).status == "CANCELLED"


def test_router_does_not_touch_someone_elses_appointment(db, mock_llm):
    """Determinism never outranks safety: an id that isn't the confirmed
    caller's must be refused by the tool even though the router proposed it."""
    other = db.execute(select(Appointment).where(
        Appointment.patient_id == 4)).scalars().first()
    conv = Conversation(status="OPEN", confirmed_patient_id=3)  # Priya, not Amit
    db.add(conv); db.commit()

    mock_llm([])
    r = svc.run_turn(conv.id, f"Cancel appointment #{other.id}")

    cancels = [e for e in r["events"] if e["tool"] == "cancel_appointment"]
    assert cancels and cancels[0]["result"]["status"] == "APPOINTMENT_NOT_FOUND"
    db.expire_all()
    assert db.get(Appointment, other.id).status == "ACTIVE"  # untouched


def test_router_leaves_genuinely_ambiguous_turn_to_the_model(db, mock_llm):
    """No explicit id, no phone, ambiguous wording -> the router must stand
    down: the turn ends on the model's reply with zero tool events."""
    conv = svc.create_conversation(db)
    mock_llm([ReplyProposal("Which appointment did you mean?")])
    r = svc.run_turn(conv.id, "I want to cancel something")
    assert r["events"] == []
    assert r["reply"].strip()
    assert r["conversation_status"] == "OPEN"


def test_router_chains_lookup_then_cancel_without_the_model(db, mock_llm):
    """One sentence, zero model help: the caller volunteers a phone AND names
    an appointment. The router resolves identity, and after that tool commits
    it re-routes and carries out the named cancel — a two-step flow the model
    used to have to drive twice."""
    appt = db.execute(select(Appointment).where(
        Appointment.patient_id == 4, Appointment.status == "ACTIVE")).scalars().first()
    conv = svc.create_conversation(db)  # brand new, unconfirmed
    mock_llm([])  # useless model never proposes a tool

    r = svc.run_turn(conv.id,
                     f"It's Amit, phone 9820000004 — please cancel appointment #{appt.id}")

    tools = [e["tool"] for e in r["events"]]
    assert tools == ["lookup_patient", "cancel_appointment"]
    assert all(e["origin"] == "deterministic" for e in r["events"])
    db.expire_all()
    assert db.get(Conversation, conv.id).confirmed_patient_id == 4
    assert db.get(Appointment, appt.id).status == "CANCELLED"
    # the model never described this write, so the reply must be the
    # deterministic confirmation, honestly flagged as substituted
    assert "cancel" in r["reply"].lower()
    assert r["reply_substituted"] is True


def test_router_recovers_hinglish_cancel(db, mock_llm):
    """Same recovery in Hinglish: transliterated verb + explicit id, confirmed
    caller, useless model — the router must still carry the cancel through."""
    appt = db.execute(select(Appointment).where(
        Appointment.patient_id == 4, Appointment.status == "ACTIVE")).scalars().first()
    conv = Conversation(status="OPEN", confirmed_patient_id=4)
    db.add(conv); db.commit()

    mock_llm([])  # model only returns prose
    r = svc.run_turn(conv.id, f"Meri appointment #{appt.id} radd kar do")

    cancels = [e for e in r["events"] if e["tool"] == "cancel_appointment"]
    assert cancels and cancels[0]["origin"] == "deterministic"
    assert cancels[0]["result"]["status"] == "CANCELLED"
    db.expire_all()
    assert db.get(Appointment, appt.id).status == "CANCELLED"
