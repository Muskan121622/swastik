"""Tool registry: the single place where name -> args model -> function is
bound. The policy validator and the LLM tool schema are both generated from
this registry, so they can never disagree about what a tool accepts.
"""
from dataclasses import dataclass, field
from typing import Callable

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.tools.lookup_patient import lookup_patient, LookupPatientArgs
from app.tools.search_slots import search_slots, SearchSlotsArgs
from app.tools.book_appointment import book_appointment, BookAppointmentArgs
from app.tools.reschedule_appointment import (reschedule_appointment,
                                              RescheduleArgs)
from app.tools.cancel_appointment import cancel_appointment, CancelArgs
from app.tools.escalate_to_human import escalate_to_human, EscalateArgs


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    fn: Callable[..., dict]
    mutates: bool = False
    terminal: bool = False


REGISTRY: dict[str, ToolSpec] = {spec.name: spec for spec in [
    ToolSpec("lookup_patient",
             "Find a patient by name, phone or date of birth. Never guess "
             "between candidates — a multi-match must be clarified with the caller.",
             LookupPatientArgs, lookup_patient),
    ToolSpec("search_slots",
             "List open consultation slots, optionally by date (YYYY-MM-DD), "
             "doctor_id or speciality.",
             SearchSlotsArgs, search_slots),
    ToolSpec("book_appointment",
             "Book a slot for the identity-confirmed patient of this conversation.",
             BookAppointmentArgs, book_appointment, mutates=True),
    ToolSpec("reschedule_appointment",
             "Move the confirmed patient's active appointment to a new slot.",
             RescheduleArgs, reschedule_appointment, mutates=True),
    ToolSpec("cancel_appointment",
             "Cancel the confirmed patient's active appointment.",
             CancelArgs, cancel_appointment, mutates=True),
    ToolSpec("escalate_to_human",
             "Hand the conversation to a human receptionist. Terminal: after "
             "this, no further tools run in the conversation.",
             EscalateArgs, escalate_to_human, mutates=True, terminal=True),
]}


def openai_style_schemas() -> list[dict]:
    """Tool definitions for the LLM, derived from the same Pydantic models
    the validator enforces — proposal grammar == acceptance grammar."""
    out = []
    for s in REGISTRY.values():
        out.append({
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.description,
                "parameters": s.args_model.model_json_schema(),
            },
        })
    return out
