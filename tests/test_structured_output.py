"""Structured-output retries and middleware degradation contracts."""

from langchain_core.messages import AIMessage, ToolMessage

from kompass.graph import critic
from kompass.guardrails.safety import Injection
from kompass.models import structured


class _Runnable:
    def __init__(self, method: str):
        self.method = method

    def invoke(self, prompt, config=None):
        if self.method == "function_calling":
            raise ValueError("malformed tool arguments")
        return Injection(is_attack=False, kind="none", reason="")


class _Model:
    def __init__(self, attempts: list[str]):
        self.attempts = attempts

    def with_structured_output(self, schema, *, method):
        self.attempts.append(method)
        return _Runnable(method)


def test_structured_output_retries_with_independent_method(monkeypatch):
    attempts: list[str] = []
    monkeypatch.setattr(structured, "pick", lambda tier: _Model(attempts))

    result = structured.invoke_structured("fast", Injection, "classify this")

    assert result.kind == "none"
    assert attempts == ["function_calling", "json_schema"]


def test_critic_parser_failure_does_not_replace_grounded_answer(monkeypatch):
    def fail(*args, **kwargs):
        raise structured.StructuredOutputError("both methods returned invalid JSON")

    monkeypatch.setattr(critic, "invoke_structured", fail)
    state = {
        "messages": [
            ToolMessage(content="policy evidence", tool_call_id="c1", name="search_docs"),
            AIMessage(content="A tool-grounded answer."),
        ]
    }

    assert critic.GroundingCritic().after_model(state, None) is None
