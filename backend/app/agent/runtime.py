"""Provider runtime: which LLM the graph talks to, decided once per process.

No API key -> an always-unavailable provider, so the system fails closed
to human handoffs rather than pretending to converse.
"""
from app.llm.base import LLMProvider, LLMUnavailableError
from app.llm.groq_provider import GroqProvider
from app.config import GROQ_API_KEY


class UnavailableProvider:
    name = "unavailable"

    def decide(self, messages, tools):
        raise LLMUnavailableError("no LLM provider configured")


_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        _provider = GroqProvider() if GROQ_API_KEY else UnavailableProvider()
    return _provider


def set_provider(provider: LLMProvider | None) -> None:
    """Tests (and local demos) inject MockLLM here."""
    global _provider
    _provider = provider
