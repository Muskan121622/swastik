"""Deterministic intent extraction — no model, no network.

The agent loop used to ask the LLM for *every* next action. When gpt-oss
returned prose or an empty completion after a successful read (a very common
real failure we logged in Tests 12/18/22), the turn stalled even though the
right tool was obvious from the caller's own words plus the DB state.

This module extracts that obvious next step from `user_text` and committed
state so the graph can route straight to `policy`. It NEVER decides business
truth: it only chooses a tool name + the arguments the caller already typed.
Every safety rule still lives in the deterministic tools, which re-check the
confirmed identity and appointment ownership before touching anything.
"""
import re
from dataclasses import dataclass
from typing import Optional

# An appointment the caller names explicitly: "#12", "appt 12", "appointment
# #12", "reference 12", "numbar 12". Requires a nearby noun or a hash so a bare
# time ("4pm") or day ("8") is never mistaken for an id.
APPT_ID = re.compile(
    r"#\s*(\d+)\b"
    r"|\b(?:appt|appointment|reference|ref|booking|numbar|number)\s*(?:id|no|number|#)?\s*(\d+)\b",
    re.IGNORECASE,
)

# English AND Hinglish/transliterated verbs, mirroring the bilingual safety
# gate. The caller's own words must not decide whether the deterministic layer
# helps them — a Hindi speaker gets the same no-LLM recovery as an English one.
# Every match here is only a *candidate*: a cancel still needs an explicit id +
# a confirmed caller, a book still needs exactly one searched slot.
CANCEL = re.compile(
    r"\bcancel\b|\bdelete\b|\bremove\b|\bdrop\b|"
    r"\bradd\b|\bhata(?:o|wa|way|do|de|dena)?\b|\bhatana\b|"
    r"\breh\s*(?:de|do|dena|nahi)\b", re.IGNORECASE)
RESCHEDULE = re.compile(
    r"\breschedule\b|\bmove\b|\bshift\b|\bchange\b|"
    r"\bbadal\b|\bbadlo\b|\bdusro?\b|\bdoosro?\b|\bany[ie]\b", re.IGNORECASE)
# An imperative to actually reserve a slot. Kept separate from a mere
# availability question so the router never auto-books someone who only
# asked "is there a slot free?". Includes transliterated "kar do / le lo /
# chahiye"; CANCEL and RESCHEDULE are checked first so "cancel kar do" never
# reads as a booking.
BOOK = re.compile(r"\bbook\b|\bconfirm\b|\bschedule\b|\bfix\b|"
                  r"\bput\s+me\s+down\b|\bi'?ll\s+take\b|"
                  r"\bkar\s*(?:do|dena|dijiye|donga|dijiye)\b|"
                  r"\ble\s*(?:lo|lijiye|leta|lungi|loonga)\b|\blena\s+hai\b|"
                  r"\bchahiye\b|\bdila(?:na|do|dijiye)?\b", re.IGNORECASE)

# A phone the caller typed: 10+ digits, optionally with +91/spaces/dashes.
PHONE = re.compile(r"(\+?91[\s-]?)?((?:\d[\s-]?){10})")


@dataclass
class Intent:
    action: Optional[str] = None        # CANCEL | RESCHEDULE | BOOK | None
    appointment_id: Optional[int] = None
    phone: Optional[str] = None
    has_phone: bool = False

    @property
    def wants_mutation(self) -> bool:
        return self.action in ("CANCEL", "RESCHEDULE", "BOOK")


def extract_intent(user_text: str) -> Intent:
    text = user_text or ""
    m = APPT_ID.search(text)
    appt_id: Optional[int] = None
    if m:
        appt_id = int(m.group(1) or m.group(2))

    # priority: an explicit cancel beats reschedule beats book. A reschedule
    # is NOT auto-routed (its destination slot needs language the model is
    # better at), but we still record the verb so nothing misreads it as cancel.
    action: Optional[str] = None
    if CANCEL.search(text):
        action = "CANCEL"
    elif RESCHEDULE.search(text):
        action = "RESCHEDULE"
    elif BOOK.search(text):
        action = "BOOK"

    pm = PHONE.search(text)
    phone = None
    if pm:
        phone = re.sub(r"\D", "", pm.group(0))[-10:]

    return Intent(action=action, appointment_id=appt_id, phone=phone,
                  has_phone=bool(pm))
