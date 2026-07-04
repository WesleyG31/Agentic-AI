"""Local trace observability: one JSON line per LLM call.

TraceHandler is attached to every chat model the router hands out (see
kompass.models.router.pick). Each completed LLM call appends a line to
runs.jsonl at the repo root:

    {"ts": ..., "thread_id": ..., "model": ..., "latency_s": ...,
     "input_tokens": ..., "output_tokens": ...}

thread_id comes from the run metadata LangGraph propagates from the config,
so lines correlate with API threads; calls made outside a graph log null.
"""

import json
import time
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from kompass.config import ROOT

TRACE_FILE = ROOT / "runs.jsonl"


class TraceHandler(BaseCallbackHandler):
    """Append one JSON line per LLM call to runs.jsonl."""

    def __init__(self):
        self._runs: dict[UUID, tuple[float, str | None, str | None]] = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        metadata = metadata or {}
        self._runs[run_id] = (
            time.monotonic(),
            metadata.get("ls_model_name"),
            metadata.get("thread_id"),
        )

    def on_llm_end(self, response: LLMResult, *, run_id, **kwargs):
        started, model, thread_id = self._runs.pop(run_id)
        usage = getattr(response.generations[0][0].message, "usage_metadata", None) or {}
        line = {
            "ts": time.time(),
            "thread_id": thread_id,
            "model": model,
            "latency_s": round(time.monotonic() - started, 3),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
        }
        with TRACE_FILE.open("a") as f:
            f.write(json.dumps(line) + "\n")
