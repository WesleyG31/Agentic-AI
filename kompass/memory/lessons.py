"""Self-improving memory: distilled operating lessons that carry across conversations.

After a conversation resolves, one generalizable operating lesson is distilled from it
("when a customer reports damage, verify the delivery date is within the return window
before drafting a refund") and stored. On future runs the most relevant lessons are
injected into the system prompt, so the agent's judgement improves with use — this is
retrieval-over-lessons feeding few-shot guidance, the "self-improving" capability.

Retrieval is deliberately embedding-free: keyword/tag overlap against the stored lessons
keeps it deterministic and free. The distiller and the middleware that wires both halves
into the agent (LessonsMiddleware) live here too.
"""

import re
import sqlite3
from datetime import date

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from kompass.config import ROOT
from kompass.models.router import pick

DB = ROOT / "kompass_lessons.db"

# Distillation runs only after one of these (gated, side-effecting) tools resolves — that
# is where a reusable operating lesson is worth the model call.
_ACTION_TOOLS = {"create_refund", "update_ticket"}

# A candidate whose token-set Jaccard similarity to an existing lesson meets this threshold
# is treated as a near-duplicate and dropped, so the store can't fill with reworded repeats.
_DUPLICATE_SIMILARITY = 0.8


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS lessons"
        " (id INTEGER PRIMARY KEY, lesson TEXT NOT NULL, tags TEXT NOT NULL,"
        " created_at TEXT NOT NULL)"
    )
    return conn


def _tokens(text: str) -> set[str]:
    """Content tokens for overlap scoring: lowercased words of 3+ chars (drops stopword noise)."""
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= 3}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Set-overlap ratio in [0, 1]; 0 when either side is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class Lesson(BaseModel):
    """One generalizable operating lesson distilled from a resolved conversation."""

    worth_keeping: bool = Field(
        description="the conversation taught a reusable rule worth remembering for future cases"
    )
    lesson: str = Field(
        description="one or two sentences: a general operating rule, NOT facts about this case"
    )
    tags: str = Field(description="3-6 lowercase space-separated keywords for retrieval")


DISTILL_PROMPT = """A customer-support conversation has just been resolved. Extract ONE
generalizable operating lesson a support agent should carry into FUTURE, unrelated cases:
a reusable rule of thumb, not facts about this specific case.

Good:  "When a customer reports a damaged item, verify the delivery date is within the
        30-day return window before drafting a refund."
Bad:   "Order 4471 was refunded EUR 80 for a damaged blender."  (case-specific, useless later)

If nothing reusable was learned (small talk, a trivial lookup), set worth_keeping to false.
tags: 3-6 lowercase space-separated keywords for retrieval (e.g. "refund damage return-window").

Conversation:
{conversation}"""


def _transcript(conversation: list[tuple[str, str]] | str) -> str:
    """Render a role/content conversation into a plain transcript; pass strings through."""
    if isinstance(conversation, str):
        return conversation
    return "\n".join(f"{role}: {content}" for role, content in conversation)


def distill_lesson(conversation: list[tuple[str, str]] | str) -> str | None:
    """Distill one reusable operating lesson from a resolved conversation and store it.

    `conversation` is either a role/content transcript or a pre-rendered string. Returns the
    lesson text when the fast-tier model judged it worth keeping (persisting it, unless a
    near-duplicate is already stored), or None when nothing generalizable was learned.
    """
    result: Lesson = (
        pick("fast")
        .with_structured_output(Lesson)
        .invoke(DISTILL_PROMPT.format(conversation=_transcript(conversation)))
    )
    if not result.worth_keeping or not result.lesson.strip():
        return None
    _store(result.lesson.strip(), result.tags.strip())
    return result.lesson.strip()


def _store(lesson: str, tags: str) -> bool:
    """Persist a lesson unless it near-duplicates one already stored. True if written."""
    conn = _db()
    candidate = _tokens(lesson)
    existing = conn.execute("SELECT lesson FROM lessons").fetchall()
    if any(_jaccard(candidate, _tokens(row[0])) >= _DUPLICATE_SIMILARITY for row in existing):
        conn.close()
        return False
    conn.execute(
        "INSERT INTO lessons (lesson, tags, created_at) VALUES (?, ?, ?)",
        (lesson, tags, date.today().isoformat()),
    )
    conn.commit()
    conn.close()
    return True


def relevant_lessons(query: str, k: int = 3) -> list[str]:
    """Top-k stored lessons by keyword/tag overlap with `query` — embedding-free and free.

    Scores each lesson by how many query tokens it shares (across its text and its tags);
    lessons with no overlap are dropped, and ties break toward the most recent.
    """
    q = _tokens(query)
    if not q:
        return []
    conn = _db()
    rows = conn.execute("SELECT lesson, tags FROM lessons ORDER BY id DESC").fetchall()
    conn.close()
    scored = [(len(q & _tokens(f"{lesson} {tags}")), lesson) for lesson, tags in rows]
    ranked = sorted((s for s in scored if s[0] > 0), key=lambda s: s[0], reverse=True)
    return [lesson for _, lesson in ranked[:k]]


def lessons_block(query: str) -> str:
    """Relevant lessons formatted as a system-prompt insert, or "" when none apply."""
    lessons = relevant_lessons(query)
    if not lessons:
        return ""
    return "Lessons from past resolutions:\n" + "\n".join(f"- {lesson}" for lesson in lessons)


def _role(message) -> str:
    """Map a message type to a transcript role for the distiller."""
    if isinstance(message, HumanMessage):
        return "customer"
    if isinstance(message, ToolMessage):
        return "tool"
    return "agent"


class LessonsMiddleware(AgentMiddleware):
    """Prime a run with past lessons, and distill a new one once the run resolves.

    `before_model` (first model turn only) injects the lessons most relevant to the latest
    user message as a SystemMessage, so prior resolutions guide THIS run. `after_model`,
    when the model has produced a final answer to a run that actually did work (tool
    evidence exists), distills and persists one lesson — a fire-and-forget side effect that
    never alters control flow, so it cannot interfere with the safety/plan/critic chain.
    """

    def before_model(self, state, runtime):
        if any(isinstance(m, AIMessage) for m in state["messages"]):
            return None  # only the first model turn primes the run with lessons
        users = [m for m in state["messages"] if isinstance(m, HumanMessage)]
        block = lessons_block(str(users[-1].content)) if users else ""
        if not block:
            return None
        return {"messages": [SystemMessage(block)]}

    def after_model(self, state, runtime):
        messages = state["messages"]
        if getattr(messages[-1], "tool_calls", None):
            return None  # not a final answer — tools are about to run
        # Only distill after an ACTION resolves — that is where a reusable operating lesson
        # lives. Read-only lookups (the common case) skip it, keeping the distill call off
        # the response hot path so latency/cost stay low.
        acted = any(
            isinstance(m, ToolMessage) and getattr(m, "name", None) in _ACTION_TOOLS
            for m in messages
        )
        if not acted:
            return None
        conversation = [
            (_role(m), str(m.content))
            for m in messages
            if str(m.content).strip() and not getattr(m, "tool_calls", None)
        ]
        distill_lesson(conversation)  # fire-and-forget: keep a lesson if one is worth keeping
        return None
