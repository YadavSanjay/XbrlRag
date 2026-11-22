# html-rag-fusion-order — the same search gives the same candidates, in the same order

`html_rag` asks Qdrant for the fused top `rerank_candidates` and passes them to the reranker in the
order Qdrant returns them ([html-rag-arm](html-rag-arm.md) requirements 8-9). Qdrant orders points
with equal fused scores arbitrarily, so the same question can send the reranker a different list.
The rerank cache's key is that list, in order, so a reordered list misses the cache, costing a CPU
rerank of about 130 s. `jina-reranker-v3` reads the candidates together, so the order can also change
the scores. A tie at the cut can change which candidates are sent at all.

## Requirement

1. **Ask for every fused candidate.** The hybrid search request is html-rag-arm requirement 8's,
   except `limit=2 * prefetch_limit`. Fusion joins two lists of at most `prefetch_limit` points, so
   no fused candidate is left out.
2. **Order them ourselves.** The returned points sort by Qdrant's fused `score`, highest first;
   points with equal scores order by their payload's `chunk_id`, ascending as strings. The first
   `rerank_candidates` of that order are the candidates.
3. **The order Qdrant returns points in does not matter.** Any permutation of the same points gives
   the same candidates, in the same order.
4. **Nothing else changes.** The prefetches, filters, fusion and its weights stay Qdrant's and stay
   as configured. Requirements 9-12 of html-rag-arm apply to the candidates of requirement 2.

**In scope:** requirements 1-4. **Out of scope:** the fusion formula; ties inside a prefetch list
at `prefetch_limit` (none measured); the reranker's own determinism; refilling the rerank cache.

## Acceptance criteria

No new function and no stub. Every test fails on the code at the base of this branch with an
assertion.

- **C1**: The search asks for `2 * prefetch_limit` points, and the reranker receives the returned
  points sorted by score descending then `chunk_id` ascending, cut to `rerank_candidates`, whatever
  order Qdrant returned them in. Property: for any scores (ties likely) and any permutation of the
  response. Oracle: requirements 1-3, the order computed by hand or by a plain sort in the test.

## Interface

No change: `src.retriever.html10k.retriever.Html10kRetriever.retrieve(query, scopes) ->
Html10kRetrieval`.

## Notes

- Evidence, 2026-11-02, the 51 `html_rag` pilot cases, first scope each, 5 identical repeats, local
  Qdrant 1.18.2, committed settings (`rerank_candidates` 50). In-loop, no LLM.

  | Over 5 repeats | Cases, of 51 |
  |---|---|
  | Dense prefetch list differs | 0 |
  | Sparse prefetch list differs | 0 |
  | Fused top 50 differs as a set | 0 |
  | Fused top 50 differs in order | 25 |
  | Fused top 50 holds a tied score | 27 |
  | A tie straddles the cut at 50 | 0 |

  At 30 candidates, the set differed in 1 of 51 cases. So the variation is in fusion's tie order,
  and the prefetch lists are stable.
- The tie-break key has no meaning beyond being fixed; `chunk_id` is unique and in every payload.
- Asking for up to 200 points instead of 50 returns more payloads; the cost is local and small
  next to the rerank.
- After this change the candidate lists differ from those cached, so each question misses the
  rerank cache once.
- html-rag-arm requirement 8's `limit` and its retired test, and the retired html-rag-candidates
  tests whose fake has no scores, are updated in this spec's PR to state the current behaviour.
- The retriever's collaborators are fakes at their I/O boundaries: Qdrant, the embedder and the
  reranker. The committed settings file is real.
