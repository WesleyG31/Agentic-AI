"""Kompass — Agentic Support & Operations Assistant (LangGraph v1).

Public entry points are added as slices land. For now this package exposes the
version and the settings singleton.
"""

__version__ = "0.1.0"

from kompass.config import settings  # noqa: E402

__all__ = ["settings", "__version__"]
