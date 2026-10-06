"""Deterministic seed data for the synthetic clinic.

Includes the ambiguity traps the reviewer needs to exercise:
- two "Rahul" patients  -> AMBIGUOUS patient path
- one Priya (unique name) but identity still requires phone confirmation
"""
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.db.models import Doctor, Patient, Slot, Appointment

DOCTORS = [
    (1, "Dr. Mehta", "General Medicine"),
    (2, "Dr. Kulkarni", "Cardiology"),
    (3, "Dr. Rao", "Orthopaedics"),
]

# phone is unique and is the identity anchor; dob is a secondary factor
PATIENTS = [
    (1, "Rahul Sharma",  "+91-9820000001", "rahul.s@example.com",  date(1990, 4, 12)),
    (2, "Rahul Kumar",   "+91-9820000002", "rahul.k@example.com",  date(1988, 9, 3)),
    (3, "Priya Singh",   "+91-9820000003", "priya.s@example.com",  date(1995, 1, 25)),
    (4, "Amit Verma",    "+91-9820000004", "amit.v@example.com",   date(1982, 7, 8)),
    (5, "Sneha Patil",   "+91-9820000005", "sneha.p@example.com",  date(1998, 11, 17)),
]

_slot_cache: dict = {}


def _weekdays(from_day: date, days: int):
    d = from_day
    while days > 0:
        if d.weekday() != 6:  # skip Sundays: clinic closed
            yield d
        d, days = d + timedelta(days=1), days - 1


def seed(db: Session, base_date: date | None = None, horizon_days: int = 10):
    """Seed (idempotent). base_date lets tests pin 'tomorrow' deterministically."""
    if db.query(Doctor).count():
        return
    for did, name, spec in DOCTORS:
        db.add(Doctor(id=did, name=name, speciality=spec))
    for pid, name, phone, email, dob in PATIENTS:
        db.add(Patient(id=pid, name=name, phone=phone, email=email, dob=dob))

    today = base_date or date.today()
    times = ["09:00", "10:00", "11:00", "12:00", "14:00", "15:00", "16:00"]
    for day in _weekdays(today + timedelta(days=1), horizon_days):
        for did, _, _ in DOCTORS:
            for t in times:
                h, m = map(int, t.split(":"))
                end = f"{h:02d}:{m+30:02d}" if m + 30 < 60 else f"{h+1:02d}:00"
                db.add(Slot(doctor_id=did, date=day, start_time=t, end_time=end))
    db.flush()

    # One pre-existing appointment for Dr. Kulkarni's FIRST seeded 10:00 slot:
    # gives the demo an existing booking to cancel/reschedule from day one.
    first = (db.query(Slot)
             .filter_by(doctor_id=2)
             .order_by(Slot.date, Slot.start_time)
             .first())
    db.add(Appointment(patient_id=4, doctor_id=2, slot_id=first.id, status="ACTIVE"))
    db.commit()
