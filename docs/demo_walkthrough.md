# Kompass — Demo Walkthrough (Screen-Recording Script & Guided Tour)

This is a 3–5 minute screen recording that shows Kompass doing the one thing most "agent"
demos skip: it **resolves** a request end to end, and it **stops for a human** before it
moves any money. It also doubles as a reader's guided tour — every scene lists the exact
command to run, what appears on screen, and the voiceover line I'd say over it.

The star of the recording is the **approve / edit / reject** card in Journey B. Everything
before it is setup; everything after it is proof the action actually landed.

**Prerequisites:** `OPENAI_API_KEY` set in `.env`, and dependencies installed
(`make install`). On Windows without `make`, run the `python -m …` command shown under each
target (see the README Quickstart). Total runtime ≈ 4:30.

---

## Scene 1 — Intro (00:00)

**Action:** Repo open in the editor, README visible (or a plain title card).

**On screen:** The Kompass README header and the business-value table — 97% resolved,
0 unsafe actions, n=35.

**Voiceover:**
- "This is Kompass — an agentic Support & Operations assistant built on LangGraph v1."
- "Most agent demos are retrieve-generate-done. This one resolves the request and acts on
  it, with a human approving anything risky. Let me show you."

---

## Scene 2 — Seed the reproducible corpus (00:20)

**Action:**
```bash
make seed        # Windows: python -m kompass.scripts.seed
```

**On screen:** A few lines of progress as the script writes the synthetic ACME operational
database — employees, orders, tickets, and two historical refunds — to `corpus/acme.db`, and
builds the local Chroma vector index from the policy and FAQ documents under `corpus/`.
Finishes in a few seconds.

**Voiceover:**
- "Everything runs on a synthetic ACME dataset — no proprietary data, fully reproducible."
- "One command seeds the SQLite operational DB and the local vector index. No Docker needed
  for the demo."

---

## Scene 3 — Journey B in the terminal: the durable HITL refund (00:45)

**Action:**
```bash
make demo        # Windows: python -m kompass.scripts.demo
```

**On screen:** The script prints the customer's message, the agent works, and then the run
**pauses** on an approval card:

```
user> Hi, I'm Lena Fischer (lena.fischer@web.de). My order 4471 arrived
damaged - the microphone housing is cracked and the monitor arm box was
crushed. I reported it in ticket 88012. Please refund the order and update
my ticket.

========================================================================
APPROVAL REQUIRED  (approve / edit / reject)
  tool: create_refund
  order_id: 4471
  amount_eur: 189.99
  reason: Damaged on arrival — cracked microphone housing, crushed
          monitor-arm box (ticket 88012)
========================================================================
reviewer> approve
```

The `reason` text is model-generated, so wording varies. A second card for `update_ticket`
on ticket 88012 follows (that tool allows **approve/reject only** — no silent edits to an
audit record). After approval, the agent prints its confirmation, then the built-in check:

```
========================================================================
VERIFY  refund row: [{'id': ..., 'amount_eur': 189.99, 'status': 'completed', 'approved_by': ...}]
VERIFY  ticket 88012 status: resolved
========================================================================
```

**Voiceover:**
- "The agent verifies order 4471 and the refund policy over MCP tools — it never trusts the
  customer's claim blindly — then drafts a 189.99-euro refund."
- "Here is the whole thesis: it does not just execute. The run halts at a durable checkpoint
  and asks a human to approve, edit, or reject."
- "I approve, the run resumes from the exact checkpoint, and the refund and ticket update
  actually land in the database — see the VERIFY lines at the bottom."

---

## Scene 4 — Launch the API and the chat UI (01:45)

**Action:** (two terminals)
```bash
make api         # Windows: python -m uvicorn kompass.api.app:app --reload --port 8000
make ui          # Windows: python -m streamlit run ui/app.py
```

**On screen:** FastAPI boots on `:8000` (`POST /chat`, `POST /resume`, `GET /runs/{id}`);
the Streamlit chat opens in the browser.

