"""Long-term memory tools: save and recall across calls, case-insensitive user key."""

from kompass.memory import store


def test_save_and_recall(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", tmp_path / "mem.db")
    store.save_memory.invoke({"user": "Lena.Fischer@web.de", "fact": "prefers email contact"})
    recalled = store.recall_memories.invoke({"user": "lena.fischer@web.de"})
    assert "prefers email contact" in recalled
    assert "No stored memories" in store.recall_memories.invoke({"user": "nobody@web.de"})
