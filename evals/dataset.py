"""Golden-dataset loading, validation and tool-trajectory normalization."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import ToolMessage

from kompass.config import ROOT

GOLDEN = ROOT / "evals" / "golden_set.json"
ALLOWED_CATEGORIES = {"rag", "sql", "multi", "action", "abstain"}


def expected_tools(item: dict) -> list[str]:
    """Return explicit expectations, with backwards-compatible category defaults."""
    if "expected_tools" in item:
        return list(item["expected_tools"])
    category = item["category"]
    if category == "rag":
        return ["search_docs"]
    if category == "sql":
        return ["query_database"]
    if category == "multi":
        return ["query_database", "search_docs"]
    if category == "action":
        if "ticket" in item["question"].lower():
            return ["query_database", "update_ticket"]
        return ["query_database", "search_docs", "create_refund"]
    if "order" in item["question"].lower() and "delete" not in item["question"].lower():
        return ["query_database"]
    return []


def load_golden(limit: int | None = None) -> list[dict]:
    items = json.loads(GOLDEN.read_text(encoding="utf-8"))
    validate_golden(items)
    return items[:limit]


def validate_golden(items: list[dict]) -> None:
    if not 50 <= len(items) <= 100:
        raise ValueError(f"golden set must contain 50-100 cases, found {len(items)}")
    ids = [item.get("id") for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("golden-set ids must be unique")
    for item in items:
        missing = {"id", "category", "question", "expected_facts", "must_cite"} - item.keys()
        if missing:
            raise ValueError(f"{item.get('id', '<unknown>')} is missing {sorted(missing)}")
        if item["category"] not in ALLOWED_CATEGORIES:
            raise ValueError(f"{item['id']} has unknown category {item['category']!r}")
        if not isinstance(item["expected_facts"], list):
            raise ValueError(f"{item['id']} expected_facts must be a list")


def tool_trajectory(messages: list[Any]) -> list[dict[str, Any]]:
    """Pair assistant tool calls with tool outputs into a JSON-safe trajectory."""
    calls: dict[str, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    for message in messages:
        for call in getattr(message, "tool_calls", ()) or ():
            row = {
                "name": call.get("name"),
                "args": call.get("args", {}),
                "result": None,
            }
            calls[str(call.get("id", ""))] = row
            ordered.append(row)
        if isinstance(message, ToolMessage):
            row = calls.get(str(message.tool_call_id))
            if row is None:
                row = {"name": message.name, "args": {}, "result": None}
                ordered.append(row)
            row["result"] = str(message.content)[:3_000]
    return ordered
