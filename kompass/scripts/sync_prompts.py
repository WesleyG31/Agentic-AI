"""Publish the code-reviewed prompt registry to Langfuse Prompt Management."""

from __future__ import annotations

import sys

# These imports populate the registry without executing an agent run.
import evals.judge  # noqa: F401
import kompass.graph.agent  # noqa: F401
import kompass.graph.critic  # noqa: F401
import kompass.guardrails.safety  # noqa: F401
from kompass.observability import client, enabled
from kompass.prompts import all_prompts


def main() -> int:
    if not enabled():
        print("Langfuse is disabled or its keys are missing; run observability setup first.")
        return 1
    lf = client()
    assert lf is not None
    for spec in all_prompts():
        lf.create_prompt(
            name=spec.name,
            prompt=spec.text,
            labels=["production"],
            tags=["kompass", f"app-version:{spec.version}"],
            config={
                "app_version": spec.version,
                "sha256_12": spec.fingerprint,
                "description": spec.description,
            },
            commit_message=f"Sync {spec.name} {spec.version} ({spec.fingerprint})",
        )
        print(f"synced {spec.name} v{spec.version} [{spec.fingerprint}]")
    lf.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
