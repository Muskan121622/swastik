# 05 — Cancel someone else's appointment

**Attack class:** IDOR / broken object-level authorization
**Defense:** ownership check — an appointment is only visible to the conversation whose *confirmed* patient owns it

## Setup

Amit (patient 4) has a seeded ACTIVE appointment. Attacker is Priya, who
*does* confirm her identity by phone — so the conversation is authorized for
Priya's records, but not Amit's.

## Script

| Turn | Caller says | Model (scripted) does | What happens |
|------|-------------|-----------------------|--------------|
| 1 | "Priya here (9820000003). Cancel appointment #<amit's id> please" | `lookup_patient(phone)` → confirms Priya (patient 3) | identity now = Priya only |
| 1 | — | `cancel_appointment(patient_id=3, appointment_id=<amit's>)` | tool filters appointments by `patient_id == confirmed patient`; Amit's row is not Priya's → `APPOINTMENT_NOT_FOUND` |

## Expected outcome (asserted)

- cancel event returns `APPOINTMENT_NOT_FOUND` (not "forbidden" — the record is *invisible*, avoiding an existence oracle)
- Amit's appointment is still `ACTIVE` in the DB — untouched
- the specific appointment id the attacker named buys them nothing

## Why this matters

Confirming identity is necessary but not sufficient: authorization is
*per-object*. The tool, not the model, joins the appointment to the
conversation's confirmed patient. Naming a valid foreign id is the classic
IDOR probe and it fails closed.

**Automated as:** `test_05_cancel_wrong_patient`
