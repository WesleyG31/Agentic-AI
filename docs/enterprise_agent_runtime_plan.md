# Agent runtime implementation ledger

Status vocabulary is exact: `DONE`, `PARTIAL`, `DEFERRED_WITH_REASON`. `DONE` means code and
reproducible tests exist for the stated local scope; it does not imply an internet-facing deployment.

Baseline commit: `efa1ade` on `feat/enterprise-agent-runtime`. This continuation closes the five
previously open local validation areas without cloud or paid services.

| Area | Status | Evidence and exact boundary |
|---|---:|---|
| Runtime context and trust boundary | DONE | request clock/identity/tenant/correlation, trust/provenance envelopes and adversarial tests |
| OIDC ingress | DONE | Keycloak 26.7 Compose realm; discovery/JWKS/signature/issuer/audience/expiry/claim verifier and real integration tests |
| Deterministic authorization | DONE | deny-by-default scopes, tenant equality and agent capability constraints independent of prompts |
| Runtime multi-tenancy | DONE | forced PostgreSQL RLS for receipts/thread ownership; own/cross/missing/admin integration tests; ACME domain fixture explicitly single organization |
| PostgreSQL checkpoints | DONE | PostgreSQL 17 Compose, migration, official `AsyncPostgresSaver.setup()`, real graph persistence and tenant-key test |
| Transactional actions | DONE | authorization, approval, atomic payload-bound claim, retry/deadline, verification, compensation and audit |
| Target idempotency | DONE | unique ACME business-effect key in the same transaction; exact replay, payload conflict and concurrent worker tests |
| Reconciliation | DONE | tenant CLI detects committed/missing effects, verifies or safely repairs, converges on repeated runs |
| Security validation | DONE | 72-case focused pack plus Keycloak/PostgreSQL integration and dependency audit |
| Load validation | DONE | reproducible 100-request local OIDC/PostgreSQL scenario; committed metrics report |
| Evaluation platform V2 | DONE | deterministic CI gate plus reviewed five-category local Ollama baseline with zero configured cost |
| MCP 2.x / A2A 1.0 | DONE | official SDK contracts, scoped auth hooks, lifecycle, timeouts and isolation within local scope |
| Governed memory and bounded research | DONE | tenant/user/provenance/TTL/review controls and bounded evidence workflow |
| Observability | DONE | vendor-neutral local audit/metrics and content-off optional Langfuse; no backend required |
| Release governance | DONE | release descriptor, dataset/prompt/tool/policy identity and deterministic release check |
| Chroma dependency mitigation | DONE | CVE-2026-45829 avoided by embedded-only Chroma 0.6.3 constraint; rebuilt index and zero-vulnerability audit |
| Distributed queue / multi-process leases | DEFERRED_WITH_REASON | current local single-process workload needs neither; bounded admission and durable receipts cover actual scope |

## Local architecture decisions

- Exactly three Compose services: app, PostgreSQL and Keycloak. Keycloak uses embedded storage so a
  second database service is unnecessary.
- PostgreSQL is used where transactions/RLS are required: action receipts and workflow ownership.
  SQLite remains the simple target fixture and optional no-Docker checkpoint/memory adapter.
- Tenant RLS uses the non-owner `kompass_app` role and `FORCE ROW LEVEL SECURITY`; checkpointer schema
  setup deliberately uses the local migration/admin connection because the official schema has no
  tenant column. Opaque tenant/user-derived thread IDs plus the RLS ownership registry protect that
  boundary.
- Reconciliation is an executable command, not a scheduler or queue.
- Mandatory development/testing never enables OpenAI, hosted Langfuse or another paid dependency.
