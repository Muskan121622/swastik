"""Tool 2 — search_slots (read).

Availability is DERIVED from the appointments table (single source of
truth): a slot is open iff no ACTIVE appointment references it. There is
no `slot.status` column that could drift from reality.
"""
from datetime import date as date_t
from typing import Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, not_
from sqlalchemy.orm import Session

from app.db.models import Slot, Doctor, Appointment
from app.schemas.results import success, failure, NOT_FOUND
from app.tools.display import slot_text


class SearchSlotsArgs(BaseModel):
    date: Optional[str] = Field(default=None, description="Day to search, YYYY-MM-DD")
    doctor_id: Optional[int] = Field(default=None, description="Filter by doctor id")
    speciality: Optional[str] = Field(default=None, description="Filter by speciality")

    @field_validator("date")
    @classmethod
    def _must_be_iso(cls, v: Optional[str]) -> Optional[str]:
        """Reject "tomorrow" / "7/10/2026" HERE, at the acceptance grammar the
        model is shown, so policy returns a structured VALIDATION_ERROR the
        model can act on. Left unvalidated, the date parse below would raise
        ValueError inside this tool body and take the whole turn down with it."""
        if v:
            date_t.fromisoformat(v)
        return v


def search_slots(db: Session, conversation_id: str, args: SearchSlotsArgs) -> dict:
    active_appt = (select(Appointment.id)
                   .where(Appointment.slot_id == Slot.id,
                          Appointment.status == "ACTIVE")
                   .exists())

    q = (select(Slot, Doctor.name)
         .join(Doctor, Doctor.id == Slot.doctor_id)
         .where(not_(active_appt)))
    if args.date:
        q = q.where(Slot.date == date_t.fromisoformat(args.date))
    if args.doctor_id:
        q = q.where(Slot.doctor_id == args.doctor_id)
    if args.speciality:
        q = q.where(Doctor.speciality.ilike(f"%{args.speciality}%"))
    q = q.order_by(Slot.date, Slot.start_time).limit(20)

    rows = db.execute(q).all()
    if not rows:
        return failure(NOT_FOUND, "No open slots match the requested day/doctor.")

    return success("OK", count=len(rows), slots=[
        {"slot_id": s.id, "doctor_id": s.doctor_id, "doctor": name,
         "date": s.date.isoformat(), "start": s.start_time, "end": s.end_time,
         # Pre-composed so the model quotes a committed string instead of
         # deriving a weekday from an ISO date (it got that wrong repeatedly).
         "display": slot_text(s.date.isoformat(), s.start_time, s.end_time, name)}
        for s, name in rows])
