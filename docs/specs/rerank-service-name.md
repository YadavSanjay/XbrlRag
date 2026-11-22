# rerank-service-name — send RagInference the reranker name it accepts

`html_rag` builds `RemoteReranker(model=config.reranker_model)`, and the committed
`reranker_model` is `jinaai/jina-reranker-v3` ([html-rag-arm](html-rag-arm.md) requirement 1).
RagInference's `/rerank` takes only `jina` or `bge` and answers 422 to the full name, so every
`html_rag` case that reaches a rerank fails. The spec tests use a fake reranker and could not see
this. It showed in the first real read of `html10k_text`.

The full name stays in the config: it is the locked value and part of the rerank cache's key. The
client translates it at the boundary, because only the client knows what the service accepts.

## Requirement

1. **The client sends the service's name.** `RemoteReranker.rerank` posts `"jina"` when its model
   is `jinaai/jina-reranker-v3`, and `"bge"` when it is `BAAI/bge-reranker-v2-m3`.
2. **What the service already accepts is sent as it is.** `jina` and `bge` are unchanged.
3. **No model, no field.** With no model, the payload has no `model` key, as today, so the service
   uses the one it has loaded.
4. **An unknown name is sent as it is.** The client does not guess. The service refuses it with a
   422, which raises, so a typo is loud.
5. **`RemoteReranker.model` keeps the name it was given.** Only the payload changes, and nothing
   else in the client: the URL, the timeout, the score parsing, the fallback and the errors.
6. **Nothing else changes.** Not `configs/pipelines/html10k/rag.yaml`, not `reranker_model`, and
   not the rerank cache key, so a score cached under the full name stays valid.

**In scope:** requirements 1-6. **Out of scope:** any change to RagInference; the bge side-by-side
run; any re-run.

## Acceptance criteria

Bug-fix change: no new function and no stub. Every test fails on the code at the base of this
branch with an assertion. Each test holds a case the base code gets wrong, with the cases it must
keep getting right beside it.

- **C1**: The posted `model` is `jina` for `jinaai/jina-reranker-v3` and `bge` for
  `BAAI/bge-reranker-v2-m3`, the payload's `query` and `documents` are unchanged, and scores come
  back aligned. Property: for any query and documents, the same. Oracle: requirement 1 and the
  service's published request schema (`jina` or `bge`).
- **C2**: `jina` and `bge` are sent unchanged, an unknown name is sent unchanged, no model means no
  `model` key, and `RemoteReranker.model` keeps the given name. Property: for any other name, it is
  sent as given. Oracle: requirements 2-5.

## Interface

No change: `src.retriever.remote_reranker.RemoteReranker(url, timeout_s, model).rerank(query,
chunks_with_text) -> list[float]`.

## Notes

- Evidence, 2026-11-01, against the running service: no model 200, `jina` 200, and
  `jinaai/jina-reranker-v3` 422 with `unknown reranker 'jinaai/jina-reranker-v3' (expected 'jina' or
  'bge')`. All three name the same loaded model in the 200 replies.
- `httpx.post` is the fake here: it is the network boundary. The client is real.
- The html-rag-arm note "`GET /healthz` names `jinaai/jina-reranker-v3`, which is what
  `reranker_model` sends" stays true of the name the service reports, not of the name it accepts.
- The bge name is the one `src/retriever/bge_reranker.py` loads.
- A retrieval check with this fault worked around found the right filer and year on three real
  questions, so no other fault blocks the pilot on that path.
