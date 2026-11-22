# CLAUDE.md — `src/retriever/html10k/` (the HTML 10-K flow's query side)

The retrieval and answering of `html_rag`, the baseline XbrlRag has to beat
([`src/eval/systems/html_rag.py`](../../eval/systems/html_rag.py)). A weak baseline proves nothing,
so a change here that could weaken it needs a reason.

- **Spec:** [`docs/specs/html-rag-arm.md`](../../../docs/specs/html-rag-arm.md); its Notes list the
  choices that could weaken the baseline and what was done about each.
- **Operator guide:** [`docs/guides/html-flow/`](../../../docs/guides/html-flow/README.md).
- **Why it exists:** [v2 plan](../../../docs/design/XbrlRag_v2_Plan.md) §5.

## Flow

```
IntentRouter.route → search_scopes → Html10kRetriever.retrieve → one html_rag call → resolve_reply
                      (scopes.py)      (retriever.py, rerank_cache.py)                (answerer.py)
```

Settings are in [`configs/pipelines/html10k/rag.yaml`](../../../configs/pipelines/html10k/rag.yaml),
read by `config.py`. Every key is required, and no default sits in code. The values are provisional,
and each answer records them in `selection["retrieval_config"]`.

## Invariants

- **No XBRL reaches this flow** (plan §5.2). At query time the arm reads `entity_master` and
  `fiscal_calendar`, through the resolver, and the Qdrant collection `html10k_text`, nothing else.
  The load reads `filing_registry`, `entity_master` and `accession`. These modules import from
  `src.retriever.xbrl` only `intent_router`, `entity_extractor`, `branch_dispatcher` and
  `xbrl_calculator_tool`; nothing from `src.pipelines.xbrl`, `xbrlrag_full` or any `xbrl_*` module
  elsewhere; from `src.ingest.sec_edgar` only `storage`. `test/spec/html-rag-arm/test_c13_leakage.py`
  checks this.
- **Only the question reaches the search.** `case.ticker`, `fy`, `cik`, `kind` and `universe` are
  never read: that is what separates this arm from an oracle.
- **The arm doesn't refuse because the resolver can't choose.** It searches on what resolved and
  records why in `selection["degraded"]`, as XbrlRag does. An ambiguous company is never filtered
  on. No relaxation of the year filter: it would answer from the wrong year.
- **One search and one rerank per filer,** each keeping `context_top_k`, so one filer can't crowd
  out another.
- **The reranker sees `embedding_input`,** with filer, year and section, in candidate order. The
  order is part of the cache key because jina-reranker-v3 is listwise.
- **Failures are loud.** Embedder, Qdrant, reranker and the expansion fetch all raise. Unlike
  `_expand_with_neighbors`, nothing falls back to unexpanded chunks.
- **The model doesn't calculate.** Every number in `numeric_values` and `rows` comes from the
  calculator, over inputs that resolved to a number printed in a chunk given in this call. A
  `value` or `numeric` key is never read. `printed_value` is the one place scale and sign are
  applied; a nil is `None`, never 0, and a scale word printed with the number beats `scale`.
- **Excerpts are data.** `build_excerpts` is a closed set of fields, and the excerpts reach the model
  only as `json.dumps` in the user message. The excerpts come first in the user template, so the
  first `[` in the message opens their list whatever the question holds.
- **A loose citation is `hedged`, not refused.** Labels compare loosely. Only a number that isn't in
  the cited chunk at all blocks an answer, and there is no score floor.
- **One rerank at a time.** `CachedReranker` holds a lock across a miss, since the reranker is
  CPU-bound and `html_rag` reranks every case. A hit doesn't wait. The lock is per object, and the arm
  holds one.
- **`CachedReranker` isn't a `BaseReranker`,** and the default backends import lazily, so the module
  stays cheap to import and `build_system("html_rag")` stays offline: the cache file appears on
  first use, not when the arm is built.

## Limits to know

The primary document is the only source, so a filer that prints its statements in an exhibit
(IBM is one) has none here. Narrative results can't be scored for retrieval yet, because
text-key spans are in the canonical HTML and chunk offsets are in the stripped document. The spec's
Notes list the rest.
