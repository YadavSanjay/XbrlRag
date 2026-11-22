"""html_rag's wiring, outside the locked spec tests (docs/specs/html-rag-arm.md).

The load's CLI, the default backends' construction, the prompt's shape, what a provider refusal
leaves in the record, and malformed replies. Only I/O boundaries are faked.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from prompts.prompt_registry import get_xbrl_registry
from src.embeddings.base import DenseEmbedding, SparseEmbedding
from src.eval.core.case import EvalCase
from src.eval.systems.html_rag import HtmlRagSystem
from src.llm.gateway import LLMRefusalError
from src.pipelines.html10k import load_to_vectordb_pipeline as load
from src.retriever.html10k.answerer import printed_value, resolve_reply
from src.retriever.html10k.config import Html10kRagConfig, load_html10k_config
from src.retriever.html10k.retriever import RetrievedChunk
from src.retriever.xbrl.entity_extractor import ResolvedCompany, ResolvedPeriod
from src.retriever.xbrl.intent_router import XBRL_INTENT, IntentRouterResult

CASE = EvalCase(id="c", question="What was AAA net sales in 2024?", kind="lookup",
                expected_value=1.0, ticker="AAA", fy=2024)
PAYLOAD = {
    "chunk_id": "c1", "accession_no": "acc", "text": "Net sales were 1,200.", "cik": "1",
    "ticker": "AAA", "company_name": "Alpha", "fiscal_year": 2024, "form_type": "10-K",
    "section_key": "item_7", "section_label": "Item 7", "contains_table": False,
    "table_index": None, "part": 0, "parts": 1, "html_char_start": 0, "html_char_end": 10,
    "prev_chunk_id": None, "next_chunk_id": None,
}


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(**PAYLOAD, rerank_score=0.25, is_expansion=False)


class _Router:
    def route(self, _question: str) -> IntentRouterResult:
        return IntentRouterResult(
            intent=XBRL_INTENT.NUMERIC_LOOKUP,
            companies=[ResolvedCompany(mention="AAA", cik="1", resolution="exact_ticker")],
            periods=[ResolvedPeriod(mention="fiscal 2024", fiscal_year=2024)],
        )


class _Embedder:
    def embed_texts(self, id_texts: list[tuple[str, str]]) -> dict[str, tuple]:
        return {k: (DenseEmbedding([1.0]), SparseEmbedding([1], [1.0])) for k, _ in id_texts}


class _Qdrant:
    def __init__(self) -> None:
        self.collections: list[str] = []

    def query_points(self, **kwargs: Any) -> Any:
        self.collections.append(kwargs["collection_name"])
        return SimpleNamespace(points=[SimpleNamespace(payload=PAYLOAD, score=1.0)])


class _Reranker:
    def __init__(self) -> None:
        self.calls = 0

    def rerank(self, _query: str, documents: list[str]) -> list[float]:
        self.calls += 1
        return [0.25] * len(documents)


def _settings(tmp_path: Path, **overrides: Any) -> Any:
    """The committed settings, with the cache in a temporary directory."""
    base = load_html10k_config().model_dump()
    return Html10kRagConfig.model_validate(
        {**base, "rerank_cache_path": str(tmp_path / "rerank.sqlite3"), **overrides}
    )


def test_the_user_prompt_opens_nothing_before_the_excerpts():
    # The arm's excerpts are found at the message's first "[", so the question comes after them.
    template = get_xbrl_registry().resolve("html_rag").user.template
    assert template.index("{excerpts}") < template.index("{question}")
    assert "[" not in template[: template.index("{excerpts}")]


def test_a_built_arm_has_no_cache_file_and_backends_are_built_on_first_use(tmp_path):
    settings = _settings(tmp_path)
    HtmlRagSystem(router=_Router(), config=settings)
    assert not settings.rerank_cache_path.exists()


def test_the_default_backends(tmp_path, monkeypatch):
    # RagInference, the QDRANT_* client and the configured reranker model; none is contacted.
    monkeypatch.setenv("RAGINFER_URL", "http://127.0.0.1:9")
    settings = _settings(tmp_path)
    arm = HtmlRagSystem(router=_Router(), config=settings)
    retriever = arm._get_retriever()
    assert type(retriever._embedder).__name__ == "RemoteEmbedder"
    assert type(retriever._client).__name__ == "QdrantClient"
    inner = retriever._reranker._inner
    assert type(inner).__name__ == "RemoteReranker"
    assert inner.model == settings.reranker_model
    assert arm._get_retriever() is retriever


def test_a_provider_refusal_is_recorded(tmp_path, monkeypatch):
    # answer_in_trace turns it into a refused answer; the record still says what happened.
    arm = HtmlRagSystem(
        router=_Router(), embedder=_Embedder(), qdrant_client=_Qdrant(), reranker=_Reranker(),
        config=_settings(tmp_path),
    )

    def refuse(_user_text: str) -> str:
        raise LLMRefusalError("declined by the provider")

    monkeypatch.setattr(arm._llm, "complete", refuse)
    answer = arm.answer(CASE)
    assert answer.refused is True
    assert answer.error is None
    assert answer.selection["status"] == "model_declined"
    assert answer.selection["reason"]
    assert [c["chunk_id"] for c in answer.selection["retrieved"]] == ["c1"]


@pytest.mark.parametrize(
    "reply",
    [
        {"inputs": "a", "rows": "b", "passages": 7},
        {"inputs": [1, None], "rows": [], "passages": ["x"]},
        {"inputs": [{"id": "a", "chunk_id": ["c1"], "printed": "1,200", "scale": 1}]},
        {"value_expr": 5},
    ],
)
def test_a_malformed_reply_declines_and_never_raises(reply):
    outcome = resolve_reply(reply, [_chunk()])
    assert outcome.status == "unsupported"
    assert outcome.numeric_values == []


def test_a_non_string_row_is_dropped():
    outcome = resolve_reply(
        {"inputs": [{"id": "a", "chunk_id": "c1", "printed": "1,200", "scale": 1}],
         "value_expr": "a", "rows": ["x", {"entity": "e"}]},
        [_chunk()],
    )
    assert outcome.status == "answered"
    assert outcome.numeric_values == [1200.0]
    assert outcome.hedged is True


def test_a_number_written_as_a_json_number_still_resolves():
    outcome = resolve_reply(
        {"inputs": [{"id": "a", "chunk_id": "c1", "printed": 1200, "scale": 1}], "value_expr": "a"},
        [_chunk()],
    )
    assert outcome.numeric_values == [1200.0]


def test_a_result_that_overflows_declines():
    outcome = resolve_reply(
        {"inputs": [{"id": "a", "chunk_id": "c1", "printed": "1,200", "scale": 1}],
         "value_expr": "a * 1e308 * 1e308"},
        [_chunk()],
    )
    assert outcome.status == "unsupported"


def test_printed_value_of_an_absurd_number_is_none():
    assert printed_value("1" * 400, 1) is None


class _Pipeline:
    def __init__(self, outcome: dict[str, list]) -> None:
        self.outcome = outcome
        self.entries: list = []

    def run(self, entries: list) -> dict[str, list]:
        self.entries = list(entries)
        return self.outcome


class _Conn:
    closed = False

    def close(self) -> None:
        self.closed = True


def _wire_cli(monkeypatch, outcome: dict[str, list]) -> tuple[_Pipeline, _Conn, dict[str, Any]]:
    pipeline, conn, seen = _Pipeline(outcome), _Conn(), {}

    def listing(_conn: Any, _root: str, *, tickers: Any, fiscal_years: Any) -> list[str]:
        seen.update(tickers=tickers, fiscal_years=fiscal_years)
        return ["entry"]

    monkeypatch.setattr(load, "_connect_with_timeouts", lambda: conn)
    monkeypatch.setattr(load, "list_annual_filings", listing)
    monkeypatch.setattr(load, "_build_pipeline", lambda _config: pipeline)
    return pipeline, conn, seen


def test_the_cli_passes_its_scope(monkeypatch, capsys):
    pipeline, conn, seen = _wire_cli(monkeypatch, {"loaded": ["a"], "skipped": [], "failed": []})
    code = load.main(["--ticker", "AAA", "--ticker", "BBB", "--fiscal-year", "2024"])
    assert code == 0
    assert seen == {"tickers": ["AAA", "BBB"], "fiscal_years": [2024]}
    assert pipeline.entries == ["entry"]
    assert conn.closed
    assert '"loaded": 1' in capsys.readouterr().out


def test_the_cli_all_loads_everything(monkeypatch):
    _, _, seen = _wire_cli(monkeypatch, {"loaded": [], "skipped": [], "failed": []})
    assert load.main(["--all"]) == 0
    assert seen == {"tickers": None, "fiscal_years": None}


def test_the_cli_reports_a_failed_filing(monkeypatch, capsys):
    failed = {"accession_no": "a", "error": "boom"}
    _wire_cli(monkeypatch, {"loaded": [], "skipped": [], "failed": [failed]})
    assert load.main(["--all"]) == 1
    assert "boom" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [[], ["--all", "--ticker", "AAA"]])
def test_the_cli_needs_one_clear_scope(monkeypatch, argv):
    _wire_cli(monkeypatch, {"loaded": [], "skipped": [], "failed": []})
    with pytest.raises(SystemExit):
        load.main(argv)
