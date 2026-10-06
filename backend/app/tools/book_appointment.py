"""Tool 3 — book_appointment (mutation).

Consistency contract
--------------------
1. Identity gate: conversation.confirmed_patient_id must equal the proposed
   patient_id. No confirmation -> no mutation (even if the LLM "knows" an id).
2. Escalation gate: an ESCALATED conversation is terminal; nothing mutates.
3. Slot uniqueness: enforced by the partial UNIQUE index `uq_active_slot`.
   The INSERT and nothing else decides truth — under concurrency exactly one
   transaction can win; the loser gets IntegrityError -> SLOT_NOT_OPEN.
   (Checked-first-insert design AND the constraint: the constraint is the
   guarantee, the pre-check only produces friendlier errors.)
"""
from datetime import date as date_t

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Conversation, Slot, Appointment, Patient, Doctor
from app.schemas.results import (success, failure, SLOT_NOT_FOUND,
                                 IDENTITY_UNCONFIRMED, SLOT_NOT_OPEN,
                                 CONVERSATION_ESCALATED)
from app.tools.display import slot_text


class BookAppointmentArgs(BaseModel):
    patient_id: int = Field(description="Confirmed patient id")
    slot_id: int = Field(description="Slot to book")


def book_appointment(db: Session, conversation_id: str, args: BookAppointmentArgs) -> dict:
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        return failure(CONVERSATION_ESCALATED, "Conversation not found.")
    if conv.status == "ESCALATED":
        return failure(CONVERSATION_ESCALATED,
                       "Conversation has been escalated to a human; no further "
                       "autonomous mutations are permitted.")
    if conv.confirmed_patient_id is None or conv.confirmed_patient_id != args.patient_id:
        return failure(IDENTITY_UNCONFIRMED,
                       "Patient identity not confirmed for this conversation. "
                       "Resolve the patient by phone first.")

    slot = db.get(Slot, args.slot_id)
    if slot is None:
        return failure(SLOT_NOT_FOUND, "No such slot.")
    if slot.date < date_t.today():
        return failure(SLOT_NOT_FOUND, "Slot is in the past.")

    existing = db.execute(
        select(Appointment).where(Appointment.slot_id == args.slot_id,
                                  Appointment.status == "ACTIVE")
    ).scalar_one_or_none()
    if existing:
        return failure(SLOT_NOT_OPEN, "The requested slot is no longer available.")

    appt = Appointment(patient_id=args.patient_id, doctor_id=slot.doctor_id,
                       slot_id=slot.id, status="ACTIVE")
    db.add(appt)
    try:
        db.commit()  # single atomic transaction: constraint decides the race
    except IntegrityError:
        db.rollback()
        return failure(SLOT_NOT_OPEN, "The requested slot is no longer available.")

    patient = db.get(Patient, args.patient_id)
    doctor = db.get(Doctor, slot.doctor_id)
    doctor_name = doctor.name if doctor else None
    return success("BOOKED", appointment_id=appt.id,
                   patient=patient.name, slot={
                       "slot_id": slot.id, "doctor_id": slot.doctor_id,
                       "doctor": doctor_name,
                       "date": slot.date.isoformat(),
                       "start": slot.start_time, "end": slot.end_time,
                       "display": slot_text(slot.date.isoformat(), slot.start_time,
                                            slot.end_time, doctor_name)})
