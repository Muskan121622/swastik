# 07 — Malformed tool arguments

**Attack class:** schema violation
**Defense:** Pydantic validates arguments *before* the tool runs; the bad call is audited, never executed

## Setup

Open conversation. Script the model to emit a structurally invalid booking.

## Script

| Turn | Caller says | Model (scripted) does | What happens |
|------|-------------|-----------------------|--------------|
| 1 | "Book slot null for patient 3" | `book_appointment(patient_id="3", slot_id=None)` | the executor parses args with the tool's Pydantic model: `slot_id=None` (and stringly-typed id) fails validation → `BLOCKED / VALIDATION_ERROR`, the tool body never runs |
| 1 | — | model recovers: "Sorry, I couldn't process that. Which slot would you like?" | grounded (claims nothing) |

## Expected outcome (asserted)

- an event with `status == "BLOCKED"` and `result.status == "VALIDATION_ERROR"`
- appointment count unchanged (only the seeded row remains)
- the model gets a structured error back and can ask a clarifying question — a
  malformed call is a recoverable turn, not a 500

## Why this matters

"Handle malformed LLM responses safely" is an explicit requirement. Types are
the last thing you want leaking into business logic: if `slot_id=None`
reached the DB layer you'd get opaque `IntegrityError`s or worse, silent
mis-buinks. Validating at the boundary turns garbage into a clean, logged,
retryable failure.

**Automated as:** `test_07_malformed_tool_call_never_executes`
