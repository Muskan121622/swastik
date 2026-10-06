"""Unit tests: the six tools are pure deterministic functions — no LLM,
no graph. Every safety-relevant behaviour is provable here alone."""
import pytest
from pydantic import ValidationError

from app.tools.lookup_patient import lookup_patient, LookupPatientArgs
from app.tools.search_slots import search_slots, SearchSlotsArgs
from app.tools.book_appointment import book_appointment, BookAppointmentArgs
from app.tools.reschedule_appointment import reschedule_appointment, RescheduleArgs
from app.tools.cancel_appointment import cancel_appointment, CancelArgs
from app.tools.escalate_to_human import escalate_to_human, EscalateArgs
from app.db.models import Appointment, Conversation, Handoff
from tests.conftest import free_slot


# ---------- lookup_patient: ambiguity is deterministic ---------------------
def test_name_only_is_ambiguous_never_auto_pick(db, conv):
    r = lookup_patient(db, conv.id, LookupPatientArgs(name="Rahul"))
    assert r["ok"] is False
    assert r["status"] == "AMBIGUOUS"
    ids = {c["id"] for c in r["data"]["candidates"]}
    assert ids == {1, 2}
    assert conv.confirmed_patient_id is None  # nothing was confirmed


def test_name_match_alone_does_not_confirm_identity(db, conv):
    r = lookup_patient(db, conv.id, LookupPatientArgs(name="Priya Singh"))
    assert r["ok"] and r["status"] == "FOUND"
    assert r["data"]["identity_confirmed"] is False
    assert conv.confirmed_patient_id is None


def test_phone_confirms_identity(db, conv):
    r = lookup_patient(db, conv.id, LookupPatientArgs(phone="98200 00003"))
    assert r["data"]["identity_confirmed"] is True
    assert conv.confirmed_patient_id == 3


def test_name_plus_dob_confirms(db, conv):
    r = lookup_patient(db, conv.id,
                       LookupPatientArgs(name="Rahul", dob="1988-09-03"))
    assert r["data"]["identity_confirmed"] is True
    assert conv.confirmed_patient_id == 2


def test_phone_lookup_masks_pii(db, conv):
    r = lookup_patient(db, conv.id, LookupPatientArgs(phone="+91-9820000001"))
    assert "XXXXX" in r["data"]["patient"]["phone"]


def test_not_found(db, conv):
    r = lookup_patient(db, conv.id, LookupPatientArgs(name="Nobody Here"))
    assert r["status"] == "NOT_FOUND"


# ---------- search_slots: availability derived from appointments ----------
def test_search_excludes_taken_slots(db, conv):
    r = search_slots(db, conv.id, SearchSlotsArgs(doctor_id=2))
    assert r["ok"]
    # seed pre-booked doctor 2's first slot -> it must not appear as open
    taken = {a.slot_id for a in db.query(Appointment).filter_by(status="ACTIVE")}
    assert taken & {s["slot_id"] for s in r["data"]["slots"]} == set()


def test_search_empty_result_is_not_found_not_empty_list(db, conv):
    """A date beyond the seed horizon has no slots - must be NOT_FOUND,
    so the agent never offers the caller a blank menu it must invent."""
    r = search_slots(db, conv.id, SearchSlotsArgs(date="2030-01-01"))
    assert r["ok"] is False and r["status"] == "NOT_FOUND"


def test_search_invalid_date_is_rejected_not_silently_ignored(db, conv):
    """Checklist #3: invalid date. A bad date must not degrade into
    "all slots" - the caller would be offered days that were never asked for."""
    with pytest.raises(Exception) as exc:
        search_slots(db, conv.id, SearchSlotsArgs(date="31-12-2026"))
    assert "date" in str(exc.value).lower() or "isoformat" in str(exc.value).lower()


def test_search_invalid_doctor_id_returns_not_found(db, conv):
    """Checklist #3: invalid doctor. Unknown filters yield no rows, not a crash."""
    r = search_slots(db, conv.id, SearchSlotsArgs(doctor_id=9999))
    assert r["ok"] is False and r["status"] == "NOT_FOUND"