**Voiceover:**
- "Same graph, real surfaces: a FastAPI backend and a Streamlit chat with citations and the
  approval card."

---

## Scene 5 — Journey A in the UI: a cited answer, zero human touch (02:05)

**Action:** In the Streamlit chat, send:

> Hi, I'm Jonas Weber (jonas.weber@acme.de). How many vacation days do I have left this year,
> and can I carry them over?

**On screen:** A streamed answer with inline citations, e.g. — *"You have 17 vacation days
left (28 total minus 11 used). Up to 5 unused days carry over into next year and must be used
by 31 March, or they expire [Vacation and Annual Leave Policy — Carry-Over Rule]."*

**Voiceover:**
- "This is a hybrid question — one number from the database, one rule from a policy document."
- "The retrieval router runs NL2SQL for the balance and RAG for the carry-over rule, and the
  answer cites its sources. No side effect, no human — pure read path."

---

## Scene 6 — Journey B in the UI: the approve/edit/reject card is the star (02:40)

**Action:** In the chat, send:

> I'm Lena Fischer (lena.fischer@web.de). My order 4471 arrived damaged — please refund it
> and update ticket 88012.

**On screen:** The agent streams its verification, then renders the **approve / edit /
reject** card for `create_refund` (order 4471, €189.99). Click **Edit**, adjust the amount or
add a goodwill note, then **Approve** — the chat confirms the refund and the ticket update.

**Voiceover:**
- "Same refund, now through the UI. This card is the point of the whole project."
- "I can approve as drafted, reject it, or edit the arguments — adjust the amount, add a
  goodwill note — and only then does it execute."
- "And because the state is checkpointed, this pause survives a restart. A reviewer could
  approve this hours later, from a different process, and it resumes cleanly."

---

## Scene 7 — The evidence: the eval table (03:55)

**Action:**
```bash
make evals       # Windows: python -m evals.run   (optional live run; a few minutes + API cost)
```
…or simply scroll to the table in the README.

**On screen:** The business-value table — resolution **97% vs 11%** naïve-RAG baseline,
grounded **97%**, cited **100%**, **0** unsafe actions, ~**10s** and ~**$0.020** per case
(n=35).

**Voiceover:**
- "None of this is a vibe — it is measured. On a 35-item golden set with an LLM judge plus
  deterministic fact, citation, and side-effect checks, Kompass resolves 97% of cases versus
  11% for a naïve-RAG baseline, and zero rejected actions ever executed."
- "`make evals` regenerates this table from a live run."

---

## Scene 8 — Close (04:15)

**Voiceover:**
- "That's Kompass: it resolves and acts, it cites its evidence, and a human owns every risky
  action — end to end, and measured."
- "The full architecture and the framework decision are in the docs."

(Optional on-screen links: architecture, framework decision, the write-up below.)

---

## Recording tips

- **Redact secrets first.** Before you hit record, close or blur `.env` — never show
  `OPENAI_API_KEY` — and clear it from terminal scrollback.
- **Resolution.** Record at 1080p (1920×1080); bump the terminal font to ~16–18pt and browser
  zoom to ~110% so text stays legible after compression.
- **Keep it under 5 minutes.** If you are tight, run `make seed` off-camera and open on
  Journey B — the HITL card is the money shot, so give it room.
- **Two terminals, one browser.** Have `make api` and `make ui` already running before you
  start Journey A, so there is no dead air waiting for boot.
- **Do a warm-up run.** The first model call per session is the slowest; a throwaway query
  before recording keeps on-camera latency near the ~10s mean.
- **Let the pause breathe.** When the approval card appears, stop talking for a beat. The
  silence sells the fact that the agent actually stopped.

---

Related: [`05_architecture.md`](05_architecture.md) · [`03_framework_decision.md`](03_framework_decision.md) · [`blog_why_kompass.md`](blog_why_kompass.md)
