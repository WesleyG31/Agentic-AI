# Case 3 — Intelligent Document / Invoice Processing (HOPn-flavored)

> A fully worked **PACTEDR** case: ingest PDFs and images (invoices, forms), extract
> structured fields with a multi-modal model, validate them deterministically, and route
> for human approval only when confidence is low or the action is risky. The point is not
> "the model can read an invoice" — it is **"we posted 78% of invoices to the ERP with no
> human touch, cut cost-per-invoice from ~€9.50 to ~€2.40, and leaked <0.5% errors."**

**TL;DR (say this in 20 seconds):** *"Document processing is the cleanest agentic-AI ROI
story I know: high volume, boring, error-prone, and measurable. Kompass ingests the PDF or
scan, a vision-language model extracts a typed Pydantic schema with per-field confidence,
deterministic validators check the math, VAT, IBAN and a 3-way match, and a LangGraph v1
HITL middleware pauses for `approve / edit / reject` only on the low-confidence or risky
20%. Straight-through processing on the rest. That's ~2,000 clerk-hours a month saved on a
25k-invoice workload."*

---

## Why "HOPn-flavored"?

At **HOPn** Wesley worked on intelligent document / process automation: taking messy
real-world documents (invoices, forms, structured PDFs and scans) and turning them into
validated, system-ready data with a human only in the exceptions loop. This case is the
Kompass reference build of exactly that pattern — same shape, same failure modes, generalized
onto the LangGraph v1 architecture and expressed in the PACTEDR frame so it's interview-ready.
The numbers below are **reference-build targets** (illustrative, benchmarked against public AP
figures such as APQC / Ardent Partners), not a claim about any one HOPn client's ledger.

> **Interview soundbite:** *"Invoice processing is where agentic AI stops being a demo and
> starts being a P&L line. It's the use case I use to prove I can reason about value, not just
> accuracy."*

---

## The PACTEDR lens (map for this case)

The full framework lives in [`framework_PACTEDR.md`](../framework_PACTEDR.md). For this case:

| Step | Letter | Focus in this case |
|---|---|---|
| **P** | Problem & Value | AP back-office is high-volume, manual, slow, error-prone; value = STP rate, cost/invoice, hours saved |
| **A** | Approach & Architecture | LangGraph pipeline: ingest → classify → preprocess → extract → validate → gate → HITL → post |
| **C** | Capabilities & Components | Multi-modal ingestion, VLM extraction, Pydantic typed output, MCP tools, ERP posting |
| **T** | Trade-offs & Technical decisions | VLM vs OCR+LLM, confidence thresholds, build-vs-buy, model routing |
| **E** | Evaluation & Guardrails | Field-level accuracy, validators, HITL on low confidence, fraud (IBAN-change) guardrail |
| **D** | Deployment & Ops | Durable checkpointer, idempotent posting, Langfuse traces, scaling & backfill |
| **R** | Results & Reflection | ROI table, error leakage, limits, what I'd do next |

---

## Scenario at a glance

A shared-services / accounts-payable (AP) team receives **~25,000 supplier invoices per
month** across email attachments, a supplier upload portal, and an SFTP drop. Formats are a
zoo: native-digital PDFs with a clean text layer, scanned PDFs, phone photos, multi-page
invoices with line-item tables, plus the occasional non-invoice (delivery note, statement,
spam). Today a team of clerks keys the data into the ERP (DATEV / SAP), matches it to purchase
orders, codes the GL account, and routes for sign-off. It is slow, expensive, and the error
rate quietly costs money in duplicate payments, late fees, and missed early-pay discounts.

Kompass turns this into an **agentic pipeline with straight-through processing (STP)** for the
easy majority and a tight **human-in-the-loop (HITL)** exception queue for everything the system
is not sure about or that is financially risky.

---

## P — Problem & Business Value

**Who hurts and why.** The AP clerk keys ~5 minutes per invoice end-to-end (entry + matching
+ coding + exception chasing). The controller waits days for cycle-time to close. The CFO eats
duplicate payments and late-payment penalties. None of this is *hard* work — it is *volume*
work with a long tail of exceptions, which is precisely the profile where agentic automation
pays off.

**Define the value metric first (before any modeling).** This is the PACTEDR discipline:

| Value metric | Definition | Why it matters |
|---|---|---|
| **STP rate** | % of invoices posted with zero human touch | The headline; every point is direct labor saved |
| **Cost per invoice** | Fully-loaded (labor + rework + penalties) ÷ volume | The CFO's number; benchmarkable externally |
| **Cycle time** | Receipt → posted/paid | Unlocks early-pay discounts, avoids late fees |
| **Error leakage** | % of *posted* invoices later found wrong | Trust gate — must be **lower** than the human baseline |
| **Hours saved / FTE-equivalent** | Baseline hours − residual review hours | The "so what" for the exec summary |

