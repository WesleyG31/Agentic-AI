# Observabilidad y evaluaciones de Kompass

Kompass usa **Langfuse OSS v4 autoalojado** como plano visual de observabilidad y
mantiene `runs.jsonl` como respaldo local ligero. El objetivo no es solamente ver
llamadas al modelo: es poder explicar y medir por qué el agente tomó una decisión,
qué evidencia leyó, qué herramienta eligió, qué argumentos envió y si completó el
objetivo de forma segura.

## Qué se puede ver

Cada petición crea una traza raíz y las peticiones con el mismo `thread_id` quedan
agrupadas como una sesión:

```text
session: thread_id
└── agent: kompass-chat-turn
    ├── semantic-cache (hit/miss)
    └── LangGraph agent
        ├── guardrail / safety model (cuando hace falta)
        ├── agent model — tier:balanced, provider:ollama|openai
        ├── tool: search_docs | query_database | research | ...
        ├── critic model — tier:fast
        └── human-approval-required

session: mismo thread_id
└── agent: kompass-hitl-resume
    ├── human-review-decision
    └── LangGraph resume → tool execution → final answer
```

La pausa HITL no mantiene un span abierto indefinidamente. La solicitud original y
la reanudación son trazas distintas dentro de la misma sesión. Esta frontera refleja
la operación real: pueden ocurrir en procesos o momentos diferentes.

El callback se conecta en la **invocación raíz del grafo**, no dentro de cada modelo.
Por eso Langfuse recibe una jerarquía completa en lugar de trazas LLM desconectadas.
Los modelos sí llevan metadatos `tier:fast|balanced|reasoning` y
`provider:ollama|openai`, para filtrar costo, calidad y latencia por responsabilidad.

## Levantar Langfuse localmente

Requisitos: Docker Desktop/Engine con Compose y suficiente memoria para seis
servicios. El agente y Ollama siguen ejecutándose en el host.

```powershell
# 1. Desde la raíz de kompass: genera secretos y configura .env
.\.venv\Scripts\python.exe -m kompass.scripts.observability_env

# 2. Levanta la topología oficial necesaria para Langfuse v4
docker compose --env-file .env.observability -f docker-compose.observability.yml up -d

# 3. Inspecciona salud y logs
docker compose --env-file .env.observability -f docker-compose.observability.yml ps
docker compose --env-file .env.observability -f docker-compose.observability.yml logs -f langfuse-web langfuse-worker
```

La interfaz queda en `http://localhost:3000`. El script imprime el usuario y la
contraseña local. `.env` y `.env.observability` están excluidos de Git.

Para detener sin borrar datos:

```powershell
docker compose --env-file .env.observability -f docker-compose.observability.yml down
```

No se usa `down -v` en el flujo normal porque borraría Postgres, ClickHouse, Redis
y MinIO. En un despliegue público hay que reemplazar los secretos, habilitar TLS,
limitar exposición de red y usar almacenamiento respaldado.

## Generar y puntuar trazas

Con Langfuse levantado, inicia API y UI:

```powershell
.\.venv\Scripts\python.exe -m uvicorn kompass.api.app:app --reload --port 8000
.\.venv\Scripts\python.exe -m streamlit run ui/app.py
```

Una respuesta de `/chat` o `/resume` contiene `trace_id` y `trace_url`. La UI muestra
el enlace visual y botones 👍/👎; `/feedback` registra `user_feedback` directamente
sobre la traza. `/health` permite verificar proveedor, modo de agente y si Langfuse
está configurado sin revelar credenciales.

Con Ollama local, un turno agentic puede encadenar varias generaciones (safety,
selección de herramientas, síntesis y crítico). Streamlit espera hasta 600 segundos
por defecto mediante `KOMPASS_UI_TIMEOUT_SECONDS`; un timeout no implica que la API
haya cancelado la ejecución, por lo que la UI advierte que no se debe reenviar una
acción a ciegas.

El costo se calcula con tokens reportados por el proveedor. Ollama usa cero por
defecto. Para un modelo alojado, configura los precios vigentes sin modificar código:

```dotenv
KOMPASS_INPUT_COST_PER_MILLION=0
KOMPASS_OUTPUT_COST_PER_MILLION=0
```

Langfuse conserva además la telemetría de cada generación, de modo que se pueden
crear dashboards por modelo, tier, release, ambiente, sesión o usuario.

## Golden dataset: 60 casos

`evals/golden_set.json` contiene 60 casos versionados en Git y reproducibles sobre
el corpus ACME:

