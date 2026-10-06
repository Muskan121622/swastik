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
    tool_calls: int
    invalid_attempts: int
    fail_closed: bool            # LLM unavailable this turn
    nudged: bool                 # first-turn re-ask already used (bounded)


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
        "tool_calls": 0,
        "invalid_attempts": 0,
        "fail_closed": False,
        "nudged": False,
    }
