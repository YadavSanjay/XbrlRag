<h1 align="center">XbrlRag</h1>

<p align="center">
  <b>Ask SEC filings a question. Get numbers you can audit.</b><br>
  An analyst desk over 10-K, 10-Q and 20-F filings that answers with <b>cited, audited numbers</b>:
  every figure is a tagged XBRL fact reached by deterministic SQL and traceable to its filing, and
  the LLM narrates the result without doing the arithmetic.
</p>

<p align="center">
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="Arelle iXBRL" src="https://img.shields.io/badge/ingest-Arelle%20iXBRL-0f766e">
  <img alt="Postgres fact store" src="https://img.shields.io/badge/facts-Postgres-4169E1?logo=postgresql&logoColor=white">
  <img alt="Qdrant hybrid search" src="https://img.shields.io/badge/prose-Qdrant%20hybrid-dc244c">
  <img alt="REST + MCP" src="https://img.shields.io/badge/API-REST%20%2B%20MCP-009688?logo=fastapi&logoColor=white">
  <img alt="React + TypeScript" src="https://img.shields.io/badge/UI-React%20%2B%20TypeScript-61DAFB?logo=react&logoColor=black">
  <img alt="SEC EDGAR public data" src="https://img.shields.io/badge/data-SEC%20EDGAR%20(public)-2ea043">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-lightgrey"></a>
</p>

---

## Why XbrlRag

Point a general-purpose LLM at a 10-K and it will answer fluently, and sometimes wrongly. It reads
the prior-year column, drops a "in millions", picks segment revenue instead of the total, or does
the percentage in its head. Nothing in the answer tells you which. For an analyst, a wrong number
that looks right is worse than "I'm not sure".

SEC filers now tag their financial statements in **Inline XBRL** (phased in from 2019): each
reported value carries its concept, period, unit and dimensions, inside the same HTML a human
reads. XbrlRag is built on that:

- **Numbers come from SQL.** Every figure resolves to a source XBRL `fact_id` in a Postgres fact
  store. The LLM plans and narrates. It never writes SQL, never picks the final concept, and never
  does arithmetic; a sandboxed calculator does.
- **The model can't invent the company or the year.** The LLM lifts phrases from the question;
  companies resolve to a CIK and periods to the filer's own fiscal calendar by SQL.
- **It says "I don't know" before it guesses.** An ambiguous lookup returns `AMBIGUOUS`, not a
  pick, and the answer names the parts it could not answer. An independent verifier removes any
  statement its own citations don't support.
- **Every number walks back to the filing:** `fact_id` → accession number → the anchor in the
  filing's HTML. Every query leaves a trace with each LLM prompt, response and cost, and the exact
  SQL that ran.
- **Accuracy is measured outside the development loop.** Answers are graded against keys built
  from the SEC's own data, on held-out filers and hand-written questions nobody tunes on, and
  compared with a frontier model given tools over the same SEC data. "Confident, cited and
  wrong" is tracked as its own rate ([how it is measured](#how-it-is-measured)).
- **Prose isn't left out.** Risk factors, MD&A and footnotes are answered from a hybrid vector
  index over the same canonical HTML, so a fact and the paragraph that discusses it are linked.

## Why XBRL is harder than it looks

