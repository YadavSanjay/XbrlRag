"""Wiring for the baseline arms that the locked spec tests don't reach.

docs/specs/baseline-arms.md: the Qdrant ticker filter (the spec tests fake
``hybrid_query_text`` itself), the arms' prompt templates, and ``run_suite.py
--system``. Fakes only at the Qdrant boundary; no network, DB or LLM.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from qdrant_client.models import FieldCondition, Filter, MatchAny

from prompts.prompt_registry import get_xbrl_registry
from src.embeddings.base import DenseEmbedding, SparseEmbedding
from src.eval.systems import system_names
from src.vectorstores.xbrl_qdrant_vectordb_client import XBRLQdrantVectorDBClient

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval" / "run_suite.py"


# --------------------------------------------------------------------------------
# hybrid_query_text: the ticker filter
# --------------------------------------------------------------------------------


class _RecordingQdrant:
    """The raw qdrant_client boundary: records query_points kwargs."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def query_points(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(points=[])


def _client() -> tuple[XBRLQdrantVectorDBClient, _RecordingQdrant]:
    # Skip __init__ (it builds a real QdrantClient); hybrid_query_text only uses .client.
    client = XBRLQdrantVectorDBClient.__new__(XBRLQdrantVectorDBClient)
    raw = _RecordingQdrant()
    client.client = raw
    return client, raw


def _query(**filters) -> Filter | None:
    client, raw = _client()
    client.hybrid_query_text(
        collection_name="xbrl_sec10k_text",
        vector_d=DenseEmbedding(values=[0.1, 0.2]),
        vector_s=SparseEmbedding(indices=[1], values=[0.5]),
        limit=10,
        **filters,
    )
    prefetch = raw.calls[0]["prefetch"]
    # Both prefetches (sparse and dense) carry the same filter.
    assert prefetch[0].filter == prefetch[1].filter
    return prefetch[0].filter


def test_ticker_filter_is_match_any_on_ticker():
    flt = _query(tickers_in_query=["AAPL", "MSFT"])
    assert flt is not None
    assert flt.must == [FieldCondition(key="ticker", match=MatchAny(any=["AAPL", "MSFT"]))]


def test_ticker_and_year_filters_are_anded():
    flt = _query(tickers_in_query=["AAPL"], fiscal_years_in_query=[2024])
    assert flt is not None
    assert FieldCondition(key="ticker", match=MatchAny(any=["AAPL"])) in flt.must
    assert FieldCondition(key="fiscal_year", match=MatchAny(any=[2024])) in flt.must
    assert len(flt.must) == 2
    assert not flt.should


@pytest.mark.parametrize("tickers", [None, []])
def test_no_ticker_means_no_ticker_condition(tickers):
    assert _query(tickers_in_query=tickers) is None


# --------------------------------------------------------------------------------
# Prompt templates
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stage, variables",
    [("closed_book", {"question"}), ("naive_rag", {"question", "excerpts"})],
)
def test_templates_take_exactly_the_arm_arguments(stage, variables):
    prompt = get_xbrl_registry().resolve(stage)
    assert prompt.user.variables == variables
    assert prompt.system.variables == set()
    # The system prompt formats with no arguments (its JSON braces are escaped) and
    # then shows the answer object's keys.
    system = prompt.system.format()
    for key in ('"value"', '"unit"', '"rows"', '"refused"', '"hedged"', '"cited_chunks"'):
        assert key in system
    assert "{{" not in system


def test_naive_rag_user_template_keeps_excerpts_out_of_the_system_prompt():
    prompt = get_xbrl_registry().resolve("naive_rag")
    assert "{excerpts}" in prompt.user.template
    assert "{excerpts}" not in prompt.system.template


# --------------------------------------------------------------------------------
# run_suite.py --system
# --------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def run_suite():
    spec = importlib.util.spec_from_file_location("run_suite_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_suite_under_test"] = module
    spec.loader.exec_module(module)
    return module


def test_run_suite_rejects_an_unknown_system(run_suite):
    with pytest.raises(SystemExit):
        run_suite.main(["--system", "naive_rag", "--no-store"])


@pytest.mark.parametrize("name", ["closed_book", "naive_rag_plain", "naive_rag_filtered"])
def test_run_suite_runs_the_selected_system(run_suite, tmp_path, monkeypatch, name):
    # The run budget allows no LLM call, so the arm halts before any provider or
    # retrieval traffic... except naive RAG, whose retrieval runs first; point it at a
    # closed port so it fails fast and becomes an error answer.
    monkeypatch.setenv("XBRLRAG_RUN_MAX_LLM_CALLS", "0")
    monkeypatch.setenv("QDRANT_HOST", "127.0.0.1")
    monkeypatch.setenv("QDRANT_PORT", "9")
    monkeypatch.setenv("RAGINFER_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("RAGINFER_FALLBACK_LOCAL", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-unit-offline")
    suite = tmp_path / "suite.json"
    suite.write_text(
        json.dumps(
            {
                "name": "unit",
                "version": "v1",
                "cases": [{"id": "c1", "question": "What was AAA revenue in 2024?",
                           "expected_value": 1.0, "ticker": "AAA", "fy": 2024}],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "report.json"

    rc = run_suite.main(
        ["--suite-path", str(suite), "--system", name, "--no-store", "--workers", "1",
         "--out", str(out)]
    )

    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["system"] == name


def test_system_names_are_the_run_suite_choices(run_suite):
    for name in system_names():
        # argparse accepts every name (an unknown one exits; see above)
        parser_ok = True
        # frontier_sec_tools needs its as-of date (docs/specs/frontier-sec-tools.md, 29).
        extra = ["--sec-as-of", "2026-10-29"] if name == "frontier_sec_tools" else []
        try:
            run_suite.main(["--system", name, "--suite-path", "missing.json", "--no-store",
                            *extra])
        except FileNotFoundError:
            pass  # got past argument parsing to the suite load
        except SystemExit:
            parser_ok = False
        assert parser_ok, name