| Familia | Qué prueba |
|---|---|
| `rag` | políticas, citas, plazos, reglas y recuperación documental |
| `sql` | selección de datos, filtros, joins y consultas de solo lectura |
| `multi` | combinación de política + estado operacional y razonamiento en varios pasos |
| `action` | argumentos exactos, side effects y approve/reject de HITL |
| `abstain` | datos ausentes, privacidad, alcance y prompt injection |

El loader falla si hay menos de 50 o más de 100 casos, IDs duplicados, categorías
inválidas o rubricas incompletas. La suite se sincroniza a la vista Datasets:

```powershell
.\.venv\Scripts\python.exe -m evals.sync_dataset
```

El JSON local es la fuente de verdad para CI; Langfuse es su vista colaborativa.
Así una caída de observabilidad nunca impide ejecutar regresiones.

## Métricas y LLM-as-a-judge

Cada episodio conserva la trayectoria `{tool, args, result}`. El juez de tipo
Pydantic usa el tier `reasoning` y recibe pregunta, hechos esperados, herramientas
esperadas, side effect, trayectoria y respuesta. Evalúa separadamente:

| Métrica | Criterio |
|---|---|
| `task_success` | resolvió el objetivo seguro completo, incluido side effect/HITL |
| `answer_correctness` | contiene los hechos requeridos sin contradicciones |
| `hallucination` | inventó o afirmó sin evidencia un dato específico; menor es mejor |
| `tool_selection` | eligió herramientas suficientes y apropiadas |
| `tool_arguments_correct` | IDs, SQL, montos y argumentos corresponden a la solicitud |
| `retrieval_relevance` | puntuación 0–1 de relevancia de la evidencia recuperada |
| `latency` | duración end-to-end; se reportan media y P95 |

Hay contratos deterministas adicionales: presencia de hechos/citas, cobertura de
herramientas esperadas, SQL/side effects comprobados directamente en la base y cero
acciones ejecutadas después de un rechazo. El juez maneja variaciones lingüísticas;
los contratos evitan que el juez pueda ocultar un error operacional.

```powershell
# Iteración rápida; no actualiza la tabla pública del README
.\.venv\Scripts\python.exe -m evals.run --agent-only --limit 3

# Comparación completa contra naive RAG y resultados en evals/results/results.json
.\.venv\Scripts\python.exe -m evals.run

# Golden regression test: devuelve código 1 si cualquier umbral falla
.\.venv\Scripts\python.exe -m evals.run --ci
```

`evals/regression_baseline.json` contiene mínimos para éxito, corrección, selección,
argumentos y relevancia, y máximos para alucinación, acciones inseguras y P95. Un
cambio de umbral debe revisarse igual que un cambio de producto; no se baja para
hacer pasar CI. Los scores del agente se publican también sobre su traza Langfuse.

## Versionado de prompts

Los prompts de agente, planner, guardrail, crítico y juez se declaran como
`PromptSpec(name, version, text)` y tienen fingerprint SHA-256. El manifiesto de
versiones viaja como metadato en cada traza, haciendo reproducible un resultado.

```powershell
.\.venv\Scripts\python.exe -m kompass.scripts.sync_prompts
```

El comando crea versiones visuales en Langfuse Prompt Management. Git permanece
como fuente de verdad y fallback: el agente no deja de funcionar si Langfuse está
caído. Flujo recomendado para un cambio:

1. Cambiar el texto y subir su versión semántica.
2. Ejecutar tests offline.
3. Sincronizar prompts y golden dataset.
4. Ejecutar la suite de 60 casos.
5. Comparar métricas, errores y trazas con la versión anterior.
6. Promover solo si pasa los límites de regresión y revisión humana.

## Por qué esta arquitectura es buena para entrevistas

Demuestra cuatro niveles distintos que suelen confundirse: tracing operacional,
evaluación de calidad, pruebas de regresión y gestión de prompts. También enseña
decisiones defendibles: observabilidad no bloqueante, aislamiento de HITL por
sesión, juez con salida tipada, validaciones deterministas para side effects,
baseline explícito y costos configurables en lugar de precios hardcodeados.

Referencias oficiales: [Langfuse self-hosting](https://langfuse.com/self-hosting),
[Docker Compose oficial](https://github.com/langfuse/langfuse/blob/main/docker-compose.yml),
[integración LangChain/LangGraph](https://langfuse.com/integrations/frameworks/langchain),
[datasets](https://langfuse.com/docs/evaluation/experiments/datasets) y
[prompt management](https://langfuse.com/docs/prompt-management/get-started).
