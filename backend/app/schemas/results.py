"""Machine-checkable tool result envelopes.

The LLM never interprets raw DB exceptions: every tool returns this shape,
and downstream code (grounding validator, policy, tests) branches on the
structured `status` / `error.code`, never on string matching.
"""
from typing import Any, Optional
from pydantic import BaseModel

# ---- status codes (stable API of the tool layer) -------------------------
OK = "OK"
AMBIGUOUS = "AMBIGUOUS"          # multiple patients matched; never auto-pick
NOT_FOUND = "NOT_FOUND"
IDENTITY_UNCONFIRMED = "IDENTITY_UNCONFIRMED"
THIRD_PARTY_IDENTITY = "THIRD_PARTY_IDENTITY"  # a second patient cannot take over the call
SLOT_NOT_FOUND = "SLOT_NOT_FOUND"
SLOT_NOT_OPEN = "SLOT_NOT_OPEN"          # includes concurrent-loss case
PATIENT_NOT_FOUND = "PATIENT_NOT_FOUND"
APPOINTMENT_NOT_FOUND = "APPOINTMENT_NOT_FOUND"
APPOINTMENT_AMBIGUOUS = "APPOINTMENT_AMBIGUOUS"
ALREADY_CANCELLED = "ALREADY_CANCELLED"
CONVERSATION_ESCALATED = "CONVERSATION_ESCALATED"
VALIDATION_ERROR = "VALIDATION_ERROR"
TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"   # defect inside a tool body, contained


class ToolError(BaseModel):
    code: str
    message: str


class ToolResult(BaseModel):
    ok: bool
    status: str
    data: dict[str, Any] = {}
    error: Optional[ToolError] = None


def success(status: str = OK, **data: Any) -> dict:
    return ToolResult(ok=True, status=status, data=data).model_dump()


def failure(status: str, message: str, **data: Any) -> dict:
    return ToolResult(ok=False, status=status, data=data,
                      error=ToolError(code=status, message=message)).model_dump()
