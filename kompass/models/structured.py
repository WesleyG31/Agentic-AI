"""Reliable structured-output calls across hosted and local chat models.

Small local models occasionally ignore one structured-output steering mechanism or
return an empty payload. Trying the provider's preferred mode and one independent
fallback prevents a transient parser failure from crashing an otherwise valid agent run.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from kompass.config import settings
from kompass.models.router import Tier, pick

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class StructuredOutputError(RuntimeError):
    """All supported structured-output strategies failed."""


def invoke_structured(
    tier: Tier,
    schema: type[SchemaT],
    prompt: Any,
    *,
    config: dict | None = None,
) -> SchemaT:
    """Invoke a typed model with an independent structured-output retry.

    Ollama tool calling is more reliable for LFM-family models, while OpenAI's
    native JSON schema is preferred there. Both paths retry through the other
    mechanism and preserve callbacks, so each attempt remains visible in Langfuse.
    """
    methods = (
        ("function_calling", "json_schema")
        if settings.llm_provider == "ollama"
        else ("json_schema", "function_calling")
    )
    errors: list[str] = []
    for method in methods:
        try:
            result = (
                pick(tier)
                .with_structured_output(schema, method=method)
                .invoke(prompt, config=config)
            )
            if result is not None:
                return result
            errors.append(f"{method}: empty structured result")
        except Exception as exc:
            errors.append(f"{method}: {type(exc).__name__}: {exc}")
    raise StructuredOutputError(" | ".join(errors))
