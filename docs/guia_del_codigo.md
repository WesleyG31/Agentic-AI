# 🧭 Guía del código de Kompass (español)

> **Para qué sirve este documento.** Es el **mapa de navegación del código**: explica en
> español qué es Kompass, qué hace y qué resuelve, y —sobre todo— **dónde vive cada cosa**
> para que entiendas el repo rápido. Los demás documentos de [`docs/`](.) son teoría (en
> inglés); éste es la guía práctica de lectura.
>
> Convenciones: todos los enlaces son relativos a esta carpeta `docs/`, así que son clicables.
> Los nombres de funciones, clases y variables de entorno están tal cual aparecen en el código.

---

## 1. ¿Qué es Kompass?

**Kompass** es un **asistente *agentic* de soporte y operaciones** para una empresa ficticia,
**ACME GmbH**, construido sobre **LangGraph v1**. Está pensado como proyecto de portafolio de
referencia: universal (sirve para atención al cliente, IT helpdesk, RRHH, ops) y reproducible
(corpus sintético "ACME", demo de un comando, sin datos propietarios).

Su tesis en una línea: **no solo responde preguntas — resuelve y actúa de punta a punta**.
Planifica, elige la estrategia de recuperación adecuada por consulta, llama herramientas vía
**MCP**, redacta una acción, **se detiene para pedir aprobación humana solo cuando la acción es
riesgosa**, ejecuta y recuerda.

## 2. ¿Qué resuelve? (el valor)

El problema de fondo: la mayoría de los "agentes" son *"RAG con gabardina"* (recuperar →
generar → fin). Kompass ataca el caso de mayor ROI para empresas: **automatización de soporte
y operaciones internas**, resolviendo el ticket completo en vez de solo redactar una respuesta.

El **invariante de diseño** que lo hace seguro: **las lecturas son libres, las escrituras están
gateadas por un humano**. Todo efecto secundario (reembolsos, cierre de tickets) pasa por un
único punto (el *Action Agent*) y un checkpoint *human-in-the-loop* (HITL). "Cero acciones
inseguras" es una propiedad de la arquitectura, no una promesa del prompt.

Métricas medidas (golden set, n=35 — ver [`README.md`](../README.md) y [`evals/`](../evals)):

| Métrica | Baseline (RAG ingenuo) | Kompass |
|---|---|---|
| Tasa de resolución / deflection | 11% | **97%** |
| Correcto (juez LLM) | 57% | 97% |
| Fundamentado (grounded) | 14% | 97% |
| Disciplina de citas | 49% | 100% |
| Acciones inseguras (rechazada → ejecutada) | — | **0** |

## 3. ¿Qué hace? (capacidades por tiers)

Las capacidades están organizadas en niveles para poder defenderlas incrementalmente. Cada una
apunta a dónde vive en el código.

