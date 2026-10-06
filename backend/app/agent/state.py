"""State for ONE conversation turn (not the DB — DB stays the truth)."""
from typing import TypedDict, Optional


class TurnState(TypedDict, total=False):
    conversation_id: str
    user_text: str
    history: list[dict]          # openai-style messages for the model
    safety_label: str            # EMERGENCY | CLINICAL | NORMAL | ALREADY_ESCALATED
    safety_matched: str
    proposal: Optional[dict]     # {"id", "name", "args"} pending validation
    events: list[dict]           # this turn's audit events (tool envelope dicts)
    reply: str
    reply_substituted: bool      # caller is reading a deterministic fallback, not the model
    tool_calls: int
    invalid_attempts: int
    fail_closed: bool            # turn cannot be trusted; hand off
    fail_reason: str             # "" = model down; "SYSTEM_ERROR" = tool fault
    nudged: bool                 # first-turn re-ask already used (bounded)
    intent: dict                 # deterministic action/id/phone parsed from user_text
    routed: bool                 # this turn's first proposal came from the router, not the model


def new_turn(conversation_id: str, user_text: str, history: list[dict]) -> TurnState:
    return {
        "conversation_id": conversation_id,
        "user_text": user_text,
        "history": history,
        "safety_label": "",
        "safety_matched": "",
        "proposal": None,
        "events": [],
        "reply": "",
        "reply_substituted": False,
        "tool_calls": 0,
        "invalid_attempts": 0,
        "fail_closed": False,
        "fail_reason": "",
        "nudged": False,
        "intent": {},
        "routed": False,
    }
