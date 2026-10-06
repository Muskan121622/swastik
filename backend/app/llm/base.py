"""LLM adapter interface. The app is built around this, not a vendor SDK.

Providers PROPOSE decisions; nothing here touches the database.
"""
from dataclasses import dataclass, field
from typing import Protocol


class LLMUnavailableError(RuntimeError):
    """Raised by providers when the model cannot be consulted.
    The graph treats this as fail-closed: hand off to a human."""


@dataclass
class ToolProposal:
    name: str
    args: dict = field(default_factory=dict)


@dataclass
class ReplyProposal:
    content: str


class LLMProvider(Protocol):
    name: str

    def decide(self, messages: list[dict], tools: list[dict]) -> ToolProposal | ReplyProposal:
        """Given the conversation (openai-style messages) and the tool
        schemas, return either a tool proposal or a text reply."""
        ...


def parse_openai_message(msg) -> ToolProposal | ReplyProposal:
    """Convert an OpenAI-style assistant message into a decision."""
    if getattr(msg, "tool_calls", None):
        tc = msg.tool_calls[0]
        import json
        fn = tc.function
        try:
            args = json.loads(fn.arguments or "{}")
        except json.JSONDecodeError:
            args = {"__malformed_json__": fn.arguments}
        return ToolProposal(name=fn.name, args=args)
    return ReplyProposal(content=(msg.content or "").strip())