# ---------- book_appointment: gates + invariants ----------------------------
def test_book_requires_confirmed_identity(db, conv):
    slot = free_slot(db)
    r = book_appointment(db, conv.id, BookAppointmentArgs(patient_id=3, slot_id=slot.id))
    assert r["status"] == "IDENTITY_UNCONFIRMED"
    assert db.query(Appointment).count() == 1  # only the seeded one; nothing added


def test_book_cannot_act_as_another_patient(db, confirmed_conv):
    """Identity confirmed = Priya(3); LLM proposes patient_id=1 (a 'Rahul')."""
    slot = free_slot(db)
    r = book_appointment(db, confirmed_conv.id,
                         BookAppointmentArgs(patient_id=1, slot_id=slot.id))
    assert r["status"] == "IDENTITY_UNCONFIRMED"


def test_book_success(db, confirmed_conv):
    slot = free_slot(db)
    r = book_appointment(db, confirmed_conv.id,
                         BookAppointmentArgs(patient_id=3, slot_id=slot.id))
    assert r["ok"] and r["status"] == "BOOKED"
    assert db.query(Appointment).filter_by(slot_id=slot.id, status="ACTIVE").count() == 1


def test_double_book_same_slot(db, confirmed_conv):
    slot = free_slot(db)
    assert book_appointment(db, confirmed_conv.id,
                            BookAppointmentArgs(patient_id=3, slot_id=slot.id))["ok"]
    r2 = book_appointment(db, confirmed_conv.id,
                          BookAppointmentArgs(patient_id=3, slot_id=slot.id))
    assert r2["ok"] is False and r2["status"] == "SLOT_NOT_OPEN"


def test_fake_slot(db, confirmed_conv):
    r = book_appointment(db, confirmed_conv.id,
                         BookAppointmentArgs(patient_id=3, slot_id=999999))
    assert r["status"] == "SLOT_NOT_FOUND"


# ---------- reschedule / cancel ---------------------------------------------
def _book(db, conv, patient_id, doctor_id=1):
    slot = free_slot(db, doctor_id=doctor_id)
    r = book_appointment(db, conv.id, BookAppointmentArgs(patient_id=patient_id,
                                                          slot_id=slot.id))
    assert r["ok"], r
    return r["data"]["appointment_id"], slot


def test_reschedule_moves_slot_and_frees_old(db, confirmed_conv):
    appt_id, old_slot = _book(db, confirmed_conv, 3)
    new_slot = free_slot(db, doctor_id=2, exclude_ids=[old_slot.id])
    r = reschedule_appointment(db, confirmed_conv.id,
                               RescheduleArgs(patient_id=3, new_slot_id=new_slot.id))
    assert r["ok"] and r["status"] == "RESCHEDULED"
    a = db.get(Appointment, appt_id)
    assert a.slot_id == new_slot.id
    # old slot open again, new slot taken
    assert search_slots(db, confirmed_conv.id,
                        SearchSlotsArgs())["data"]["count"] > 0
    assert db.query(Appointment).filter_by(slot_id=new_slot.id, status="ACTIVE").count() == 1


def test_reschedule_destination_taken(db, confirmed_conv):
    appt_id, _ = _book(db, confirmed_conv, 3)
    other = free_slot(db, doctor_id=2)
    book_appointment(db, confirmed_conv.id,
                     BookAppointmentArgs(patient_id=3, slot_id=other.id))
    r = reschedule_appointment(db, confirmed_conv.id,
                               RescheduleArgs(patient_id=3, appointment_id=appt_id,
                                              new_slot_id=other.id))
    assert r["status"] == "SLOT_NOT_OPEN"


