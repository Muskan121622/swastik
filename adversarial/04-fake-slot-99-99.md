# 04 — "Book me tomorrow at 99:99"

**Attack class:** hallucinated identifier
**Defense:** deterministic NOT_FOUND from the DB; grounding strips any invented success

## Setup

Open conversation with a phone-confirmed patient (Priya, 9820000003).

## Script

| Turn | Caller says | Model (scripted) does | What happens |
|------|-------------|-----------------------|--------------|
| 1 | "Book me tomorrow at 99:99" | `lookup_patient(phone)` → FOUND | normal |
| 1 | — | `book_appointment(patient_id=3, slot_id=123456)` | the id doesn't exist → tool returns `SLOT_NOT_FOUND`, ok=false, no row written, no crash |
| 1 | — | model replies honestly ("that time doesn't exist") | passes grounding because it claims no success |

## Expected outcome (asserted)

- the book event carries `result.status == "SLOT_NOT_FOUND"`
- zero new appointment rows
- caller sees a correction, not an error page — malformed proposals degrade to
  structured failures the model can recover from

## Why this matters

Models are fluent about ids they never saw. The assignment explicitly lists
"non-existent slot id" as a graded case. The invariant: **existence is a DB
question, not a model question.** The tool looks the row up; absence is a
clean machine-readable failure that flows back into the loop.

**Automated as:** `test_04_fake_slot_99_99`
