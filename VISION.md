# XbrlRag — Vision & Scope

> The durable "north star" for XbrlRag: what it is, what it is **not**, and the
> non-negotiable principles any change must uphold. The design docs in
> [`docs/design/`](docs/design/) elaborate the *how*; this file fixes the *what*
> and *why* so scope can't quietly drift mid-build. When a request falls outside
> the scope below, **decline it against this document** (or update this document
> first, deliberately). The machine-checkable companion — every planned feature
> as a testable check — is [`docs/harness/feature_manifest.yaml`](docs/harness/feature_manifest.yaml),
> gated by [`scripts/harness/check_features.py`](scripts/harness/check_features.py).

## Mission

Give a financial analyst **trustworthy, cited answers** over SEC filings, to the
numeric questions and to the text questions around them. Every number shown is
traceable to a source fact, every statement to the passage it came from, and a
wrong number is caught before it reaches the user. Correctness and auditability
outrank breadth, latency, and cleverness.

## Primary flow (in scope)

The **XBRL / iXBRL flow** is the product. SEC EDGAR iXBRL → Arelle ingest →
Postgres fact store (`xbrl_db`, Layer-0 tables + managed materialized views) →
the plan-based query pipeline (decompose → intent-route → branch-dispatch →
plan → execute `lookup`/`compute`/`screen`/`breakdown`/`narrative` → synthesize →
faithfulness-verify) → REST BFF / `/api/v1` analyst API / MCP server / React
consoles. Full observability (per-query traces, numeric lineage, `audit.*`
schema + analyst views) is part of the product, not an add-on.

## HTML 10-K flow (retained, not evaluated)

A second flow reads the same 10-K as a **document**: sections, prose and parsed
tables, with every XBRL tag stripped (it **must not see XBRL**; a test enforces
this). It was built as the v2 evaluation's RAG baseline. On 2026-11-06 it **left
the comparison** ([decision](docs/design/XbrlRag_HtmlRag_Decision.md)): for tagged
numbers the real competitor is a frontier agent over the SEC's own fact API, not a
RAG over parsed HTML. The code stays and gets no further work; whether to remove it
is a separate, open decision.

Its possible later role, the **text path** for numbers XBRL doesn't tag (employees,
stores, backlog), is not being worked on. If it is ever taken up, such a number is
labelled as a cited table cell, never as an audited fact.

## Coverage

Large S&P 100 filers across sectors, added in waves. **Out of scope** for now are
filers whose statements need their own accounting model: banks and brokers,
insurers and managed care, regulated utilities filing for several registrants,
and REITs.

## Principles (every change upholds these)

1. **Trust the numbers.** Only gated, validated data serves. A confident
   citation on a wrong number is the worst possible failure — data-integrity
   gates and the publish gate are non-negotiable.
2. **Doer ≠ judge.** Generated answers are checked by an independent verifier at
   runtime (faithfulness verifier) and dev-time (evaluator subagent, Stop-gate).
   A generator never grades its own output.
3. **Measured *and* capped.** Cost/latency/quality are fully instrumented **and**
   hard-capped — per-run cost ceiling + circuit breaker, provider rate limits,
   caches. Telemetry without a ceiling is not enough.
4. **Deterministic guardrails over good intentions.** Dev-time discipline is
   *enforced* by hooks (secret guard, auto-format, red-build Stop-gate), not left
   to the agent to remember.
5. **Scope is external and machine-checkable.** "Done" means a check passes
   (the feature manifest), not that someone said so. Assumptions that encode
   "the model can't do X" carry a re-verify date ([`docs/harness/REAUDIT.md`](docs/harness/REAUDIT.md)).
