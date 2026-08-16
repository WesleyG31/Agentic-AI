# Local governed runtime operations

Kompass has two reproducible modes. The default uses Ollama, SQLite checkpoints and a development
identity adapter. `docker compose up -d --build` runs the complete governed path with the API,
PostgreSQL 17 and Keycloak 26.7. Both modes use only local, open-source/free components.

## Identity and request boundary

`KOMPASS_AUTH_MODE=local` is explicit development behavior: request fields supply tenant/user data.
Compose selects `oidc`. The ingress then requires a bearer access token and performs:

- issuer discovery and exact discovery-issuer comparison;
- JWKS retrieval/caching and RS256 key selection by `kid`;
- cryptographic signature, configured issuer and audience validation;
- required `exp`, `iat`, `iss`, `aud` and `sub` claims with bounded clock skew;
- required tenant mapping and deterministic permission mapping;
- rejection of expired, manipulated, wrong-issuer/audience, missing-claim and unknown-key tokens.

The optional discovery/JWKS transport overrides solve Docker hostname routing only. They do not
change the expected issuer or audience. HTTP is accepted only outside `production`; the Compose
provider is deliberately local development mode. Request tenant/user fields cannot override token
claims. Protected responses carry no-store, nosniff, frame, referrer and CSP headers, while bodies
and typed fields have explicit size limits.

The imported realm contains `alice`/`tenant-a` and `bob`/`tenant-b` with different permissions.
Credentials are obvious local fixtures committed solely for the zero-cost test environment.

## Persistence and tenant isolation

`kompass.persistence.checkpoint_saver` selects async SQLite when no DSN is set, or the official
`AsyncPostgresSaver` when `KOMPASS_CHECKPOINT_POSTGRES_DSN` is present. The saver runs `setup()`;
integration tests compile and execute a real graph against PostgreSQL and prove tenant-derived
thread keys cannot collide.

`infra/postgres/001_runtime.sql` creates two application tables:

- `workflow_threads`: tenant/user ownership of opaque checkpoint thread IDs;
- `action_receipts`: payload-bound action state and reconciliation evidence.

Both tables have constraints, supporting indexes, `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL
SECURITY`, and default-deny tenant policies. The runtime connection uses `kompass_app`, a
non-superuser/non-owner role without `BYPASSRLS`; every transaction sets its trusted tenant context.
Tests prove own-tenant read/write, invisible cross-tenant reads, denied cross-tenant writes, failure
without tenant context, and deliberate admin visibility. The PostgreSQL admin account exists only
for local initialization/checkpointer migrations.

The ACME SQLite database is one synthetic company's business system. It is not presented as a
multi-tenant domain database. RLS protects the actual multi-tenant runtime metadata described above.

## Actions, idempotency and reconciliation

The action path is:

```text
authorize -> preconditions -> approval -> atomic payload-bound claim
          -> bounded attempt/deadline -> target effect -> state verification
          -> durable receipt | compensation/reconciliation evidence
```

An idempotency key is scoped by tenant and bound to the canonical action name/arguments hash.
In-memory, SQLite and PostgreSQL stores implement atomic claims. Concurrent workers therefore cannot
both acquire the same action. Reusing a key with a different payload fails closed.

The key is also injected into the target ticket/refund operation. The ACME database records it in a
unique `business_effects` transaction (and on refunds), so a timeout after commit cannot create a
second refund or append a ticket note twice. This target guarantee is independent of receipt state.

`python -m kompass.scripts.reconcile --tenant TENANT` inspects only stale `processing` receipts. It
detects already committed target effects, verifies them, safely replays missing effects with the
same target key, and writes a diagnostic final receipt. Running it repeatedly is a no-op after
convergence. No scheduler is required.

## Security and load validation

The deterministic security pack covers OIDC crypto/claims, authentication, authorization, tenant
isolation/RLS, direct endpoint access, invalid and oversized payloads, SQL injection/stacked SQL,
security headers, prompt injection, sandbox traversal/imports, sensitive error suppression,
idempotency concurrency and reconciliation. Real Keycloak/PostgreSQL tests are opt-in only when the
local endpoints are supplied, and `make test-integration` supplies them for Compose.

`python -m kompass.scripts.load_test` obtains a real Keycloak token and concurrently exercises
`/health` plus the authenticated PostgreSQL checkpoint read path. The committed bounded report at
`reports/load-baseline.json` contains completion/error counts, latency, throughput and duplicate
effect status.

## Evaluation and cost

`python -m evals.offline --ci` is deterministic and model-free. `make evals-live-baseline` runs five
representative RAG, SQL, multi-source, action and abstention cases against local Ollama
`lfm2.5:8b`, judges observable outcomes locally, and writes `evals/live_baseline.json`. The artifact
is reviewed and records failures rather than turning them into success via assistant prose.

Required development/test cost is 0 EUR:

- Python, PostgreSQL, Keycloak, Ollama, SQLite, Chroma 0.6 and the test/audit tools are local;
- Langfuse and OpenAI are disabled and unnecessary for mandatory validation;
- hosted-model evaluation remains an optional adapter, never a prerequisite.

## Actual local limitations

- Compose uses HTTP, fixture passwords and Keycloak `start-dev`; it must remain bound to localhost.
- Keycloak uses its embedded local database; deleting its Compose volume resets the imported realm.
- The app is one process and admission control is process-local.
- ACME domain rows are a single-organization fixture.
- The local 8B evaluation baseline is variable and currently passes 4/5 selected contracts; the
  multi-tool case was factually correct but omitted its required citation.
- The deprecated A2A protobuf warnings originate in the installed official SDK.
