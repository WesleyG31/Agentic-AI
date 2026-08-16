# Enterprise runtime operations

This document distinguishes implemented local behavior from deployment integrations that need
external infrastructure. It does not claim the repository is production-ready by itself.

## Request and trust flow

The application boundary creates `RuntimeContext` from verified facts: time, timezone, locale,
tenant, subject, roles/scopes, request ID, and correlation ID. The local API adapter accepts
tenant/user request fields only while `KOMPASS_AUTH_MODE=local`. OIDC mode deliberately returns
503 until a deployment adapter supplies already-verified claims.
The proactive ticket webhook is disabled until `KOMPASS_TRIGGER_BEARER_TOKEN` is explicitly set;
the fixed token is a local adapter, not a substitute for a production gateway/OIDC verifier.

Direct and indirect content is classified by origin. Untrusted content is presented to the model
as delimited data and cannot create permissions, policy, or procedural memory. Deterministic
authorization checks principal scopes, requested tenant, environment, and the agent's declared
tool capabilities. Human approval does not replace authorization; write actions are checked again
after approval.

## Protocols

MCP uses the official Python SDK 2.x. Local mode spawns three stdio servers with an explicit
environment; remote mode uses Streamable HTTP, deadlines, bearer credentials, resource reads,
per-tool scopes, and structured audit events. The included fixed-token verifier exists only for
local contract tests. Production must validate OAuth/OIDC access tokens against the configured
issuer/resource and use TLS.

A2A uses the official SDK and specification 1.0 data types and route factories. It advertises
JSON-RPC and HTTP+JSON interfaces, emits working/artifact/terminal task events, supports streaming
and cancellation, limits concurrency, bounds task duration, and isolates task keys by tenant/user.
The default A2A task store is process-local and therefore not a distributed durable store.

## Persistence and execution semantics

`kompass.persistence.checkpoint_saver` owns backend selection:

- No PostgreSQL DSN: async SQLite, appropriate for one local process and tests.
- `KOMPASS_CHECKPOINT_POSTGRES_DSN` set: official `AsyncPostgresSaver`; `setup()` is invoked.

The PostgreSQL package is optional so unit tests stay small. Install it only in the deployment
image:

```bash
python -m pip install -r requirements-production.txt
```

`SQLiteExecutionStore` provides a local reference implementation for payload-bound idempotency,
atomic claims, cancellation requests, bounded retry disposition, and tenant-scoped failed/dead-
letter inspection. It is not a distributed work queue. A multi-worker deployment must implement
the same contract with transactional leases, stale-lease recovery, bounded ingress/queue capacity,
and operational dead-letter handling. Agent, A2A, MCP, research, and action timeouts/concurrency
limits are separately configured.

The API also applies process-local admission control using `KOMPASS_EXECUTION_MAX_CONCURRENCY`,
`KOMPASS_EXECUTION_QUEUE_CAPACITY`, and `KOMPASS_EXECUTION_DEADLINE_SECONDS`; overload returns 429,
and an execution deadline returns 504 (or a terminal SSE failure event after streaming begins).

Checkpoint and memory contents may contain customer data. Production databases need encryption at
rest, TLS, least-privilege service identities, retention/deletion jobs, tested backup/restore, and
tenant enforcement such as RLS. Application-level hashed thread keys are defense in depth, not a
replacement for database policy.

## Actions

The action executor follows:

```text
authorize -> validate precondition -> require approval -> claim idempotency key
          -> execute with deadline/bounded retry -> verify business state
          -> complete receipt | compensate and record failure
```

The ACME adapter verifies refunds/ticket state in SQLite and supports compensation for its local
fixture. Real tools must propagate the idempotency key to the external system; otherwise a timeout
after a remote commit can still create an ambiguous duplicate. Reconciliation is required for
ambiguous outcomes.

## Memory

Working/session memory is the LangGraph checkpoint. Long-lived episodic/semantic memory uses one
SQLite schema with tenant, user, provenance, trust, creation time, optional expiry, confidence, and
type. Deletion and filtered retrieval are deterministic. Distilled procedural lessons are stored as
unapproved candidates; `memory:approve` is required before they can enter a future prompt. Memory is
never consulted for authorization.

## Observability

Security decisions use the structured `AuditSink`; operational measurements use a vendor-neutral
`TelemetrySink`. Both carry request/correlation IDs and tenant context. The telemetry tenant value is
an opaque partition hash. Sensitive attribute names are redacted. Langfuse remains optional and raw
prompt/result export defaults off (`LANGFUSE_CAPTURE_CONTENT=false`), producing a content hash/type/
size instead.

Recommended counters/histograms:

- task and end-state success; hallucinated-success count;
- policy denials and unauthorized tool attempts;
- tool/A2A/MCP errors, latency, timeout, cancellation, retry, and dead-letter rates;
- human approval requested/approved/rejected rate and latency;
- agent steps, research queries/sources, tokens, and configured model cost;
- tenant-isolation violations and memory-write denials.

Configure exporter retention, access control, redaction, sampling, and incident alerts before
enabling raw content capture.

## Release and promotion

`agent_release.json` pins workflow, tool-schema, policy, protocol, and evaluation-dataset identity.
`python -m kompass.release` adds registered prompt fingerprints and configured model names, verifies
the dataset hash, and prints a deterministic release ID. A dataset change fails closed until reviewed
and deliberately recorded.

Promotion path:

1. Development: lint, unit tests, deterministic integration tests, offline eval.
2. Security evaluation: trust/auth/tenant/action/red-team suites and dependency review.
3. Staging or shadow: real identity, PostgreSQL, protocol interoperability, telemetry, load/failure.
4. Canary: bounded tenants/traffic, alerting, action reconciliation, rollback rehearsed.
5. Production: approved release ID and immutable configuration.
6. Rollback: restore the prior release identity; preserve receipts/checkpoints and reconcile in-flight
   side effects rather than replaying them blindly.

Full model/judge evaluations are opt-in because they cost money and can vary. Mandatory CI runs only
deterministic tests and `python -m evals.offline --ci`.
