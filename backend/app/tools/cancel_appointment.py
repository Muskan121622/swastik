"""Tool 5 — cancel_appointment (mutation).

Cancelling frees the slot because availability is derived from ACTIVE
appointments — no second bookkeeping flag to keep in sync. Cancelling an
already-cancelled appointment is a structured error, not a silent no-op.
"""
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Conversation, Appointment, Patient
from app.schemas.results import (success, failure, IDENTITY_UNCONFIRMED,
                                 APPOINTMENT_NOT_FOUND, ALREADY_CANCELLED,
                                 APPOINTMENT_AMBIGUOUS, CONVERSATION_ESCALATED)
from app.tools.display import appointment_brief


class CancelArgs(BaseModel):
    patient_id: int = Field(description="Confirmed patient id")
    appointment_id: Optional[int] = Field(
        default=None,
        description="Appointment to cancel. Omit only when the patient has "
                    "exactly one ACTIVE appointment.")


def _find_target(db: Session, args: CancelArgs):
    if args.appointment_id:
        appt = db.get(Appointment, args.appointment_id)
        if appt is None:
            return None, failure(APPOINTMENT_NOT_FOUND, "No appointment with that id.")
        if appt.patient_id != args.patient_id:
            # Ownership first: an id belonging to someone else must not even
            # be reported as "already cancelled".
            return None, failure(APPOINTMENT_NOT_FOUND, "No appointment with that id.")
        if appt.status == "CANCELLED":
            return None, failure(ALREADY_CANCELLED, "That appointment is already cancelled.")
        if appt.status != "ACTIVE":
            return None, failure(APPOINTMENT_NOT_FOUND, "Appointment is not active.")
        return appt, None

    rows = db.execute(select(Appointment).where(
        Appointment.patient_id == args.patient_id,
        Appointment.status == "ACTIVE")).scalars().all()
    if not rows:
        return None, failure(APPOINTMENT_NOT_FOUND, "This patient has no active appointment.")
    if len(rows) > 1:
        # Candidates must be describable in the caller's own terms (day, time,
        # doctor). Ids alone are useless: the caller already said "October 8 at
        # 11:00 AM" and there is nothing here to match those words against.
        return None, failure(APPOINTMENT_AMBIGUOUS,
                             "Patient has multiple active appointments. Ask which "
                             "one to cancel, using the day/time/doctor shown, or "
                             "the appointment_id if the caller states one.",
                             candidates=[appointment_brief(db, a) for a in rows])
    return rows[0], None


def cancel_appointment(db: Session, conversation_id: str, args: CancelArgs) -> dict:
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

    appt.status = "CANCELLED"
    db.commit()
    patient = db.get(Patient, args.patient_id)
    return success("CANCELLED", appointment_id=appt.id, patient=patient.name,
                   freed_slot_id=appt.slot_id,
                   cancelled=appointment_brief(db, appt))
