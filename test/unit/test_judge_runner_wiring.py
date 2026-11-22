"""Unit tests for the judge and key-free wiring outside the locked spec tests.

Covers ``scripts/eval/run_suite.py``'s ``--judge`` / ``--keyfree`` / ``--perturb-twins``
flags and a few edges of ``src/eval/keyfree/runner.py`` (docs/specs/judge-runner-wiring.md).
Fakes sit only at the I/O boundaries: the LLM provider behind a real ``LLMGateway`` and
the system under test. No network, database or paid call.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

import src.eval.core.graders.judge as judge_module
from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer
from src.eval.keyfree.runner import metamorphic_pass, perturbation_twins
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "eval" / "run_suite.py"
_PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")


# --------------------------------------------------------------------------------
# fakes at the boundaries
# --------------------------------------------------------------------------------


class _JudgeAdapter(ProviderAdapter):
    """A provider that marks every claim correct and supported, every key point covered."""

    def __init__(self) -> None:
        self.provider = "fake"
        self.calls = 0

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        self.calls += 1
        user = next(m["content"] for m in params["messages"] if m["role"] == "user")
        payload, _ = json.JSONDecoder().raw_decode(user, user.index("{"))
        return json.dumps({
            "claims": [{"id": c["id"], "correct": True, "supported": True}
                       for c in payload["claims"]],
            "key_points": [{"index": p["index"], "covered": True}
                           for p in payload["key_points"]],
        })

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=raw, finish_reason="stop", prompt_tokens=1000,
                              completion_tokens=200, reasoning_tokens=0,
                              cached_prompt_tokens=0)


class _System:
    """A system under test answering every case with one cited statement."""

    name = "fake_system"

    def __init__(self, cost: float = 0.01) -> None:
        self.answered: list[str] = []
        self._cost = cost

    def answer(self, case: EvalCase) -> SystemAnswer:
        self.answered.append(case.id)
        steps = [{"step_id": "s1", "op": "lookup", "status": "OK", "value": 100.0,
                  "unit": "USD", "citation": {"fact_id": 7}}]
        plan = [{"id": "s1", "op": "lookup", "ticker": case.ticker, "fy": case.fy,
                 "concept_family": "Revenue"}]
        raw = {"plan": {"steps": plan}, "execution_result": {"steps": steps},
               "answer_details": [{"statement": "Revenue was 100.", "fact_ids": [7]}],
               "narrative_chunks": []}
        return SystemAnswer(text="Revenue was 100.", raw=raw, numeric_values=[100.0],
                            fact_ids=[7], cost_usd=self._cost)


@pytest.fixture(autouse=True)
def _no_audit_sink():
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


@pytest.fixture(scope="module")
def run_suite():
    spec = importlib.util.spec_from_file_location("run_suite_judge_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_suite_judge_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def wired(run_suite, monkeypatch, tmp_path):
    """run_suite with the fake system and a fake judge provider; returns a runner."""
    system = _System()
    adapter = _JudgeAdapter()
    gateway = LLMGateway(stage="eval_judge", cost_tracker=CostTracker(), rate_limiter=None,
                         run_budget=RunBudget(), max_attempts=1, sleep_fn=lambda _s: None,
                         adapters={p: adapter for p in _PROVIDERS})
    monkeypatch.setattr(run_suite, "build_system", lambda name, **kw: system)
    monkeypatch.setattr(judge_module, "build_xbrl_gateway", lambda stage: gateway)
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"name": "unit", "version": "v1", "cases": [
        {"id": "n1", "question": "How did AAA's revenue do in 2024?", "kind": "narrative",
         "expected_text": "It grew.", "key_points": ["grew"], "ticker": "AAA", "fy": 2024},
        {"id": "l1", "question": "What was AAA revenue in 2024?", "expected_value": 100.0,
         "ticker": "AAA", "fy": 2024, "concept": "Revenue"},
        {"id": "l2", "question": "What was BBB revenue in 2024?", "expected_value": 50.0,
         "ticker": "BBB", "fy": 2024, "concept": "Revenue"},
    ]}), encoding="utf-8")
    out = tmp_path / "report.json"

    def run(*extra: str) -> tuple[int, dict | None]:
        rc = run_suite.main(["--suite-path", str(suite), "--system", "closed_book",
                             "--llm-profile", "openai", "--no-store", "--workers", "1",
                             "--out", str(out), *extra])
        report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
        return rc, report

    return run, system, adapter


# --------------------------------------------------------------------------------
# run_suite.py flags
# --------------------------------------------------------------------------------


def test_default_run_has_no_judge_and_no_keyfree(wired):
    run, system, adapter = wired
    rc, report = run()
    assert rc == 0
    assert adapter.calls == 0
    assert "judge_version" not in report["config"]
    assert report["config"]["judge_summary"]["status"] == "none"
    assert "keyfree" not in report["config"]
    assert "perturbation" not in report["config"]
    assert sorted(system.answered) == ["l1", "l2", "n1"]


def test_judge_flag_judges_the_text_case(wired):
    run, _, adapter = wired
    rc, report = run("--judge", "--keyfree")
    assert rc == 0
    assert adapter.calls == 1  # only n1 has expected_text and key points
    assert report["config"]["judge_version"] == "v2:claude-haiku-5-5"
    assert report["config"]["judge_summary"]["status"] == "uncalibrated"
    assert report["config"]["judge_summary"]["means"] is None
    assert report["config"]["judge_cost_usd"] > 0
    by_id = {r["case_id"]: r for r in report["results"]}
    assert by_id["n1"]["judge_correctness"] == 1.0
    assert by_id["l1"]["judge_correctness"] is None
    assert set(report["config"]["keyfree"]["checks"]) == {
        "breakdown_sum", "growth", "screen_order"}


def test_same_family_judge_is_refused_before_any_call(wired):
    run, system, adapter = wired
    rc, _ = run("--judge", "--judge-model", "gpt-5.6-luna")  # openai judge, openai profile
    assert rc == 2
    assert system.answered == []
    assert adapter.calls == 0


def test_unknown_judge_model_is_refused(wired):
    run, system, _ = wired
    rc, _ = run("--judge", "--judge-model", "gpt-5")
    assert rc == 2
    assert system.answered == []


def test_judge_model_needs_judge(wired):
    run, _, _ = wired
    with pytest.raises(SystemExit):
        run("--judge-model", "claude-haiku-5-5")


def test_perturbation_needs_a_cost_cap(wired):
    run, system, _ = wired
    with pytest.raises(SystemExit):
        run("--perturb-twins", "2")
    assert system.answered == []


def test_perturbation_runs_under_the_remaining_cap(wired):
    run, system, _ = wired
    rc, report = run("--perturb-twins", "3", "--max-cost-usd", "5")
    assert rc == 0
    block = report["config"]["perturbation"]
    assert block["n_twins"] == 3
    assert block["n_pairs"] == 3
    # tolerance: three suite answers at $0.01 subtracted from the $5 cap
    assert block["max_cost_usd"] == pytest.approx(5 - 0.03, abs=1e-9)
    # narrative cases get no twins; l1's original is answered once for its two twins
    assert all("n1" not in cid for cid in system.answered[3:])
    assert system.answered[3:].count("l1") == 1


def test_company_names_make_ticker_twins_from_name_phrased_questions(run_suite, tmp_path):
    lexicon = tmp_path / "lexicon.yaml"
    lexicon.write_text(
        "companies:\n"
        "  AAA: {short: Alpha, legal: Alpha Holdings Inc.}\n"
        "  BBB: {short: Beta, legal: Beta Corp.}\n"
        "metrics: {Revenue: {label: revenue, synonyms: [], shorthand: []}}\n"
        "dimensions: {}\nuniverses: {covered: [the covered companies]}\nperiod_ends: {}\n",
        encoding="utf-8",
    )
    names = run_suite._company_names(lexicon)
    assert names == {"AAA": ["Alpha", "Alpha Holdings Inc."], "BBB": ["Beta", "Beta Corp."]}
    cases = [EvalCase(id="c1", question="What was Alpha's revenue in fiscal 2024?",
                      ticker="AAA", fy=2024),
             EvalCase(id="c2", question="What was Beta's revenue in fiscal 2024?",
                      ticker="BBB", fy=2024)]
    with_names = perturbation_twins(cases, seed=1, names=names, max_twins=10)
    without = perturbation_twins(cases, seed=1, names={}, max_twins=10)
    assert sum("~ticker:" in t["twin"].id for t in with_names) == 2
    assert sum("~ticker:" in t["twin"].id for t in without) == 0


def test_the_committed_lexicon_names_every_company(run_suite):
    names = run_suite._company_names()
    assert names and all(forms for forms in names.values())


def test_a_halt_in_the_perturbation_pass_keeps_the_graded_report(wired, run_suite,
                                                                monkeypatch):
    from src.llm.run_budget import RunBudgetExceeded

    def halt(*_args, **_kwargs):
        raise RunBudgetExceeded("budget spent")

    monkeypatch.setattr(run_suite, "run_perturbation_checks", halt)
    run, system, _ = wired
    rc, report = run("--perturb-twins", "2", "--max-cost-usd", "5")
    assert rc == 0
    assert report is not None  # --out was still written
    assert sorted(system.answered) == ["l1", "l2", "n1"]
    block = report["config"]["perturbation"]
    assert "budget spent" in block["halted"]
    assert block["n_twins"] == 2


def test_perturbation_skips_everything_once_the_cap_is_spent(wired):
    run, _, _ = wired
    # the suite run itself spends the whole $0.02 cap (two answers, then it aborts)
    rc, report = run("--perturb-twins", "2", "--max-cost-usd", "0.02")
    assert rc == 0
    assert report["status"] == "ABORTED"
    block = report["config"]["perturbation"]
    assert block["max_cost_usd"] == 0.0
    assert block["n_pairs"] == 0
    assert len(block["skipped"]) == 2


# --------------------------------------------------------------------------------
# keyfree runner edges
# --------------------------------------------------------------------------------


def _screen(plan_step: dict) -> tuple[EvalCase, SystemAnswer]:
    rows = [{"ticker": "AAA", "value": 2.0, "unit": "USD"},
            {"ticker": "BBB", "value": 1.0, "unit": "USD"}]
    raw = {"plan": {"steps": [{"id": "s1", "op": "screen", **plan_step}]},
           "execution_result": {"steps": [{"step_id": "s1", "op": "screen", "status": "OK",
                                           "rows": rows}]}}
    case = EvalCase(id="sc", question="q", kind="screen", fy=2024, concept="Revenue")
    return case, SystemAnswer(raw=raw, rows=rows)


def _lookup(case_id: str, ticker: str, value: float) -> tuple[EvalCase, SystemAnswer]:
    raw = {"execution_result": {"steps": [{"step_id": "s1", "op": "lookup", "status": "OK",
                                           "value": value, "unit": "USD"}]}}
    case = EvalCase(id=case_id, question="q", ticker=ticker, fy=2024, concept="Revenue")
    return case, SystemAnswer(raw=raw, numeric_values=[value])


@pytest.mark.parametrize("plan_step", [
    {"axis": "srt:ProductOrServiceAxis"},
    {"metric_kind": "growth"},
    {"dimensions": {"srt:StatementGeographicalAxis": "US"}},
])
def test_screen_order_skips_non_value_screens(plan_step):
    pairs = [_screen(plan_step), _lookup("a", "AAA", 20.0), _lookup("b", "BBB", 10.0)]
    assert metamorphic_pass(pairs)["verdicts"] == []


@pytest.mark.parametrize("plan_step", [{}, {"dimensions": "none"}, {"metric_kind": "value"}])
def test_screen_order_applies_to_plain_value_screens(plan_step):
    pairs = [_screen(plan_step), _lookup("a", "AAA", 20.0), _lookup("b", "BBB", 10.0)]
    out = metamorphic_pass(pairs)
    assert out["checks"]["screen_order"]["consistent"] == ["sc"]
    assert out["verdicts"][0]["related_case_ids"] == ["a", "b"]


def test_growth_without_a_prior_year_lookup_has_no_verdict():
    growth = EvalCase(id="g", question="q", kind="compute", ticker="AAA", fy=2024,
                      concept="Revenue", tags=["growth"])
    out = metamorphic_pass([(growth, SystemAnswer()), _lookup("a", "AAA", 20.0)])
    assert out["verdicts"] == []
    assert out["counts"]["growth"] == {"consistent": 0, "inconsistent": 0,
                                       "not_applicable": 0}


def test_single_ticker_suite_gets_year_twins_only():
    case = EvalCase(id="c", question="What was AAA revenue in 2024?", ticker="AAA",
                    fy=2024)
    twins = perturbation_twins([case], seed=1, names={}, max_twins=5)
    assert [t["twin"].id for t in twins] == ["c~year:2023"]
    assert twins[0]["twin"].question == "What was AAA revenue in 2023?"
