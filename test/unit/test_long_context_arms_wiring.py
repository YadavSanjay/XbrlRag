"""Wiring for the long-context arms that the locked spec tests don't reach.

docs/specs/long-context-arms.md: ``run_suite.py``'s ``--model``, ``--system long_context`` and
``fit_by_k``; the ``long_context_multi`` prompt; the default router's construction; and the
selection record of an answer whose provider call failed. No network, database or LLM.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from prompts.prompt_registry import get_xbrl_registry
from src.eval.core.case import EvalCase
from src.eval.core.system import CallableSystem, SystemAnswer
from src.eval.systems import LONG_CONTEXT_NAMES, build_system
from src.eval.systems.long_context import split_user_template
from src.eval.systems.long_context_arms import LongContextMultiSystem, LongContextResolvedSystem
from src.llm.gateway import LLMRefusalError
from src.llm.stage_models import resolve_stage
from src.retriever.xbrl.entity_extractor import EntityExtractor, ResolvedCompany, ResolvedPeriod
from src.retriever.xbrl.intent_router import XBRL_INTENT, IntentRouterResult

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval" / "run_suite.py"
_ARMS = [LongContextResolvedSystem, LongContextMultiSystem]


@pytest.fixture(scope="module")
def run_suite():
    spec = importlib.util.spec_from_file_location("run_suite_lca_wiring", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_suite_lca_wiring"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def suite_file(tmp_path: Path) -> Path:
    suite = tmp_path / "suite.json"
    suite.write_text(
        json.dumps(
            {
                "name": "unit",
                "version": "v1",
                "cases": [
                    {"id": f"c{i}", "question": f"Question {i}?", "expected_value": 1.0,
                     "ticker": "AAA", "fy": 2024}
                    for i in range(3)
                ],
            }
        ),
        encoding="utf-8",
    )
    return suite


# --------------------------------------------------------------------------------
# run_suite.py
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("name", LONG_CONTEXT_NAMES)
def test_run_suite_takes_a_model_for_every_long_context_name(
    run_suite, suite_file, tmp_path, monkeypatch, name
):
    built: list[tuple[str, str | None]] = []

    def build(system: str, *, persist_audit: bool, model: str | None) -> CallableSystem:
        built.append((system, model))
        return CallableSystem(system, lambda _q: SystemAnswer(refused=True))

    monkeypatch.setattr(run_suite, "build_system", build)
    rc = run_suite.main(
        ["--suite-path", str(suite_file), "--system", name, "--model", "gpt-5-mini", "--no-store",
         "--workers", "1", "--out", str(tmp_path / "report.json")]
    )
    assert rc == 0
    assert built == [(name, "gpt-5-mini")]


def _selections() -> list[dict[str, Any]]:
    return [
        {"status": "sent", "k": 2},
        {"status": "over_window", "k": 3},
        {"status": "undecided", "k": 0},
    ]


def _answers_in_turn(selections: list[dict[str, Any]]) -> Any:
    remaining = list(selections)

    def answer(_question: str) -> SystemAnswer:
        selection = remaining.pop(0)
        if selection["status"] == "over_window":
            return SystemAnswer(error="excluded: over the window", selection=selection)
        return SystemAnswer(refused=True, selection=selection)

    return answer


def test_a_multi_run_records_and_prints_fit_by_k(
    run_suite, suite_file, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        run_suite, "build_system",
        lambda system, **_kw: CallableSystem(system, _answers_in_turn(_selections())),
    )
    out = tmp_path / "report.json"
    rc = run_suite.main(
        ["--suite-path", str(suite_file), "--system", "long_context_multi", "--no-store",
         "--workers", "1", "--out", str(out)]
    )
    assert rc == 0
    # JSON turns the integer k into a string key; the undecided case is not a fit result.
    assert json.loads(out.read_text(encoding="utf-8"))["config"]["fit_by_k"] == {
        "2": {"sent": 1, "over_window": 0},
        "3": {"sent": 0, "over_window": 1},
    }
    printed = capsys.readouterr().out
    assert "k=2" in printed
    assert "k=3" in printed


def test_no_other_run_records_fit_by_k(run_suite, suite_file, tmp_path, monkeypatch):
    monkeypatch.setattr(
        run_suite, "build_system",
        lambda system, **_kw: CallableSystem(system, _answers_in_turn(_selections())),
    )
    out = tmp_path / "report.json"
    run_suite.main(
        ["--suite-path", str(suite_file), "--system", "long_context_resolved", "--no-store",
         "--workers", "1", "--out", str(out)]
    )
    assert "fit_by_k" not in json.loads(out.read_text(encoding="utf-8"))["config"]


# --------------------------------------------------------------------------------
# The long_context_multi prompt
# --------------------------------------------------------------------------------


def test_multi_prompt_says_several_filings_and_takes_filing_then_question():
    prompt = get_xbrl_registry().resolve("long_context_multi")
    assert prompt.version == "v1"
    filing, question = split_user_template(prompt.user)
    assert filing.variables == {"filing"} and question.variables == {"question"}
    assert prompt.system.variables == set()
    system = prompt.system.format()
    assert '"ticker"' in system and '"fiscal_year"' in system
    assert "refuse" in system
    for key in ('"value"', '"unit"', '"rows"', '"refused"', '"hedged"', '"cited_chunks"'):
        assert key in system
    assert "{{" not in system


def test_the_oracle_prompt_stays_where_it_was():
    assert get_xbrl_registry().resolve("long_context").version == "v2"


# --------------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("cls", _ARMS)
def test_a_model_with_no_price_is_refused(cls):
    with pytest.raises(ValueError, match="MODEL_PRICING"):
        cls(model="no-such-model")


@pytest.mark.parametrize("cls", _ARMS)
def test_the_default_router_is_built_on_first_use_as_the_pipeline_builds_it(cls, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-unit-offline")
    monkeypatch.delenv("XBRLRAG_LLM_PROFILE", raising=False)
    arm = cls()
    assert arm._router is None

    router = arm._get_router()

    assert arm._get_router() is router
    assert isinstance(router.entity_extractor, EntityExtractor)
    assert router.known_tickers == []
    assert router.model == resolve_stage("intent_router").model


def test_an_unknown_system_still_raises():
    with pytest.raises(ValueError, match="unknown system"):
        build_system("long_context_typo")


# --------------------------------------------------------------------------------
# The record survives a failed answer call
# --------------------------------------------------------------------------------


class _Router:
    """The router's result for one company and year; the LLM and SQL are behind it."""

    def route(self, _question: str) -> IntentRouterResult:
        return IntentRouterResult(
            intent=XBRL_INTENT.NUMERIC_LOOKUP,
            companies=[ResolvedCompany(mention="AAA", ticker="AAA", resolution="exact_ticker")],
            periods=[ResolvedPeriod(mention="fiscal 2024", fiscal_year=2024)],
        )


