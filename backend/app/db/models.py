"""Schema. The one source of truth for clinic state.

Booking invariant
-----------------
`uq_active_slot` is a PARTIAL UNIQUE INDEX: at most one ACTIVE appointment
may reference a slot, enforced by SQLite itself — not by application hope.
Two concurrent transactions for the same slot cannot both commit; the loser
gets IntegrityError, which the booking tool maps to SLOT_ALREADY_BOOKED.
"""
from datetime import datetime, date as date_t
import uuid

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Date, ForeignKey, Index, text,
)
from sqlalchemy.orm import relationship

from app.db.database import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class Doctor(Base):
    __tablename__ = "doctors"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    speciality = Column(String, nullable=False)

    slots = relationship("Slot", back_populates="doctor")


class Patient(Base):
    __tablename__ = "patients"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, index=True)
    phone = Column(String, nullable=False, unique=True)
    email = Column(String, nullable=True)
    dob = Column(Date, nullable=False)

    appointments = relationship("Appointment", back_populates="patient")


class Slot(Base):
    """A fixed consultation window. A slot is *taken* iff an ACTIVE
    appointment references it — availability is derived, never duplicated,
    so slot status can never drift from appointment reality."""
    __tablename__ = "slots"
    id = Column(Integer, primary_key=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    date = Column(Date, nullable=False, index=True)
    start_time = Column(String, nullable=False)   # "HH:MM" (24h, IST)
    end_time = Column(String, nullable=False)

    doctor = relationship("Doctor", back_populates="slots")


class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    slot_id = Column(Integer, ForeignKey("slots.id"), nullable=False)
    status = Column(String, nullable=False, default="ACTIVE", index=True)  # ACTIVE | CANCELLED | RESCHEDULED
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    patient = relationship("Patient", back_populates="appointments")
    slot = relationship("Slot")
    doctor = relationship("Doctor")


Index(
    "uq_active_slot",
    Appointment.slot_id,
    unique=True,
    sqlite_where=text("status = 'ACTIVE'"),
)


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(String, primary_key=True, default=_uuid)
    status = Column(String, nullable=False, default="OPEN")  # OPEN | ESCALATED | CLOSED
    # Set only by lookup_patient when identity is confirmed by phone (or
    # phone+dob). All mutations require this to match the acting patient.
    confirmed_patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages = relationship("Message", back_populates="conversation",
                            cascade="all, delete-orphan")
    events = relationship("ToolEvent", back_populates="conversation",
                          cascade="all, delete-orphan")
    handoffs = relationship("Handoff", back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    role = Column(String, nullable=False)          # user | agent | system
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")


class ToolEvent(Base):
    """Audit log: EVERY tool execution — proposed, validated, blocked,
    succeeded or failed — is persisted here. The UI timeline and the
    grounding validator both read from this table."""
    __tablename__ = "tool_events"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    tool_name = Column(String, nullable=False)
    arguments = Column(Text, nullable=False)       # JSON
    result = Column(Text, nullable=False)          # JSON envelope
    status = Column(String, nullable=False)        # SUCCESS | ERROR | BLOCKED
    created_at = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="events")


class Handoff(Base):
    __tablename__ = "handoffs"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    reason = Column(String, nullable=False)  # EMERGENCY | CLINICAL | LLM_UNAVAILABLE | POLICY_BLOCK | USER_REQUEST
    summary = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="OPEN")  # OPEN | RESOLVED
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

    conversation = relationship("Conversation", back_populates="handoffs")
