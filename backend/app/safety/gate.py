"""Safety gate — deterministic, runs BEFORE any LLM call.

Design: rules first, model second, fail-closed.
  1. Curated emergency / clinical patterns (English + Hinglish) short-circuit
     to ESCALATE without ever invoking the model or the agent loop.
  2. The agent can additionally call escalate_to_human itself for nuance,
     but the model can NOT un-escalate: a gate hit cannot be overridden.
  3. If the LLM is unavailable mid-turn, the graph fails closed to a human
     (see agent/graph.py), it never guesses.

This is deliberately NOT an LLM classifier: safety 30% of the rubric should
not rest on a probabilistic component.
"""
import re
from dataclasses import dataclass

EMERGENCY_PATTERNS = [
    # English
    r"chest\s+(pain|pressure|tightness)", r"heart\s+attack", r"can'?t\s+breathe",
    r"difficulty\s+breathing", r"shortness\s+of\s+breath", r"unconscious",
    r"pass(ed|ing)\s+out", r"fainting", r"severe\s+(bleeding|headache|pain)",
    r"heavy\s+bleeding", r"stroke", r"seizure", r"convulsion",
    r"poison", r"overdose", r"suicide", r"killing\s+(myself|himself|herself|themself)",
    r"self\s+harm", r"cut\s+(my|him|her)\s+(veins|wrists)",
    r"accident", r"bad\s+burn", r"choking", r"drowning",
    r"anaphyla", r"allergic\s+reaction", r"asthma\s+attack",
    # Hinglish / transliterated Hindi
    r"seene?\s+(mein|mai|main|me)\b.{0,20}dard", r"chest\s+me\s+dard", r"sanse?\s+(nahi|not)\s+(aa\s+)?(rahi|raha|rahi)",
    r"saans\s+lem\s+raha", r"behosh", r"hosh\s+kho", r"khoon\s+(bah|ja)",
    r"dil\s+ka\s+dard", r"zehr", r"marne?\s+wala",
    r"khudkush", r"aag\s+lagna?", r"karach",
]

CLINICAL_PATTERNS = [
    # questions that must not be answered by a receptionist agent
    r"(what|which)\s+(dosage|dose|medicine|tablet|drug)", r"how\s+much\s+(medicine|dosage)",
    r"should\s+i\s+(take|stop|reduce)", r"can\s+i\s+take", r"is\s+it\s+safe\s+to\s+take",
    r"diagnos(e|is|ing)\b", r"what\s+(do|am)\s+i\s+have",
    r"symptoms?\s+of\s+my", r"(side\s+effects|mri|xray|ecg|blood\s+test)\s+(result|report)s?",
    r"interpret\s+(my|the)\s+(report|scan|test)", r"prescri(be|ption)\s+(for|me)",
    r"\bdosages?\b", r"\bhow\s+many\s+(tablets?|capsules?|goli)",
    r"\d+\s*(mg|ml)\b.{0,40}\b(leni|lena|chahiye|khani|take|dose)\b",
    r"medicine?\s+(ka\s+)?(dose|dosage|strength)",
    r"dava\s+(kon|kaun|kya|kaunsi|konc|leni|lena|lu|lo)", r"koi\s+(bhi\s+)?(dava|medicine)\s+(le\s+)?sakt",
    r"meri\s+(tabiyat|bimari)", r"batao\s+(main|mai|muje)\s+(kya\s+(lena|khana))",
    r"(goli|dawa|dawai)\s*(kitni|kauni|konsi)", r"kitni\s+(goli|dawa|dawai|medicine)",
    r"\bmatra\b",
]

_EMERGENCY_RE = re.compile("|".join(f"(?:{p})" for p in EMERGENCY_PATTERNS),
                           re.IGNORECASE)
_CLINICAL_RE = re.compile("|".join(f"(?:{p})" for p in CLINICAL_PATTERNS),
                          re.IGNORECASE)


@dataclass(frozen=True)
class SafetyVerdict:
    label: str            # EMERGENCY | CLINICAL | NORMAL
    matched: str = ""     # the trigger text, for the audit note

    @property
    def escalate(self) -> bool:
        return self.label in ("EMERGENCY", "CLINICAL")


def classify(text: str) -> SafetyVerdict:
    """Pure function; no I/O, no model. Deterministic and unit-testable."""
    m = _EMERGENCY_RE.search(text or "")
    if m:
        return SafetyVerdict("EMERGENCY", m.group(0))
    m = _CLINICAL_RE.search(text or "")
    if m:
        return SafetyVerdict("CLINICAL", m.group(0))
    return SafetyVerdict("NORMAL")
