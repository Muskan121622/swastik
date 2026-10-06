"""Grounding validator — the honesty enforcement point.

A reply may ASSERT a mutation only if a committed, successful tool event
for that mutation exists in the same turn. No event -> the claim is
stripped and replaced with an honest, deterministic fallback.

This is regex-checked code, not a prompt: "don't lie" instructions are
probabilistic; this validator is not.
"""
import re

# assertion phrase -> tool whose SUCCESS result is required to make it
CLAIMS: list[tuple[re.Pattern, str, set[str]]] = [
    (re.compile(r"\b(has|have|had|we'?ve|i'?ve)?\s*been\s*booked\b|"
                r"\bbooked\s+successfully\b|\bis\s+(now\s+)?booked\b|"
                r"\bappointment\s+is\s+confirmed\b|\bconfirmed\s+your\s+appointment\b|"
                r"\bbooking\s+(is\s+)?(complete|confirmed|done)\b", re.I),
     "book_appointment", {"BOOKED"}),
    (re.compile(r"\breschedul(ed|ule confirmed|ule is now)\b", re.I),
     "reschedule_appointment", {"RESCHEDULED"}),
    (re.compile(r"\b(cancell?ed|cancelled|cancellation complete)\b", re.I),
     "cancel_appointment", {"CANCELLED", "ALREADY_CANCELLED"}),
    (re.compile(r"\b(handed (over|to a human)|connected (you )?to a human|"
                r"front desk (will take over|staff )|human receptionist)\b", re.I),
     "escalate_to_human", {"ESCALATED", "ALREADY_ESCALATED"}),
]

UNGROUNDED_FALLBACK = (
    "I'm sorry — I can't actually confirm that action was completed. "
    "I've flagged this for our front desk so a person can help you right away."
)


# statuses that truthfully describe existing state even when ok=False
# (idempotent reports: "already cancelled" is a real, committed fact)
IDEMPOTENT_TRUTHS = {"ALREADY_CANCELLED", "ALREADY_ESCALATED", "NOT_FOUND",
                     "AMBIGUOUS", "SLOT_NOT_OPEN", "IDENTITY_UNCONFIRMED"}


def allowed_statuses(events: list[dict]) -> dict[str, set[str]]:
    """tool_name -> set of result statuses this turn may be asserted from.

    Success envelopes always qualify. Failed envelopes qualify only for
    idempotent-truth codes — they describe the committed DB state as it is
    (e.g. ALREADY_CANCELLED), so honestly mentioning them is grounded.
    """
    out: dict[str, set[str]] = {}
    for ev in events:
        res = ev.get("result", {})
        status = res.get("status", "OK")
        if ev.get("status") == "SUCCESS" and res.get("ok"):
            out.setdefault(ev["tool"], set()).add(status)
        elif status in IDEMPOTENT_TRUTHS:
            out.setdefault(ev["tool"], set()).add(status)
    return out


def find_ungrounded_claims(reply: str, events: list[dict]) -> list[str]:
    ok = allowed_statuses(events)
    violations = []
    for pattern, tool, statuses in CLAIMS:
        if pattern.search(reply or "") and not (ok.get(tool, set()) & statuses):
            violations.append(tool)
    return violations


def ground_reply(reply: str, events: list[dict]) -> str:
    """Return the reply only if every mutation claim is backed by a
    successful tool event; otherwise replace it with the honest fallback."""
    if find_ungrounded_claims(reply, events):
        return UNGROUNDED_FALLBACK
    return reply
