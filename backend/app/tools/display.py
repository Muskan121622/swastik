"""Human-readable slot/appointment text, computed once in deterministic code.

These strings are handed to the model to be *copied*, not composed. Weekday
arithmetic is precisely the kind of fact that must not be delegated to an LLM:
in live testing the model rendered Thursday 8 Oct as "Saturday 8 Oct" seven
times while every deterministic field in the same turn said Thu.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import Appointment, Doctor, Slot


def when(iso_date) -> str:
    """'2026-10-08' -> 'Thu 8 Oct'."""
    try:
        d = datetime.strptime(str(iso_date), "%Y-%m-%d")
    except ValueError:
        return str(iso_date)
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def slot_text(date_iso, start, end, doctor: str | None = None) -> str:
    base = f"{when(date_iso)}, {start}\u2013{end}"
    return f"{base} with {doctor}" if doctor else base


def appointment_brief(db: Session, appt: Appointment) -> dict:
    """Everything needed to identify one appointment *in the caller's terms*.

    An ambiguity envelope that carries only ids is unusable to the model: the
    caller already said "October 8 at 11:00 AM", and ids cannot be matched
    against that. So every candidate ships its doctor, day and time.
    """
    slot = db.get(Slot, appt.slot_id)
    doctor = db.get(Doctor, appt.doctor_id)
    brief = {"appointment_id": appt.id, "slot_id": appt.slot_id,
             "doctor_id": appt.doctor_id, "status": appt.status}
    if slot:
        brief.update({
            "doctor": doctor.name if doctor else None,
            "date": slot.date.isoformat(),
            "start": slot.start_time,
            "end": slot.end_time,
            "display": slot_text(slot.date.isoformat(), slot.start_time,
                                 slot.end_time, doctor.name if doctor else None),
        })
    return brief
