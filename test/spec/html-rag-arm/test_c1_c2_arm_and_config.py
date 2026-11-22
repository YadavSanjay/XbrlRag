"""C1 and C2: the arm's name, model tier and prompt; the settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from prompts.prompt_registry import get_xbrl_registry
from src import PROJECT_ROOT
from src.eval.core.system import SystemUnderTest
from src.eval.systems import build_system, system_names
from src.eval.systems.html_rag import HtmlRagSystem
from src.llm.gateway import resolve_model_ref
from src.llm.stage_models import resolve_stage
from src.retriever.html10k.config import load_html10k_config

EXISTING = (
    "xbrlrag_full",
    "closed_book",
    "naive_rag_plain",
    "naive_rag_filtered",
    "long_context_oracle",
    "long_context_resolved",
    "long_context_multi",
)
ROOT = Path(PROJECT_ROOT)
CACHE_FILE = ROOT.joinpath("data", "cache", "html10k_rerank.sqlite3")


def _build(name: str, **kwargs):
    """build_system, with today's "unknown name" reported as this spec's failure."""
    try:
        return build_system(name, **kwargs)
    except ValueError as exc:
        pytest.fail(f"build_system({name!r}) refused: {exc}")


@pytest.mark.criterion("C1")
def test_html_rag_follows_the_existing_names():
    # oracle: requirement 25 — it joins after the existing names; no exact tuple, so that
    # frontier_sec_tools can add its own
    names = system_names()
    assert names[: len(EXISTING)] == EXISTING
    assert "html_rag" in names[len(EXISTING) :]


@pytest.mark.criterion("C1")
def test_it_builds_offline_under_its_name(offline_env):
    # oracle: requirement 25 — nothing is contacted at build time, every port here is closed
    arm = _build("html_rag")
    assert isinstance(arm, HtmlRagSystem)
    assert isinstance(arm, SystemUnderTest)
    assert arm.name == "html_rag"


@pytest.mark.criterion("C1")
def test_it_takes_no_model(offline_env):
    # oracle: requirement 25 — the tier is XbrlRag's, so a run can't compare models by accident
    assert _build("html_rag").name == "html_rag"
    with pytest.raises(ValueError):
        build_system("html_rag", model="gpt-5")


@pytest.mark.criterion("C1")
def test_the_stage_is_prompt_v1_with_the_two_placeholders(offline_env):
    # oracle: requirement 16 and the Interface note on the template; the arm is built first
    # because the stage does not exist until then
    arm = HtmlRagSystem()
    ref = resolve_model_ref(arm.model)
    prompt = get_xbrl_registry().resolve("html_rag", provider=ref.provider, series=ref.series)
    assert prompt.version == "v1"
    user = prompt.user.format(question="QMARK", excerpts="EMARK")
    assert "QMARK" in user and "EMARK" in user
    assert prompt.system.format()


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@pytest.mark.parametrize("profile", ["openai", "anthropic"])
def test_the_model_follows_the_synthesizer_on_each_profile(offline_env, monkeypatch, profile):
    # oracle: requirement 25 and plan 3.6 — the same model as the synthesizer under each
    # profile; resolve_stage reads the profile when called
    monkeypatch.setenv("XBRLRAG_LLM_PROFILE", profile)
    assert HtmlRagSystem().model == resolve_stage("synthesizer").model


@pytest.mark.criterion("C2")
def test_the_committed_file(monkeypatch):
    # oracle: requirement 1's table, by hand
    monkeypatch.delenv("XBRLRAG_HTML10K_RERANK_CACHE", raising=False)
    config = load_html10k_config()
    assert config.rrf_k == 2
    # tolerance: exact literals, approx only because the rubric asks for it on floats
    assert config.dense_weight == pytest.approx(2.0, abs=1e-12)
    assert config.sparse_weight == pytest.approx(1.0, abs=1e-12)
    assert config.expansion_score_threshold == pytest.approx(0.5, abs=1e-12)
    assert config.prefetch_limit == 100
    assert config.rerank_candidates == 50  # html-rag-candidates changed it from 30
    assert config.context_top_k == 20
    assert config.reranker_model == "jinaai/jina-reranker-v3"
    assert config.expand_neighbors is True
    assert config.expansion_max_chunks == 20
    assert config.max_filer_searches == 10
    assert config.table_max_chars == 1200
    assert config.embed_batch_size == 32
    assert config.rerank_cache_path == CACHE_FILE


@pytest.mark.criterion("C2")
def test_a_mistyped_key_raises(make_config):
    # oracle: requirement 1 and the repo's rule — a mistyped key raises, it doesn't default
    with pytest.raises(ValueError):
        make_config(rrf_kk=2)


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(
    "key",
    ["rrf_k", "dense_weight", "sparse_weight", "prefetch_limit", "rerank_candidates",
     "context_top_k", "reranker_model", "expand_neighbors", "expansion_score_threshold",
     "expansion_max_chunks", "max_filer_searches", "table_max_chars", "embed_batch_size",
     "rerank_cache_path"],
)
def test_a_missing_key_raises(write_settings, default_settings, tmp_path, key):
    # oracle: requirement 1 — no default for any of them sits in code
    settings = {**default_settings, "rerank_cache_path": str(tmp_path / "c.sqlite3")}
    del settings[key]
    with pytest.raises(ValueError):
        load_html10k_config(write_settings(settings))


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(
    "overrides",
    [
        {"rrf_k": 0},
        {"dense_weight": 0},
        {"sparse_weight": -1},
        {"prefetch_limit": 0},
        {"rerank_candidates": 0},
        {"context_top_k": 0},
        {"max_filer_searches": 0},
        {"table_max_chars": 0},
        {"embed_batch_size": 0},
        {"expansion_max_chunks": -1},
        {"rerank_candidates": 101},
        {"context_top_k": 31},
    ],
)
def test_a_value_out_of_range_raises(make_config, overrides):
    # oracle: requirement 2, each bound by hand against the defaults of requirement 1
    with pytest.raises(ValueError):
        make_config(**overrides)


@pytest.mark.criterion("C2")
def test_the_cache_path_expands(write_settings, default_settings, monkeypatch, tmp_path):
    # oracle: requirement 3 — the repo's ${VAR:-default}; relative paths sit under PROJECT_ROOT
    settings = {**default_settings, "rerank_cache_path": "${HTML10K_SPEC_VAR:-cache/here.db}"}
    monkeypatch.delenv("HTML10K_SPEC_VAR", raising=False)
    fallback = load_html10k_config(write_settings(settings)).rerank_cache_path
    assert isinstance(fallback, Path)
    assert fallback == ROOT / "cache" / "here.db"
    monkeypatch.setenv("HTML10K_SPEC_VAR", str(tmp_path / "set.db"))
    assert load_html10k_config(write_settings(settings)).rerank_cache_path == tmp_path / "set.db"


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@pytest.mark.parametrize(
    "key, value",
    [("rrf_k", 60), ("dense_weight", 3.0), ("sparse_weight", 0.5), ("prefetch_limit", 50),
     ("rerank_candidates", 25), ("context_top_k", 5), ("reranker_model", "BAAI/bge-reranker-v2-m3"),
     ("expand_neighbors", False), ("expansion_score_threshold", 0.25),
     ("expansion_max_chunks", 3), ("max_filer_searches", 4), ("table_max_chars", 2000),
     ("embed_batch_size", 8)],
)
def test_changing_one_key_changes_that_attribute_alone(
    write_settings, default_settings, tmp_path, key, value
):
    # oracle: requirement 1 — each key is its own setting
    settings = {**default_settings, "rerank_cache_path": str(tmp_path / "c.sqlite3")}
    base = load_html10k_config(write_settings(settings))
    changed = load_html10k_config(write_settings({**settings, key: value}))
    assert getattr(changed, key) == value
    for other in settings:
        if other != key:
            assert getattr(changed, other) == getattr(base, other)
