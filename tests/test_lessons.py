"""Self-improving lessons memory: keyword retrieval, dedup, prompt formatting — all LLM-free.

The distiller (`distill_lesson`) needs a model, so these tests seed lessons via the internal
`_store` helper and exercise the deterministic retrieval/formatting halves against a temp DB.
"""

from kompass.memory import lessons

REFUND_LESSON = (
    "When a customer reports a damaged item, verify the delivery date is within the "
    "return window before drafting a refund."
)
SHIPPING_LESSON = (
    "Escalate to a supervisor before promising a shipping-date change on a dispatched order."
)


def test_relevant_lessons_matches_by_keyword_and_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(lessons, "DB", tmp_path / "lessons.db")
    lessons._store(REFUND_LESSON, "refund damage return-window eligibility")
    lessons._store(SHIPPING_LESSON, "shipping dispatch escalation")

    hits = lessons.relevant_lessons("damaged item refund")

    assert hits == [REFUND_LESSON]  # only the refund lesson shares tokens with the query


def test_near_duplicate_lesson_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(lessons, "DB", tmp_path / "lessons.db")
    assert lessons._store(
        "Always verify the delivery date is within the 30-day return window before a refund.",
        "refund return-window",
    ) is True
    # Same rule, trivially reworded/repunctuated — caught as a near-duplicate and dropped.
    assert lessons._store(
        "Always verify the delivery date is within the 30 day return window before a refund!",
        "refund window",
    ) is False

    conn = lessons._db()
    (count,) = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()
    conn.close()
    assert count == 1


def test_lessons_block_formats_and_empties(tmp_path, monkeypatch):
    monkeypatch.setattr(lessons, "DB", tmp_path / "lessons.db")
    assert lessons.lessons_block("anything") == ""  # nothing stored yet

    lessons._store(
        "Confirm the order is delivered before offering a replacement.",
        "replacement delivery order",
    )

    block = lessons.lessons_block("replacement for delivered order")
    assert block.startswith("Lessons from past resolutions:\n- ")
    assert "replacement" in block

    assert lessons.lessons_block("weather forecast tomorrow") == ""  # no overlap -> empty