class _Resolver:
    def canonical_key(self, ticker: str, fiscal_year: int) -> str:
        return f"{ticker}/{fiscal_year}/10-K/canonical.htm"


class _Store:
    def exists(self, _key: str) -> bool:
        return True

    def read_text(self, _key: str) -> str:
        return "<html><body><p>Revenue was 7.</p></body></html>"


class _Gateway:
    """The provider boundary: the answer call fails with *error*."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def complete(self, **_kwargs: Any) -> Any:
        raise self.error


@pytest.mark.parametrize("cls", _ARMS)
@pytest.mark.parametrize(
    "error, refused",
    [
        pytest.param(RuntimeError("provider down"), False, id="provider-error"),
        pytest.param(LLMRefusalError("declined"), True, id="provider-refusal"),
    ],
)
def test_the_selection_survives_a_failed_answer_call(cls, error, refused):
    arm = cls(
        gateway=_Gateway(error), router=_Router(), resolver=_Resolver(), store=_Store()
    )
    answer = arm.answer(EvalCase(id="c1", question="Q?", kind="lookup", expected_value=1.0,
                                 ticker="AAA", fy=2024))
    assert answer.refused is refused
    assert bool(answer.error) is not refused
    assert answer.selection["status"] == "sent"
    assert answer.selection["filings"][0]["key"] == "AAA/2024/10-K/canonical.htm"