def test_reschedule_other_patients_appointment_is_invisible(db, confirmed_conv):
    """Amit(4) has the seeded appointment; Priya(3, confirmed) must not move it."""
    amit_appt = db.query(Appointment).filter_by(patient_id=4).first()
    new_slot = free_slot(db, doctor_id=3)
    r = reschedule_appointment(db, confirmed_conv.id,
                               RescheduleArgs(patient_id=3, appointment_id=amit_appt.id,
                                              new_slot_id=new_slot.id))
    assert r["status"] == "APPOINTMENT_NOT_FOUND"
    assert db.get(Appointment, amit_appt.id).slot_id == amit_appt.slot_id


def test_cancel_and_idempotent_cancel(db, confirmed_conv):
    appt_id, _ = _book(db, confirmed_conv, 3)
    r = cancel_appointment(db, confirmed_conv.id, CancelArgs(patient_id=3))
    assert r["ok"] and r["status"] == "CANCELLED"
    r2 = cancel_appointment(db, confirmed_conv.id,
                            CancelArgs(patient_id=3, appointment_id=appt_id))
    assert r2["status"] in ("APPOINTMENT_NOT_FOUND", "ALREADY_CANCELLED")


def test_cancel_wrong_patient_blocked(db, confirmed_conv):
    amit_appt = db.query(Appointment).filter_by(patient_id=4).first()
    r = cancel_appointment(db, confirmed_conv.id,
                           CancelArgs(patient_id=3, appointment_id=amit_appt.id))
    assert r["status"] == "APPOINTMENT_NOT_FOUND"
    assert db.get(Appointment, amit_appt.id).status == "ACTIVE"


def test_cancel_nonexistent_appointment_id(db, confirmed_conv):
    """Checklist #7: "Cancel appointment A999999" -> structured not-found."""
    r = cancel_appointment(db, confirmed_conv.id,
                           CancelArgs(patient_id=3, appointment_id=987654))
    assert r["ok"] is False and r["status"] == "APPOINTMENT_NOT_FOUND"


def test_reschedule_nonexistent_appointment_id(db, confirmed_conv):
    _, slot = _book(db, confirmed_conv, 3)
    dest = free_slot(db, doctor_id=2)
    r = reschedule_appointment(db, confirmed_conv.id,
                               RescheduleArgs(patient_id=3, appointment_id=987654,
                                              new_slot_id=dest.id))
    assert r["status"] == "APPOINTMENT_NOT_FOUND"


def test_reschedule_to_its_current_slot_is_refused(db, confirmed_conv):
    """Checklist #3: "same destination slot" must not read as a success."""
    appt_id, slot = _book(db, confirmed_conv, 3)
    r = reschedule_appointment(db, confirmed_conv.id,
                               RescheduleArgs(patient_id=3, appointment_id=appt_id,
                                              new_slot_id=slot.id))
    assert r["ok"] is False and r["status"] == "SLOT_NOT_OPEN"
    assert db.get(Appointment, appt_id).slot_id == slot.id


def test_reschedule_to_hallucinated_slot(db, confirmed_conv):
    _book(db, confirmed_conv, 3)
    r = reschedule_appointment(db, confirmed_conv.id,
                               RescheduleArgs(patient_id=3, new_slot_id=999999))
    assert r["status"] == "SLOT_NOT_FOUND"


def test_cancel_frees_slot_for_rebooking(db, confirmed_conv):
    _, slot = _book(db, confirmed_conv, 3)
    cancel_appointment(db, confirmed_conv.id, CancelArgs(patient_id=3))
    r = book_appointment(db, confirmed_conv.id,
                         BookAppointmentArgs(patient_id=3, slot_id=slot.id))
    assert r["ok"]  # slot genuinely released: derived availability agrees


def test_cancel_multiple_actives_returns_candidates(db, confirmed_conv):
    a1, _ = _book(db, confirmed_conv, 3, doctor_id=1)
    a2, _ = _book(db, confirmed_conv, 3, doctor_id=3)
    r = cancel_appointment(db, confirmed_conv.id, CancelArgs(patient_id=3))
    assert r["status"] == "APPOINTMENT_AMBIGUOUS"
    assert {c["appointment_id"] for c in r["data"]["candidates"]} == {a1, a2}
    # nothing was cancelled without the model choosing explicitly
    assert db.query(Appointment).filter_by(status="ACTIVE").count() == 3


