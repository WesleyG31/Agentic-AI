# Enterprise Agent Runtime implementation ledger

Status vocabulary is exact: `DONE`, `PARTIAL`, `BLOCKED_MANUAL`, `DEFERRED_WITH_REASON`.
`DONE` means implementation and deterministic tests exist. `PARTIAL` identifies a concrete boundary,
not aspirational scaffolding.

Baseline: clean `main` at `deb0760`; implementation branch
`feat/enterprise-agent-runtime`. Baseline lint passed and the initial focused 26-test set passed.

| Area | Status | Evidence and boundary |
|---|---:|---|
| Runtime context and injectable clock | DONE | `kompass/runtime.py`; request time/locale/identity/tenant/correlation propagation and temporal tests |
| Trust boundary / indirect injection | DONE | origin/trust/provenance envelopes, deterministic memory/policy restrictions, audit events, adversarial tests |
| Identity and authorization | PARTIAL | claims adapter and deny-by-default policy are implemented; real OIDC token verification is `BLOCKED_MANUAL` and OIDC API mode fails closed |
| Multi-tenancy | PARTIAL | memory, lessons, cache, checkpoints, A2A tasks, receipts, execution failures are scoped; synthetic ACME DB is a single-tenant fixture and production RLS is manual |
| Transactional actions | DONE | authorization, approval, idempotency, timeout/retry, verification, compensation, receipt/audit and deterministic failure-path tests |
| A2A modernization | PARTIAL | official SDK/spec 1.0 lifecycle, streaming, cancellation, auth hook and isolation tests; distributed task persistence/TLS/OIDC are external |
| MCP enterprise path | PARTIAL | official SDK 2.x stdio/Streamable HTTP, tools/resources, auth/scope/deadline/audit tests; production OAuth/TLS is external |
| Memory governance | DONE | tenant/user, type, provenance, trust, TTL, confidence, deletion; procedural candidates quarantined pending authorized review |
| Durable/distributed execution | PARTIAL | durable local checkpoints/execution records plus official PostgreSQL checkpoint adapter; distributed queue/leases/backpressure require platform integration |
| Agentic research | DONE | bounded plan/gather/normalize/dedupe/disagreement/evidence workflow with fixture failure/deadline tests |
| Evaluation platform V2 | DONE | result schema, observable trajectory, deterministic state predicates, safety/tenant/action metrics and offline CI gate; live judge remains opt-in |
| Observability | PARTIAL | vendor-neutral audit/metric ports and optional Langfuse with content-off default; exporter/alerts/retention are external |
| Release governance | DONE | versioned descriptor, prompt/model/tool/policy/protocol/dataset identity, deterministic release ID and documented promotion/rollback |
| Experiments vs core | DONE | README classifies debate/CAG/GraphRAG/multimodal/sandbox/framework spikes; production assembly does not import them |
| Threat model | DONE | concrete threat/mitigation/test/residual-risk register and OWASP Agentic Top 10 mapping |
| README and configuration | DONE | architecture, core/experimental split, honest capability matrix, operational boundaries, blank-secret `.env.example` |
| External identity and infrastructure | BLOCKED_MANUAL | OIDC provider/gateway, TLS/DNS, secrets, production PostgreSQL/RLS/backup, distributed task/execution persistence, telemetry backend |
| Distributed queue implementation | DEFERRED_WITH_REASON | no queue framework added: workload/SLO/platform requirements are unknown, and SQLite reference plus bounded protocol/action paths cover deterministic local development |

## Phase completion notes

- Existing LangGraph, HITL, golden-set judge, red-team, saga, and Langfuse work was preserved.
- Custom HMAC/hand-written A2A transport was replaced by the official SDK rather than maintained in
  parallel. The former `langchain-mcp-adapters` dependency was removed because its MCP-major pin was
  incompatible with the official 2.x SDK path.
- Live LLM evaluation, public-network tests, production migrations, and deployments are intentionally
  not part of deterministic development validation.
