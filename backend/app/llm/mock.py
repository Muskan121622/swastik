"""Scripted provider for tests. Deterministic, zero-cost, and able to
behave badly ON PURPOSE (malformed args, hallucinated confirmations,
outages) so the safety layers are what get tested, not the model."""
from app.llm.base import ToolProposal, ReplyProposal, LLMUnavailableError


class MockLLM:
    name = "mock"

    def __init__(self, script: list | None = None, default_reply: str = "How can I help?"):
        # script: list of ToolProposal | ReplyProposal | LLMUnavailableError
        self.script = list(script or [])
        self.default_reply = default_reply
        self.calls: list[list[dict]] = []   # for assertions on what it was shown

    def decide(self, messages: list[dict], tools: list[dict]):
        self.calls.append(messages)
        if not self.script:
            return ReplyProposal(self.default_reply)
        nxt = self.script.pop(0)
        if isinstance(nxt, LLMUnavailableError):
            raise nxt
        return nxt