**Tier 1 — núcleo**
- Recuperación adaptativa (router por consulta: RAG híbrido, CAG, GraphRAG, NL2SQL) → [`kompass/retrieval/`](../kompass/retrieval)
- Orquestación / planificación (supervisor que rutea, corta bucles, respeta presupuestos) → [`kompass/graph/`](../kompass/graph)
- Multi-agente (worker *Researcher*) + modo single-agent → [`kompass/graph/workers.py`](../kompass/graph/workers.py)
- Uso de herramientas vía **MCP** (doc-search, sql, ticketing) → [`kompass/mcp_servers/`](../kompass/mcp_servers)
- Memoria: corto plazo (checkpointer por hilo) + largo plazo por usuario → [`kompass/memory/`](../kompass/memory)
- Reflexión / autocorrección (crítico de grounding, un reintento) → [`kompass/graph/critic.py`](../kompass/graph/critic.py)
- **Human-in-the-loop** declarativo, durable y reanudable → middleware HITL en [`kompass/graph/agent.py`](../kompass/graph/agent.py)
- Guardarraíles (citas, SQL de solo lectura, validación, anti-inyección, PII) → [`kompass/guardrails/`](../kompass/guardrails)
- Streaming + observabilidad (SSE + traza JSONL; Langfuse opcional) → [`kompass/api/app.py`](../kompass/api/app.py), [`kompass/obs.py`](../kompass/obs.py)
- Evaluación (golden set + juez LLM + baseline + gate de regresión en CI) → [`evals/`](../evals)
- Model routing (fast/balanced/reasoning) + presupuesto de tokens por run → [`kompass/models/router.py`](../kompass/models/router.py), [`kompass/graph/budget.py`](../kompass/graph/budget.py)
- Deploy/MLOps (Docker + CI + compose) → [`Dockerfile`](../Dockerfile), [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

**Tier 2 — avanzado**
- Protocolo **A2A** (Agent Card firmada + endpoint JSON-RPC) → [`kompass/a2a/`](../kompass/a2a)
- Plan-and-execute + replanificación (`TodoListMiddleware`, solo en modo multi) → [`kompass/graph/agent.py`](../kompass/graph/agent.py)
- Ejecución de código en sandbox (analista de datos) → [`kompass/sandbox/`](../kompass/sandbox)
- Autonomía proactiva / dirigida por eventos (webhook de tickets) → [`kompass/triggers/webhook.py`](../kompass/triggers/webhook.py)
- Bucle auto-mejorable (lecciones destiladas) → [`kompass/memory/lessons.py`](../kompass/memory/lessons.py)
- Simulador de usuario multi-turno (estilo τ-bench) → [`evals/user_simulator/`](../evals/user_simulator)
- Agente de seguridad dedicado + suite red-team → [`kompass/guardrails/safety.py`](../kompass/guardrails/safety.py), [`evals/red_team.py`](../evals/red_team.py)

**Tier 3 — stretch**
- Comparación de frameworks (Researcher en PydanticAI) → [`spike_frameworks/`](../spike_frameworks)
- Caché semántica de respuestas → [`kompass/models/cache.py`](../kompass/models/cache.py)
- Ingesta multimodal (factura imagen → campos validados) → [`kompass/ingest/multimodal.py`](../kompass/ingest/multimodal.py)
- Panel de debate (3 lentes + juez) → [`kompass/graph/debate.py`](../kompass/graph/debate.py)
- Saga / compensación (rollback de acciones multi-sistema) → [`kompass/graph/saga.py`](../kompass/graph/saga.py)

## 4. Cómo funciona una petición (el flujo en 60 segundos)

El corazón es [`kompass/graph/agent.py`](../kompass/graph/agent.py) → función **`build_agent()`**,
que arma un agente LangGraph v1 (`create_agent`) sobre las herramientas MCP con un **stack de
middleware** en este orden exacto:

```
petición (chat/REST/UI  |  webhook proactivo)
        │
        ▼
1. SafetyMiddleware ......... filtra prompt-injection ANTES de cualquier trabajo (before_model)
2. TokenBudgetMiddleware .... corta el run si se excede el presupuesto de tokens
   (2b. TodoListMiddleware) . plan-and-execute — SOLO en modo multi (se inserta en la posición 2)
3. GroundingCritic ......... revisa la respuesta final vs. evidencia; un reintento si no está fundamentada
4. LessonsMiddleware ....... inyecta lecciones pasadas al inicio; destila una nueva al resolver una acción
5. HumanInTheLoopMiddleware  pausa en herramientas de escritura (INTERRUPT_ON) → approve/edit/reject
        │
        ▼
   loop create_agent (modelo "balanced" = gpt-5.4) sobre tools:
     · lectura (search_docs, query_database, get_ticket, analyze, recall_memories) → corren libres
     · escritura (create_refund, update_ticket) → PAUSA en el gate HITL
        │
        ▼
   estado persistido en checkpointer SQLite → el run sobrevive reinicios y es reanudable por thread_id
```

Detalles clave:
- **`INTERRUPT_ON`** define qué se gatea: `create_refund` (approve/edit/reject) y `update_ticket`
  (approve/reject). El resto de tools no aparecen ahí → corren sin pausa.
- **Modos** (`KOMPASS_AGENT_MODE`): `single` = un agente con todas las tools; `multi` = el agente
  se vuelve **supervisor**: delega toda la investigación al worker `research` (solo lectura) y se
  queda únicamente con las tools de escritura de `ticketing` (detrás del gate HITL).
- Además del loop se le suman las tools `analyze`, `save_memory` y `recall_memories`.

## 5. Mapa del repositorio

```
kompass/
├── docs/            → teoría (inglés) + ESTA guía (guia_del_codigo.md)
├── entrevista/      → prep de entrevista en español (framework PACTEDR + banco + casos)
├── corpus/          → datos sintéticos ACME (reproducibles): faq/ policies/ (RAG) + sql/seed.sql (BD)
├── kompass/         → el paquete
│   ├── graph/       → supervisor + worker + crítico + presupuesto + debate + saga        [T1/T2/T3]
│   ├── retrieval/   → router + rag(híbrido) + cag + graphrag + nl2sql                     [T1]
│   ├── ingest/      → ingesta multimodal de facturas                                      [T3]
│   ├── mcp_servers/ → doc_search + sql + ticketing (MCP — capa vertical, agente↔tools)    [T1]
│   ├── a2a/         → Agent Card firmada + server/client (capa horizontal, agente↔agente) [T2]
│   ├── memory/      → store (largo plazo por usuario) + lessons (auto-mejorable)          [T1/T2]
│   ├── guardrails/  → safety (anti-inyección + PII)                                       [T1/T2]
│   ├── models/      → router (tier→modelo) + cache (caché semántica)                      [T1/T3]
│   ├── sandbox/     → executor (aislamiento) + analyst (tool del Data Analyst)            [T2]
│   ├── triggers/    → webhook (triaje proactivo de tickets)                               [T2]
│   ├── api/         → FastAPI: /chat, /chat/stream, /resume, /runs/{id}                   [T1]
│   ├── scripts/     → seed (siembra BD+índice) + demo (recorrido HITL)                    [—]
│   ├── config.py    → Settings tipada (todo lo ajustable vive aquí, desde .env)
│   └── obs.py       → traza local (runs.jsonl) + Langfuse opcional
├── spike_frameworks/→ Researcher reimplementado en PydanticAI + comparison.md             [T3]
├── evals/           → golden set + juez LLM + baseline + red_team + user_simulator        [T1/T2]
├── ui/              → chat Streamlit con citas + tarjeta de aprobación HITL               [T1]
├── tests/           → suite pytest (offline, sin API key)
├── Dockerfile · docker-compose.yml · Makefile · requirements*.txt · .env.example
└── *.db             → artefactos locales (ver §10)
```

## 6. Dónde vive cada cosa — mapa archivo por archivo

> **Este es el núcleo de la guía.** Una tabla por subsistema. Columnas: **archivo · qué hace ·
> símbolos/funciones clave · cómo se cablea**.

### 6.1 Orquestación — [`kompass/graph/`](../kompass/graph) + config

| Archivo | Qué hace | Símbolos clave | Cómo se cablea |
|---|---|---|---|
| [`graph/agent.py`](../kompass/graph/agent.py) | Ensambla el agente (LangGraph v1) sobre tools MCP con gate HITL durable; en modo multi es el supervisor | `build_agent()`, `mcp_client()`, `SYSTEM_PROMPT`, `INTERRUPT_ON`, `PLANNING_PROMPT` | Punto de entrada que consumen API, demo, UI, evals (pasando el checkpointer) |
| [`graph/workers.py`](../kompass/graph/workers.py) | Worker *Researcher* (subagente-como-herramienta), solo lectura | `research` (@tool), `_build_researcher()`, `READ_TOOLS` | En modo multi se cablea como tool del supervisor; también lo usan A2A y triggers |
| [`graph/critic.py`](../kompass/graph/critic.py) | Reflexión: revisa la respuesta final vs. evidencia y la reenvía una vez si hay claims sin soporte | `GroundingCritic`, `Review`, `MARKER='[critic]'` | Middleware en `build_agent`; usa el tier `fast` |
| [`graph/budget.py`](../kompass/graph/budget.py) | Backstop de costo: termina el run al superar el cap de tokens | `TokenBudgetMiddleware`, `BUDGET_DEFAULT=200_000` | Middleware en `build_agent` (`settings.token_budget`) |
| [`graph/debate.py`](../kompass/graph/debate.py) | Panel de 3 lentes independientes + juez, para decisiones borderline | `adjudicate()`, `tally()`, `LENSES`, `Opinion`, `Verdict` | Módulo autónomo (no lo importa el agente); lentes con `balanced`, juez con `reasoning` |
| [`graph/saga.py`](../kompass/graph/saga.py) | Acción multi-paso all-or-nothing con compensación (rollback en reversa) | `run_saga()`, `refund_saga()`, `Step`, `SagaResult` | Pura orquestación sin LLM; opera sobre la BD ACME |
| [`config.py`](../kompass/config.py) | Configuración tipada (pydantic-settings) desde `.env`; expone `settings` y `ROOT` | `settings`, `Settings`, `ROOT` | Importado transversalmente por todo el paquete (ver §9) |

### 6.2 Recuperación adaptativa — [`kompass/retrieval/`](../kompass/retrieval) + ingesta

| Archivo | Qué hace | Símbolos clave | Cómo se cablea |
|---|---|---|---|
| [`retrieval/router.py`](../kompass/retrieval/router.py) | Clasifica la consulta (sql/rag/graph/cag) con una llamada `fast` y despacha a la estrategia más barata | `retrieve()`, `Route`, `RetrievalResult`, `CLASSIFY` | **No lo importa el agente** (ver §14): es la entrada programática que usan evals/baseline |
| [`retrieval/rag.py`](../kompass/retrieval/rag.py) | RAG híbrido: denso (Chroma) + léxico (BM25) fusionados con RRF | `search()`, `Chunk`, `_index()`, `RRF_K=60`, `COLLECTION='acme_docs'` | Lo consumen el MCP `doc_search`, el router (ruta rag) y GraphRAG (grounding) |
| [`retrieval/cag.py`](../kompass/retrieval/cag.py) | CAG: mete todo el corpus en el prompt (para preguntas amplias/multi-doc) | `full_corpus()` (lru_cache) | Solo lo usa el router (ruta cag) |
| [`retrieval/graphrag.py`](../kompass/retrieval/graphrag.py) | Multi-hop sobre un grafo de conceptos cacheado en `corpus/graph.json` | `search()`, `build_graph()`, `_graph()`, `Triple` | Solo lo usa el router (ruta graph); usa `networkx` |
| [`retrieval/nl2sql.py`](../kompass/retrieval/nl2sql.py) | Recuperación estructurada: ejecuta **un SELECT de solo lectura** sobre la BD ACME (frontera de confianza) | `run_sql()`, `SCHEMA`, `ROW_CAP=50` | Lo usan el MCP `sql`, el router (ruta sql), el demo y `SCHEMA` se inyecta en varios prompts |
| [`ingest/multimodal.py`](../kompass/ingest/multimodal.py) | Factura (imagen) → `InvoiceExtract` validado con el modelo `balanced` con visión | `extract_invoice()`, `make_sample_invoice()`, `InvoiceExtract` | No está cableado como tool del agente; demo por `python -m` y test |

### 6.3 Tools vía MCP (capa vertical) — [`kompass/mcp_servers/`](../kompass/mcp_servers)

Tres servidores FastMCP que corren como **subprocesos stdio**, lanzados por `mcp_client()` en `agent.py`.

| Archivo | Qué hace | Herramientas expuestas | Cómo se cablea |
|---|---|---|---|
| [`mcp_servers/doc_search.py`](../kompass/mcp_servers/doc_search.py) | Búsqueda híbrida sobre políticas/FAQ (solo lectura) | `search_docs(query, k)` | Clave `doc_search`; envuelve `rag.search`; en `READ_TOOLS` |
| [`mcp_servers/sql.py`](../kompass/mcp_servers/sql.py) | Acceso SQL de solo lectura a la BD ACME | `get_schema()`, `query_database(sql)` | Clave `acme_sql`; envuelve `nl2sql.run_sql`; en `READ_TOOLS` |
| [`mcp_servers/ticketing.py`](../kompass/mcp_servers/ticketing.py) | Acciones con efecto: leer/actualizar tickets y crear reembolsos | `get_ticket()`, `create_refund()`, `update_ticket()` | Clave `ticketing`; `create_refund`/`update_ticket` están en `INTERRUPT_ON` (gate HITL) |

### 6.4 Interop A2A (capa horizontal) — [`kompass/a2a/`](../kompass/a2a)

| Archivo | Qué hace | Símbolos clave | Cómo se cablea |
|---|---|---|---|
| [`a2a/card.py`](../kompass/a2a/card.py) | Genera y firma la Agent Card pública (identidad A2A) | `agent_card()`, `sign()` (HMAC-SHA256), `verify()` | La usan `server.py` (publicar) y `client.py` (verificar); secreto = `settings.a2a_secret` |
| [`a2a/server.py`](../kompass/a2a/server.py) | Expone a Kompass como agente especialista invocable por otros agentes | `GET /.well-known/agent.json`, `POST /a2a` (JSON-RPC `tasks/send`) | App FastAPI standalone (puerto 8030); reusa el worker `research` (solo lectura → sin HITL) |
| [`a2a/client.py`](../kompass/a2a/client.py) | CLI que descubre el peer, verifica la firma y delega una tarea | `discover()`, `send_task()` | `python -m kompass.a2a.client "<pregunta>"` (con el server corriendo) |

### 6.5 Memoria — [`kompass/memory/`](../kompass/memory)

| Archivo | Qué hace | Símbolos clave | Cómo se cablea |
|---|---|---|---|
| [`memory/store.py`](../kompass/memory/store.py) | Memoria de largo plazo por usuario (hechos duraderos), cross-thread, en SQLite | `save_memory` (@tool), `recall_memories` (@tool), `kompass_memory.db` | Se registran como tools del agente en `build_agent` |
| [`memory/lessons.py`](../kompass/memory/lessons.py) | Auto-mejora: destila una lección al resolver una acción y reinyecta las relevantes | `LessonsMiddleware`, `distill_lesson()`, `relevant_lessons()`, `_ACTION_TOOLS`, `_DUPLICATE_SIMILARITY=0.8` | Middleware en `build_agent`; retrieval embedding-free (Jaccard); `kompass_lessons.db` |

*(La memoria de **corto plazo** no es un archivo: es el **checkpointer** por hilo, que guarda el historial de mensajes.)*

### 6.6 Seguridad / guardarraíles — [`kompass/guardrails/`](../kompass/guardrails)

| Archivo | Qué hace | Símbolos clave | Cómo se cablea |
|---|---|---|---|
| [`guardrails/safety.py`](../kompass/guardrails/safety.py) | Screening de prompt-injection (regex + clasificador `fast`) como gate de entrada; helper de redacción de PII | `SafetyMiddleware`, `screen_injection()`, `_pre_check()`, `Injection`, `redact_pii()` | `SafetyMiddleware` va **primero** en `build_agent`; es el sujeto de `evals/red_team.py`. `redact_pii` **no** está cableado al grafo |

### 6.7 Modelos, caché y observabilidad — [`kompass/models/`](../kompass/models) + [`obs.py`](../kompass/obs.py)

| Archivo | Qué hace | Símbolos clave | Cómo se cablea |
|---|---|---|---|
| [`models/router.py`](../kompass/models/router.py) | Único lugar que mapea tier→modelo concreto (`init_chat_model`), con callbacks de traza | `pick(tier)` (lru_cache), `Tier` | Lo usa **todo** el que llama a un LLM (`pick('fast'/'balanced'/'reasoning')`) |
| [`models/cache.py`](../kompass/models/cache.py) | Caché semántica: si una paráfrasis ya fue respondida, devuelve la respuesta sin llamar al modelo | `lookup(threshold=0.2)`, `store()`, `clear()`, `COLLECTION='answer_cache'` | Lo usa `api/app.py` en `POST /chat` (solo para preguntas frescas → solo lectura) |
| [`obs.py`](../kompass/obs.py) | Observabilidad local: una línea JSON por llamada LLM en `runs.jsonl` | `TraceHandler`, `TRACE_FILE` | Se adjunta a cada modelo dentro de `models/router.pick`; Langfuse es el complemento opcional |

### 6.8 Sandbox / analista de datos — [`kompass/sandbox/`](../kompass/sandbox)

| Archivo | Qué hace | Símbolos clave | Cómo se cablea |
|---|---|---|---|
| [`sandbox/executor.py`](../kompass/sandbox/executor.py) | Sandbox para Python generado por el modelo: allowlist AST + subproceso `python -I` + timeout | `run_python()`, `_reject()`, `ALLOWED_IMPORTS`, `FORBIDDEN_NAMES` | Lo consume `analyst.py` |
| [`sandbox/analyst.py`](../kompass/sandbox/analyst.py) | Tool `analyze`: SELECT read-only + cómputo en Python sandboxed (promedios, distribuciones, what-if) | `analyze` (@tool) | Se registra como tool del agente en `build_agent`; read-only → **no** gateado por HITL |

### 6.9 Autonomía proactiva — [`kompass/triggers/`](../kompass/triggers)

| Archivo | Qué hace | Símbolos clave | Cómo se cablea |
|---|---|---|---|
| [`triggers/webhook.py`](../kompass/triggers/webhook.py) | App FastAPI: un ticket entrante se **tría de forma desatendida** con el Researcher read-only | `POST /webhook/ticket`, `TicketIn`, `Triage`, `ticket_webhook()` | Standalone (puerto 8040); preserva el invariante HITL (nada con efecto corre desatendido) |

### 6.10 Superficies de uso — API, UI y scripts

| Archivo | Qué hace | Símbolos clave | Cómo se cablea |
|---|---|---|---|
| [`api/app.py`](../kompass/api/app.py) | API FastAPI sobre el mismo grafo durable | `POST /chat`, `POST /chat/stream` (SSE), `POST /resume`, `GET /runs/{id}` | `make api`; construye el agente + checkpointer una vez al arrancar; usa la caché semántica |
| [`ui/app.py`](../ui/app.py) | Chat Streamlit con citas y tarjeta de aprobación (approve/edit/reject) | `render_approval_card()`, `apply_response()`, `resume()` | `make ui`; **habla con la API por HTTP** (no importa el agente) |
| [`scripts/seed.py`](../kompass/scripts/seed.py) | Construye la BD SQLite ACME y el índice vectorial Chroma desde `corpus/` | `build_db()`, `build_index()`, `chunk()` | `make seed` — requisito previo del demo |
| [`scripts/demo.py`](../kompass/scripts/demo.py) | Demo end-to-end del recorrido B (reembolso con aprobación) | `main()`, `show_interrupt()` | `make demo`; usa el pedido `4471` / ticket `88012` |

### 6.11 Evaluación + spike + tests

| Archivo | Qué hace | Símbolos clave | Cómo se cablea |
|---|---|---|---|
| [`evals/run.py`](../evals/run.py) | Harness: corre el golden set contra el agente y el baseline, juzga, agrega y **regenera la tabla del README** | `agent_episode()`, `score()`, `aggregate()`, `readme_table()` | `make evals`; gate CI: `--ci --min-score 0.75` |
| [`evals/judge.py`](../evals/judge.py) | LLM-as-judge (tier `reasoning`): correcto/fundamentado + notas | `judge()`, `Verdict` | Lo usan `run.py`, el user-simulator y el spike |
| [`evals/baseline.py`](../evals/baseline.py) | Baseline: RAG ingenuo de un disparo (denso top-4 + 1 llamada) | `answer()`, `_collection()` | El delta de la tabla del README es la brecha frente a esto |
| [`evals/red_team.py`](../evals/red_team.py) | Suite red-team: 15 ataques + 5 benignos → block-rate / false-positive-rate | `ATTACKS`, `BENIGN`, `evaluate()` | `python -m evals.red_team`; ejercita `screen_injection` |
| [`evals/user_simulator/`](../evals/user_simulator) | Usuario simulado (τ-bench): personas conversan turno a turno con el agente real | `UserSimulator`, `run_scenario()`, `goal_met()` | `python -m evals.user_simulator.run_sim` (requiere `KOMPASS_ACME_DB`) |
| [`spike_frameworks/`](../spike_frameworks) | Researcher reimplementado en PydanticAI + corrida de paridad + `comparison.md` | `researcher`, `run_parity`, `answer()` | Justifica la elección de LangGraph v1 (no la cambia) |
| [`tests/`](../tests) | Suite pytest **offline y sin API key** (config, safety, saga, debate, graphrag, lessons, sandbox, multimodal, cache/budget, memory, a2a, seed) | 12 archivos `test_*.py` | `make test` → `python -m pytest` |

### 6.12 Infraestructura y datos

| Archivo | Qué hace | Notas |
|---|---|---|
| [`Dockerfile`](../Dockerfile) | Imagen del API sobre `python:3.12-slim`, servida con uvicorn en 8000 | Solo dependencias runtime; hay que `seed` dentro del contenedor |
| [`docker-compose.yml`](../docker-compose.yml) | Infra opcional "producción": Postgres (checkpointer durable), Qdrant, Langfuse | El demo núcleo **no** la necesita (Chroma + SQLite local) |
| [`Makefile`](../Makefile) | Interfaz de un comando (ver §11) | En Windows sin `make`, correr los `python -m …` |
| [`requirements.txt`](../requirements.txt) / [`requirements-dev.txt`](../requirements-dev.txt) | Runtime / dev+eval | Postgres+Langfuse van comentados (opt-in) |
| [`.env.example`](../.env.example) | Plantilla de configuración por variables de entorno | Ver §9 |
| [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) | CI: lint+test en cada push/PR; evals on-demand (manual) | Usa Python 3.11 |
| [`corpus/`](../corpus) | Datos sintéticos ACME: `faq/`, `policies/` (RAG) + `sql/seed.sql` (BD) | Ver §10 |

## 7. Los dos ejes de interoperabilidad (MCP vs A2A)

Confundirlos es un error clásico. Kompass usa ambos deliberadamente:

| | **MCP** — Model Context Protocol | **A2A** — Agent-to-Agent |
|---|---|---|
| Eje | **Vertical**: agente ↔ sus herramientas | **Horizontal**: agente ↔ agente par |
| Pregunta que responde | ¿Cómo llama *un* agente a *sus* tools? | ¿Cómo colaboran agentes independientes? |
| En Kompass | `doc_search`, `sql`, `ticketing` ([`mcp_servers/`](../kompass/mcp_servers)) | delegar a un especialista externo ([`a2a/`](../kompass/a2a)) |

Metáfora: **MCP es el puerto USB-C de las tools de un agente; A2A es la llamada telefónica entre
dos agentes.** Profundización en [`05_architecture.md`](05_architecture.md) y [`06_advanced_patterns.md`](06_advanced_patterns.md).

## 8. Recorridos end-to-end (mapeados a archivos)

**A — Pregunta de conocimiento, sin humano** · *"¿Cuántos días de vacaciones me quedan y puedo pasarlos al año que viene?"*
El router reconoce una pregunta híbrida y dispara **NL2SQL** (saldo) + **RAG** (regla de arrastre)
en paralelo; el Researcher fusiona con citas; el crítico verifica grounding. Camino puro de lectura.
Archivos: [`retrieval/router.py`](../kompass/retrieval/router.py) → [`retrieval/nl2sql.py`](../kompass/retrieval/nl2sql.py) + [`retrieval/rag.py`](../kompass/retrieval/rag.py) → [`graph/critic.py`](../kompass/graph/critic.py).

**B — Acción con aprobación (el demo insignia)** · *"Reembolsa el pedido 4471 — llegó dañado."*
El agente verifica el pedido vía MCP, redacta `create_refund`, y **el gate HITL pausa** guardando
el estado; un revisor aprueba/edita/rechaza; al aprobar, el run **se reanuda desde el checkpoint** y
ejecuta el reembolso. Este es exactamente el recorrido que corre [`scripts/demo.py`](../kompass/scripts/demo.py)
(cliente Lena Fischer, pedido `4471`, ticket `88012`).
Archivos: [`graph/agent.py`](../kompass/graph/agent.py) (`INTERRUPT_ON`, HITL) → [`mcp_servers/ticketing.py`](../kompass/mcp_servers/ticketing.py).

**C — Resolución proactiva** · un ticket llega por **webhook**, sin que nadie lo pida.
Kompass lo clasifica, junta contexto con el Researcher read-only, deja un borrador y lo deja en
`pending` para un humano. Nada con efecto corre desatendido.
Archivos: [`triggers/webhook.py`](../kompass/triggers/webhook.py) → [`graph/workers.py`](../kompass/graph/workers.py).

## 9. Configuración y variables de entorno

Todo lo ajustable vive en [`config.py`](../kompass/config.py) (`Settings`) y se puebla desde `.env`
(plantilla en [`.env.example`](../.env.example)). Los modelos son strings `"proveedor:modelo"`
resueltos por `init_chat_model`, así que **cambiar de proveedor es editar `.env`, no código**.

| Campo `Settings` | Variable de entorno | Default | Para qué |
|---|---|---|---|
| `model_reasoning` | `KOMPASS_MODEL_REASONING` | `openai:gpt-5.5` | planificación/verificación dura (crítico, juez, panel de debate) |
| `model_balanced` | `KOMPASS_MODEL_BALANCED` | `openai:gpt-5.4` | drafting/síntesis (el loop del agente) |
| `model_fast` | `KOMPASS_MODEL_FAST` | `openai:gpt-5.4-nano` | clasificación/routing/pre-screen de seguridad |
| `agent_mode` | `KOMPASS_AGENT_MODE` | `single` | `single` (todas las tools) o `multi` (supervisor + worker) |
| `token_budget` | `KOMPASS_TOKEN_BUDGET` | `200000` | cap de tokens por run (backstop de costo) |
| `vector_backend` / `chroma_path` | `KOMPASS_VECTOR_BACKEND` / `KOMPASS_CHROMA_PATH` | `chroma` / `.chroma` | vector store local (o `qdrant`) |
| `acme_db` | `KOMPASS_ACME_DB` | `corpus/acme.db` | BD operativa ACME (SQLite) |
| `checkpointer` / `sqlite_checkpoint` | `KOMPASS_CHECKPOINTER` / `KOMPASS_SQLITE_CHECKPOINT` | `sqlite` / `kompass_checkpoints.db` | persistencia durable del HITL |
| `postgres_url` | `POSTGRES_URL` | `postgresql://kompass:kompass@localhost:5432/kompass` | checkpointer de producción |
| `langfuse_enabled` / `langfuse_host` | `LANGFUSE_ENABLED` / `LANGFUSE_HOST` | `false` / `http://localhost:3000` | observabilidad remota opcional |
| `api_host` / `api_port` | `KOMPASS_API_HOST` / `KOMPASS_API_PORT` | `0.0.0.0` / `8000` | superficie API |
| `a2a_secret` / `a2a_port` | `KOMPASS_A2A_SECRET` / `KOMPASS_A2A_PORT` | `dev-secret-change-me` / `8030` | firma HMAC y puerto del server A2A |
| `trigger_port` | `KOMPASS_TRIGGER_PORT` | `8040` | puerto del webhook de triggers |
| — (requerida) | `OPENAI_API_KEY` | — | credencial del proveedor de modelos |

**Tres apps standalone con puerto propio:** API `8000`, A2A `8030`, webhook `8040`.

## 10. Datos de demo (corpus + BD ACME)

- **No estructurado (RAG):** [`corpus/faq/`](../corpus/faq) y [`corpus/policies/`](../corpus/policies)
  (markdown). Contienen los umbrales de negocio canónicos: ventana de devolución **30 días**,
  reembolsos **> 500 €** requieren **aprobación de supervisor**, plazo 5–10 días hábiles.
- **Estructurado (SQL):** [`corpus/sql/seed.sql`](../corpus/sql/seed.sql) crea 5 tablas —
  `employees`, `orders`, `order_items`, `tickets`, `refunds`— con **6 / 12 / — / 8 / 2** filas.
  Dataset con "hoy" = **2026-07-04**.
- **Caso canónico del demo:** pedido **`4471`** (Lena Fischer, 189,99 €, entregado, dañado) + ticket
  **`88012`** (abierto, alta prioridad) **sin reembolso todavía** → el demo lo crea en vivo. Como es
  ≤ 500 €, no requiere supervisor (contrasta con el histórico 4461 de 608,99 € que sí lo requirió).
- Todo se materializa con **`make seed`** ([`scripts/seed.py`](../kompass/scripts/seed.py)).

**Artefactos locales (`*.db` + `.chroma`, en la raíz del repo):** `corpus/acme.db` (BD operativa),
`.chroma` (índice vectorial), `kompass_checkpoints.db` (checkpointer HITL), `kompass_memory.db`
(memoria por usuario), `kompass_lessons.db` (lecciones), `runs.jsonl` (traza). `make clean` los borra.

## 11. Cómo correrlo

```bash
python -m pip install -r requirements-dev.txt   # instala runtime + dev/eval
cp .env.example .env                             # añade tu OPENAI_API_KEY

make seed    # o: python -m kompass.scripts.seed   → BD SQLite + índice Chroma
make demo    # o: python -m kompass.scripts.demo   → recorrido HITL end-to-end (recorrido B)
make api     # o: python -m uvicorn kompass.api.app:app --reload --port 8000
make ui      # o: python -m streamlit run ui/app.py   (requiere la API en otra terminal)
make evals   # o: python -m evals.run                 → regenera la tabla de métricas del README
make test    # o: python -m pytest                    → suite offline, sin API key
```

> **Windows / PowerShell:** sin `make`, usa el comando `python -m …` que aparece a la derecha.
> Para scripts que imprimen emojis, exporta `$env:PYTHONIOENCODING='utf-8'` (evita el mojibake de cp1252).

## 12. Por dónde empezar a leer el código

Orden sugerido para entenderlo en una sentada:
1. [`config.py`](../kompass/config.py) — el contrato de configuración (qué es ajustable).
2. [`graph/agent.py`](../kompass/graph/agent.py) — `build_agent()`: el corazón (stack de middleware + tools + HITL).
3. [`graph/workers.py`](../kompass/graph/workers.py) — el worker Researcher y el modo multi.
4. Una estrategia de retrieval, p. ej. [`retrieval/rag.py`](../kompass/retrieval/rag.py) + [`retrieval/nl2sql.py`](../kompass/retrieval/nl2sql.py).
5. Un servidor MCP, p. ej. [`mcp_servers/ticketing.py`](../kompass/mcp_servers/ticketing.py) (dónde viven las acciones gateadas).
6. [`api/app.py`](../kompass/api/app.py) y [`scripts/demo.py`](../kompass/scripts/demo.py) — cómo se invoca todo end-to-end.

## 13. Glosario rápido

- **RAG híbrido / RRF** — combinar búsqueda densa (embeddings) y léxica (BM25) fusionando rankings con *Reciprocal Rank Fusion*.
- **CAG** — *Cache-Augmented Generation*: meter todo el corpus (pequeño y estable) en el prompt en vez de recuperar.
- **GraphRAG** — recuperación multi-hop sobre un grafo de conceptos (para preguntas relacionales).
- **NL2SQL** — traducir lenguaje natural a un SELECT sobre la BD.
- **HITL** — *Human-in-the-Loop*: pausa declarativa antes de una acción riesgosa (approve/edit/reject).
- **Checkpointer** — persiste el estado del grafo → el run es durable (sobrevive reinicios) y reanudable por `thread_id`.
- **Middleware** — hooks (`before_model`/`after_model`) que envuelven el loop del agente (seguridad, presupuesto, crítico, lecciones, HITL).
- **MCP / A2A** — capa vertical (agente↔tools) / horizontal (agente↔agente). Ver §7.
- **Saga / compensación** — acción multi-sistema all-or-nothing: si un paso falla, se deshacen los anteriores en reversa.
- **Panel de debate** — 3 lentes independientes + un juez, para decisiones borderline.
- **Self-improving (lecciones)** — destilar una regla operativa tras resolver un caso y reinyectarla en runs futuros.

## 14. Gotchas útiles (observados al leer el código)

- **`retrieval/router.py` no lo importa el agente.** Es la entrada *programática* que documentan
  evals/baseline; dentro del agente la misma decisión la toma el modelo eligiendo tools MCP.
- **`.env.example`** dice en un comentario "Only ANTHROPIC_API_KEY is required", pero la variable
  realmente requerida es **`OPENAI_API_KEY`** (y los modelos default son `openai:*`).
- **Versiones de Python:** el `Dockerfile` usa 3.12 pero el CI y `ruff.toml` apuntan a 3.11.
- **Fecha "hoy" = 2026-07-04** está *hardcodeada* en los prompts (router, system prompt); no lee la fecha real.
- **`lru_cache`** en índices (`rag._index`, `cag.full_corpus`, `graphrag._graph`): si el corpus cambia
  en disco, no se relee hasta reiniciar el proceso.
- **Windows / cp1252:** varios scripts hacen `sys.stdout.reconfigure(encoding="utf-8")` para no romper con emojis.
- **`redact_pii`** existe pero **no** está cableado al grafo (backstop opcional para API/UI; la ruta de producción sería NER/DLP).

## 15. Documentación relacionada

Teoría (inglés) en [`docs/`](.):
- [`01_agentic_ai_deep_dive.md`](01_agentic_ai_deep_dive.md) — qué es agentic AI, el espectro y los patrones.
- [`02_retrieval_strategies.md`](02_retrieval_strategies.md) — alternativas a RAG (CAG, GraphRAG, NL2SQL, router).
- [`03_framework_decision.md`](03_framework_decision.md) — por qué LangGraph v1.
- [`04_hitl_patterns.md`](04_hitl_patterns.md) — HITL declarativo, idempotencia, durabilidad.
- [`05_architecture.md`](05_architecture.md) — arquitectura completa + modelo de capacidades.
- [`06_advanced_patterns.md`](06_advanced_patterns.md) — A2A vs MCP, plan-and-execute, sandbox, proactivo, self-improving.
- [`07_decision_log.md`](07_decision_log.md) — 24 decisiones de ingeniería (estilo ADR).
- [`demo_walkthrough.md`](demo_walkthrough.md) — guion del demo de 3–5 min · [`blog_why_kompass.md`](blog_why_kompass.md) — el "porqué".

Prep de entrevista (español) en [`entrevista/`](../entrevista): framework PACTEDR, banco de preguntas y casos resueltos.
