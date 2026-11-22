# XbrlRag design docs

How the **XBRL flow** works today and why it is built that way. Start with the
[architecture](XbrlRag_Architecture.md); each other doc goes one level deeper into a single
subsystem. To *operate* the system (install, ingest, run, troubleshoot), use the
[guides](../guides/README.md) instead. Live feature status comes from the manifest gate
(`python scripts/harness/check_features.py`), and measured accuracy from the `eval` schema
(rendered at `/tech/eval`), not from these documents.

## Suggested reading order

1. [Architecture](XbrlRag_Architecture.md): the whole system on one page.
2. [Fact-store data model](XbrlRag_Materialized_Views.md) and
   [HTML canonical source](XbrlRag_HTML_Canonical_Source.md): where the numbers and the
   prose come from.
3. [LLM harness](XbrlRag_LLM_Harness.md) and [observability](XbrlRag_Observability_Tracing.md):
   how every model call is controlled, costed and traced.
4. [Evaluation](XbrlRag_Evaluation.md), then [Evaluation v2 architecture](XbrlRag_Eval_v2_Architecture.md):
   how accuracy is measured.

## System design

| Doc | Covers |
|---|---|
| [Architecture](XbrlRag_Architecture.md) | The hybrid fact-store + narrative design, ingest order, the query pipeline, serving surfaces, what is not built |
| [HTML canonical source](XbrlRag_HTML_Canonical_Source.md) | iXBRL HTML as the single address space: canonicalization, section tree, fact ↔ prose joins, chunking invariants |
| [Fact-store data model](XbrlRag_Materialized_Views.md) | What Arelle's xbrlDB populates, the Layer-0 tables, the 19 materialized views, data invariants, the publish gate, IFRS |
| [LLM harness](XbrlRag_LLM_Harness.md) | Provider-neutral gateway, profiles and tiers, prompt registry, model escalation, cost controls |
| [Observability & tracing](XbrlRag_Observability_Tracing.md) | Trace model, event kinds, the `audit` schema, metrics export |
| [UI design](XbrlRag_UI_Design.md) | Personas, information architecture, shared components |
| [Local / AWS deployment](XbrlRag_AWS_Deployment.md) | The dual-mode seams, images, AWS topology, ingest on AWS |

## Evaluation and testing

| Doc | Covers |
|---|---|
| [Evaluation](XbrlRag_Evaluation.md) | The v1 SQL-derived suite, graders and metrics, the rules learned from the accuracy work, how v1 relates to v2 |
| [Evaluation v2 architecture](XbrlRag_Eval_v2_Architecture.md) | The v2 evaluation in diagrams: question sources, independent answer keys, who sees which set, a run end to end, grading, components |
| [Evaluation semantics](XbrlRag_Eval_Semantics.md) | Rules S1–S6 for what a question means: restatements, fiscal periods across filers, universe disclosure, ratio definitions, comparable concepts, units |
| [Test strategy](XbrlRag_Test_Strategy.md) | How the tests prove the XBRL depth: independent oracles, the nuance matrix, test types and layout, the gates |

## Plan

| Doc | Covers |
|---|---|
| [v2 plan](XbrlRag_v2_Plan.md) | What v2 sets out to show: XbrlRag against a frontier agent with SEC tools, the question sets and who sees them, the success bar, phases and where they stand |

## Decision records and measurements

Dated documents. They record a decision or a measurement as of their date and are not
rewritten as the system moves on.

| Doc | Covers |
|---|---|
| [Concept decisions](XbrlRag_Concept_Decisions.md) | 2026-11-07 to 11-11: the 52 decisions (D1–D52) that resolved the concept-coverage gaps: same measure, stated substitute or different metric |
| [`html_rag` leaves the v2 comparison](XbrlRag_HtmlRag_Decision.md) | 2026-11-06: the evaluation compares XbrlRag with `frontier_sec_tools`, not with a RAG pipeline over HTML; what it reports and what it gives up |
| [Dev baseline, 2026-11-10](XbrlRag_Dev_Baseline_2026-11.md) | The first paid baseline on dev and validation, against the frontier agent, long context, naive RAG and closed book. Dev only, never evidence for the bar |
| [Accuracy plan, Oct 2026](XbrlRag_Accuracy_Plan_Oct2026.md) | Pointer to the closed v1 accuracy work and the rules it left |

## Related

- [Guides](../guides/README.md): operating the system.
- [Harness](../harness/README.md): the dev-time guards, feature manifest and subagents.
- [Evaluation records](../eval/README.md): preregistrations, sealed hashes, calibration data.
- Change-level specs written test-first live in [`docs/specs/`](../specs/); each records one
  change and is not kept current after the change merges.
- Module invariants live beside the code, in the module `CLAUDE.md` files
  ([query pipeline](../../src/retriever/xbrl/CLAUDE.md), [ingest](../../src/ingest/sec_edgar/CLAUDE.md),
  [API](../../src/api/CLAUDE.md), [MCP server](../../src/mcp_server/CLAUDE.md)).
