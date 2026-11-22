# html-rag-candidates — send the reranker 50 candidates, not 30

`html_rag` keeps the fused top 30 and reranks them ([html-rag-arm](html-rag-arm.md) requirement 1).
In the first pilot, numbers the answer needed were indexed and inside the top 100 of each search,
but below the fused cut, so the reranker never saw them. The model then declined. That is a limit
in the baseline, not in the filings, and it would be charged to `html_rag`.

Once a needed table reaches the reranker it usually ranks in the first few, so the cut is the part
that fails. This change moves the cut, and nothing else.

## Requirement

1. **The committed value is 50.** `configs/pipelines/html10k/rag.yaml` holds
   `rerank_candidates: 50`.
2. **Every other setting stays.** All other keys in requirement 1 of
   [html-rag-arm](html-rag-arm.md) keep their values, including `prefetch_limit` 100 and
   `context_top_k` 20.
3. **The retriever uses it.** A fused candidate at any position up to 50 reaches the reranker, and
   when it scores highest it is the first chunk returned.
4. **Nothing else changes.** Not the load, the chunks, the code, the prompt or the rerank cache's
   format. A run records the new value in `selection["retrieval_config"]`, as it does today.

**In scope:** requirements 1-4. **Out of scope:** why identical searches can order tied candidates
differently; the cases that still decline at 50; any reload; any tuning on a held-out set.

## Acceptance criteria

Settings change: no new function and no stub. Every test fails on the code at the base of this
branch with an assertion.

- **C1**: The committed settings equal requirement 1 of html-rag-arm with `rerank_candidates` 50,
  and a candidate at any fused position from 31 to 50 that scores highest is returned first.
  Property: for any such position. Oracle: requirements 1-3, and the funnel in html-rag-arm
  requirement 7.

## Interface

No change: `src.retriever.html10k.config.load_html10k_config(path=None) -> Html10kRagConfig` and
`src.retriever.html10k.retriever.Html10kRetriever.retrieve(query, scopes) -> Html10kRetrieval`.

## Notes

- Evidence, 2026-11-01, `html_rag` on `deepseek-flash` over the 51 pilot cases, in-loop, on the
  21 loaded filings. These are not baseline numbers. A needed number is one a table chunk prints.
  That is optimistic for short numbers.

  | Fused depth | Needed numbers, of 96 | Cases with every number, of 51 |
  |---|---|---|
  | 30 | 77 | 39 |
  | 50 | 92 | 48 |
  | 100 | 96 | 51 |

- Through the real reranker, in the 20 chunks kept, on the 12 cases that lost a number at the cut
  and 12 seeded controls that didn't:

  | Group | Numbers kept at 30 | At 50 | Value right at 30 | At 50 |
  |---|---|---|---|---|
  | Affected | 4 of 23 | 18 of 23 | 1 of 12 | 8 of 12 |
  | Controls | 16 of 16 | 16 of 16 | 11 of 12 | 11 of 12 |

- The value-right columns compare the pilot with a later run on the same cases, and that run also
  has the 8,000-token budget of [stage-llm-output-cap](stage-llm-output-cap.md). The control that
  was wrong both times was an `ERROR` at the old budget and answers wrongly now, so that budget, not
  the candidates, is the likely change there. Two controls hit a connection timeout in one 30-second
  window and were rerun.
- 50 is the depth where the gain flattens: 40 holds 88 numbers, 50 holds 92 and 60 holds 93. No
  wider value was tested through the reranker. It is provisional, like the rest of the file.
- Cost: rerank time grows with the candidates, about 130 s a search against 75 s on CPU, for a
  search the cache doesn't hold. LLM cost moves only with what the reranker keeps: `context_top_k`
  stays 20. The change to the cache's key is the candidate list itself, so earlier entries for a
  question simply miss.
- Requirement 1's table in html-rag-arm and its retired test are updated in this spec's PR, so that
  they state the current value.
- The retriever's collaborators are fakes at their I/O boundaries: Qdrant, the embedder and the
  reranker. The committed file is real.
