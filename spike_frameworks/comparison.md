# Framework Comparison Spike — LangGraph v1 vs. an Alternative

> **Status: planned (produced in a later slice).** This file exists so cross-links resolve.
> The spike re-implements the **Researcher** worker in a second framework and documents the
> trade-offs, giving the interview line: *"I built the same agent in two frameworks — here is
> the state / HITL / cost / DX trade-off."*

## Goal

Kompass's primary framework decision is **LangGraph v1** — see
[`../docs/03_framework_decision.md`](../docs/03_framework_decision.md). This spike does **not**
change that decision; it demonstrates judgment by rebuilding one bounded component
(the Researcher agent: retrieve → synthesize with mandatory citations) in an alternative and
comparing honestly.

## Candidate alternative

**PydanticAI** *or* **OpenAI Agents SDK** (pick one). Rationale for each is in the framework
decision doc.

## What this doc will contain (planned structure)

1. The identical task spec both implementations satisfy (same tools, same eval).
2. Side-by-side code for the Researcher in each framework.
3. A trade-off table across the axes that matter in production:

   | Axis | LangGraph v1 | Alternative |
   |---|---|---|
   | State / persistence | _tbd_ | _tbd_ |
   | HITL ergonomics | _tbd_ | _tbd_ |
   | Type-safety / validated outputs | _tbd_ | _tbd_ |
   | Multi-agent orchestration | _tbd_ | _tbd_ |
   | Streaming | _tbd_ | _tbd_ |
   | Cost / token overhead | _tbd_ | _tbd_ |
   | Developer experience | _tbd_ | _tbd_ |
   | Best fit | _tbd_ | _tbd_ |

4. Eval parity check: both implementations run against the same golden set; report the delta.
5. Conclusion: when you'd reach for each — and why LangGraph v1 remains the right primary for Kompass.

## Related
- [`../docs/03_framework_decision.md`](../docs/03_framework_decision.md) — the primary decision + sources.
- [`../docs/05_architecture.md`](../docs/05_architecture.md) — where the Researcher sits in the graph.