**Anti-goal:** we do **not** optimize for "highest extraction accuracy on a held-out set."
We optimize for **STP rate at a capped error leakage** — accuracy is an input, not the goal.

> **Interview soundbite:** *"I always define the value metric before the model. Here it's
> straight-through-processing rate at a hard cap on error leakage — accuracy is just one lever
> to move it."*

---

## A — Approach & Architecture

A directed LangGraph `StateGraph`. Each stage is a node; a **conditional edge** after
validation decides straight-through vs. human review. State is durable on a **Postgres
checkpointer**, so a run can pause at the human step for hours and resume exactly where it left
off — the same durability story as the rest of Kompass (see
[`../../docs/04_hitl_patterns.md`](../../docs/04_hitl_patterns.md)).

```
  ┌──────────────────────────────────────────────────────────────────┐
  │ SOURCES →  AP inbox · supplier upload portal · SFTP / shared drive │
  └──────────────────────────────────────────────────────────────────┘
                          │  (MCP: doc-intake server)
                          ▼
  ingest ─▶ classify ─▶ preprocess ─▶ extract ─▶ validate ─▶ confidence_gate
   fetch     invoice?    OCR / VLM     Pydantic   arithmetic     (route)
   dedupe    PO/receipt  render        + per-      VAT · IBAN
   hash      /other?     @300 dpi      field conf  3-way match · dup
                          │
        ┌─────────────────┴──────────────────────────┐
        ▼ auto (valid & conf≥τ & €≤cap & IBAN ok)     ▼ low-conf / fail / >€cap / IBAN change
   post_to_ERP  ◀──── approve / edit ────────────  human_review   (interrupt_on)
   (DATEV/SAP)                                       approve · edit · reject
        │                                                   │ reject
        ▼                                                   ▼
   notify + archive (immutable audit trail)          archive_rejected
```

**The graph in ~15 lines of LangGraph pseudocode:**

```python
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver

builder = StateGraph(InvoiceState)
for name, fn in [("ingest", ingest), ("classify", classify),
                 ("preprocess", preprocess), ("extract", extract),
                 ("validate", validate), ("human_review", human_review),
                 ("post_to_erp", post_to_erp), ("archive", archive)]:
    builder.add_node(name, fn)

builder.add_edge(START, "ingest")
builder.add_edge("ingest", "classify")
# non-invoices bail out early
builder.add_conditional_edges("classify",
    lambda s: "preprocess" if s["doc_type"] == "invoice" else "archive")
builder.add_edge("preprocess", "extract")
builder.add_edge("extract", "validate")
builder.add_conditional_edges("validate", route_by_confidence,
    {"auto": "post_to_erp", "review": "human_review"})
builder.add_edge("post_to_erp", "archive")
builder.add_edge("archive", END)

graph = builder.compile(checkpointer=PostgresSaver.from_conn_string(DB_URL))
```

**Why a graph and not a free-roaming ReAct agent?** This is a *bounded, auditable process*, not
open-ended reasoning. An explicit graph gives deterministic control flow, a clean place to hang
validators and the HITL gate, and a checkpoint per node for audit — exactly the reasoning in
[`../../docs/03_framework_decision.md`](../../docs/03_framework_decision.md) and the full system
view in [`../../docs/05_architecture.md`](../../docs/05_architecture.md). The LLM does the one
thing LLMs are uniquely good at (reading a messy document into structure); everything else is
deterministic code.

> **Interview soundbite:** *"I don't hand the ERP to a chatty agent. Extraction is the only
> non-deterministic step; validation, routing and posting are plain code. That's what makes it
> safe to run at 25k invoices a month."*

---

## C — Capabilities & Components

### 1. Multi-modal ingestion

The single hardest real-world problem is that "PDF" means five different things. The
`preprocess` node branches:

| Input | Path | Notes |
|---|---|---|
| Native-digital PDF (has text layer) | Parse text layer + render page image | Cheapest; still send image so the VLM can read tables/layout |
| Scanned PDF / photo | Render page → OCR **and/or** send image straight to the VLM | Skew/deskew, 300 dpi render |
| Multi-page | Page-split, per-page extract, then merge line items | Handle "continued on page 2" tables |
| Non-invoice | Classified out at `classify`, archived | Keeps the extractor's precision honest |

