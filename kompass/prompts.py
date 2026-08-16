"""Code-first prompt registry with explicit semantic versions and fingerprints.

Runtime prompts remain reviewable in Git and work while Langfuse is offline.
The sync command mirrors them to Langfuse Prompt Management for visual history.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

_REGISTRY: dict[str, PromptSpec] = {}


@dataclass(frozen=True)
class PromptSpec:
    name: str
    version: str
    text: str
    description: str = ""

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:12]

    def render(self, **variables: object) -> str:
        return self.text.format(**variables)


def register(prompt: PromptSpec) -> PromptSpec:
    previous = _REGISTRY.get(prompt.name)
    if previous is not None and previous != prompt:
        raise ValueError(f"prompt {prompt.name!r} was registered twice with different content")
    _REGISTRY[prompt.name] = prompt
    return prompt


def prompt_manifest() -> dict[str, dict[str, str]]:
    return {
        name: {"version": spec.version, "fingerprint": spec.fingerprint}
        for name, spec in sorted(_REGISTRY.items())
    }


def all_prompts() -> tuple[PromptSpec, ...]:
    return tuple(_REGISTRY[name] for name in sorted(_REGISTRY))
