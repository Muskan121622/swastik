"""LangGraph workflow — orchestration only. Business truth lives in tools+DB.

    START
      │
      ▼
  safety_gate ──(EMERGENCY/CLINICAL)──► hard_stop ──► finalize ──► END
      │                                      (escalate executed
      │                                       by CODE, no LLM, no tools)
      ├──(conversation already ESCALATED)──► locked_out ──► END
      ▼
  agent_llm ──(reply)───────────────────────────────► finalize
      │  ▲                                             ▲
      │  └── invalid proposal ── policy ──(error msg)──┘ (bounded retries)
      ▼
   proposal?
      │
      ▼
    policy ──(valid)──► tool_executor ──(terminal)──► finalize
      │                      │                        ▲
      └─(budget/invalid)─────┴──(loop, max 4 tools)───┘

Every transition that mutates state goes through: Pydantic arg validation
-> deterministic tool -> committed DB -> persisted ToolEvent -> grounded reply.
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
from app.tools.registry import REGISTRY, openai_style_schemas
from app.tools.escalate_to_human import escalate_to_human, EscalateArgs

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
LOCKED_REPLY = (
    "This conversation has already been handed to our front desk. A human "
    "will continue from here — I won't make any further changes myself."
)
BUDGET_REPLY = (
    "I've gathered what I can, but I don't want to keep guessing. Our front "
    "desk can finish this for you."
)
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


def _when(iso_date) -> str:
    """'2026-10-07' -> 'Tue 7 Oct' — deterministic templates speak human."""
    try:
        d = datetime.strptime(str(iso_date), "%Y-%m-%d")
        return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"
    except ValueError:
        return str(iso_date)


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
            return "llm_down"
        if state.get("proposal") is not None:
            return "policy"
        return "finalize"

    def llm_down(state: TurnState) -> dict:
        """Fail CLOSED: model unusable -> human handoff, never a guess."""
        db = session_factory()
        try:
            result = escalate_to_human(db, state["conversation_id"], EscalateArgs(
                reason="LLM_UNAVAILABLE",
                summary=f"LLM provider unavailable while handling: {state['user_text'][:200]}"))
            ev = {"tool": "escalate_to_human", "arguments": {"reason": "LLM_UNAVAILABLE"},
                  "result": result, "status": "SUCCESS" if result["ok"] else "ERROR",
                  "origin": "fail_closed", "created_at": _now()}
            _persist_event(db, state["conversation_id"], ev)
        finally:
            db.close()
        return {"events": state.get("events", []) + [ev], "reply": LLM_DOWN_REPLY}

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
        try:
            result = spec.fn(db, state["conversation_id"], args_obj)
        finally:
            db.close()
        ev = {"tool": p["name"], "arguments": p["args"], "result": result,
              "status": "SUCCESS" if result["ok"] else "ERROR",
              "origin": "agent", "created_at": _now()}
        db = session_factory()
        try:
            _persist_event(db, state["conversation_id"], ev)
        finally:
            db.close()
        history = list(state["history"])
        history.append({"role": "tool", "tool_call_id": p["id"],
                        "content": json.dumps(result)})
        return {"events": state.get("events", []) + [ev],
                "history": history,
                "tool_calls": state.get("tool_calls", 0) + 1,
                "proposal": None}

    def route_after_execution(state: TurnState) -> str:
        last = state["events"][-1]
        spec = REGISTRY.get(last["tool"])
        if spec and spec.terminal and last["result"].get("ok"):
            return "finalize"
        if state.get("tool_calls", 0) >= MAX_TOOL_CALLS_PER_TURN:
            return "finalize"
        return "agent_llm"

    def finalize(state: TurnState) -> dict:
        reply = state.get("reply", "")
        events = state.get("events", [])
        if not reply and events:
            reply = _template_from_events(events)
        grounded = ground_reply(reply, events) if reply else (
            "How can I help you with your appointment today?")
        return {"reply": grounded}

    # ---------- edges ------------------------------------------------------
    g = StateGraph(TurnState)
    g.add_node("safety_gate", safety_gate)
    g.add_node("hard_stop", hard_stop)
    g.add_node("locked_out", locked_out)
    g.add_node("agent_llm", agent_llm)
    g.add_node("llm_down", llm_down)
    g.add_node("policy", policy)
    g.add_node("tool_executor", tool_executor)
    g.add_node("finalize", finalize)

    g.set_entry_point("safety_gate")
    g.add_conditional_edges("safety_gate", route_after_safety,
                            {"hard_stop": "hard_stop", "locked_out": "locked_out",
                             "agent_llm": "agent_llm"})
    g.add_conditional_edges("agent_llm", route_after_llm,
                            {"policy": "policy", "finalize": "finalize",
                             "llm_down": "llm_down"})
    g.add_conditional_edges("policy", route_after_policy,
                            {"tool_executor": "tool_executor",
                             "agent_llm": "agent_llm", "finalize": "finalize"})
    g.add_conditional_edges("tool_executor", route_after_execution,
                            {"agent_llm": "agent_llm", "finalize": "finalize"})
    g.add_edge("hard_stop", "finalize")
    g.add_edge("locked_out", "finalize")
    g.add_edge("llm_down", "finalize")
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
    """Deterministic grounded sentence for the LAST successful mutation —
    used when the loop ended without the model drafting a reply."""
    for ev in reversed(events):
        res = ev["result"]
        st = res.get("status")
        d = res.get("data", {})
        if st == "BOOKED":
            s = d.get("slot", {})
            doc = f" with {s['doctor']}" if s.get("doctor") else ""
            return (f"Done — {d.get('patient', 'your appointment')}'s appointment "
                    f"is booked for {_when(s.get('date'))} at {s.get('start', '')}"
                    f"{doc} (reference #{d.get('appointment_id')}).")
        if st == "RESCHEDULED":
            s = d.get("to_slot", {})
            doc = f" with {s['doctor']}" if s.get("doctor") else ""
            return (f"Your appointment #{d.get('appointment_id')} is now moved to "
                    f"{_when(s.get('date'))} at {s.get('start', '')}{doc}.")
        if st == "CANCELLED":
            return f"Your appointment #{d.get('appointment_id')} has been cancelled."
        if st in ("ESCALATED", "ALREADY_ESCALATED"):
            return LLM_DOWN_REPLY
    return ""
