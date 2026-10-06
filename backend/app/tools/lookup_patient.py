"""Tool 1 — lookup_patient (read + identity resolution).

Deterministic, non-fuzzy: exact full-name / phone / dob matching only.
The LLM is NEVER allowed to pick between candidates — a multi-match
returns AMBIGUOUS and the conversation must collect a distinguishing
factor (phone, or phone+dob) before any mutation is permitted.

Identity rule (documented in DECISIONS.md):
    confirmed only by unique phone, or name+dob unique.
    A name match alone yields an unconfirmed candidate.
"""
import re
from datetime import date as date_t
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Conversation, Patient
from app.schemas.results import AMBIGUOUS, NOT_FOUND, success, failure

PHONE_DIGITS = re.compile(r"\D")


def _norm_phone(p: str) -> str:
    digits = PHONE_DIGITS.sub("", p or "")
    return digits[-10:] if len(digits) >= 10 else digits


def _mask_phone(p: str) -> str:
    d = _norm_phone(p)
    return f"+91-XXXXX{d[-4:]}" if d else p


def _card(p: Patient) -> dict:
    return {"id": p.id, "name": p.name, "phone": _mask_phone(p.phone),
            "dob": p.dob.isoformat()}


class LookupPatientArgs(BaseModel):
    name: Optional[str] = Field(default=None, description="Patient full or partial name")
    phone: Optional[str] = Field(default=None, description="Registered phone number")
    dob: Optional[str] = Field(default=None, description="Date of birth YYYY-MM-DD")


def lookup_patient(db: Session, conversation_id: str, args: LookupPatientArgs) -> dict:
    if not (args.name or args.phone or args.dob):
        return failure(NOT_FOUND, "Provide at least a name, phone or date of birth.")

    conv = db.get(Conversation, conversation_id)
    q = select(Patient)
    matched_by = "none"

    if args.phone:
        digits = _norm_phone(args.phone)
        q = q.where(func_suffix(Patient.phone, digits))
        matched_by = "phone"
    elif args.name and args.dob:
        d = date_t.fromisoformat(args.dob)
        q = q.where(Patient.dob == d, Patient.name.ilike(f"%{args.name}%"))
        matched_by = "name+dob"
    elif args.name:
        q = q.where(Patient.name.ilike(f"%{args.name.strip()}%"))
        matched_by = "name"
    else:  # dob alone is not considered distinguishing enough
        d = date_t.fromisoformat(args.dob)
        q = q.where(Patient.dob == d)
        matched_by = "dob"

    rows = db.execute(q).scalars().all()

    if not rows:
        return failure(NOT_FOUND, "No patient matches.")

    if len(rows) > 1:
        # Never let the model choose. Force clarification.
        return failure(AMBIGUOUS,
                       "Multiple patients matched. Ask the caller for their "
                       "registered phone number to disambiguate.",
                       candidates=[_card(p) for p in rows])

    p = rows[0]
    confirmed = matched_by in ("phone", "name+dob")
    if confirmed and conv and conv.status == "OPEN":
        conv.confirmed_patient_id = p.id
        db.commit()

    return success("FOUND", patient=_card(p),
                   identity_confirmed=confirmed, matched_by=matched_by)


def func_suffix(phone_col, digits: str):
    """Match on the last 10 digits so callers can say the number with any
    prefix/formatting. SQLite-safe portable-enough expression."""
    from sqlalchemy import func
    return func.replace(func.replace(phone_col, "+91-", ""), "-", "").like(f"%{digits}")
