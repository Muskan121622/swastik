"""Safety gate is a pure function — testable with zero infrastructure."""
import pytest
from app.safety.gate import classify


@pytest.mark.parametrize("text", [
    "I have severe chest pain since morning",
    "My father is unconscious, we need help now",
    "There is heavy bleeding from a wound",
    "I think it's an overdose, please help",
    "Seene mein bahut dard ho raha hai",     # Hinglish: chest pain
    "Maa ko behosh kar diya hai",            # Hinglish: unconscious
    "khoon bah raha hai bahut",              # Hinglish: severe bleeding
])
def test_emergency(text):
    v = classify(text)
    assert v.label == "EMERGENCY"
    assert v.escalate


@pytest.mark.parametrize("text", [
    "What dosage of paracetamol should I take?",
    "Can I take my blood pressure medicine before the visit?",
    "Please diagnose my symptoms",
    "Meri tabiyat kharab hai kya karu",
    "Mai kya dava lu ghar par",
    # regression: this exact live-audit phrasing once passed the gate
    "Doctor ne 500mg dikhai thi, ab mujhe 1000mg leni chahiye ya 1500mg? "
    "Please dosage bata do.",
    "How many tablets should I take daily?",
    "Kitni goli leni chahiye ek baar me?",
])
def test_clinical(text):
    v = classify(text)
    assert v.label == "CLINICAL"
    assert v.escalate


@pytest.mark.parametrize("text", [
    "I need an appointment for tomorrow at 4",
    "Book Rahul Sharma with Dr. Mehta on Monday",
    "Can I reschedule my visit to next week?",
    "Cancel my appointment, I'm busy that day",
    "Do you have any slot with the cardiologist Friday?",
])
def test_normal_passes_through(text):
    assert classify(text).label == "NORMAL"


def test_emergency_wins_over_clinical():
    v = classify("chest pain and also tell me what medicine dosage to take")
    assert v.label == "EMERGENCY"
