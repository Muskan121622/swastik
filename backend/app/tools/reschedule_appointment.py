"""Tool 4 — reschedule_appointment (mutation).

One UPDATE inside one transaction: the row's slot_id moves, the partial
UNIQUE index still guards the destination slot, so a concurrent reschedule
onto the same target cannot both commit. Ownership is re-checked against
the confirmed identity — you cannot move someone else's appointment.
"""
from datetime import date as date_t
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Conversation, Slot, Appointment, Patient, Doctor
from app.schemas.results import (success, failure, IDENTITY_UNCONFIRMED,
                                 SLOT_NOT_FOUND, SLOT_NOT_OPEN,
                                 APPOINTMENT_NOT_FOUND, APPOINTMENT_AMBIGUOUS,
                                 CONVERSATION_ESCALATED)
from app.tools.display import appointment_brief, slot_text


class RescheduleArgs(BaseModel):
    patient_id: int = Field(description="Confirmed patient id")
    new_slot_id: int = Field(description="Destination slot")
    appointment_id: Optional[int] = Field(
        default=None,
        description="Appointment to move. Omit only when the patient has "
                    "exactly one ACTIVE appointment.")


def _find_target(db: Session, args: RescheduleArgs):
    if args.appointment_id:
        appt = db.get(Appointment, args.appointment_id)
        if appt is None or appt.status != "ACTIVE":
            return None, failure(APPOINTMENT_NOT_FOUND,
                                 "No active appointment with that id.")
        if appt.patient_id != args.patient_id:
            # Ownership: never disclose or touch another patient's record.
            return None, failure(APPOINTMENT_NOT_FOUND,
                                 "No active appointment with that id.")
        return appt, None
    rows = db.execute(select(Appointment).where(
        Appointment.patient_id == args.patient_id,
        Appointment.status == "ACTIVE")).scalars().all()
    if not rows:
        return None, failure(APPOINTMENT_NOT_FOUND,
                             "This patient has no active appointment.")
    if len(rows) > 1:
        return None, failure(APPOINTMENT_AMBIGUOUS,
                             "Patient has multiple active appointments. Ask which "
                             "one to move, using the day/time/doctor shown, or "
                             "the appointment_id if the caller states one.",
                             candidates=[appointment_brief(db, a) for a in rows])
    return rows[0], None


def reschedule_appointment(db: Session, conversation_id: str, args: RescheduleArgs) -> dict:
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.status == "ESCALATED":
        return failure(CONVERSATION_ESCALATED,
                       "Conversation is not open for autonomous mutations.")
    if conv.confirmed_patient_id != args.patient_id:
        return failure(IDENTITY_UNCONFIRMED,
                       "Patient identity not confirmed for this conversation.")

    appt, err = _find_target(db, args)
    if err:
        return err

    new_slot = db.get(Slot, args.new_slot_id)
    if new_slot is None or new_slot.date < date_t.today():
        return failure(SLOT_NOT_FOUND, "Destination slot does not exist or is past.")
    if appt.slot_id == args.new_slot_id:
        return failure(SLOT_NOT_OPEN, "That is the appointment's current slot.")

    taken = db.execute(select(Appointment.id).where(
        Appointment.slot_id == args.new_slot_id,
        Appointment.status == "ACTIVE")).scalar_one_or_none()
    if taken:
        return failure(SLOT_NOT_OPEN, "The requested slot is no longer available.")

    old = appointment_brief(db, appt)
    appt.slot_id = args.new_slot_id
    appt.doctor_id = new_slot.doctor_id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return failure(SLOT_NOT_OPEN, "The requested slot is no longer available.")

    patient = db.get(Patient, args.patient_id)
    doctor = db.get(Doctor, new_slot.doctor_id)
    to_slot = {"slot_id": new_slot.id, "date": new_slot.date.isoformat(),
               "start": new_slot.start_time, "end": new_slot.end_time,
               "doctor_id": new_slot.doctor_id,
               "doctor": doctor.name if doctor else None}
    # `display` on both ends so the reply copies committed text instead of
    # recomputing a weekday it cannot see.
    old["display"] = old.get("display") or ""
    to_slot["display"] = slot_text(new_slot.date.isoformat(), new_slot.start_time,
                                   new_slot.end_time, doctor.name if doctor else None)
    return success("RESCHEDULED", appointment_id=appt.id, patient=patient.name,
                   from_slot=old, to_slot=to_slot)
