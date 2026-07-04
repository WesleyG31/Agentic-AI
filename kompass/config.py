"""Centralized, typed configuration.

All runtime knobs live here and are populated from environment variables / a
local ``.env`` file (see ``.env.example``). Import the module-level ``settings``
singleton anywhere in the package:

    from kompass.config import settings
    model = settings.model_reasoning
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Model provider ────────────────────────────────────────────────
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    model_reasoning: str = Field(default="claude-opus-4-8", alias="KOMPASS_MODEL_REASONING")
    model_balanced: str = Field(default="claude-sonnet-5", alias="KOMPASS_MODEL_BALANCED")
    model_fast: str = Field(default="claude-haiku-4-5-20251001", alias="KOMPASS_MODEL_FAST")

    # ── Retrieval ─────────────────────────────────────────────────────
    vector_backend: str = Field(default="chroma", alias="KOMPASS_VECTOR_BACKEND")
    chroma_path: str = Field(default=".chroma", alias="KOMPASS_CHROMA_PATH")
    qdrant_url: str = Field(default="http://localhost:6333", alias="QDRANT_URL")
    acme_db: str = Field(default="corpus/acme.db", alias="KOMPASS_ACME_DB")

    # ── Persistence / durable HITL ────────────────────────────────────
    checkpointer: str = Field(default="sqlite", alias="KOMPASS_CHECKPOINTER")
    sqlite_checkpoint: str = Field(
        default="kompass_checkpoints.db", alias="KOMPASS_SQLITE_CHECKPOINT"
    )
    postgres_url: str = Field(
        default="postgresql://kompass:kompass@localhost:5432/kompass", alias="POSTGRES_URL"
    )

    # ── Observability ─────────────────────────────────────────────────
    langfuse_enabled: bool = Field(default=False, alias="LANGFUSE_ENABLED")
    langfuse_host: str = Field(default="http://localhost:3000", alias="LANGFUSE_HOST")
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")

    # ── Serving ───────────────────────────────────────────────────────
    api_host: str = Field(default="0.0.0.0", alias="KOMPASS_API_HOST")
    api_port: int = Field(default=8000, alias="KOMPASS_API_PORT")


settings = Settings()