The deep treatment of multi-modal ingestion — VLM-vs-OCR trade-offs, page rendering, table
extraction, and bounding-box grounding for auditability — lives in
[`../../docs/06_advanced_patterns.md`](../../docs/06_advanced_patterns.md). Modern
vision-language models let us often **skip a separate OCR engine** and read the page image
directly, which is more robust to bad scans and preserves layout signal (which column is the
tax, which is the line total).

### 2. Typed / structured output (Pydantic)

The extractor does not return prose. It returns a **validated Pydantic model** via the model's
structured-output binding — the schema *is* the contract, and invalid extractions fail fast.
Kompass uses Pydantic typed outputs everywhere for exactly this reason.

```python
from pydantic import BaseModel, Field
from datetime import date
from typing import Literal

class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    tax_rate: Literal[0, 7, 19]          # German USt rates
    line_total: float

class Invoice(BaseModel):
    vendor_name: str
    vendor_vat_id: str = Field(pattern=r"^DE\d{9}$", description="USt-IdNr.")
    invoice_number: str
    invoice_date: date
    due_date: date | None = None
    currency: Literal["EUR", "USD", "GBP"] = "EUR"
    iban: str
    po_reference: str | None = None
    line_items: list[LineItem]
    subtotal: float
    tax_total: float
    grand_total: float

# The extractor call — page image goes in, typed object comes out
structured = model.with_structured_output(Invoice)
invoice = structured.invoke([{
    "role": "user",
    "content": [
        {"type": "text", "text": EXTRACTION_PROMPT},
        {"type": "image_url", "image_url": {"url": page_data_uri}},
    ],
}])
```

**Per-field confidence.** We don't trust a single number for the whole document. Each field
carries a confidence derived from token log-probabilities and/or a cheap second-pass
self-consistency check; we aggregate to `min_field_confidence` (the weakest link) rather than a
mean, because one wrong IBAN is worse than five perfect fields. The VLM is also asked to return
**bounding boxes** for the key fields, so a reviewer sees *where on the page* the number came
from — pure gold for auditability and trust.

### 3. Validation (deterministic, not the LLM)

```python
def validate(state: InvoiceState) -> InvoiceState:
    inv, flags = state["invoice"], []
    # arithmetic: line items reconcile to totals (allow rounding cents)
    if abs(sum(li.line_total for li in inv.line_items) - inv.subtotal) > 0.02:
        flags.append("SUBTOTAL_MISMATCH")
    if abs(inv.subtotal + inv.tax_total - inv.grand_total) > 0.02:
        flags.append("TOTAL_MISMATCH")
    if not valid_iban(inv.iban):                       flags.append("IBAN_CHECKSUM")
    if inv.iban != vendor_master.iban(inv.vendor_vat_id):
        flags.append("IBAN_CHANGED")                   # <-- fraud signal
    if is_duplicate(inv.invoice_number, inv.vendor_vat_id):
        flags.append("DUPLICATE")
    if not three_way_match(inv.po_reference, inv):     flags.append("PO_MISMATCH")
    return {**state, "validation_flags": flags}
```

These checks catch the failures LLMs are *bad* at (arithmetic, cross-referencing a master DB)
and are the backbone of the error-leakage guarantee. Vendor-master and PO lookups run over the
same MCP **sql** server the rest of Kompass uses; a semantic vendor-name match can fall back to
the retrieval layer ([`../../docs/02_retrieval_strategies.md`](../../docs/02_retrieval_strategies.md)).

### 4. Action & tools (MCP)

Posting to the ERP, creating the AP entry, scheduling payment, and notifying the requester are
**tools exposed via MCP** (the same vertical tool layer described in
[`../../docs/06_advanced_patterns.md`](../../docs/06_advanced_patterns.md)). The posting tool is
**idempotent** — keyed on `(vendor_vat_id, invoice_number)` — so a resume-after-crash never
double-posts.

---

## T — Trade-offs & Key Decisions

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Extraction engine | **VLM on page image** (OCR as fallback) | Dedicated OCR + text LLM | Robust to bad scans, keeps layout signal, one fewer moving part |
| Output format | **Pydantic structured output** | Free-text + regex parsing | Schema is the contract; invalid = fail fast, not silently wrong |
| Control flow | **Explicit LangGraph pipeline** | Autonomous ReAct agent | Bounded, auditable, deterministic routing (see docs/03) |
| Confidence aggregation | **min over fields** | mean | One wrong IBAN outweighs five right fields |
| Build vs. buy | **Build on Kompass + a VLM API** | Off-the-shelf IDP SaaS | Full control of the HITL gate, guardrails, audit trail; no per-page vendor lock-in |
| Model routing | **Cheap model for classify, strong VLM for extract** | One model for all | Cost: classification is easy, extraction is where accuracy pays |

