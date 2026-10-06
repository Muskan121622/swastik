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


# ---------- D7: a read-only turn must still answer, never greet -------------
def rev(tool, ok, status, data):
    return {"tool": tool, "status": "SUCCESS" if ok else "ERROR",
            "result": {"ok": ok, "status": status, "data": data, "error": None}}


def test_search_results_are_templated_not_dropped():
    from app.agent.graph import _template_from_events, NO_REPLY_FALLBACK
    events = [rev("search_slots", True, "OK", {"count": 3, "slots": [
        {"slot_id": 1, "display": "Thu 8 Oct, 09:00–09:30 with Dr. Kulkarni"}]})]
    t = _template_from_events(events)
    assert t and t != NO_REPLY_FALLBACK
    assert "Thu 8 Oct, 09:00–09:30 with Dr. Kulkarni" in t


def test_no_availability_is_stated_instead_of_a_greeting():
    """Live Test 18: the tools knew 25 Dec was empty and the caller was asked
    how they could be helped."""
    from app.agent.graph import _template_from_events, NO_REPLY_FALLBACK
    t = _template_from_events([rev("search_slots", False, "NOT_FOUND", {})])
    assert t and t != NO_REPLY_FALLBACK
    assert "couldn't find an open slot" in t.lower()


def test_ambiguous_appointments_are_listed_with_their_times():
    """Live Tests 8/11/12 ended in the canned opener; they must end in a
    question the caller can actually answer."""
    from app.agent.graph import _template_from_events, NO_REPLY_FALLBACK
    events = [rev("cancel_appointment", False, "APPOINTMENT_AMBIGUOUS", {"candidates": [
        {"appointment_id": 12, "display": "Thu 8 Oct, 11:00–11:30 with Dr. Mehta"},
        {"appointment_id": 13, "display": "Thu 8 Oct, 14:00–14:30 with Dr. Mehta"}]})]
    t = _template_from_events(events)
    assert t != NO_REPLY_FALLBACK
    assert "#12" in t and "#13" in t and "Thu 8 Oct" in t


def test_third_party_refusal_is_explained():
    from app.agent.graph import _template_from_events, NO_REPLY_FALLBACK
    t = _template_from_events([rev("lookup_patient", False, "THIRD_PARTY_IDENTITY", {})])
    assert t and t != NO_REPLY_FALLBACK
    assert "someone else" in t.lower()
