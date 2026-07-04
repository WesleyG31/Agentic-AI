# Interview Prep — Agentic AI

Study material that doubles as the reasoning spine of the [Kompass](../README.md) project.
Everything here is in English (the language you'll interview in for EU/DE roles) and grounded in
the theory docs under [`../docs/`](../docs/).

## Contents

- **[framework_PACTEDR.md](framework_PACTEDR.md)** — the **P-A-C-T-E-D-R** framework: a 7-step
  structure for answering any agentic-AI system-design / business-case question
  (Problem → Agent-or-not → Capabilities → Tech design → Evaluation → Deploy & cost → Risks).
- **[banco_preguntas.md](banco_preguntas.md)** — 45+ Q&A across fundamentals, retrieval & RAG
  alternatives, architecture, MCP/A2A, memory, HITL/durable execution, evaluation, guardrails,
  cost/latency, failure modes, and framework comparison.
- **[casos/](casos/)** — four fully worked cases (each applies PACTEDR end-to-end):
  1. [Company-wide knowledge assistant](casos/caso_01_knowledge_assistant.md) *(= Kompass core)*
  2. [Customer-support agent that resolves tickets](casos/caso_02_customer_support.md) *(DHL-flavored)*
  3. [Intelligent document / invoice processing](casos/caso_03_document_processing.md) *(HOPn-flavored)*
  4. [NL2SQL data-analyst agent](casos/caso_04_nl2sql_analyst.md) *(Police/SCOUT-flavored)*

## How to use it

1. Internalize the **PACTEDR** framework first — it's the scaffold for everything else.
2. Drill the **question bank** out loud; each answer is written in your voice, 3-5 sentences.
3. Rehearse the **cases** as if whiteboarding: state the KPI, justify agent-or-not, sketch the
   architecture, name the evals and guardrails, quantify the value.
4. Cross-reference the theory in [`../docs/`](../docs/) whenever a claim needs backing.
