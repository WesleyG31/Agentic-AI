"""Smoke tests — verify the package imports and config loads with defaults.

These run without any API key or external service, so CI stays green from the
first commit. Behavioral tests land alongside their slices.
"""

import kompass
from kompass.config import Settings


def test_version():
    assert kompass.__version__ == "0.1.0"


def test_settings_defaults():
    # Instantiate in isolation (ignore any local .env) to assert the defaults.
    s = Settings(_env_file=None)
    assert s.model_reasoning == "openai:gpt-5.5"
    assert s.model_fast == "openai:gpt-5.4-nano"
    assert s.vector_backend == "chroma"
    assert s.checkpointer == "sqlite"
    assert s.api_port == 8000


def test_settings_singleton_importable():
    assert kompass.settings is not None
