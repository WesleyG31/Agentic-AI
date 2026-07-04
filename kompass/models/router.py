"""Model routing: one place that maps a capability tier to a concrete chat model.

Tiers come from config as "provider:model" strings (init_chat_model format), so
swapping provider or model is a .env change — no code touches a vendor SDK.
"""

from functools import lru_cache
from typing import Literal

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from kompass.config import settings

Tier = Literal["fast", "balanced", "reasoning"]


@lru_cache
def pick(tier: Tier) -> BaseChatModel:
    """Return the chat model for a tier: fast (routing/classification),
    balanced (drafting/synthesis), reasoning (hard planning/verification)."""
    spec = {
        "fast": settings.model_fast,
        "balanced": settings.model_balanced,
        "reasoning": settings.model_reasoning,
    }[tier]
    return init_chat_model(spec)
