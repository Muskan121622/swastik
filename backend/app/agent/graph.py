"""LangGraph workflow — orchestration only. Business truth lives in tools+DB.

    START
      │
      ▼
  safety_gate ──(EMERGENCY/CLINICAL)──► hard_stop ──► finalize ──► END
      │                                      (escalate executed
      │                                       by CODE, no LLM, no tools)
      ├──(conversation already ESCALATED)──► locked_out ──► END
      ▼
  agent_llm ──(proposal)──► policy ──(valid)──► tool_executor ──► (loop)
      │                      │  ▲                     │
      │                    (invalid -> bounded       terminal/budget -> finalize
      │                     re-ask to agent_llm)
      └──(prose reply)──► router ──(action obvious from
                        │  text+DB state)──► policy   [deterministic recovery,
                        │                              no model consulted]
                        └──(ambiguous)──► finalize

Every transition that mutates state goes through: Pydantic arg validation
-> deterministic tool -> committed DB -> persisted ToolEvent -> grounded reply.
The model authors language and leads the loop; the router is a safety net so a
model that stalls into prose never blocks a step the caller already made
unambiguous with their own words plus committed state.
"""
import json
from datetime import date, datetime, timezone
from typing import Callable

from pydantic import ValidationError
from langgraph.graph import StateGraph, END

from app.agent.state import TurnState
from app.agent.grounding import ground_reply, UNGROUNDED_FALLBACK
from app.config import MAX_TOOL_CALLS_PER_TURN
from app.llm.base import LLMProvider, LLMUnavailableError, ToolProposal, ReplyProposal
from app.safety.gate import classify
from app.schemas.results import failure, TOOL_EXECUTION_ERROR
from app.tools.registry import REGISTRY, openai_style_schemas
from app.tools.escalate_to_human import escalate_to_human, EscalateArgs
from app.tools.display import slot_text, when as _when
from app.agent.intent import extract_intent

