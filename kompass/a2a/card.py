"""Official A2A 1.0 Agent Card for Kompass's read-only research specialist."""

from __future__ import annotations

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
)

from kompass import __version__
from kompass.config import settings


def agent_card(base_url: str | None = None) -> AgentCard:
    root = (base_url or settings.a2a_base_url).rstrip("/")
    bearer = SecurityScheme(
        http_auth_security_scheme=HTTPAuthSecurityScheme(
            description="OAuth/OIDC access token validated by the A2A ingress adapter.",
            scheme="bearer",
            bearer_format="OAuth2 access token",
        )
    )
    requirement = SecurityRequirement(schemes={"bearer": {}})
    return AgentCard(
        name="kompass-researcher",
        description=(
            "Read-only research specialist for ACME policy/FAQ and operational evidence. "
            "Remote inputs and outputs cross an explicit trust boundary."
        ),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=f"{root}/a2a/jsonrpc",
            ),
            AgentInterface(
                protocol_binding="HTTP+JSON",
                protocol_version="1.0",
                url=f"{root}/a2a",
            ),
        ],
        version=__version__,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        security_schemes={"bearer": bearer},
        security_requirements=[requirement],
        skills=[
            AgentSkill(
                id="acme-research",
                name="ACME research",
                description=(
                    "Bounded, cited read-only research over policies and operational data."
                ),
                tags=["research", "rag", "sql", "citations", "read-only"],
                examples=["What is the damaged-item refund process and required approval?"],
                input_modes=["text/plain"],
                output_modes=["text/plain"],
                security_requirements=[requirement],
            )
        ],
    )