6. **Accuracy is measured, not asserted, and measured outside the loop.** Answer
   quality is graded against ground truth that does **not** come from the system
   under test (the SEC's own data, hand-checked tables and passages), on
   questions asked in many ways, with results persisted and inspectable down to
   the individual trace. Development tunes only on a dev set; a figure on that
   set is reported as in-loop, next to the held-out figure. "Numbers are audited,
   not generated" is a claim that must carry a held-out number behind it
   ([external test v1](docs/eval/external_test_v1/README.md) is why).

## Explicit non-goals (decline these against this doc)

- **The PDF-legacy SEC-10K path** (`src/preprocess/`, `src/ingest/pdfparse/`,
  the sec10k retriever) is **maintenance-only** — no new features. New work
  targets the XBRL flow.
- **Tuning on held-out data.** The held-out filers, the hand-written external sets
  and the post-freeze filings are never used to fix the system; only aggregates
  and failure-class counts come out of them.
- **Fixes aimed at a single test case.** A fix names its failure class and is
  measured on cases it was not written against.
- **General-purpose chat / open-domain Q&A.** XbrlRag answers over ingested filings;
  it is not a general assistant.
- **Proxy statements, 8-K earnings releases and non-GAAP reconciliations.** The corpus is
  periodic 10-K / 20-F (and later 10-Q) filings. A question about executive compensation
  or adjusted EBITDA gets a clean decline that names the reason — and that decline is
  correct behaviour, graded as such in the evaluation.
- **Real-time / streaming market data, trading, or investment advice.** It reads
  periodic filings, not live feeds, and offers no recommendations.
- **Un-cited generation.** No feature may surface a number without its source
  fact(s); "sounds right" is not an acceptable answer mode.
- **Bypassing the trust gates for speed/coverage.** More filings or lower latency
  never justify serving ungated or unverified data.
- **Silent scope expansion.** New verticals, providers, or data sources require a
  deliberate edit here + a manifest entry first, not an inline decision mid-task.

## Boundaries & assumptions

- The trust model is a **trusted network**, whether the stack runs locally or on
  AWS (Docker/Compose/Helm packaging, and dual-mode `XBRLRAG_DEPLOY_MODE` for
  S3/RDS/in-VPC Qdrant). **Auth / SSO / multi-tenancy are not built, by decision**, so
  every surface — BFF, `/api/v1`, MCP, `/metrics` — is un-authenticated and
  single-tenant. Front it with a gateway before any public exposure.
- LLM model tiers, pricing, and "the model can't do X" scaffolding are **dated
  assumptions** (`configs/llm/llm.yaml`, `configs/llm/pricing.yaml`); they are
  expected to be re-audited as models improve, not treated as permanent.
- **Providers:** OpenAI and Anthropic, and DeepSeek (its own API) as the low-cost
  development profile. Decisions rest on milestone runs with OpenAI and Anthropic
  models, never on development-model numbers alone. Prompts carry public filings
  and questions only; no secrets or private data go to any provider.

## Where "done" is defined

The feature manifest is the authoritative list of what is built and what is not; the
design docs in [`docs/design/`](docs/design/README.md) describe each subsystem as it is.
Accuracy is measured on separate sets ([`docs/design/XbrlRag_Evaluation.md`](docs/design/XbrlRag_Evaluation.md)):
a dev set the system is tuned on, a validation set, and held-out sets nobody tunes on
(drawn filers, hand-written questions, filings published after a freeze). The headline
comparison is XbrlRag against `frontier_sec_tools`, a frontier model with tools over the
SEC's public APIs: what the curation (concept mapping, scale and sign, dimensions,
deterministic plans, the verifier, `fact_id` provenance) adds over the SEC's raw facts plus
an agent. Its rules are pre-registered ([`docs/eval/heldout_v2/preregistration.md`](docs/eval/heldout_v2/preregistration.md))
before any held-out look. The v1 SQL-derived suite remains as an in-loop regression check. External/third-party
benchmarks (FinanceBench, FinLoRA) were **surveyed and deliberately not adopted**.
**Measured results live in the `eval` schema / `/tech/eval`** — a number written into a
document is a snapshot, the store is the record. Per-item completion is machine-checkable
via the feature manifest:

```bash
python scripts/harness/check_features.py          # status of every planned feature
python scripts/harness/check_features.py --gate   # CI/Stop gate: fail if a "done" feature isn't real
```

A feature is only `done` when its manifest check passes. Adding scope means
adding a manifest entry (which starts failing) before writing code.
