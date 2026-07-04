"""Centralized, typed configuration.

All runtime knobs live here and are populated from environment variables / a
local ``.env`` file (see ``.env.example``). Import the module-level ``settings``
singleton anywhere in the package:

    from kompass.config import settings
    model = settings.model_reasoning
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root — relative paths in settings (DB, Chroma) resolve against this, so
# MCP subprocesses and scripts work regardless of their working directory.
ROOT = Path(__file__).resolve().parents[1]

# Export .env into the process environment: provider SDKs (OpenAI, Anthropic, ...)
# read their API keys from os.environ, which keeps the model layer provider-agnostic.
load_dotenv(ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Model provider ────────────────────────────────────────────────
    # Models are "provider:model" strings resolved by langchain's init_chat_model,
    # so switching provider is a config change, not a code change.
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    model_reasoning: str = Field(default="openai:gpt-5.5", alias="KOMPASS_MODEL_REASONING")
    model_balanced: str = Field(default="openai:gpt-5.4", alias="KOMPASS_MODEL_BALANCED")
    model_fast: str = Field(default="openai:gpt-5.4-nano", alias="KOMPASS_MODEL_FAST")

    # ── Agent ─────────────────────────────────────────────────────────
    # single = one agent with all tools; multi = supervisor delegates research
    # to a worker agent, keeping the write tools (and HITL gate) to itself.
    agent_mode: str = Field(default="single", alias="KOMPASS_AGENT_MODE")

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
