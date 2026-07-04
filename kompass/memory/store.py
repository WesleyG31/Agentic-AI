"""Long-term memory: user-scoped facts that persist across conversation threads.

Short-term memory is the checkpointer (per-thread message history); this is the
other half — durable facts ("prefers email contact", "is a wholesale customer")
keyed by who the user says they are, available to any future thread.
"""

import sqlite3
from datetime import date

from langchain_core.tools import tool

from kompass.config import ROOT

DB = ROOT / "kompass_memory.db"


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memories"
        " (user TEXT NOT NULL, fact TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    return conn


@tool
def save_memory(user: str, fact: str) -> str:
    """Persist a durable fact about a user (a preference, recurring context, or
    standing instruction) so future conversations can use it. `user` is the
    person's email if known, otherwise their full name. Do not store secrets
    or sensitive personal data beyond what support work needs."""
    conn = _db()
    conn.execute(
        "INSERT INTO memories VALUES (?, ?, ?)",
        (user.lower(), fact, date.today().isoformat()),
    )
    conn.commit()
    conn.close()
    return f"Remembered about {user}: {fact}"


@tool
def recall_memories(user: str) -> str:
    """Fetch previously saved facts about a user. Call this when a user identifies
    themselves, before answering questions where their preferences could matter."""
    conn = _db()
    rows = conn.execute(
        "SELECT fact, created_at FROM memories WHERE user = ? ORDER BY created_at",
        (user.lower(),),
    ).fetchall()
    conn.close()
    if not rows:
        return f"No stored memories for {user}"
    return "\n".join(f"- {fact} (saved {when})" for fact, when in rows)
