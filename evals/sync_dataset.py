"""Upsert the local golden set into the visual Langfuse Datasets view."""

from __future__ import annotations

import sys
from uuid import NAMESPACE_URL, uuid5

from evals.dataset import expected_tools, load_golden
from kompass.observability import client, enabled

DATASET_NAME = "kompass-golden-v1"


def main() -> int:
    if not enabled():
        print("Langfuse is disabled or its keys are missing; run observability setup first.")
        return 1
    lf = client()
    assert lf is not None
    items = load_golden()
    lf.create_dataset(
        name=DATASET_NAME,
        description="60 interview-grade cases covering RAG, SQL, multi-step, HITL and abstention.",
        metadata={"schema_version": "1.0", "case_count": len(items)},
    )
    for item in items:
        lf.create_dataset_item(
            id=str(uuid5(NAMESPACE_URL, f"{DATASET_NAME}:{item['id']}")),
            dataset_name=DATASET_NAME,
            input={"question": item["question"], "category": item["category"]},
            expected_output={
                "facts": item["expected_facts"],
                "citation": item["must_cite"],
                "tools": expected_tools(item),
                "action": item.get("action"),
            },
            metadata={"case_id": item["id"]},
        )
        print(f"synced {item['id']}")
    lf.flush()
    print(f"dataset {DATASET_NAME}: {len(items)} cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