Tagged doesn't mean clean. Each filer is free to extend the standard taxonomy, and each one does
it differently. The assumptions a naive "just query the XBRL" approach makes break on real
filings. The figures below were measured in October 2026 on the project's first corpus (15
filers, 94 filings, 141,410 facts); the corpus has grown since ([the corpus](#the-corpus)):

| You might assume… | What filings actually do | In this corpus |
|---|---|---|
| A concept means the same thing at every company | Filers define their own concepts when the standard taxonomy doesn't fit their business | **23.5%** of the concepts a filer reports are its own extensions, from 10% (AMD) to 41% (TSMC) |
| Segments and products have standard names | "Data Center", "iPhone" and "AWS" are filer-defined dimension members, and most numbers are dimensional | **64%** of 3,096 members are extensions; **56%** of facts carry a dimension |
| "Revenue" is one tag | Filers pick different standard concepts for the headline line, split product and service revenue, and switch concepts between years | 3 headline revenue concepts across 15 filers; **2,081** per-filer mapping overrides |
| A concept's identifier is stable | Every annual filing references a new date-stamped taxonomy, so the same concept has a new identifier each year | 6 US GAAP versions (2021–2026) and 4 IFRS releases |
| A filing reports its own year | Prior-year comparatives sit beside current values, with the same concept and the same dimensions | **55%** of facts are comparatives |
| The fiscal year is the calendar year | Year-ends fall in seven different months, and 52/53-week calendars move the date every year | Apple's year ended Sep 24, Sep 30, Sep 28 and Sep 27 in 2022–2025 |
| One number is one fact | The same value is tagged again at lower precision ("$20.8 billion" beside 20,832 million) | Answered from the most precise fact |
| Everyone reports US GAAP in dollars | TSMC files a 20-F under IFRS in New Taiwan dollars with USD convenience translations; IFRS has no mandated operating-income subtotal | Never mixed across units: a cross-unit join once produced "+3,999%" |
| The XBRL database is ready to query | Arelle's schema leaves fiscal year, form type and "is extension" empty, and has no ticker at all | XbrlRag derives them in its own registries |

These are the reasons the LLM never chooses a concept. It names a fuzzy concept family, and a
deterministic resolver maps it to the filer's own concepts, members and fiscal calendar, with
per-filer overrides where the taxonomy runs out. The rules came out of real failures on this
corpus: [fact-store data model and invariants](docs/design/XbrlRag_Materialized_Views.md).

## Questions it answers

| You want to know… | XbrlRag gives you… |
|---|---|
| What was NVIDIA's revenue in fiscal 2025? | One fact, one unit, with its `fact_id`, filing and source anchor, or `AMBIGUOUS` rather than a guess |
| How much did Microsoft's operating income grow from FY2023 to FY2025? | The two cited facts and a sandboxed calculation over them, never mental arithmetic |
| Which of these companies has the highest free-cash-flow margin? R&D intensity? ROIC? | A cross-filer **screen**: ranked rows over derived metrics, every row cited, plus a group total or median when asked |
| How does Apple's revenue split by product, or by geography? | A **breakdown**: the members that partition the filer's reported total, each cited |
| What does TSMC say about export-control risk? | Cited passages from the filing's own sections, reranked and section-aware |
| The top three by revenue growth: what do they say about power constraints? | A compound question, split into ordered sub-queries; the second uses the first's result |
| Where did that number come from? | Numeric lineage and a full trace: every LLM call and the exact SQL |

## What an answer looks like

A plain-language summary plus statements, each citing the facts (or passages) it rests on
(abbreviated):

```json
{
  "answer_summary": "NVIDIA's revenue rose 114.2% in fiscal 2025, from $60.9 billion to $130.5 billion.",
  "answer_details": [
    { "statement": "Revenue was $130,497 million in fiscal 2025.",            "fact_ids": ["65593"] },
    { "statement": "Revenue was $60,922 million in fiscal 2024.",             "fact_ids": ["64207"] },
    { "statement": "Revenue grew 114.2% year over year.",                     "fact_ids": ["s3"] }
  ],
  "unanswered_parts": [],
  "metadata": { "fully_answered": true }
}
```

Each `fact_id` is a row in the fact store, and `s3` is the compute step that divided them. Fact
`65593` resolves to accession `0001045810-25-000023`, *Consolidated Statements of Income*, anchor
`#f-1335`, with the narrative chunk that discusses it attached. (These ids come from the
maintainer's local fact store, and yours will differ; the shape will not.) The response also
carries the plan and every step's result, and a `query_id` that opens the full trace in the UI.

**See it end to end:** [one compound question, traced](docs/showcase/compound-query-trace.md)
walks through a real trace in the UI with screenshots: the cited answer and its sources, then
every step from decomposition to the faithfulness verifier.

---

## Architecture at a glance

Filings are ingested in two arms into two stores, one for exact numbers and one for prose, and
nothing serves until a publish gate passes. At query time the LLM proposes intent, SQL resolves
it, and deterministic tools produce every number:

<p align="center">
  <a href="docs/images/architecture.svg">
    <img src="docs/images/architecture.svg" width="100%"
         alt="XbrlRag architecture: SEC EDGAR Inline XBRL filings (10-K, 10-Q and IFRS 20-F) for 33 filers are ingested in two arms. Arelle loads the tagged facts into Postgres and assigns each a fact_id, then registries and concept mapping are built; the prose arm renders canonical HTML, places facts in sections and chunks, cross-links and embeds the text. A publish gate rebuilds 19 materialized views and runs trust gates. Two stores, a Postgres fact store and a Qdrant narrative index, are linked by fact_id. At query time the LLM decomposes, routes and plans while SQL resolves companies and periods; the executor runs lookup, compute, screen, breakdown and narrative steps with deterministic SQL and sandboxed arithmetic. A cited synthesis is checked by an independent faithfulness verifier, and every number walks back through its fact_id to the filing and the source HTML. Answers are served over a BFF, an analyst API, an MCP server and a React UI, with evaluation and a controls spine alongside.">
  </a>
</p>

- **Fuzzy intent, deterministic resolution.** The planner sees a concept family ("Revenue"), a
  dimension hint ("data center") and a period, never the us-gaap taxonomy or the schema. A
  resolver maps those hints to exact concepts, members and rows using the filer's own dimension
  catalog, which is what copes with filer-specific extension concepts and the taxonomy namespaces
  that change every year.
- **Real filings break literal rules, so the tolerance is explicit.** A few narrow fallbacks
  (e.g. deriving gross profit as revenue minus cost of revenue, citing both facts) are accepted
  only when the result matches one-to-one, and each is logged as a decision in the trace.
- **One service layer, four surfaces.** A FastAPI BFF with streaming, a granular `/api/v1` analyst
  API (coverage, concepts, fact lookup / compute / bridge, metrics grid, screen, narrative
  search, answer), an MCP server exposing the same services as agent tools, and React
  *Business* and *Technical* consoles over one `query_id`.
- **Laptop or AWS.** The same code and images run locally or on AWS (`XBRLRAG_DEPLOY_MODE`), with
  RDS and S3 in AWS mode. Embedding and reranking come from a separate inference service over
  HTTP, so the serving image carries no torch.

| Concern | Choice |
|---|---|
| Fact ingest | [Arelle](https://arelle.org/) xbrlDB load of the primary Inline XBRL document; US GAAP and IFRS |
| Fact store | Postgres: Arelle's schema, XbrlRag registries, 19 managed materialized views |
| Concept vocabulary | `concept_mapping` bootstrapped from HiFi-KPI synonym mining plus curation, per-filer overrides |
| Narrative search | Qdrant hybrid: `BAAI/bge-m3` dense + sparse, `jina-reranker-v3` rerank |
| Generation | OpenAI or Anthropic, selected by profile (DeepSeek as a low-cost development profile); each stage keeps its own model tier |
| Controls | One LLM gateway: retry, escalation, rate limiting, per-run cost ceiling and circuit breaker; versioned prompts; semantic query cache |
| Observability | Per-query traces and numeric lineage in an `audit` schema, Prometheus `/metrics`, Grafana dashboards and alerts |

## The corpus

**33 large S&P 100 filers across sectors**, added in waves. The first 15 were chosen for the AI
trade (NVIDIA, Microsoft, Alphabet, Amazon, Meta, Apple, Tesla, Oracle, Broadcom, AMD, Micron,
Intel, TSMC, and the power producers Constellation and Vistra). The first v2 wave added
consumer, health-care, industrial and further technology names, from Walmart, Coca-Cola and
Eli Lilly to Lockheed Martin and Union Pacific. Annual reports from fiscal 2022 or 2023, plus
later 10-Qs. TSMC files a **20-F under IFRS**, so the fact store maps `ifrs-full` concepts
alongside `us-gaap`.

Banks and brokers, insurers, multi-registrant utilities and REITs are out of scope for now: their
statements need their own accounting model ([`VISION.md`](VISION.md)). Scope is one config file
([`configs/pipelines/sec_edgar_downloads.yaml`](configs/pipelines/sec_edgar_downloads.yaml));
adding a filer is a download and an ingest run, not a code change.

## How it is measured

**Why the measurement was rebuilt.** The first benchmark was internal: cases generated by SQL
from the fact store, keyed from the same views the pipeline queries. The pipeline was tuned on it
and reached 96.7% (2026-10-17). On 2026-10-28 it met 47 questions written by hand and sealed
before the run (set B): value accuracy fell to **75.2%**, while a frontier model given the whole
filing scored 93.6%. An in-loop number had told us little
([the first out-of-loop test](docs/eval/external_test_v1/README.md)).

**How it is measured now (v2).** The comparison that matters is XbrlRag against
**`frontier_sec_tools`**: a frontier model with tools over the SEC's own public APIs
(`companyfacts`, `frames`). It asks what the curation adds over SEC's raw facts plus a capable
agent. The rules are fixed in a [pre-registration](docs/eval/heldout_v2/preregistration.md)
before any held-out look.

| Part | What it is |
|---|---|
| **Answer keys** | Built from the SEC's data under written rules (restatements, fiscal calendars, units, ratio definitions), not from XbrlRag's views ([semantics](docs/design/XbrlRag_Eval_Semantics.md)) |
| **Question sets** | Dev and validation sets for development; held-out filers, hand-written questions and post-freeze filings that no one tunes on, read only at the final milestone |
| **Questions asked many ways** | Templates plus paraphrases in analysts' own idiom; stability across phrasings is reported beside accuracy |
| **Comparators** | The frontier agent; a long-context model handed the right filing (the single-filer ceiling); closed book (the floor) |
| **Headline dimensions** | Value accuracy on cross-filer, cross-period and compound questions; confident-wrong rate; grounding precision (the *right* fact cited); cost and latency per question |
| **Regression checks** | The v1 SQL-derived suite (513 cases, in-loop), the golden set, drift and a prompt × model matrix |

Held-out results are published once the final look is taken; until then, development numbers are
reported as in-loop only. Every run lives in an `eval` schema and renders in the UI at
`/tech/eval`, where each graded case opens its own trace. Design:
[evaluation v2](docs/design/XbrlRag_Eval_v2_Architecture.md) ·
[v2 plan](docs/design/XbrlRag_v2_Plan.md) · [v1 evaluation](docs/design/XbrlRag_Evaluation.md).

## Built for audit and governance

The controls a model-risk or internal-audit reviewer asks about are in the code, not left to good
intentions.

### Lineage and audit trail

- **Every query is a trace,** keyed by one `query_id` and persisted to an `audit` schema: the
  plan, every LLM call with its prompt version, response and cost, every SQL statement, each
  fallback decision, and the final answer.
- **Numeric lineage for every displayed number:** fact → filing and accession → SQL → arithmetic
  → displayed value, recorded on failure as well as success. The reverse also works:
  `audit.queries_for_fact(fact_id)` lists every answer that used a given fact.
- **Re-derivable answers.** The model id, content-hashed prompt versions, plan, SQL, retrieved
  `fact_id`s and `chunk_id`s are stored with each answer, so a past answer can be explained
  months later. Ingest runs are audited the same way, with each failure classed as retryable or
  permanent.

Detail: [observability and tracing](docs/design/XbrlRag_Observability_Tracing.md) ·
[UI: Trace Explorer and lineage views](docs/design/XbrlRag_UI_Design.md).

### Cost tracking and controls

- **One gateway for every LLM call** (`LLMGateway`): retry, tracing, prompt-registry resolution
  and model escalation, so no stage can spend outside it.
- **Every call is priced** from a committed rate card (`configs/llm/pricing.yaml`), including
  cached and cache-write tokens. An unpriced model raises an error instead of recording $0.
- **Hard caps, not just dashboards**, switched on per deployment: a per-run ceiling on cost,
  calls and tokens, a circuit breaker on consecutive failures, and a process-wide rate limiter.
  A trip halts the run with a degraded answer rather than overspending. Eval runs take a hard
  `--max-cost-usd` ceiling.
- **Spend is visible** per stage, model and prompt version in the `audit` views, exported to
  Prometheus and Grafana. An opt-in semantic query cache, invalidated on every publish, avoids
  paying twice for the same question.

Detail: [LLM harness: gateway, escalation and cost controls](docs/design/XbrlRag_LLM_Harness.md) ·
[monitoring](configs/monitoring/README.md).

### AI-assisted development lifecycle

The repo is developed with AI coding agents (Claude Code), so the process is built to keep their
changes honest:

- **Specs and tests come first.** Each change starts as a written spec (`docs/specs/<id>.md`).
  Its tests are proven red, locked, and merged by a human before any implementation. The agent
  that implements may not edit a locked test; if a test looks wrong, it files a dispute and
  stops.
- **Doer ≠ judge.** At runtime an independent verifier checks the synthesizer's citations. At
  dev time a separate read-only evaluator agent checks a changed pipeline's answers, and an
  evidence-backed review applies project lenses for RAG integrity and XBRL fact handling.
- **Accuracy changes are measured, not asserted.** A fix is validated on a cheap targeted eval
  slice (about $0.30–0.50) and compared case by case; one full run confirms at the end.
- **Humans hold the keys.** Agents never push or merge, and never write to the fact store or run
  ingest. They hand over the exact commands instead.

Detail: [test-first workflow](#test-first-workflow-tdd-spec) · [scope and non-goals](VISION.md) ·
[evaluation workflow](docs/guides/xbrl-flow/11-evaluation.md).

### Harness engineering

The Claude Code harness around the repo is engineered and tested like the product:

- **Deterministic hooks** ([`.claude/hooks/`](.claude/hooks/README.md)): a secret guard that
  fails closed, lint-on-edit, and a Stop gate that refuses to end a turn on a red unit suite.
- **Machine-checked scope:** every planned feature is an entry with an executable check in a
  [feature manifest](docs/harness/README.md). A feature counts as done only when its check passes,
  and CI fails if a "done" feature regresses.
- **Subagents with narrow roles** ([roster](docs/harness/SUBAGENTS.md)): a read-only evaluator, a
  read-only explorer, and refactor workers that each run in their own git worktree.
- **Bounded autonomy:** an [unattended-run policy](docs/harness/UNATTENDED_RUNS.md) with cost
  caps and a runaway-process watchdog.
- **A dated re-audit** ([cadence](docs/harness/REAUDIT.md)): every 90 days, and after any model
  change, each piece of scaffolding is re-checked against evidence and retired if the current
  model no longer needs it. A check fails once the audit is overdue.

---

## Quick start

The full flow (schema, download, ingest, vector load) is in the
[XBRL-flow guides](docs/guides/xbrl-flow/README.md). Bringing up the serving stack:

```bash
cp .env.example .env    # OPENAI_API_KEY, RAGINFER_URL, SEC_EDGAR_IDENTITY="Name email@domain", …
docker compose --env-file .env -f infra/docker/docker-compose.yml up -d
curl localhost:8000/api/health    # API → http://localhost:8000 · UI → http://localhost:8080

# optional: Prometheus + Grafana
docker compose --env-file .env -f infra/docker/docker-compose.yml --profile monitoring up -d
```

Starting the services doesn't load any data. The fact store still needs its schema and an ingest
run ([guides 2–5](docs/guides/xbrl-flow/README.md)).

**You provide two things.** The images don't bundle the embedding and reranking models. They call
a BGE-M3 embedder and a Jina reranker over HTTP at `RAGINFER_URL`. The maintainer runs
[RagInference](https://github.com/YadavSanjay/RagInference), a separate repository, for this. SEC
EDGAR also requires a contact in `SEC_EDGAR_IDENTITY`, and there is no default. Docs that mention
sibling projects ([RagInference](https://github.com/YadavSanjay/RagInference), `aws-genai-iac`,
[MacroRAG](https://github.com/YadavSanjay/MacroRag)) describe the maintainer's own setup, and this
repo does not depend on their source.

## Explore further

| If you want to… | Start here |
|---|---|
| Set it up end to end | [XBRL-flow guides](docs/guides/xbrl-flow/README.md): [setup](docs/guides/xbrl-flow/01-setup-and-dependencies.md) · [Postgres](docs/guides/xbrl-flow/02-postgres-setup.md) · [download](docs/guides/xbrl-flow/03-data-download.md) · [ingest](docs/guides/xbrl-flow/04-ingest-and-populate.md) · [vector load](docs/guides/xbrl-flow/05-vectordb-load.md) |
| Understand the query pipeline | [Retrieval pipeline guide](docs/guides/xbrl-flow/06-retrieval-pipeline.md) · [module notes](src/retriever/xbrl/CLAUDE.md) |
| Call it from code or an LLM agent | [REST `/api/v1` and MCP](docs/guides/xbrl-flow/09-programmatic-access-rest-and-mcp.md) |
| Use the UI, traces and metrics | [API, UI and observability](docs/guides/xbrl-flow/07-api-ui-observability.md) · [UI design](docs/design/XbrlRag_UI_Design.md) |
| Understand the design decisions | [Architecture](docs/design/XbrlRag_Architecture.md) · [fact store](docs/design/XbrlRag_Materialized_Views.md) · [HTML-canonical source](docs/design/XbrlRag_HTML_Canonical_Source.md) · [LLM harness](docs/design/XbrlRag_LLM_Harness.md) · [tracing](docs/design/XbrlRag_Observability_Tracing.md) |
| Run or extend the evaluation | [Evaluation guide](docs/guides/xbrl-flow/11-evaluation.md) · [v2 design](docs/design/XbrlRag_Eval_v2_Architecture.md) · [v1 design](docs/design/XbrlRag_Evaluation.md) · [protocol records](docs/eval/README.md) |
| Deploy with Docker, Helm or AWS | [Packaging](deploy/README.md) · [images](docs/guides/xbrl-flow/10-packaging-images.md) · [AWS design](docs/design/XbrlRag_AWS_Deployment.md) · [monitoring](configs/monitoring/README.md) |
| Know what is in and out of scope | [VISION.md](VISION.md) · live status: `python scripts/harness/check_features.py` |
| Work in the code with Claude Code | [CLAUDE.md](CLAUDE.md) and the per-module `CLAUDE.md` files |

**Not built, by decision:** an ingest DAG and EDGAR feed polling (ingest is operator-run),
authentication and multi-tenancy (trusted network only), multi-turn conversation, and adapters
for external benchmarks. One defect is open: segment percentages double-count when a filer tags
both an aggregate member and its children (`WS1-A4`). A legacy PDF flow for 10-Ks is still in the
repo but is maintenance-only ([guides](docs/guides/pdf-legacy-flow/README.md)).

<details>
<summary><b>Repository layout</b></summary>

```
src/ingest/sec_edgar/   EDGAR download → Arelle fact ingest → registries → canonical HTML → publish gate
src/retriever/xbrl/     the query pipeline: decompose → route → plan → execute → synthesize → verify
src/pipelines/xbrl/     orchestration: retrieval pipeline, vector-DB load, semantic query cache
src/llm/                provider-neutral LLM gateway, model tiers + pricing, cost, rate limit, budget
src/observability/      traces, numeric lineage, audit store, Prometheus exporter
src/api/                FastAPI BFF + /api/v1 analyst API + GET /metrics      src/mcp_server/  MCP tools
src/eval/               eval framework (cases, systems, graders), golden set, drift, prompt × model
src/chunking/ src/embeddings/ src/vectorstores/   canonical-HTML chunking, BGE-M3 (local or remote), Qdrant
scripts/ddl/            Postgres schema: vendored Arelle base, Layer-0, 19 materialized views, audit, eval
prompts/                versioned prompt templates, resolved by (stage, provider, series, version)
ui/                     React + TypeScript business and technical consoles (Vite)
configs/                pipelines, LLM profiles + pricing, entity aliases, eval templates, monitoring
infra/ deploy/          Dockerfiles + Compose, Helm chart, packaging and CI overview
test/                   unit, live-backend integration, locked spec tests; committed eval suites
docs/                   guides/ (how to run) · design/ (why) · specs/ (test-first change specs)
```

</details>

<details>
<summary><b>Engineering and quality gate</b></summary>

- **Gate:** `ruff check .` → `mypy src/` → `pytest test/unit/`. CI
  ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs ruff, the standards ratchet and the
  unit tests; separate workflows build the images (and lint Compose and Helm) and run the
  golden-set regression and drift checks.
- **Tests use fakes at I/O boundaries only** (LLM provider, network, external DB); the unit under
  test and its numerics are never mocked. Live-backend tests skip without the services.
- **Ruff findings only shrink:** existing ones live in `ruff-burndown.toml`, which may lose
  entries but never gain them.
- **Prompts are versioned files**, never inline strings; every LLM call records its prompt version.

```bash
pytest test/unit/ -v                                # unit suite (self-contained; live tests skip)
python scripts/harness/check_features.py --gate     # scope / feature gate
ruff check --fix .                                  # lint + safe fixes
```

</details>

### Test-first workflow (tdd-spec)

A change that could go wrong unnoticed, and whose right answer an independent source can state,
starts with spec tests written from `docs/specs/<id>.md`, proven red, locked and
merged before implementation; other changes carry a `Spec-Skip` commit trailer (see the skill).
The branch sets the role: `spec/<id>` writes the spec, tests and
stubs; `impl/<id>` writes `src/` against the locked tests. Spec tests live in `test/spec/`, unit
tests in `test/unit/`. PRs merge with **merge commits only**, because the gate reads locks and
retirements from main's merge history.

<details>
<summary><b>Setup, day-to-day commands and rules</b></summary>

The tools come from the `tdd-spec` skill in the Claude Code config directory
(`$env:CLAUDE_CONFIG_DIR\skills\tdd-spec`) and are vendored into `tools/tdd/`. The pack is
installed (`.claude/tdd/`, `tools/tdd/`, `test/spec/conftest.py`, and the `tdd-path-guard` /
`tdd-spec` workflows).

**Managed hooks, once per machine** (and again after any change to the skill's `scripts/`):
close VS Code and every `claude` session, open PowerShell with *Run as administrator*, and run

```powershell
powershell -ExecutionPolicy Bypass -File "$env:CLAUDE_CONFIG_DIR\skills\tdd-spec\install.ps1" -AcceptWritableSource
```

Expect `OK: only administrators and SYSTEM can write to the managed install`, then start VS Code
normally (never elevated). Check with `conda run -n py312 python tools/tdd/tdd.py status`.

**Day-to-day:**

```powershell
conda run -n py312 python tools/tdd/tdd.py lint <id>     # spec author: mechanical rubric
conda run -n py312 python tools/tdd/tdd.py red <id>      # tests fail against stubs and wrong values
conda run -n py312 python tools/tdd/tdd.py lock <id>     # then commit tests + stubs + spec + lock
conda run -n py312 python tools/tdd/tdd.py run <id>      # implementer loop on impl/<id>
conda run -n py312 python tools/tdd/tdd.py verify <id>   # fresh hypothesis seeds before merge
conda run -n py312 python tools/tdd/tdd.py status        # locks, states, warnings
```

A test that looks wrong is never edited on `impl/<id>`: write `.claude/tdd/disputes/<id>.md` and
stop; a corrected test arrives through a re-lock on main.

**Rules that keep the cycle working:**

- **A spec PR may add only stubs and imports to `src/`.** A brand-new stub module may not even
  carry a module docstring. A behaviour fix to an existing function needs a spec with no stubs
  (`tdd red` accepts it as bug-fix mode).
- **Prove a spec satisfiable before locking it**, with a throwaway prototype in a separate
  worktree, run under `--hypothesis-profile=tdd-verify`. This catches contradictory or vacuous
  tests that would otherwise need a re-lock PR.
- **Name the implementation branch exactly `impl/<id>` and build it on the spec commit.** The lock
  retires only on a merge from `impl/<id>`.
- **A pushed branch is neither a PR nor a merge.** Before building on a spec, or before any
  database reload or view rebuild that depends on new code, check
  `git merge-base --is-ancestor <commit> origin/main`.
- **Keep an implementation branch out of the main checkout until its spec is on `origin/main`**
  (the Stop gate flags spec files that differ from `origin/main`). Build in a throwaway worktree,
  then check it out in the main checkout (which has `.env`) for the paid eval slice, and validate
  before the impl PR merges ([guide 11](docs/guides/xbrl-flow/11-evaluation.md)).
- **Prompt-only changes** have no unit to lock: version the prompt and validate with a slice.
- **Database writes and ingest** (the publish gate, `rebuild_mvs.py`, Arelle ingest, the Qdrant
  load) are run by the maintainer on the updated `main`; agents hand over the exact PowerShell
  commands. Read-only queries and eval runs are fine.

</details>

---

## License and support

MIT — see [`LICENSE`](LICENSE). The vendored Arelle schema in
[`scripts/ddl/base/`](scripts/ddl/base/README.md) is Apache-2.0 (license text alongside it).

The models the pipeline calls are not part of this repository and carry their own licenses; check
them before you deploy:

| Model | Used for | License |
|---|---|---|
| [`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3) | dense + sparse embeddings | MIT |
| [`jinaai/jina-reranker-v3`](https://huggingface.co/jinaai/jina-reranker-v3) | narrative reranking | **CC BY-NC 4.0 — non-commercial use only** |
| [`BAAI/bge-reranker-v2-m3`](https://huggingface.co/BAAI/bge-reranker-v2-m3) | legacy PDF-flow reranker | see its model card |

Numeric answers (lookups, computations, screens, breakdowns) are SQL over the fact store and do
not use the reranker; only narrative retrieval does.

**Source data.** No filings are committed: the downloader fetches public filings from SEC EDGAR
into a gitignored directory on your machine, under SEC's fair-access rules (identify yourself in
`SEC_EDGAR_IDENTITY` and stay within its request rate).

This is a personal project, shared as-is: no support, no stability guarantees, and no promise to
review issues or pull requests. Not investment advice.
