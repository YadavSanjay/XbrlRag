"""C1: the arm's new name, the two new names, and the old name's rows."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from prompts.prompt_registry import get_xbrl_registry
from src.eval.core.system import SystemUnderTest
from src.eval.systems import build_system, stored_system_names, system_names
from src.eval.systems.long_context import LongContextSystem
from src.eval.systems.long_context_arms import LongContextMultiSystem, LongContextResolvedSystem
from src.llm.config import MODEL_PRICING
from src.llm.gateway import resolve_model_ref

NAMES = (
    "xbrlrag_full",
    "closed_book",
    "naive_rag_plain",
    "naive_rag_filtered",
    "long_context_oracle",
    "long_context_resolved",
    "long_context_multi",
)
LONG_CONTEXT = ("long_context_oracle", "long_context_resolved", "long_context_multi")
OTHER_MODEL = "gpt-5"
_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "eval" / "run_suite.py"


def _build(name: str, **kwargs):
    """build_system, with today's "unknown name" reported as this spec's failure."""
    try:
        return build_system(name, **kwargs)
    except ValueError as exc:
        pytest.fail(f"build_system({name!r}) refused: {exc}")


@pytest.mark.criterion("C1")
def test_system_names_exact():
    # oracle: requirement 17's literal tuple; html-rag-arm and frontier-sec-tools add names after
    # these, and this holds on every side.
    assert system_names()[: len(NAMES)] == NAMES


@pytest.mark.criterion("C1")
def test_new_arms_build_offline_under_their_names(offline_env):
    # oracle: requirement 17 — every backend is a closed port, so a build that reached one
    # would fail; answer() is never called
    resolved = _build("long_context_resolved")
    multi = _build("long_context_multi")
    assert isinstance(resolved, SystemUnderTest)
    assert isinstance(multi, SystemUnderTest)
    assert isinstance(resolved, LongContextResolvedSystem)
    assert isinstance(multi, LongContextMultiSystem)
    assert resolved.name == "long_context_resolved"
    assert multi.name == "long_context_multi"
    # oracle: requirement 15's default was the literal gpt-5.6-sol; arm-model-from-profile
    # makes it the active profile's frontier model and pins that in its own C1. On either
    # side both arms default to the same priced model.
    assert resolved.model in MODEL_PRICING
    assert multi.model == resolved.model


@pytest.mark.criterion("C1")
def test_oracle_arm_is_renamed_and_otherwise_the_same(offline_env):
    # oracle: requirement 17 — the name changes; the class, stage and prompt version do not
    system = _build("long_context_oracle")
    assert isinstance(system, LongContextSystem)
    assert system.name == "long_context_oracle"
    assert LongContextSystem().name == "long_context_oracle"
    ref = resolve_model_ref(system.model)
    resolved = get_xbrl_registry().resolve("long_context", provider=ref.provider, series=ref.series)
    assert resolved.version == "v2"


@pytest.mark.criterion("C1")
def test_old_name_still_builds_and_stores_under_the_new_one(offline_env):
    # oracle: requirement 17 — an old command runs, and no new row carries the old name
    assert "long_context" not in system_names()
    for system in (build_system("long_context"), build_system("long_context", model=OTHER_MODEL)):
        assert system.name == "long_context_oracle"


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("name", ["long_context_oracle", "long_context"])
def test_stored_names_of_the_oracle_arm(name):
    # oracle: requirement 17 — six persisted rows carry the old name (read 2026-10-30)
    assert stored_system_names(name) == ("long_context_oracle", "long_context")


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(
    "name", [n for n in NAMES if n != "long_context_oracle"]
)
def test_stored_names_of_the_rest(name):
    # oracle: requirement 17 — no other arm was ever stored under another name
    assert stored_system_names(name) == (name,)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_rerun_failed_last_finds_a_run_stored_under_the_old_name(monkeypatch):
    # oracle: requirement 17 — the query's system filter covers both names, and the rows the
    # fake database holds carry the old one. psycopg2.connect is the eval store's boundary.
    runs = [("eval-000000000001", "s", "long_context")]

    class Cursor:
        description = None
        rows: list = []

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def execute(self, sql, params):
            if "SELECT run_id" in sql:
                suite, system = params
                names = [system] if isinstance(system, str) else list(system)
                self.description = [("run_id",)]
                self.rows = [(r[0],) for r in runs if r[1] == suite and r[2] in names]
            else:
                self.description = [("case_id",)]
                self.rows = [("c9",)]

        def fetchall(self):
            return self.rows

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def cursor(self):
            return Cursor()

        def close(self):
            return None

    monkeypatch.setattr("psycopg2.connect", lambda **_kwargs: Connection())

    spec = importlib.util.spec_from_file_location("run_suite_lca_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_suite_lca_under_test"] = module
    spec.loader.exec_module(module)

    wanted = module._failed_case_ids("last", "s", "long_context_oracle", None)
    assert wanted == {"c9"}


@pytest.mark.criterion("C1")
@given(name=st.sampled_from(LONG_CONTEXT), model=st.sampled_from(sorted(MODEL_PRICING)))
def test_any_priced_model_reaches_any_long_context_arm(name, model):
    # oracle: requirement 17 — the model is passed as given, to all three arms
    assert _build(name, model=model).model == model


@pytest.mark.criterion("C1")
@given(
    name=st.sampled_from([n for n in NAMES if n not in LONG_CONTEXT]),
    model=st.sampled_from(sorted(MODEL_PRICING)),
)
def test_a_model_for_any_other_system_raises(name, model):
    # oracle: requirement 17 — only the long-context arms take a model; the first assert
    # keeps this from passing against older code, and later arms add names after these
    assert system_names()[: len(NAMES)] == NAMES
    with pytest.raises(ValueError):
        build_system(name, model=model)


@pytest.mark.criterion("C1")
@given(
    name=st.text(min_size=0, max_size=30).filter(
        lambda s: s not in (*system_names(), "long_context")
    )
)
def test_any_other_name_raises(name):
    # oracle: requirement 17 — the accepted set is system_names() plus the one alias
    assert system_names()[: len(NAMES)] == NAMES
    with pytest.raises(ValueError):
        build_system(name)
