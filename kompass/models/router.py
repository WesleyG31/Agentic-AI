"""Model routing: one place that maps a capability tier to a concrete chat model.

Tiers come from config as "provider:model" strings (init_chat_model format), so
swapping provider or model is a .env change — no code touches a vendor SDK.
"""

from functools import lru_cache
from typing import Literal

# Previous generic factory kept for reference, as requested:
# from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from kompass.config import settings
from kompass.obs import TraceHandler

Tier = Literal["fast", "balanced", "reasoning"]


@lru_cache
def pick(tier: Tier) -> BaseChatModel:
    """Return the chat model for a tier: fast (routing/classification),
    balanced (drafting/synthesis), reasoning (hard planning/verification).

    Every model carries the lightweight local trace handler (runs.jsonl).
    Langfuse is attached once at the root graph invocation so the complete
    agent/tool tree is one trace instead of one disconnected trace per model."""
    # Previous implementation kept here as comments, as requested:
    # spec = {
    #     "fast": settings.model_fast,
    #     "balanced": settings.model_balanced,
    #     "reasoning": settings.model_reasoning,
    # }[tier]
    # callbacks: list = [TraceHandler()]
    # if settings.langfuse_enabled:
    #     from langfuse.langchain import CallbackHandler
    #
    #     callbacks.append(CallbackHandler())
    # return init_chat_model(spec, callbacks=callbacks)

    callbacks: list = [TraceHandler()]
    if settings.llm_provider == "ollama":
        model = {
            "fast": settings.ollama_model_fast,
            "balanced": settings.ollama_model_balanced,
            "reasoning": settings.ollama_model_reasoning,
        }[tier]
        return ChatOllama(
            model=model,
            base_url=settings.ollama_base_url,
            temperature=0,
            callbacks=callbacks,
            tags=["kompass", f"tier:{tier}", "provider:ollama"],
            metadata={"kompass.model_tier": tier, "kompass.provider": "ollama"},
        )

    spec = {
        "fast": settings.model_fast,
        "balanced": settings.model_balanced,
        "reasoning": settings.model_reasoning,
    }[tier]
    # ChatOpenAI expects the bare model name, while the old generic factory used
    # provider:model strings. Accept both formats so existing .env files still work.
    model = spec.removeprefix("openai:")
    return ChatOpenAI(
        model=model,
        callbacks=callbacks,
        tags=["kompass", f"tier:{tier}", "provider:openai"],
        metadata={"kompass.model_tier": tier, "kompass.provider": "openai"},
    )
