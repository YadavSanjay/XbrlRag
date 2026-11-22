"""C1-C2: the client sends RagInference the reranker name it accepts."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.remote_reranker import RemoteReranker

_URL = "http://svc:8799/rerank"
_JINA = "jinaai/jina-reranker-v3"
_BGE = "BAAI/bge-reranker-v2-m3"


class _Service:
    """``httpx.post``: the network boundary. Records each body and scores in order."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    def __call__(self, url: str, json: dict, timeout: float) -> _Service:
        self.bodies.append(json)
        return self

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"scores": [1.0 / (i + 1) for i in range(len(self.bodies[-1]["documents"]))]}


def _post_once(monkeypatch, model, query="revenue", documents=("a", "b")) -> tuple[dict, list]:
    service = _Service()
    monkeypatch.setattr("httpx.post", service)
    scores = RemoteReranker(url=_URL, model=model).rerank(query, list(documents))
    return service.bodies[0], scores


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("model, sent", [(_JINA, "jina"), (_BGE, "bge")])
def test_the_full_name_is_translated(monkeypatch, model, sent):
    # oracle: requirement 1 and the service's schema, which accepts 'jina' or 'bge'
    body, scores = _post_once(monkeypatch, model)
    assert body["model"] == sent
    assert body["query"] == "revenue"
    assert body["documents"] == ["a", "b"]
    # tolerance: exact, the fake's own values
    assert scores == pytest.approx([1.0, 0.5], abs=1e-12)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(
    query=st.text(min_size=1, max_size=40),
    documents=st.lists(st.text(max_size=40), min_size=1, max_size=5),
    pair=st.sampled_from([(_JINA, "jina"), (_BGE, "bge")]),
)
def test_any_payload_keeps_its_text(query, documents, pair):
    # oracle: requirement 1 — only the name is translated, and one score comes back per document
    service = _Service()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("httpx.post", service)
        scores = RemoteReranker(url=_URL, model=pair[0]).rerank(query, documents)
    assert service.bodies == [{"query": query, "documents": documents, "model": pair[1]}]
    assert len(scores) == len(documents)


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("name", ["jina", "bge", "not-a-reranker", "JINA"])
def test_other_names_go_as_given(monkeypatch, name):
    # oracle: requirements 2 and 4, a typo reaches the service and is refused loudly there; the
    # first line is the case the base code gets wrong
    body, _ = _post_once(monkeypatch, _JINA)
    assert body["model"] == "jina"
    body, _ = _post_once(monkeypatch, name)
    assert body["model"] == name


@pytest.mark.criterion("C2")
def test_no_model_means_no_field(monkeypatch):
    # oracle: requirement 3; the first line is the case the base code gets wrong
    monkeypatch.delenv("RAGINFER_RERANK_MODEL", raising=False)
    body, _ = _post_once(monkeypatch, _BGE)
    assert body["model"] == "bge"
    body, _ = _post_once(monkeypatch, None)
    assert "model" not in body


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("model", [_JINA, _BGE, "jina"])
def test_the_attribute_keeps_the_given_name(monkeypatch, model):
    # oracle: requirement 5; the last line is the case the base code gets wrong
    assert RemoteReranker(url=_URL, model=model).model == model
    body, _ = _post_once(monkeypatch, _JINA)
    assert body["model"] == "jina"


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(name=st.text(min_size=1, max_size=30).filter(lambda s: s.strip() and s not in (_JINA, _BGE)))
def test_any_other_name_is_sent_as_given(name):
    # oracle: requirements 2 and 4, the client does not guess; the second body is the base bug
    service = _Service()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("httpx.post", service)
        RemoteReranker(url=_URL, model=name).rerank("q", ["d"])
        RemoteReranker(url=_URL, model=_JINA).rerank("q", ["d"])
    assert [body["model"] for body in service.bodies] == [name, "jina"]
