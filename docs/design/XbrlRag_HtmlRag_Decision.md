# Decision: `html_rag` leaves the v2 comparison

A short decision record: why the HTML 10-K RAG baseline (`html_rag`) is no longer an arm of
the v2 evaluation, and what replaced it. Read it before proposing an HTML baseline again, or
when deciding what to do with the HTML 10-K code.

**Status:** decided 2026-11-06 by the maintainer, and put into effect by pre-registration
[Amendment 1](../eval/heldout_v2/preregistration.md) (D4). It supersedes an earlier plan to
rebuild `html_rag` as a full-planner ablation.

## Decision

- `html_rag` runs on no set, dev included. The bar line "XbrlRag beats `html_rag` on the
  single-filer numeric kinds" is withdrawn, and so is `html_rag`'s part in the H-X direction
  check (pre-registration §4).
- The v2 evaluation compares XbrlRag with **`frontier_sec_tools`**: a frontier model with
  tools over the SEC's public APIs, run on two models (Amendment 1, D5).
  `long_context_oracle` stays as the single-filer ceiling, and `closed_book` as the floor.
- The effort the rebuild would have taken goes into `frontier_sec_tools` instead: a fixed,
  documented tool set and prompt, budgets set by a stated rule (Amendment 1, D6), and a smoke
  and a dev slice before the arm is frozen, as for any arm.

## Why

1. **For tagged numbers, a database is the obvious design.** Nobody building over SEC XBRL
   data would answer numeric questions by retrieving passages of parsed HTML, so a win over
   such a pipeline, however carefully built, would show little.
2. **The real competitor is an agent over the SEC's own data.** `frontier_sec_tools` reads
   `companyfacts` and `frames`, which are XBRL facts too. The comparison therefore asks what
   XbrlRag adds over the SEC's raw fact API plus a frontier agent: concept mapping, scale and
   sign handling, dimensions, deterministic plans, the faithfulness verifier and `fact_id`
   provenance. That is a harder bar, and the right one.
3. **It removes the critical path.** Only `html_rag` reranked heavily: about 48 to 53 hours of
   CPU reranking for the held-out runs, plus loading the held-out filings into `html10k_text`.

## What the comparison reports

Each dimension is reported for XbrlRag and `frontier_sec_tools` on the same cases:

| Dimension | How it is read |
|---|---|
| Cross-filer, cross-period and compound kinds | Value accuracy, pooled and per kind (bar line 1) |
| Confident-wrong rate | [`arm-neutral-grading`](../specs/arm-neutral-grading.md): the same rule for every arm |
| Grounding precision | XbrlRag only; the agent cites no `fact_id`, and the report says so rather than leaving the cell blank |
| Cost per question | Mean, as the gateway records it (bar line 1's tenth-of-the-cost test) |
| Latency per question | p50 and p95 end to end, from the runner's per-case `latency_ms`, with each arm's service tier and concurrency stated |

The gap on simple single-filer lookups is expected to be small: v1's long-context arm reached
93.6% when handed the right filing. The case for XbrlRag rests on the rows above, not on
lookups.

## Consequences

- **Narrative questions have no RAG comparator.** XbrlRag answers narrative and hybrid
  questions by retrieval too; without `html_rag`, that retrieval is compared only with
  `long_context_oracle` and the agent. The results write-up states this gap.
- **The HTML 10-K code stays** (`src/ingest/html10k/`, `src/chunking/html10k_chunking.py`,
  `src/retriever/html10k/`, `src/pipelines/html10k/`, `src/eval/systems/html_rag.py`) but gets
  no further work for the evaluation; removing it is a separate, open decision. No held-out
  filings are loaded into `html10k_text`.
- **What was seen before the decision:** only `html_rag`'s in-loop pilots (51 cases on
  2026-11-01, and a 24-case candidates check). No arm had run on a held-out set. The decision
  rests on what the comparison should show, not on those scores.

## Related

- [v2 plan](XbrlRag_v2_Plan.md) · [Pre-registration](../eval/heldout_v2/preregistration.md)
- [HTML 10-K module notes](../../src/retriever/html10k/CLAUDE.md) ·
  [HTML flow guide](../guides/html-flow/README.md)
