# Kompass threat model

Last reviewed: 2026-08-16. Scope: the Kompass code in this repository and its synthetic ACME
adapters. This is an architectural review, not a penetration test, certification, or assertion of
absolute security.

## Assets and boundaries

Protected assets are identity/tenant context, authorization policy, tool credentials, customer and
business data, checkpoint/memory contents, approvals, action receipts, and audit evidence.

Trusted application inputs are deployed configuration, application code, verified identity claims,
deterministic policy decisions, and reviewer decisions received through an authenticated channel.
User text, documents, retrieval chunks, tool/MCP results, A2A responses, model output, and memory
derived from those sources remain untrusted data. A model can recommend an action; only deterministic
code can authorize or commit it.

## Threat register

| Threat / attack path | Implemented mitigation | Deterministic evidence | Residual risk |
|---|---|---|---|
| Direct prompt injection in a user turn | Safety middleware screens before tools; authorization and action checks remain outside prompts | `tests/test_safety.py`, `tests/test_trust_boundary.py` | Classifiers/patterns are incomplete; novel text may reach the model, but cannot independently grant scopes |
| Indirect injection in documents, web results, MCP/tool output | Origin/provenance envelope, untrusted-data instruction, policy/memory denial, length bound | `tests/test_trust_boundary.py`, `tests/test_mcp_v2.py` | A model may still reason badly about malicious evidence; retrieval corpus governance is deployment-specific |
| Tool misuse or excessive agency | Deny-by-default scopes, agent capability allowlist, read/write split, HITL, transaction policy, budgets/timeouts | `tests/test_authorization.py`, `tests/test_action_executor.py` | A correctly authorized but harmful request can be approved; business policy coverage must evolve |
| Privilege escalation through prompt, memory, or remote output | OIDC discovery/JWKS crypto validation; principal only from verified claims; request tenant/user cannot override claims | `tests/test_oidc.py`, `tests/integration/test_keycloak_oidc.py`, authorization/memory/A2A tests | Local identity mode trusts request fields and must remain development-only |
| Confused deputy across a user, agent, MCP, or A2A hop | Verified issuer/audience/tenant/scopes carried independently of natural language; capabilities minimized per specialist | OIDC, authorization, A2A and MCP suites | Fixed local A2A/MCP dev-token adapters are not interchangeable with the OIDC API path |
| Cross-tenant checkpoint, cache, memory, task, receipt, or failure access | Opaque tenant/user keys plus PostgreSQL forced RLS on receipt/thread ownership using a non-owner role | `tests/integration/test_postgres_runtime.py` and tenant/cache/memory/A2A suites | Synthetic ACME domain data is intentionally one organization, not tenant-partitioned |
| Insecure A2A communication, spoofed card, or remote-agent poisoning | Official SDK contract, bearer verifier hook, tenant task owner, bounded lifecycle, output trust envelope | `tests/test_a2a.py`, `tests/test_trust_boundary.py` | No TLS, service discovery trust, key rotation, or distributed task store is supplied locally |
| Spoofed or replayed proactive webhook | Endpoint is disabled without an explicit token; constant-time bearer comparison and service authorization | `tests/test_ingress_security.py` | Fixed token has no replay protection; production needs sender signatures/nonces or an authenticated gateway |
| MCP tool poisoning or unauthorized invocation | Official MCP SDK, explicit server config, advertised schemas, per-tool scopes, auth verifier, deadlines, output envelope | `tests/test_mcp_v2.py`, `tests/test_authorization.py` | Dynamic third-party MCP registries and schema/signature pinning are outside repository scope |
| Durable memory/context poisoning | Suspicious/secret/policy writes denied; tenant/provenance/trust metadata; procedural candidates require review | `tests/test_memory.py`, `tests/test_lessons.py` | A reviewer may approve a subtly harmful lesson; review UI and retention jobs are not implemented |
| Data leakage through prompts, tools, logs, or telemetry | Least privilege, bounded content, PII mask, sensitive-key redaction, raw trace content opt-in, hashed tenant telemetry | `tests/test_observability.py`, `tests/test_trust_boundary.py` | Model providers and configured telemetry backends have their own retention/access policies |
| Secret leakage | Empty example credentials, audit/metric redaction, tokens excluded from context/prompts, no `.env` commit | `tests/test_observability.py`, configuration review | Exception strings from third-party SDKs could include sensitive values; production log filters remain necessary |
| Unexpected code execution or sandbox escape | Production agent does not depend on experimental sandbox; sandbox uses AST allowlist, subprocess, timeout | `tests/test_sandbox.py`, import/dependency review | The demo sandbox is not a hardened container and must not process hostile code in production |
| Unbounded resource consumption | Agent token budget, bounded critic retry, research query/source/concurrency/deadline limits, A2A/MCP/action timeouts | `tests/test_cache_budget.py`, `tests/test_research_workflow.py`, `tests/test_a2a.py`, `tests/test_mcp_v2.py` | API-wide distributed rate limiting/backpressure requires an ingress/runtime platform |
| Duplicate or ambiguous side effects after retry/timeout | Atomic payload-bound receipt claim plus target-system unique key and transaction | action, reconciliation and PostgreSQL concurrency tests | A replacement target adapter must implement the same key contract |
| Partial/cascading failure | Bounded retry, verification/compensation, durable processing receipt and repeatable reconcile command | reconciliation, saga, action, persistence and research suites | Unknown action types fail reconciliation closed until a handler exists |
| Human-agent trust exploitation | Approval card contains concrete tool/arguments; edit/reject supported; environment verification ignores success prose | `tests/test_action_executor.py`, `tests/test_offline_evaluation.py` | The included UI is not a complete anti-coercion reviewer UX; reviewer identity assurance depends on ingress |
| Supply-chain compromise | Official protocol SDKs, constrained ranges, no dynamic tool loading, local `pip-audit`; vulnerable Chroma 1.x server line excluded | requirements, MCP/A2A contracts, zero-vulnerability audit | Container image provenance is not signed in this local reference repository |
| Rogue/misaligned agent behavior | Model lacks authorization authority; least-capability tools, HITL writes, budget, checkpoint/audit, verifier | authorization/action/security/eval tests | Semantic output can still be wrong; monitoring, canary limits, incident response, and kill switches are required |

