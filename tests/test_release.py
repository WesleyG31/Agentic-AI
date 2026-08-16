import json

import pytest

from kompass.config import ROOT
from kompass.release import build_release


def test_release_identity_is_reproducible_and_complete():
    first = build_release()
    second = build_release()

    assert first.release_id == second.release_id
    assert len(first.release_id) == 16
    assert first.prompts["kompass-agent-system"]["version"] == "2.2.0"
    assert first.eval_dataset.path == "evals/golden_set.json"
    assert set(first.models) == {"provider", "reasoning", "balanced", "fast"}


def test_release_fails_closed_when_dataset_drifted(tmp_path):
    descriptor = json.loads((ROOT / "agent_release.json").read_text(encoding="utf-8"))
    descriptor["eval_dataset"]["sha256"] = "0" * 64
    path = tmp_path / "agent_release.json"
    path.write_text(json.dumps(descriptor), encoding="utf-8")

    with pytest.raises(ValueError, match="dataset hash mismatch"):
        build_release(path)
