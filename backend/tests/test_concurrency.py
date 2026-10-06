"""THE proof: 10 concurrent bookings for ONE slot -> exactly 1 succeeds,
9 receive SLOT_NOT_OPEN. The partial UNIQUE index (uq_active_slot) decides;
no application-level lock, no optimistic retry loop.

This mirrors the README question "how do your functions keep data
consistent on update" — here is the executable answer.
"""
import threading

from app.db.database import SessionLocal
from app.db.models import Conversation, Appointment
from app.tools.book_appointment import book_appointment, BookAppointmentArgs
from tests.conftest import free_slot


def test_ten_way_race_one_winner():
    db = SessionLocal()
    slot = free_slot(db, doctor_id=1)
    target = slot.id

    # 5 patients, each with their own identity-confirmed conversation
    conv_ids = []
    for pid in range(1, 6):
        c = Conversation(status="OPEN", confirmed_patient_id=pid)
        db.add(c)
        db.commit()
        conv_ids.append(c.id)
    db.close()

    results = []
    barrier = threading.Barrier(10)
    lock = threading.Lock()

    def attempt(i):
        barrier.wait()  # maximise overlap: all threads fire together
        s = SessionLocal()
        try:
            r = book_appointment(s, conv_ids[i % 5],
                                 BookAppointmentArgs(patient_id=(i % 5) + 1,
                                                     slot_id=target))
            with lock:
                results.append(r)
        finally:
            s.close()

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    wins = [r for r in results if r["ok"]]
    losses = [r for r in results if not r["ok"]]
    assert len(wins) == 1, f"expected exactly 1 winner, got {len(wins)}"
    assert all(r["status"] == "SLOT_NOT_OPEN" for r in losses)

    check = SessionLocal()
    try:
        n = check.query(Appointment).filter_by(slot_id=target, status="ACTIVE").count()
        assert n == 1  # DB agrees with the envelope: one truth, one booking
        # run with `pytest -s` to print the numbers quoted in the README
        print(f"\n  10 racing bookings on slot {target}: "
              f"{len(wins)} BOOKED, {len(losses)} SLOT_NOT_OPEN, "
              f"{n} ACTIVE row in DB, 0 inconsistent states")
    finally:
        check.close()
