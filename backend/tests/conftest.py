"""Test-wide setup: an isolated SQLite file (never the dev DB), recreated
per test, with deterministic seed data. The LLM is always MockLLM —
the full suite runs offline, deterministically, at zero API cost."""
import os
import sys
import tempfile

# --- must happen BEFORE any app import: config reads env at import time ---
_TMP = tempfile.mkdtemp(prefix="swasthiq_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP, 'test.db')}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.db.database import Base, engine, SessionLocal
from app.db.seed import seed
from app.db.models import Conversation
from app.agent import runtime
from app.llm.mock import MockLLM


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    seed(db)
    db.close()
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def conv(db):
    """A fresh OPEN conversation."""
    c = Conversation(status="OPEN")
    db.add(c)
    db.commit()
    return c


@pytest.fixture
def confirmed_conv(db, conv):
    """Conversation where identity is already confirmed (Priya Singh, id 3)."""
    conv.confirmed_patient_id = 3
    db.commit()
    return conv


@pytest.fixture
def mock_llm():
    """Injects a MockLLM with an empty script; tests pass decisions in."""
    def _install(script=None, default_reply="How can I help?"):
        m = MockLLM(script=script, default_reply=default_reply)
        runtime.set_provider(m)
        return m
    yield _install
    runtime.set_provider(None)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    # Safety net: never hit a real provider from tests, even if a test
    # forgets to install its scripted mock.
    runtime.set_provider(MockLLM())
    with TestClient(app) as c:
        yield c
    runtime.set_provider(None)


def free_slot(db, doctor_id=1, exclude_ids=()):
    """First open slot for a doctor (mirrors search_slots' derivation)."""
    from app.db.models import Slot, Appointment
    from sqlalchemy import select
    taken = select(Appointment.slot_id).where(Appointment.status == "ACTIVE")
    return (db.query(Slot)
            .filter(Slot.doctor_id == doctor_id, Slot.id.not_in(taken),
                    Slot.id.not_in(list(exclude_ids)))
            .order_by(Slot.date, Slot.start_time)
            .first())
