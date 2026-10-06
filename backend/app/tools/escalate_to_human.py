"""Tool 6 — escalate_to_human (TERMINAL mutation).

Sets the conversation to ESCALATED and opens a Handoff. From this point
the graph refuses to run ANY other tool for this conversation — emergency
handling is a hard stop, not a suggestion to the model.
"""
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Conversation, Handoff
from app.schemas.results import success, failure, CONVERSATION_ESCALATED


class EscalateArgs(BaseModel):
    reason: Literal["EMERGENCY", "CLINICAL", "LLM_UNAVAILABLE", "POLICY_BLOCK", "USER_REQUEST"] = Field(
        description="Why a human must take over")
    summary: str = Field(description="Short handoff note for the receptionist")


def escalate_to_human(db: Session, conversation_id: str, args: EscalateArgs) -> dict:
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        return failure(CONVERSATION_ESCALATED, "Conversation not found.")

    already = (db.query(Handoff)
               .filter_by(conversation_id=conversation_id, status="OPEN")
               .first())
    if conv.status == "ESCALATED" and already:
        return success("ALREADY_ESCALATED", handoff_id=already.id,
                       reason=already.reason)

    conv.status = "ESCALATED"
    h = Handoff(conversation_id=conversation_id, reason=args.reason,
                summary=args.summary, status="OPEN")
    db.add(h)
    db.commit()
    return success("ESCALATED", handoff_id=h.id, reason=args.reason)