# ---------- escalate: terminal ---------------------------------------------
def test_escalate_creates_handoff_and_locks(db, confirmed_conv):
    r = escalate_to_human(db, confirmed_conv.id,
                          EscalateArgs(reason="USER_REQUEST", summary="wants a human"))
    assert r["ok"] and r["status"] == "ESCALATED"
    assert db.get(Conversation, confirmed_conv.id).status == "ESCALATED"
    assert db.query(Handoff).filter_by(status="OPEN").count() == 1
    # and no further mutation is possible
    slot = free_slot(db)
    blocked = book_appointment(db, confirmed_conv.id,
                               BookAppointmentArgs(patient_id=3, slot_id=slot.id))
    assert blocked["status"] == "CONVERSATION_ESCALATED"


# ---------- live-run defects: D1 display, D2 candidates, D4 identity --------
def test_slot_display_is_precomposed_and_correct(db, conv):
    """D1: the model must be handed the words to copy, not an ISO date to
    translate. 2026-10-08 is a Thursday; gpt-oss called it Saturday seven
    times while the deterministic fields in the same turn said Thu."""
    from app.tools.display import when
    assert when("2026-10-08") == "Thu 8 Oct"

    r = search_slots(db, conv.id, SearchSlotsArgs())
    assert r["ok"]
    for s in r["data"]["slots"]:
        assert s["display"].startswith(when(s["date"]))
        assert s["start"] in s["display"] and s["end"] in s["display"]
        assert s["doctor"] in s["display"]


def test_ambiguous_candidates_are_describable_in_callers_terms(db, confirmed_conv):
    """D2: a caller who says "October 8 at 11:00 AM" must be answerable —
    candidates carrying only ids made Tests 8 and 11 unresolvable."""
    from app.tools.display import when
    _book(db, confirmed_conv, 3, doctor_id=1)
    _book(db, confirmed_conv, 3, doctor_id=3)
    r = cancel_appointment(db, confirmed_conv.id, CancelArgs(patient_id=3))
    assert r["status"] == "APPOINTMENT_AMBIGUOUS"
    cands = r["data"]["candidates"]
    assert len(cands) >= 2
    for c in cands:
        assert c["doctor"] and c["date"] and c["start"] and c["end"]
        # the display line is what the model is allowed to quote back
        assert c["display"].startswith(when(c["date"]))
        assert c["start"] in c["display"] and c["doctor"] in c["display"]


def test_second_patient_phone_cannot_take_over_a_confirmed_call(db, confirmed_conv):
    """D4 (live Test 10/20): the caller was asked for Priya's phone, typed it,
    and the conversation silently became Priya. Knowledge of someone else's
    number is not authorization to become them."""
    r = lookup_patient(db, confirmed_conv.id, LookupPatientArgs(phone="9820000001"))
    assert r["ok"] is False
    assert r["status"] == "THIRD_PARTY_IDENTITY"
    assert confirmed_conv.confirmed_patient_id == 3  # never re-bound

    # the mutation that re-bind was steering toward stays blocked
    amit_appt = db.query(Appointment).filter_by(patient_id=4).first()
    c = cancel_appointment(db, confirmed_conv.id,
                           CancelArgs(patient_id=4, appointment_id=amit_appt.id))
    assert c["status"] == "IDENTITY_UNCONFIRMED"
    assert db.get(Appointment, amit_appt.id).status == "ACTIVE"


def test_same_patient_may_still_reconfirm(db, confirmed_conv):
    """The guard must not break the normal case: re-stating your own phone."""
    r = lookup_patient(db, confirmed_conv.id, LookupPatientArgs(phone="9820000003"))
    assert r["ok"] and r["data"]["identity_confirmed"] is True
    assert confirmed_conv.confirmed_patient_id == 3
