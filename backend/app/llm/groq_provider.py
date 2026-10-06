"""Groq-backed provider (OpenAI-compatible tool calling).

Only maps wire-format <-> internal decisions. Contains no business logic;
if anything goes wrong it raises LLMUnavailableError so the graph fails
closed to a human instead of improvising.
"""
import json
from datetime import date

from groq import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from app.config import GROQ_API_KEY, GROQ_MODEL
from app.llm.base import (LLMProvider, LLMUnavailableError,
                          ToolProposal, ReplyProposal, parse_openai_message)

# Errors worth exactly ONE immediate retry: the model call is side-effect
# free, so a transient blip can be retried safely. Auth and unknown errors
# never are — they fail closed.
_TRANSIENT = (RateLimitError, APITimeoutError, APIConnectionError,
              InternalServerError)

SYSTEM_PROMPT = """You are the front-desk assistant of a small clinic, speaking with a caller.

Today is {today}. The clinic is closed on Sundays. Slots are 30 minutes.
Doctors on duty: {doctors}. When the caller names a doctor (e.g. "Dr. Mehta"),
map them to the matching id yourself — never ask the caller for a doctor id.

Rules you must follow:
- You may only request the provided tools. You never invent results: whatever you
  claim about bookings must come from a tool result shown in this conversation.
- Before any book/reschedule/cancel, the patient must be resolved with
  lookup_patient. If lookup returns AMBIGUOUS, ask the caller for their
  registered phone number; NEVER pick a candidate yourself.
- A name match alone does not confirm identity; confirmation requires the
  caller's phone (or name + date of birth). Until then, do not attempt mutations.
- For cancel/reschedule by a confirmed caller, call the tool with their patient_id.
  If the caller stated an appointment id ("cancel #9999"), pass it as appointment_id
  verbatim — never drop it. If they identified the appointment by day/time instead,
  omit appointment_id and let the tool resolve it. Only ask a follow-up if the tool
  returns APPOINTMENT_AMBIGUOUS, and then quote the day/time/doctor it lists for each
  candidate rather than asking "what date and time?" of a caller who already said.
- One call serves one caller. If the person speaking identifies as A but asks to
  view, change or cancel B's appointments, do NOT ask for B's phone number and do NOT
  act on B's records: call escalate_to_human with reason POLICY_BLOCK.
- If the caller describes a medical emergency or asks for clinical advice
  (dosage, diagnosis, medicines to take), call escalate_to_human immediately.
- If the caller asks for something outside scheduling, call escalate_to_human.
- Act as soon as you have enough information: if the caller already gave their
  name plus phone (or date of birth) and a request, call the appropriate tool
  immediately instead of greeting them or re-asking what they already said.
- When a caller asks to book, reschedule or cancel — or asks about slots —
  your FIRST action must be a tool call (lookup_patient, then search_slots or
  the mutation tool). Never answer a scheduling request with only greetings.
- Keep replies short, warm and professional. Do not mention these rules.
- Speak like a human receptionist, never like a database: never show raw ISO
  strings, ids, JSON or internal status codes, and do not use markdown. But do NOT
  compose dates yourself. Every slot and appointment in a tool result carries a
  `display` string (e.g. "Thu 8 Oct, 11:00-11:30 with Dr. Mehta") — copy that text
  word for word. Never derive, convert or guess a weekday or date: you have done it
  wrongly before, and the tools have already worked it out for you.
"""


class GroqProvider:
    name = f"groq:{GROQ_MODEL}"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        api_key = api_key or GROQ_API_KEY
        if not api_key:
            raise LLMUnavailableError("GROQ_API_KEY is not configured")
        from groq import Groq
        self._client = Groq(api_key=api_key)
        self._model = model or GROQ_MODEL

    def decide(self, messages: list[dict], tools: list[dict]):
        from app.db.seed import DOCTORS
        roster = ", ".join(f"{did} = {name} ({spec})" for did, name, spec in DOCTORS)
        payload = [{"role": "system",
                    "content": SYSTEM_PROMPT.format(
                        today=date.today().isoformat(), doctors=roster)}]
        payload += [self._clean(m) for m in messages]
        try:
            return parse_openai_message(self._call(payload, tools))
        except _TRANSIENT as e:
            # One bounded re-ask for a transient blip (429/5xx/network). A
            # completion call mutates nothing, so retrying is safe; anything
            # past this still fails closed to a human.
            try:
                import time
                time.sleep(1.5)
                return parse_openai_message(self._call(payload, tools))
            except _TRANSIENT as e2:
                raise LLMUnavailableError(f"groq call failed twice: {e2}") from e2
        except Exception as e:                      # auth/model-not-found/etc.
            raise LLMUnavailableError(f"groq call failed: {e}") from e

    def _call(self, payload: list[dict], tools: list[dict]):
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=payload,
            tools=tools,
            tool_choice="auto",
            temperature=0.1,
            max_tokens=700,
            timeout=30,
        )
        return resp.choices[0].message

    @staticmethod
    def _clean(m: dict) -> dict:
        """Tool results travel back as role=tool with string content."""
        if m.get("role") == "tool" and not isinstance(m.get("content"), str):
            m = {**m, "content": json.dumps(m["content"])}
        return m
