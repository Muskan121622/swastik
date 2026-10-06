# DECISIONS.md

Design decisions, their reasoning, and what was rejected — written so a
reviewer can argue with the trade-offs instead of guessing at them.

---

## D1. The database is the only source of truth; the model never holds state

**Decision.** Slots have no `status` column. A slot is open iff no `ACTIVE`
appointment references it. Double-booking is prevented by a *partial unique
index* (`uq_active_slot`), not by check-then-insert code.

**Why.** The rubric's determinism score is about *outcomes under contention*.
Any in-memory "is this slot free?" flag is a lie waiting to drift. Letting
SQLite enforce the invariant means the worst possible agent bug — booking two
people into one 30-minute cardiology slot — is unrepresentable, even for code
I haven't written yet.

**Rejected.** `slot.status = 'TAKEN'` updated by the tool (TOCTOU race between
two reads and two writes; needs table locks or serialised transactions);
optimistic row-version checks (correct but far more machinery for the same
guarantee the index gives for free).

**Proven by.** `tests/test_concurrency.py` — 10 threads released by one
barrier race for a single slot: exactly 1 `BOOKED`, 9 `SLOT_NOT_OPEN`, exactly
1 ACTIVE appointment in the DB.

## D2. A deterministic safety gate runs before the LLM — always

**Decision.** Emergency/clinical detection is a regex classifier (English +
Hinglish) executed *before* any model call. A hit escalates via **code**:
`escalate_to_human` is invoked directly by the graph, the reply is a fixed
template, and the LLM is never consulted for that turn.

**Why.** An LLM classifier adds latency, cost, and — worst — a failure mode
where the model is having a bad day and calls a heart attack "user making
conversation". Safety that depends on sampling temperature isn't safety.

**Rejected.** LLM-as-judge for emergencies (non-deterministic, injectable);
hybrid "LLM double-checks the regex" (the regex false-*positives* are merely
annoying — an unnecessary human handoff — while false-*negatives* are
unacceptable, so the gate is tuned deliberately itchy and needs no second
opinion).

**Note.** Hinglish patterns ("seene mein dard", "saans lene me takleef",
"daava/medicine leni") are included because the assignment is an Indian
clinic; a monolingual gate would be theatre, not safety.

## D3. Identity: confirmation is a property of the conversation, not of a lookup

**Decision.** A patient is *confirmed* only by a unique phone or
name+dob match. `lookup_patient("Rahul")` returns `AMBIGUOUS` with no
candidates exposed as actionable. Every mutating tool additionally enforces
`conversation.confirmed_patient_id == args.patient_id` — so even a
hallucinated or injected `patient_id` cannot touch a record.

**Why.** "Cancel my appointment" said by the wrong Rahul is the kind of
correctness bug a real clinic feels immediately. The authorization check
living in the *tool* (not the prompt, not the graph) means it holds for every
caller of the tool — API, agent, or future test script.

**Rejected.** Letting the model "pick the most likely Rahul" (the classic);
returning ranked candidates to the model with instructions to disambiguate
(invites guessing); per-request confirmation without conversation state
(would force re-identification every turn).

## D4. Typed result envelopes everywhere; string-returning tools are a bug farm

**Decision.** All six tools return
`{ok, status, data, error{code,message}}` with stable, enumerated status
codes, and the *same* envelope is what the model sees, what the audit table
stores, and what the grounding validator checks.

**Why.** MedFlow (my previous project) returned prose strings from tools; the
downstream cost was that nothing could be asserted about them — tests,
grounding, and the UI all did string-matching on human-readable text. One
shared machine-readable shape makes three of the rubric's four axes easier at
once.

## D5. Grounded replies: the model may narrate, not decide

**Decision.** After the loop, a validator scans the draft reply for claims
(“booked”, “cancelled”, “escalated”…) and permits them only if a matching
*successful* tool event exists this turn. Truthful-but-failed statuses
(`ALREADY_CANCELLED`, `AMBIGUOUS`, `SLOT_NOT_OPEN`, …) are whitelisted as
idempotent truths. Anything else → deterministic template built from the
actual events.

**Why.** The dangerous failure isn't the agent doing something wrong — it's
the agent *saying* something happened when it didn't ("Your appointment is
confirmed!" about a slot that was taken). This turns hallucinated outcomes
into a testable, enforced property.

**Rejected.** `tool_choice="required"` / constrained decoding (models can
still lie in the narration *after* a real tool call — the problem is output
grounding, not input freedom).

## D6. One validated agent loop, not an intent-router fan-out

**Decision.** A single LangGraph loop (propose → policy → execute → re-
propose → finalize), bounded at 4 tool calls and 3 invalid proposals per
turn. No separate "intent classifier → pipeline per intent" graph.