**The core tension:** raise the confidence threshold τ and STP rate drops (more goes to humans,
higher cost) but error leakage falls; lower τ and STP climbs but errors leak. τ is a **business
dial**, tuned against the cost of a review minute vs. the cost of a leaked error — not an ML
hyperparameter chosen in a notebook.

> **Interview soundbite:** *"The confidence threshold isn't an ML knob, it's a business dial.
> I tune it against the cost of a review minute versus the cost of a wrong payment, and I show
> the CFO the trade-off curve."*

---

## E — Evaluation, Guardrails & Human Review

### Evaluation

We measure **field-level accuracy** against a golden set (double-keyed by humans), split by
field class, because header fields and line items fail differently:

| Field class | Target accuracy | Typical failure mode |
|---|---|---|
| Header (invoice #, date, total, VAT ID) | **96–98%** | OCR digit confusion (0/O, 1/7) |
| IBAN / bank details | **99%+** (must-verify) | Any error is a payment-routing risk |
| Line items (desc, qty, price, tax) | **92–95%** | Merged/split table rows, multi-page tables |

But the metric that goes on the slide is **STP rate at capped leakage**, evaluated end-to-end
through the graph — the same evals philosophy as the rest of the project (golden set + value
metrics + baseline; see [`../../docs/05_architecture.md`](../../docs/05_architecture.md)).

### Guardrails

- **Input:** file-type/size limits, malware scan, PII/GDPR handling on ingest (this is EU data).
- **Extraction:** Pydantic schema + enum/regex constraints (e.g. VAT rate ∈ {0, 7, 19}); a
  structurally invalid extraction can't proceed.
- **Action:** amount caps, **dual control** on payments over a threshold, and an
  **allowlist / IBAN-change fraud check** — if the extracted IBAN differs from the vendor
  master, force human review regardless of confidence. Vendor-bank-detail-change is the #1
  invoice-fraud vector, so this guardrail is non-negotiable.

### Human review — the LangGraph v1 declarative HITL middleware

The routing policy (the "business dial" made concrete):

| Condition | Route | Decision types offered |
|---|---|---|
| All validators pass **and** `min_field_confidence ≥ 0.95` **and** `total ≤ €2,000` **and** IBAN matches master | **auto (STP)** | — |
| `0.80 ≤ min_field_confidence < 0.95` | human review | **edit** (correct fields), approve |
| Any validation flag (mismatch, duplicate, PO mismatch) | human review | edit, **reject** |
| `IBAN_CHANGED` flag | human review + fraud queue | **approve/reject** under dual control |
| `total > €10,000` | human **approve** (even if confident) | approve, reject |

Kompass uses LangGraph v1's **declarative HITL middleware** (`interrupt_on` with the standard
`approve / edit / reject` decision types) for the risky *action* — posting to the ERP — layered
on top of the dynamic `interrupt()` primitive for the *review* node. Both patterns and their
durability/idempotency guarantees are documented in
[`../../docs/04_hitl_patterns.md`](../../docs/04_hitl_patterns.md).

```python
# Declarative: pause before the ERP-posting tool actually fires
from langchain.agents.middleware import HumanInTheLoopMiddleware

hitl = HumanInTheLoopMiddleware(
    interrupt_on={
        "post_to_erp": {                       # tool name
            "allowed_decisions": ["approve", "edit", "reject"],
        },
    },
)

# Dynamic: the explicit review node, backed by interrupt() + durable checkpointer
from langgraph.types import interrupt, Command

def human_review(state: InvoiceState) -> Command:
    decision = interrupt({                     # run pauses here, survives restarts
        "invoice": state["invoice"].model_dump(),
        "flags":   state["validation_flags"],
        "confidence": state["min_field_confidence"],
        "boxes":   state["field_boxes"],       # where each field was read from
    })
    if decision["type"] == "reject":
        return Command(goto="archive", update={"status": "rejected"})
    if decision["type"] == "edit":             # reviewer fixed fields → re-validate cheaply
        fixed = Invoice(**decision["args"])
        return Command(goto="post_to_erp",
                       update={"invoice": fixed, "status": "human_edited"})
    return Command(goto="post_to_erp", update={"status": "human_approved"})
```

Human edits are captured and fed back as few-shot corrections / eval cases — the self-improving
loop (Tier 2) that slowly *raises* the STP rate over time.

> **Interview soundbite:** *"Low confidence and financial risk are two different gates. Low
> confidence gets an **edit** UI with the bounding boxes so the reviewer just fixes a digit; a
> €50k payment or a changed IBAN gets an **approve/reject** with dual control — even if the
> model is 99% sure."*

