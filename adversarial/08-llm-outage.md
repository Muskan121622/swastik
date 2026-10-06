# 08 — LLM outage mid-conversation

**Attack class:** dependency failure
**Defense:** fail closed to a human — never fabricate help the model couldn't provide

## Setup

Open conversation. Script the provider to raise an outage.

## Script

| Turn | Caller says | Model (scripted) does | What happens |
|------|-------------|-----------------------|--------------|
| 1 | "I want to move my appointment to Friday" | provider raises `LLMUnavailableError("groq 503")` | the adapter classifies it: transient (429/5xx/timeout) → **exactly one** bounded retry; still failing → `fail_closed` path |
| 1 | — | — | conversation → ESCALATED with a `LLM_UNAVAILABLE` handoff; caller gets a warm "a human at the front desk will help" line; **no tool ran, nothing was invented** |

## Expected outcome (asserted)

- `conversation_status == "ESCALATED"`
- a handoff exists with `reason == "LLM_UNAVAILABLE"`
- reply mentions "human" / "front desk" — the honest truth, not a fake reschedule

## Why this matters

The single most important reliability property: **when the model is down, the
agent must not pretend to work.** A silent hang or a hallucinated "done!"
would be worse than an escalation. Deterministic code owns the fail-closed
transition and the machine-readable reason, so the human reviewer sees
exactly why the call landed in their queue. (Retries live in the adapter —
`DECISIONS.md` D7 — and are bounded to one because a completion call is
side-effect-free; auth/model-not-found errors skip even that and fail instantly.)

**Automated as:** `test_08_llm_failure_fails_closed`
