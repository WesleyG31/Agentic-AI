"""Centralized, typed configuration.

All runtime knobs live here and are populated from environment variables / a
local ``.env`` file (see ``.env.example``). Import the module-level ``settings``
singleton anywhere in the package:

    from kompass.config import settings
    model = settings.model_reasoning
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import AliasChoices, Field
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
    # Select the chat backend without touching application code.
    # Ollama runs locally; OpenAI uses OPENAI_API_KEY.
    llm_provider: Literal["openai", "ollama"] = Field(
        default="ollama", alias="KOMPASS_LLM_PROVIDER"
    )

    # OpenAI model tiers. Existing provider:model values remain supported.
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    model_reasoning: str = Field(default="openai:gpt-5.5", alias="KOMPASS_MODEL_REASONING")
    model_balanced: str = Field(default="openai:gpt-5.4", alias="KOMPASS_MODEL_BALANCED")
    model_fast: str = Field(default="openai:gpt-5.4-nano", alias="KOMPASS_MODEL_FAST")

    # Ollama model tiers. One local model can serve all three roles, or each
    # tier can point at a different model downloaded with `ollama pull`.
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model_reasoning: str = Field(default="lfm2.5:8b", alias="KOMPASS_OLLAMA_MODEL_REASONING")
    ollama_model_balanced: str = Field(default="lfm2.5:8b", alias="KOMPASS_OLLAMA_MODEL_BALANCED")
    ollama_model_fast: str = Field(default="lfm2.5:8b", alias="KOMPASS_OLLAMA_MODEL_FAST")

    # ── Agent ─────────────────────────────────────────────────────────
    # single = one agent with all tools; multi = supervisor delegates research
    # to a worker agent, keeping the write tools (and HITL gate) to itself.
    agent_mode: str = Field(default="single", alias="KOMPASS_AGENT_MODE")
    # Per-run token cap enforced by TokenBudgetMiddleware (runaway-loop backstop).
    token_budget: int = Field(default=200_000, alias="KOMPASS_TOKEN_BUDGET")
    environment: Literal["development", "test", "staging", "production"] = Field(
        default="development", alias="KOMPASS_ENVIRONMENT"
    )

    # Request defaults are for the local identity adapter only. Production identity and
    # tenant values must come from verified OAuth/OIDC claims at the ingress boundary.
    auth_mode: Literal["local", "oidc"] = Field(default="local", alias="KOMPASS_AUTH_MODE")
    default_tenant_id: str = Field(default="local", alias="KOMPASS_DEFAULT_TENANT_ID")
    default_timezone: str = Field(default="Europe/Berlin", alias="KOMPASS_DEFAULT_TIMEZONE")
    default_locale: str = Field(default="en-US", alias="KOMPASS_DEFAULT_LOCALE")
    oidc_issuer_url: str = Field(default="", alias="KOMPASS_OIDC_ISSUER_URL")
    oidc_audience: str = Field(default="", alias="KOMPASS_OIDC_AUDIENCE")
    # Optional transport overrides keep the externally visible issuer stable when the
    # verifier runs inside Docker. They never replace issuer/audience validation.
    oidc_discovery_url: str = Field(default="", alias="KOMPASS_OIDC_DISCOVERY_URL")
    oidc_jwks_url: str = Field(default="", alias="KOMPASS_OIDC_JWKS_URL")
    oidc_timeout_seconds: float = Field(default=5.0, alias="KOMPASS_OIDC_TIMEOUT_SECONDS")
    oidc_clock_skew_seconds: int = Field(default=30, alias="KOMPASS_OIDC_CLOCK_SKEW_SECONDS")
    max_request_bytes: int = Field(default=64_000, alias="KOMPASS_MAX_REQUEST_BYTES")

    # ── Retrieval ─────────────────────────────────────────────────────
    chroma_path: str = Field(default=".chroma", alias="KOMPASS_CHROMA_PATH")
    acme_db: str = Field(default="corpus/acme.db", alias="KOMPASS_ACME_DB")

    # ── Persistence / durable HITL ────────────────────────────────────
    sqlite_checkpoint: str = Field(
        default="kompass_checkpoints.db", alias="KOMPASS_SQLITE_CHECKPOINT"
    )
    checkpoint_postgres_dsn: str = Field(default="", alias="KOMPASS_CHECKPOINT_POSTGRES_DSN")
    database_url: str = Field(default="", alias="KOMPASS_DATABASE_URL")
    execution_store_db: str = Field(
        default=".runtime/executions.db", alias="KOMPASS_EXECUTION_STORE_DB"
    )
    execution_max_concurrency: int = Field(
        default=8, alias="KOMPASS_EXECUTION_MAX_CONCURRENCY"
    )
    execution_queue_capacity: int = Field(
        default=32, alias="KOMPASS_EXECUTION_QUEUE_CAPACITY"
    )
    execution_deadline_seconds: float = Field(
        default=120.0, alias="KOMPASS_EXECUTION_DEADLINE_SECONDS"
    )
    execution_max_attempts: int = Field(default=2, alias="KOMPASS_EXECUTION_MAX_ATTEMPTS")
    action_receipts_db: str = Field(
        default=".runtime/action_receipts.db", alias="KOMPASS_ACTION_RECEIPTS_DB"
    )
    action_timeout_seconds: float = Field(default=30.0, alias="KOMPASS_ACTION_TIMEOUT_SECONDS")
    action_max_attempts: int = Field(default=2, alias="KOMPASS_ACTION_MAX_ATTEMPTS")

    # ── Observability ─────────────────────────────────────────────────
    langfuse_enabled: bool = Field(default=False, alias="LANGFUSE_ENABLED")
    langfuse_base_url: str = Field(
        default="http://localhost:3000",
        validation_alias=AliasChoices("LANGFUSE_BASE_URL", "LANGFUSE_HOST"),
    )
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    langfuse_environment: str = Field(default="development", alias="LANGFUSE_TRACING_ENVIRONMENT")
    langfuse_release: str = Field(default="local", alias="LANGFUSE_RELEASE")
    langfuse_mask_pii: bool = Field(default=True, alias="LANGFUSE_MASK_PII")
    langfuse_capture_content: bool = Field(default=False, alias="LANGFUSE_CAPTURE_CONTENT")
    # Ollama is free locally. Hosted-provider prices are explicit configuration
    # because vendor price lists change independently from this repository.
    input_cost_per_million: float = Field(default=0.0, alias="KOMPASS_INPUT_COST_PER_MILLION")
    output_cost_per_million: float = Field(default=0.0, alias="KOMPASS_OUTPUT_COST_PER_MILLION")

    # ── Serving ───────────────────────────────────────────────────────
    api_host: str = Field(default="0.0.0.0", alias="KOMPASS_API_HOST")
    api_port: int = Field(default=8000, alias="KOMPASS_API_PORT")

    # ── A2A ───────────────────────────────────────────────────────────
    # A2A 1.0 surface. The dev token adapter is disabled when the token is blank;
    # production deployments replace it with an OAuth/OIDC-verifying ingress.
    a2a_port: int = Field(default=8030, alias="KOMPASS_A2A_PORT")
    a2a_base_url: str = Field(default="http://localhost:8030", alias="KOMPASS_A2A_BASE_URL")
    a2a_bearer_token: str = Field(default="", alias="KOMPASS_A2A_BEARER_TOKEN")
    a2a_dev_token: str = Field(default="", alias="KOMPASS_A2A_DEV_TOKEN")
    a2a_dev_subject: str = Field(default="local-a2a-client", alias="KOMPASS_A2A_DEV_SUBJECT")
    a2a_dev_tenant: str = Field(default="local", alias="KOMPASS_A2A_DEV_TENANT")
    a2a_dev_scopes: str = Field(default="research:read", alias="KOMPASS_A2A_DEV_SCOPES")
    a2a_timeout_seconds: float = Field(default=60.0, alias="KOMPASS_A2A_TIMEOUT_SECONDS")
    a2a_max_concurrency: int = Field(default=4, alias="KOMPASS_A2A_MAX_CONCURRENCY")

    # MCP local stdio remains the default. Set a URL/token to use the production-oriented
    # Streamable HTTP resource-server path instead.
    mcp_remote_url: str = Field(default="", alias="KOMPASS_MCP_REMOTE_URL")
    mcp_bearer_token: str = Field(default="", alias="KOMPASS_MCP_BEARER_TOKEN")
    mcp_timeout_seconds: float = Field(default=30.0, alias="KOMPASS_MCP_TIMEOUT_SECONDS")
    mcp_http_host: str = Field(default="127.0.0.1", alias="KOMPASS_MCP_HTTP_HOST")
    mcp_http_port: int = Field(default=8050, alias="KOMPASS_MCP_HTTP_PORT")
    mcp_dev_token: str = Field(default="", alias="KOMPASS_MCP_DEV_TOKEN")
    mcp_dev_subject: str = Field(default="local-mcp-client", alias="KOMPASS_MCP_DEV_SUBJECT")
    mcp_dev_tenant: str = Field(default="local", alias="KOMPASS_MCP_DEV_TENANT")
    mcp_dev_scopes: str = Field(
        default=(
            "mcp:invoke documents:read operations:read tickets:read "
            "refunds:create tickets:write"
        ),
        alias="KOMPASS_MCP_DEV_SCOPES",
    )
    mcp_issuer_url: str = Field(default="", alias="KOMPASS_MCP_ISSUER_URL")
    mcp_resource_url: str = Field(default="", alias="KOMPASS_MCP_RESOURCE_URL")

    # ── Triggers ──────────────────────────────────────────────────────
    # Event-driven surface (kompass/triggers): the port the standalone
    # webhook app listens on.
    trigger_port: int = Field(default=8040, alias="KOMPASS_TRIGGER_PORT")
    trigger_bearer_token: str = Field(default="", alias="KOMPASS_TRIGGER_BEARER_TOKEN")


settings = Settings()