EMERGENCY_REPLY = (
    "I'm sorry — what you're describing sounds like an emergency. I've alerted "
    "our front desk and they will take over immediately. If this is life-"
    "threatening, please call 112 (India emergency) or go to the nearest "
    "hospital now."
)
CLINICAL_REPLY = (
    "That's a medical question our reception agent can't answer. I'm handing "
    "you to a human who can direct you properly."
)
LLM_DOWN_REPLY = (
    "I'm unable to safely complete your request right now. I've connected "
    "you with the front desk and they'll pick this up."
)
SYSTEM_FAULT_REPLY = (
    "Something went wrong on our side while handling that, so I've stopped "
    "rather than guess. The front desk has your conversation and will take it "
    "from here — nothing has been changed."
)
LOCKED_REPLY = (
    "This conversation has already been handed to our front desk. A human "
    "will continue from here — I won't make any further changes myself."
)
BUDGET_REPLY = (
    "I've gathered what I can, but I don't want to keep guessing. Our front "
    "desk can finish this for you."
)
# Last-resort line when a turn produced no text at all. Reported to the UI as a
# substitution, never as a grounded answer.
NO_REPLY_FALLBACK = "How can I help you with your appointment today?"
# Some chat models answer the very first message of a call with a greeting
# even when the caller already stated their business. One bounded, harmless
# re-ask (no state touched, no tools executed) fixes that without moving any
# decision logic out of the model.
FIRST_TURN_NUDGE = (
    "[system] The caller's message above is their complete opening request. "
    "If it contains the information needed for a tool call (or implies one "
    "is needed, like lookup_patient), call the appropriate tool now instead "
    "of greeting them. Only reply with text if the request is truly unclear."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _call_id(decision) -> str:
    return (f"call_{abs(hash(json.dumps(decision.args, sort_keys=True))) % 10**8}")


def _confirmed_pid(session_factory, conversation_id: str) -> int | None:
    from app.db.models import Conversation
    db = session_factory()
    try:
        c = db.get(Conversation, conversation_id)
        return c.confirmed_patient_id if c else None
    finally:
        db.close()


def _next_obvious_action(intent, events: list[dict],
                         confirmed_pid: int | None) -> dict | None:
    """Return a proposal when the caller's own words + committed state make
    the next tool call unambiguous — so the model is never required to re-
    decide a step it has repeatedly dropped (empty content, lost ids).

    Safety is NOT relaxed: the returned patient_id is the DB-confirmed one,
    and the tool itself still enforces identity / ownership / slot-uniqueness
    before writing. Returns None whenever anything is ambiguous, in which
    case the model stays in charge."""
    # never drive anything further once this turn has escalated
    if any(e["tool"] == "escalate_to_human" and e["result"].get("ok")
           for e in events):
        return None

    def already(name, args):
        key = json.dumps(args, sort_keys=True)
        return any(e["tool"] == name and
                   json.dumps(e["arguments"], sort_keys=True) == key
                   for e in events)

    # 1. unconfirmed + caller typed a phone -> resolve identity
    if confirmed_pid is None and intent.has_phone:
        args = {"phone": intent.phone}
        if not already("lookup_patient", args):
            return {"name": "lookup_patient", "args": args}
        return None

    # 2. confirmed + caller explicitly named an appointment to cancel
    if confirmed_pid and intent.action == "CANCEL" and intent.appointment_id:
        args = {"patient_id": confirmed_pid,
                "appointment_id": intent.appointment_id}
        if not already("cancel_appointment", args):
            return {"name": "cancel_appointment", "args": args}
        return None

    # 3. confirmed + imperative to book + a search returned exactly one slot.
    #    Only a single unambiguous candidate auto-books; two or more means the
    #    caller has to choose, which is language work for the model.
    if confirmed_pid and intent.action == "BOOK":
        if any(e["tool"] == "book_appointment" and e["result"].get("ok")
               for e in events):
            return None
        last = next((e for e in reversed(events)
                     if e["tool"] == "search_slots" and e["result"].get("ok")),
                    None)
        if last:
            slots = last["result"].get("data", {}).get("slots", [])
            if len(slots) == 1:
                args = {"patient_id": confirmed_pid,
                        "slot_id": slots[0]["slot_id"]}
                if not already("book_appointment", args):
                    return {"name": "book_appointment", "args": args}
        return None

    return None


def build_graph(provider: LLMProvider, session_factory: Callable):
    """Compiled per-turn graph. `provider` is injected so tests can run the
    ENTIRE agent deterministically with MockLLM (no API, no cost)."""

    tool_schemas = openai_style_schemas()

    # ---------- nodes ------------------------------------------------------
    def safety_gate(state: TurnState) -> dict:
        from app.db.models import Conversation
        db = session_factory()
        try:
            conv = db.get(Conversation, state["conversation_id"])
            if conv and conv.status == "ESCALATED":
                return {"safety_label": "ALREADY_ESCALATED"}
        finally:
            db.close()
        verdict = classify(state["user_text"])
        return {"safety_label": verdict.label, "safety_matched": verdict.matched}

    def route_after_safety(state: TurnState) -> str:
        label = state.get("safety_label", "NORMAL")
        if label == "ALREADY_ESCALATED":
            return "locked_out"
        if label in ("EMERGENCY", "CLINICAL"):
            return "hard_stop"
        return "agent_llm"

    def router(state: TurnState) -> dict:
        """Deterministic recovery. Only reached when the model returned PROSE
        instead of a tool call. If the caller's own words plus committed DB
        state make an action unambiguous (a stated phone to look up, an
        explicitly-named appointment to cancel, a single searched slot to
        book), fire it here instead of finalizing on a non-action — the exact
        stall we logged in Tests 1/9/12/18/22. Ambiguous steps are left alone
        and the model's reply stands, so restraint is never traded away.
        Safety is unchanged: patient_id is the DB-confirmed one and the tool
        still enforces identity/ownership/uniqueness before writing."""
        if state.get("proposal") is not None:
            return {}
        intent = extract_intent(state["user_text"])
        pid = _confirmed_pid(session_factory, state["conversation_id"])
        out = {"intent": {"action": intent.action,
                          "appointment_id": intent.appointment_id,
                          "phone": intent.phone, "has_phone": intent.has_phone,
                          "confirmed_patient_id": pid}}
        if state.get("tool_calls", 0) >= MAX_TOOL_CALLS_PER_TURN:
            return out
        nxt = _next_obvious_action(intent, state.get("events", []), pid)
        if nxt:
            call_id = (f"call_{abs(hash(json.dumps(nxt['args'], sort_keys=True))) % 10**8}")
            out["proposal"] = {"id": call_id, "name": nxt["name"],
                               "args": nxt["args"], "origin": "deterministic"}
            out["routed"] = True
        return out

    def route_after_router(state: TurnState) -> str:
        return "policy" if state.get("proposal") is not None else "finalize"

    def hard_stop(state: TurnState) -> dict:
        """Escalation executed by CODE. The model is never consulted and no
        other tool can run after this point in the conversation."""
        label = state["safety_label"]
        reason = "EMERGENCY" if label == "EMERGENCY" else "CLINICAL"
        summary = (f"Safety gate hit ({label}; matched \"{state.get('safety_matched','')}\"). "
                   f"Caller said: {state['user_text'][:300]}")
        db = session_factory()
        try:
            result = escalate_to_human(db, state["conversation_id"],
                                       EscalateArgs(reason=reason, summary=summary))
            ev = {"tool": "escalate_to_human", "arguments": {"reason": reason},
                  "result": result, "status": "SUCCESS" if result["ok"] else "ERROR",
                  "origin": "safety_gate", "created_at": _now()}
            _persist_event(db, state["conversation_id"], ev)
        finally:
            db.close()
        reply = EMERGENCY_REPLY if reason == "EMERGENCY" else CLINICAL_REPLY
        return {"events": state.get("events", []) + [ev], "reply": reply}

    def locked_out(state: TurnState) -> dict:
        """A previously escalated conversation is terminal: no LLM, no tools."""
        return {"reply": LOCKED_REPLY, "safety_label": "ALREADY_ESCALATED"}

    def agent_llm(state: TurnState) -> dict:
        try:
            decision = provider.decide(state["history"], tool_schemas)
        except LLMUnavailableError:
            return {"fail_closed": True}
        if isinstance(decision, ReplyProposal):
            first_turn = sum(1 for m in state["history"]
                             if m.get("role") == "user") == 1
            if (first_turn and not state.get("nudged")
                    and state.get("tool_calls", 0) == 0
                    and not state.get("events")):
                hist = list(state["history"]) + [
                    {"role": "user", "content": FIRST_TURN_NUDGE}]
                try:
                    decision = provider.decide(hist, tool_schemas)
                except LLMUnavailableError:
                    return {"fail_closed": True, "nudged": True}
                if isinstance(decision, ReplyProposal):
                    return {"reply": decision.content, "proposal": None,
                            "nudged": True}
                return {"proposal": {"id": _call_id(decision),
                                     "name": decision.name,
                                     "args": decision.args},
                        "history": hist, "nudged": True}
            return {"reply": decision.content, "proposal": None}
        return {"proposal": {"id": _call_id(decision), "name": decision.name,
                             "args": decision.args}}

    def route_after_llm(state: TurnState) -> str:
        if state.get("fail_closed"):
            return "fault_exit"
        if state.get("proposal") is not None:
            return "policy"
        # model answered with prose -> give the deterministic layer a chance to
        # carry out the obvious action the model just failed to take
        return "router"

    def fault_exit(state: TurnState) -> dict:
        """The single fail-CLOSED exit: human handoff, never a guess. Covers an
        unreachable model AND a tool that raised inside its own body — in both
        cases the deterministic layer can no longer be trusted for this turn,
        so nothing further may run."""
        reason = state.get("fail_reason") or "LLM_UNAVAILABLE"
        if reason == "SYSTEM_ERROR":
            # the fault is already an audited event; quote it into the note so
            # the receptionist sees which tool failed and why
            last = state.get("events", [])[-1] if state.get("events") else {}
            detail = ((last.get("result") or {}).get("error") or {}).get("message", "")
            summary = (f"Tool {last.get('tool', '?')} failed ({detail}). "
                       f"Caller said: {state['user_text'][:200]}")
        else:
            summary = f"LLM provider unavailable while handling: {state['user_text'][:200]}"
        db = session_factory()
        try:
            result = escalate_to_human(db, state["conversation_id"],
                                       EscalateArgs(reason=reason, summary=summary))
            ev = {"tool": "escalate_to_human", "arguments": {"reason": reason},
                  "result": result, "status": "SUCCESS" if result["ok"] else "ERROR",
                  "origin": "fail_closed", "created_at": _now()}
            _persist_event(db, state["conversation_id"], ev)
        finally:
            db.close()
        reply = (LLM_DOWN_REPLY if reason == "LLM_UNAVAILABLE"
                 else SYSTEM_FAULT_REPLY)
        return {"events": state.get("events", []) + [ev], "reply": reply}

    def policy(state: TurnState) -> dict:
        """Validates the proposal BEFORE execution: known tool, schema-clean
        args, and budget. Invalid calls never reach the tool layer; the model
        gets the structured error and may recover (bounded)."""
        p = state["proposal"]
        events = list(state.get("events", []))
        history = list(state["history"])

        # terminal already reached this turn? refuse follow-ups outright
        if any(e["tool"] == "escalate_to_human" and e["result"].get("ok") for e in events):
            return _blocked(state, p, events, history, "CONVERSATION_ESCALATED",
                            "Conversation is escalated; no further tools may run.")

        spec = REGISTRY.get(p["name"])
        if spec is None or "__malformed_json__" in p["args"]:
            return _blocked(state, p, events, history, "VALIDATION_ERROR",
                            f"Unknown tool or malformed arguments: {p['name']}")

        try:
            args_obj = spec.args_model.model_validate(p["args"])
        except ValidationError as e:
            return _blocked(state, p, events, history, "VALIDATION_ERROR",
                            str(e))

        if state.get("tool_calls", 0) >= MAX_TOOL_CALLS_PER_TURN:
            return {"reply": BUDGET_REPLY, "proposal": None}

        normalized = args_obj.model_dump(mode="json")
        history.append({"role": "assistant", "content": "",
                        "tool_calls": [{"id": p["id"], "type": "function",
                                        "function": {"name": p["name"],
                                                     "arguments": json.dumps(normalized)}}]})
        return {"proposal": {**p, "args": normalized}, "history": history,
                "invalid_attempts": state.get("invalid_attempts", 0)}

    def _blocked(state, p, events, history, code, message):
        err = {"ok": False, "status": code, "error": {"code": code, "message": message},
               "data": {}}
        events.append({"tool": p["name"], "arguments": p["args"], "result": err,
                       "status": "BLOCKED", "origin": "policy", "created_at": _now()})
        history.append({"role": "assistant", "content": "",
                        "tool_calls": [{"id": p["id"], "type": "function",
                                        "function": {"name": p["name"],
                                                     "arguments": json.dumps(p["args"],
                                                                            default=str)}}]})
        history.append({"role": "tool", "tool_call_id": p["id"],
                        "content": json.dumps(err)})
        db = session_factory()
        try:
            _persist_event(db, state["conversation_id"], events[-1])
        finally:
            db.close()
        attempts = state.get("invalid_attempts", 0) + 1
        if attempts >= 3:
            return {"events": events, "history": history, "proposal": None,
                    "invalid_attempts": attempts,
                    "reply": "I'm having trouble with that request — let me get "
                             "a human to help."}
        return {"events": events, "history": history, "proposal": None,
                "invalid_attempts": attempts}

    def route_after_policy(state: TurnState) -> str:
        if state.get("proposal") is not None:
            return "tool_executor"
        return "agent_llm" if not state.get("reply") else "finalize"

    def tool_executor(state: TurnState) -> dict:
        p = state["proposal"]
        spec = REGISTRY[p["name"]]
        args_obj = spec.args_model.model_validate(p["args"])
        db = session_factory()
        fault = None
        try:
            result = spec.fn(db, state["conversation_id"], args_obj)
        except Exception as exc:
            # A defect INSIDE a tool body (bad data, driver hiccup) must never
            # surface as a 500 in front of a patient, and must never be
            # swallowed either: it becomes an audited failure and the turn
            # stops. Validated code decides; a broken tool decides nothing.
            db.rollback()
            fault = f"{type(exc).__name__}: {str(exc)[:200]}"
            result = failure(TOOL_EXECUTION_ERROR, fault)
        finally:
            db.close()
        ev = {"tool": p["name"], "arguments": p["args"], "result": result,
              "status": "SUCCESS" if result["ok"] else "ERROR",
              "origin": p.get("origin", "agent"), "created_at": _now()}
        db = session_factory()
        try:
            _persist_event(db, state["conversation_id"], ev)
        finally:
            db.close()
        history = list(state["history"])
        history.append({"role": "tool", "tool_call_id": p["id"],
                        "content": json.dumps(result)})
        out = {"events": state.get("events", []) + [ev],
               "history": history,
               "tool_calls": state.get("tool_calls", 0) + 1,
               "proposal": None}
        if fault:  # the deterministic layer itself is unwell -> stop and tell a human
            out.update(fail_closed=True, fail_reason="SYSTEM_ERROR")
        return out

    def route_after_execution(state: TurnState) -> str:
        if state.get("fail_closed"):
            return "fault_exit"
        last = state["events"][-1]
        spec = REGISTRY.get(last["tool"])
        if spec and spec.terminal and last["result"].get("ok"):
            return "finalize"
        if state.get("tool_calls", 0) >= MAX_TOOL_CALLS_PER_TURN:
            return "finalize"
        # re-enter the model: it writes the reply or decides a harder step.
        # (If it stalls into prose, route_after_llm hands to the router.)
        return "agent_llm"

    def finalize(state: TurnState) -> dict:
        reply = state.get("reply", "")
        events = state.get("events", [])
        # `reply_substituted` records that the caller is NOT reading the model's
        # own words. The trace used to print "grounded" green over a hardcoded
        # string, which is exactly the overstatement this whole layer exists to
        # prevent — so the substitution is reported instead of hidden.
        substituted = False
        # If the router (not the model) carried out a write this turn, the
        # model never proposed it and its prose may not describe it — so state
        # the committed fact instead of trusting disconnected text.
        det_write = any(e.get("origin") == "deterministic"
                        and e.get("status") == "SUCCESS"
                        and (REGISTRY.get(e["tool"]) is not None
                             and REGISTRY[e["tool"]].mutates)
                        for e in events)
        if det_write:
            tmpl = _template_from_events(events)
            if tmpl:
                reply, substituted = tmpl, True
        if not reply and events:
            reply = _template_from_events(events)
            substituted = substituted or bool(reply)
        if reply:
            grounded = ground_reply(reply, events)
            substituted = substituted or grounded != reply
        else:
            grounded, substituted = NO_REPLY_FALLBACK, True
        return {"reply": grounded, "reply_substituted": substituted}

    # ---------- edges ------------------------------------------------------
    g = StateGraph(TurnState)
    g.add_node("safety_gate", safety_gate)
    g.add_node("hard_stop", hard_stop)
    g.add_node("locked_out", locked_out)
    g.add_node("router", router)
    g.add_node("agent_llm", agent_llm)
    g.add_node("fault_exit", fault_exit)
    g.add_node("policy", policy)
    g.add_node("tool_executor", tool_executor)
    g.add_node("finalize", finalize)

    g.set_entry_point("safety_gate")
    g.add_conditional_edges("safety_gate", route_after_safety,
                            {"hard_stop": "hard_stop", "locked_out": "locked_out",
                             "agent_llm": "agent_llm"})
    g.add_conditional_edges("agent_llm", route_after_llm,
                            {"policy": "policy", "router": "router",
                             "fault_exit": "fault_exit"})
    g.add_conditional_edges("router", route_after_router,
                            {"policy": "policy", "finalize": "finalize"})
    g.add_conditional_edges("policy", route_after_policy,
                            {"tool_executor": "tool_executor",
                             "agent_llm": "agent_llm", "finalize": "finalize"})
    g.add_conditional_edges("tool_executor", route_after_execution,
                            {"agent_llm": "agent_llm", "finalize": "finalize",
                             "fault_exit": "fault_exit"})
    g.add_edge("hard_stop", "finalize")
    g.add_edge("locked_out", "finalize")
    g.add_edge("fault_exit", "finalize")
    g.add_edge("finalize", END)

    return g.compile()


# ---------- helpers ----------------------------------------------------------
def _persist_event(db, conversation_id: str, ev: dict):
    from app.db.models import ToolEvent
    db.add(ToolEvent(conversation_id=conversation_id, tool_name=ev["tool"],
                     arguments=json.dumps(ev["arguments"]),
                     result=json.dumps(ev["result"]),
                     status=ev["status"]))
    db.commit()


def _template_from_events(events: list[dict]) -> str:
    """Deterministic grounded sentence for the LAST meaningful event — used
    when the loop ended without the model drafting a reply.

    Read-only and refusal outcomes must be covered here too. When they were
    not, a turn that had genuinely fetched the answer fell through to a
    hardcoded greeting and the caller got a non-answer (live Tests 8, 12, 18).
    """
    for ev in reversed(events):
        res = ev["result"]
        st = res.get("status")
        d = res.get("data", {})
        if st == "BOOKED":
            s = d.get("slot", {})
            when_text = s.get("display") or f"{_when(s.get('date'))} at {s.get('start', '')}"
            return (f"Done — {d.get('patient', 'your appointment')}'s appointment "
                    f"is booked for {when_text} (reference #{d.get('appointment_id')}).")
        if st == "RESCHEDULED":
            s = d.get("to_slot", {})
            when_text = s.get("display") or f"{_when(s.get('date'))} at {s.get('start', '')}"
            return (f"Your appointment #{d.get('appointment_id')} is now moved to "
                    f"{when_text}.")
        if st == "CANCELLED":
            return f"Your appointment #{d.get('appointment_id')} has been cancelled."
        if st in ("ESCALATED", "ALREADY_ESCALATED"):
            return LLM_DOWN_REPLY

    # No mutation happened: say what the reads actually established.
    for ev in reversed(events):
        res = ev["result"]
        st = res.get("status")
        d = res.get("data", {})
        if st == "APPOINTMENT_AMBIGUOUS":
            cands = d.get("candidates", [])
            listed = "; ".join(f"#{c.get('appointment_id')} "
                               f"{c.get('display') or 'time unknown'}" for c in cands)
            return (f"You have {len(cands)} upcoming appointments on record "
                    f"({listed}). Which one would you like me to change?")
        if st == "APPOINTMENT_NOT_FOUND":
            return ("I can't find an active appointment on your record for that. "
                    "Would you like me to look up what you do have booked?")
        if st == "THIRD_PARTY_IDENTITY":
            return ("This call is already handling one patient's bookings, so I can't "
                    "make changes for someone else. I'll hand that to a person who "
                    "can verify the request.")
        if st == "IDENTITY_UNCONFIRMED":
            return ("I need to confirm who I'm speaking with first — could you give "
                    "me your registered phone number?")
        if st == "SLOT_NOT_OPEN":
            return ("That slot has just been taken. Let me find you another open "
                    "time instead.")
        if ev["tool"] == "search_slots":
            slots = d.get("slots", [])
            if slots:
                earliest = slots[0].get("display") or "the earliest open time"
                return (f"There {d.get('count', len(slots))} open slot(s) matching "
                        f"that. The earliest is {earliest}. Shall I book it for you?")
            return ("I couldn't find an open slot matching that day and doctor. "
                    "Would a different day or another doctor work?")
    return ""