**Why.** Router graphs multiply paths that each need safety review, and real
calls mix intents ("book Rahul, and if that's not possible cancel yesterday's
— also my chest hurts"). One loop with hard budgets keeps every mutation
through the same chokepoint. I deliberately kept MedFlow's
extract→validate→execute→NLG shape but replaced its keyword router with the
model's own tool selection — because the policy node makes selection safety
independent of *how* the choice was made.

**Rejected.** Multi-agent supervisor (cost/latency with zero rubric upside);
fully deterministic state machine with the LLM only for phrasing (maximally
safe, but then it's not an agent and fails the assignment's premise).

## D7. Fail closed on any model misbehaviour

**Decision.** Provider errors are classified: *transient* ones (429, 5xx,
timeout, connection) get **exactly one immediate retry** — a model completion
call mutates nothing, so retrying it is safe — and if that also fails, the
turn fails closed into an `LLM_UNAVAILABLE` handoff with the transcript so
far. Auth/model errors fail closed instantly (retrying them is pointless).
A malformed tool-call is `BLOCKED`, audited, and fed back as a structured
error; three strikes → human.

**Why.** "The demo degraded silently into a guess" is the worst outcome in an
evaluation. A visible human handoff *is* correct behaviour, and the queue
screen makes it obvious.

**Rejected.** Silent retries with exponential backoff (hides outages and
blows latency); falling back to a canned "I didn't understand" loop (leaves
the caller stranded with no human in the picture).

## D8. MockLLM is a first-class provider, not a monkey-patch

**Decision.** `LLMProvider` is a Protocol; tests inject a scripted `MockLLM`
via the same seam production uses (`runtime.set_provider`). The full graph —
gate, policy, executor, grounding — runs in tests; 64 tests, zero API calls,
~15 s.

**Why.** Determinism claims that require an API key to verify are
unverifiable by the reviewer. The adversarial suite makes MockLLM *behave
badly on purpose* (lying confirmations, fake slot ids, `drop_database`
proposals, mid-turn outages) — what's under test is the harness's defence,
not the model's manners.

## D9. The first-turn nudge (the one place I re-ask the model)

**Decision.** If the very first decision of a conversation is a greeting
rather than acting on the caller's already-complete request, the graph makes
exactly one bounded re-ask ("call the appropriate tool instead of greeting").
State is untouched; no tool runs; cannot loop.

**Why.** Observed with `gpt-oss-120b`/`20b`: they open with pleasantries even
when the caller gave identity + request in one message. One retry got demo-
quality one-shot flows without moving any decision logic into code.

**Rejected.** Keyword-detecting "is this a request?" in code (that's the
beginning of a router — D6); `tool_choice="required"` (breaks pure-greeting
calls and small-talk turns).

## D10. Deliberately NOT built (restraint ledger)

| Skipped | Because |
|---|---|
| Auth / RBAC / sessions | single-clinic demo; would add 200 lines of ceremony and zero rubric points |
| SSE / websockets | a turn is one request/reply; polling the queue every 5 s is honest |
| Idempotency keys | no retry-on-disconnect story to serve; the DB invariant already prevents the harmful duplicate |
| Alembic migrations | schema ships once; `create_all` + wipe-able dev DB |
| Docker / CI | README quickstart is 6 commands; a Dockerfile would be decoration |
| Vector memory / RAG | patients are exact-match by design (D3); fuzzy recall is the *opposite* of safe |
| Slots beyond seed horizon | reschedule-to-arbitrary-date needs pricing/policies a 3-day brief can't specify |

Each of these would have been *plausible*; none was necessary. If I had extra
day, I'd add appointment reminder *reads* to the tools, not more
infrastructure.

## D11. Known limitations (stated, not hidden)

* The emergency gate is regex — it will miss novel phrasings. Mitigation:
  it's a floor, not a ceiling; anything the model *also* recognises as an
  emergency triggers `escalate_to_human` through the normal path.
* SQLite partial indexes assume single-writer-ish load; Postgres would move
  the same invariant to a `UNIQUE … WHERE` constraint with real concurrency
  headroom — the schema was written to port with one line changed.
* Time zones are naive local (IST clinic); `created_at` is UTC. Fine for one
  clinic, wrong for a chain.
* A patient may hold multiple ACTIVE appointments (no conflict rule) — the
  brief didn't define one and inventing a policy in a tool is how hidden
  requirements creep in.

## D12. Model choice: gpt-oss-120b over llama-3.3-70b

**Decision.** Default `GROQ_MODEL=openai/gpt-oss-120b`.

**Why.** Empirical: on this Groq account, llama models were inaccessible
(404), while gpt-oss models are tool-call capable and follow the identity/
ambiguity rules with temperature 0.1. The provider keeps everything else
model-agnostic — one env var swaps it, tests never notice (D8).
