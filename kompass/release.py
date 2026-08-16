"""Reproducible identity for a Kompass agent release."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from kompass import __version__
from kompass.config import ROOT, settings
from kompass.prompts import prompt_manifest

DESCRIPTOR = ROOT / "agent_release.json"
_PROMPT_MODULES = (
    "kompass.graph.agent",
    "kompass.graph.critic",
    "kompass.guardrails.safety",
    "evals.judge",
)


class DatasetIdentity(BaseModel):
    version: str
    path: str
    sha256: str


class AgentRelease(BaseModel):
    schema_version: int
    release_version: str
    workflow_version: str
    tool_schema_version: str
    policy_version: str
    eval_dataset: DatasetIdentity
    protocols: dict[str, str]
    prompts: dict[str, dict[str, str]]
    models: dict[str, str]
    release_id: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_release(descriptor_path: Path = DESCRIPTOR) -> AgentRelease:
    descriptor: dict[str, Any] = json.loads(descriptor_path.read_text(encoding="utf-8"))
    if descriptor["release_version"] != __version__:
        raise ValueError("release descriptor and package version do not match")
    dataset = descriptor["eval_dataset"]
    dataset_path = ROOT / dataset["path"]
    actual_hash = _sha256(dataset_path)
    if actual_hash != dataset["sha256"]:
        raise ValueError(
            f"evaluation dataset hash mismatch for {dataset['path']}; "
            "review the change and update agent_release.json deliberately"
        )
    for module in _PROMPT_MODULES:
        importlib.import_module(module)
    models = {
        "provider": settings.llm_provider,
        "reasoning": (
            settings.ollama_model_reasoning
            if settings.llm_provider == "ollama"
            else settings.model_reasoning
        ),
        "balanced": (
            settings.ollama_model_balanced
            if settings.llm_provider == "ollama"
            else settings.model_balanced
        ),
        "fast": (
            settings.ollama_model_fast
            if settings.llm_provider == "ollama"
            else settings.model_fast
        ),
    }
    identity = {**descriptor, "prompts": prompt_manifest(), "models": models}
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return AgentRelease(**identity, release_id=hashlib.sha256(encoded).hexdigest()[:16])


def main() -> None:
    print(build_release().model_dump_json(indent=2))


if __name__ == "__main__":
    main()