---

## D — Deployment & Operations

- **Durability:** Postgres checkpointer means a run can sit in the review queue for hours or
  days and resume deterministically. No lost work on deploy or crash.
- **Idempotency:** the posting tool is keyed on `(vendor_vat_id, invoice_number)`; a retried or
  resumed run detects the prior post and no-ops. This is what makes "resume after crash" safe.
- **Observability:** every node emits a **Langfuse** trace — input document, extracted fields,
  confidences, validator flags, routing decision, human action, final post — a complete,
  queryable audit trail per invoice (and a compliance artifact).
- **Throughput & scaling:** ingestion is embarrassingly parallel (one graph run per document);
  scale the extract workers horizontally. A month-end spike or a historical **backfill** is just
  more concurrent runs against the same durable store.
- **Cost control:** model routing (cheap classifier, strong VLM only for extraction) plus prompt
  caching of the fixed extraction instructions keeps per-invoice inference cost well under the
  labor it replaces.

---

## R — Results, ROI & Reflection

**Reference-build targets** on the 25,000-invoices/month workload (illustrative, benchmarked
against public AP figures):

| Metric | Manual baseline | Kompass | Δ |
|---|---|---|---|
| STP rate (no human touch) | 0% | **78%** | +78 pts |
| Cost per invoice (fully loaded) | €9.50 | **€2.40** | −75% |
| Cycle time (receipt → posted) | 6–10 days | **<5 min** (STP) / <4 h (reviewed) | ~99% |
| Header extraction accuracy | ~97% (human) | **97%** | on par |
| Error leakage (posted-then-wrong) | 3.2% | **<0.5%** | −85% |
| Effective throughput / reviewer | ~90 inv/day | **~700 inv/day** | ~8× |

**Hours-saved math (the number the exec wants):**

```
Manual:  25,000 inv × 5 min           = 125,000 min ≈ 2,083 hrs/month
Kompass: 78% auto (0 min)
         22% reviewed = 5,500 inv × 1.2 min = 6,600 min ≈ 110 hrs/month
Saved:   2,083 − 110 ≈ 1,973 hrs/month  (~95%, ≈ 11–12 FTE)
@ €32/hr loaded ≈ €63k/month ≈ €0.76M/year in labor
         + avoided late-payment penalties + captured early-pay discounts
```

**What I'd flag honestly (reflection):**

- **The long tail is real.** New vendor layouts, handwriting, and non-Latin scripts drag the
  line-item accuracy down; that's *why* the HITL queue exists and why STP is 78%, not 100%.
- **Line items are harder than headers.** Multi-page tables and merged rows are the residual
  error source; targeted few-shots per problem vendor help more than a bigger model.
- **The IBAN-change guardrail is worth more than any accuracy point.** One prevented fraudulent
  payment can dwarf a year of labor savings — a great "I think about risk, not just accuracy"
  point.

**What I'd do next:** feed reviewer edits into a self-improving few-shot store to lift STP past
85%; add per-vendor template caching (CAG-style, see
[`../../docs/02_retrieval_strategies.md`](../../docs/02_retrieval_strategies.md)); and a
saga/compensation flow so a wrongly posted invoice can be automatically reversed.

> **Interview soundbite:** *"I'd rather ship 78% straight-through with sub-0.5% leakage and a
> tight exception queue than chase 95% automation that quietly leaks wrong payments. Trust is
> the product."*

---

## Related / further reading

- Framework: [`framework_PACTEDR.md`](../framework_PACTEDR.md) · Question bank: [`banco_preguntas.md`](../banco_preguntas.md)
- Sibling cases: [`caso_02_customer_support.md`](caso_02_customer_support.md) · [`caso_04_nl2sql_analyst.md`](caso_04_nl2sql_analyst.md) · [`caso_01_knowledge_assistant.md`](caso_01_knowledge_assistant.md)
- Theory:
  [`../../docs/06_advanced_patterns.md`](../../docs/06_advanced_patterns.md) (multi-modal ingestion, MCP tools) ·
  [`../../docs/04_hitl_patterns.md`](../../docs/04_hitl_patterns.md) (declarative HITL, durability, idempotency) ·
  [`../../docs/05_architecture.md`](../../docs/05_architecture.md) (full architecture) ·
  [`../../docs/03_framework_decision.md`](../../docs/03_framework_decision.md) (why LangGraph v1) ·
  [`../../docs/02_retrieval_strategies.md`](../../docs/02_retrieval_strategies.md) (vendor/PO lookup) ·
  [`../../docs/01_agentic_ai_deep_dive.md`](../../docs/01_agentic_ai_deep_dive.md)
