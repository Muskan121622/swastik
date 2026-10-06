"""The grounding validator must neutralise hallucinated confirmations —
even when the model says them with full confidence and no tool ran."""
from app.agent.grounding import ground_reply, find_ungrounded_claims, UNGROUNDED_FALLBACK


def ev(tool, ok, status):
    return {"tool": tool, "status": "SUCCESS" if ok else "ERROR",
            "result": {"ok": ok, "status": status, "data": {}, "error": None}}


def test_lie_about_booking_is_stripped():
    lie = "Great news — your appointment is booked and confirmed for tomorrow!"
    assert ground_reply(lie, events=[]) == UNGROUNDED_FALLBACK


def test_claim_allowed_with_matching_success_event():
    truth = "Your appointment is booked for tomorrow at 10:00."
    events = [ev("book_appointment", True, "BOOKED")]
    assert ground_reply(truth, events) == truth


def test_failed_booking_blocks_success_claim():
    fake = "Done! Your appointment is booked."
    events = [ev("book_appointment", False, "SLOT_NOT_OPEN")]
    assert ground_reply(fake, events) == UNGROUNDED_FALLBACK


def test_cancel_claim_requires_cancel_event():
    lie = "I've cancelled your appointment."
    events = [ev("book_appointment", True, "BOOKED")]  # wrong tool's success
    assert ground_reply(lie, events) == UNGROUNDED_FALLBACK


def test_already_cancelled_is_grounded_truth():
    reply = "That appointment was already cancelled."
    events = [ev("cancel_appointment", False, "ALREADY_CANCELLED")]
    assert ground_reply(reply, events) == reply


def test_non_mutation_chat_untouched():
    reply = "Dr. Mehta has openings tomorrow at 9:00 and 11:00."
    assert ground_reply(reply, events=[]) == reply


def test_escalation_claim_checked():
    lie = "I've handed you over to a human receptionist."
    assert ground_reply(lie, events=[]) == UNGROUNDED_FALLBACK
