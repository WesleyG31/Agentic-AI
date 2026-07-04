"""Model routing: one place that maps a capability tier to a concrete chat model.

Tiers come from config as "provider:model" strings (init_chat_model format), so
swapping provider or model is a .env change — no code touches a vendor SDK.
"""

from functools import lru_cache
from typing import Literal

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from kompass.config import settings
from kompass.obs import TraceHandler

Tier = Literal["fast", "balanced", "reasoning"]


@lru_cache
def pick(tier: Tier) -> BaseChatModel:
    """Return the chat model for a tier: fast (routing/classification),
    balanced (drafting/synthesis), reasoning (hard planning/verification).

    Every model carries the local trace handler (runs.jsonl), plus the
    Langfuse handler when LANGFUSE_ENABLED is set (langfuse is an optional
    extra — imported only inside the branch)."""
    spec = {
        "fast": settings.model_fast,
        "balanced": settings.model_balanced,
        "reasoning": settings.model_reasoning,
    }[tier]
    callbacks: list = [TraceHandler()]
    if settings.langfuse_enabled:
        from langfuse.langchain import CallbackHandler

        callbacks.append(CallbackHandler())
    return init_chat_model(spec, callbacks=callbacks)