## OWASP Agentic Top 10 mapping

The mapping uses the official [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
as a risk taxonomy, not a compliance claim.

| OWASP risk | Kompass controls | Coverage |
|---|---|---|
| ASI01 Agent Goal Hijack | direct/indirect screening, provenance envelopes, deterministic authorization | Partial; semantic attacks remain possible |
| ASI02 Tool Misuse & Exploitation | capability/scopes, HITL, action transaction layer, read-only specialist | Implemented local controls; production business policy required |
| ASI03 Identity & Privilege Abuse | real local OIDC verification, tenant/scopes, deny-by-default policy, RLS | Implemented and integration-tested locally |
| ASI04 Agentic Supply Chain Vulnerabilities | official SDKs, bounded versions, explicit tool registration, no dynamic loading | Partial; signed artifacts/SBOM scanning external |
| ASI05 Unexpected Code Execution | experimental sandbox excluded from core, AST/subprocess boundary | Core avoids arbitrary code; demo sandbox not hardened |
| ASI06 Memory & Context Poisoning | trust/provenance metadata, write policy, TTL/deletion, reviewed procedural memory | Implemented local controls |
| ASI07 Insecure Inter-Agent Communication | official A2A, auth hook, task ownership, trust-wrapped output, timeout/cancel | Partial until TLS/OIDC/distributed persistence |
| ASI08 Cascading Failures | budgets, deadlines, bounded retry, verification, compensation, dead letters | Implemented locally; distributed recovery external |
| ASI09 Human-Agent Trust Exploitation | explicit action review, editable/rejectable approval, state-based eval | Partial; production reviewer UX and identity external |
| ASI10 Rogue Agents | deterministic policy/effect boundary, least privilege, bounded steps, audit | Risk reduced, not eliminated |

## Local exposure boundary

Compose is a localhost development/test configuration: HTTP, fixture passwords, Keycloak
`start-dev`, local volumes and one app process. It is not safe to bind to a public interface. The
tests cover issuer/audience/expiry/tampering, two-tenant RLS, action reconciliation, dependency
audit and bounded load within that exact scope. Real business integrations would require their own
authorization, idempotency and retention rules; none are implied by the synthetic ACME adapter.
