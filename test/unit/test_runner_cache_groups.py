"""Cache groups in the eval runner: cases on one filing run one after another."""

from __future__ import annotations

import importlib.util
import threading
import time
from itertools import pairwise
from pathlib import Path

import pytest

from src.eval.core import EvalCase, EvalRunner, RunnerConfig, SystemAnswer
from src.eval.core.runner import filing_group, schedule

ROOT = Path(__file__).resolve().parents[2]


def _case(cid, ticker=None, fy=None):
    return EvalCase(id=cid, question="q?", kind="lookup", expected_value=100.0,
                    rel_tol=1e-4, ticker=ticker, fy=fy)


def _ids(tasks):
    return [[c.id for c in task] for task in tasks]


class _Recorder:
    """Records each call's start and end, per filing, and peak concurrency overall."""

    def __init__(self, delay=0.02, name="recorder", log=None):
        self.name = name
        self._delay = delay
        self._lock = threading.Lock()
        self._active = 0
        self.peak = 0
        self.spans: dict[str | None, list[tuple[str, float, float]]] = {}
        self._log = log  # shared across systems: (filing, system, case id, start, end)

    def answer(self, case):
        with self._lock:
            self._active += 1
            self.peak = max(self.peak, self._active)
        start = time.perf_counter()
        time.sleep(self._delay)
        end = time.perf_counter()
        with self._lock:
            self._active -= 1
            self.spans.setdefault(filing_group(case), []).append((case.id, start, end))
        if self._log is not None:
            self._log.append((filing_group(case), self.name, case.id, start, end))
        return SystemAnswer(numeric_values=[100.0], fact_ids=[1], cost_usd=0.01)


def test_filing_group_key():
    assert filing_group(_case("a", "msft", 2024)) == "MSFT:2024"
    assert filing_group(_case("a", "MSFT", None)) is None
    assert filing_group(_case("a", None, 2024)) is None


def test_no_grouping_is_one_task_per_case_in_order():
    cases = [_case("a", "X", 1), _case("b", "X", 1), _case("c")]
    assert _ids(schedule(cases, None)) == [["a"], ["b"], ["c"]]


def test_filing_groups_largest_first_then_ungrouped_cases():
    cases = [_case("a", "AAA", 2024), _case("b", "BBB", 2024), _case("s1"),
             _case("c", "bbb", 2024), _case("d", "CCC", 2023), _case("e", "AAA", 2024),
             _case("f", "BBB", 2024), _case("s2")]
    assert _ids(schedule(cases, "filing")) == [
        ["b", "c", "f"], ["a", "e"], ["d"], ["s1"], ["s2"],
    ]


def test_an_unknown_grouping_raises_before_any_case_is_answered():
    system = _Recorder()
    with pytest.raises(ValueError, match="cache_groups"):
        EvalRunner(RunnerConfig(cache_groups="bogus")).run([_case("a", "X", 1)], system)
    assert system.spans == {}


def test_a_filing_s_cases_never_overlap_and_keep_suite_order():
    cases = [_case(f"{t}{i}", t, 2024) for i in range(4) for t in ("AAA", "BBB", "CCC")]
    system = _Recorder()
    report = EvalRunner(RunnerConfig(max_workers=3, cache_groups="filing")).run(
        cases, system)
    assert report.metrics.n == 12 and report.config["cache_groups"] == "filing"
    for key, spans in system.spans.items():
        assert [cid for cid, _, _ in spans] == [f"{key[0] * 3}{i}" for i in range(4)]
        for (_, _, prev_end), (_, next_start, _) in pairwise(spans):
            assert next_start >= prev_end
    assert system.peak > 1  # the filings themselves still run in parallel


def test_the_budget_still_stops_a_grouped_run():
    cases = [_case(f"c{i}", "AAA" if i % 2 else "BBB", 2024) for i in range(20)]
    report = EvalRunner(RunnerConfig(max_workers=2, max_cost_usd=0.03,
                                     cache_groups="filing")).run(cases, _Recorder(0.005))
    assert report.status == "ABORTED"
    assert report.metrics.n + report.n_skipped == 20


def test_run_many_gives_each_case_to_every_system_in_turn_within_a_filing():
    log: list = []
    oracle = _Recorder(0.01, "long_context_oracle", log)
    resolved = _Recorder(0.01, "long_context_resolved", log)
    cases = [_case(f"{t}{i}", t, 2024) for i in range(3) for t in ("AAA", "BBB")]
    reports = EvalRunner(RunnerConfig(max_workers=2, cache_groups="filing")).run_many(
        cases, [oracle, resolved])
    assert [r.system for r in reports] == ["long_context_oracle", "long_context_resolved"]
    assert [r.metrics.n for r in reports] == [6, 6]
    assert reports[0].config["scheduled_with"] == ["long_context_resolved"]
    assert reports[1].config["scheduled_with"] == ["long_context_oracle"]
    for filing in ("AAA:2024", "BBB:2024"):
        calls = sorted((e for e in log if e[0] == filing), key=lambda e: e[3])
        prefix = filing[:3]
        assert [(s, c) for _, s, c, _, _ in calls] == [
            (system, f"{prefix}{i}") for i in range(3)
            for system in ("long_context_oracle", "long_context_resolved")
        ]
        for (*_, prev_end), (*_, next_start, _) in pairwise(calls):
            assert next_start >= prev_end


def test_run_many_shares_one_budget():
    cases = [_case(f"c{i}", "AAA", 2024) for i in range(10)]
    reports = EvalRunner(RunnerConfig(max_workers=1, max_cost_usd=0.05,
                                      cache_groups="filing")).run_many(
        cases, [_Recorder(0, "a"), _Recorder(0, "b")])
    assert all(r.status == "ABORTED" for r in reports)
    assert sum(r.metrics.n for r in reports) == 5


def test_run_many_refuses_what_it_cannot_split_before_answering():
    system = _Recorder(0, "a")
    runner = EvalRunner()
    with pytest.raises(ValueError, match="distinct"):
        runner.run_many([_case("x", "A", 1)], [system, _Recorder(0, "a")])
    with pytest.raises(ValueError, match="one system"):
        runner.run_many([_case("x", "A", 1)], [system, _Recorder(0, "b")],
                        on_result=lambda r: None)
    assert system.spans == {}


def test_run_graded_many_keeps_each_system_s_answers_apart():
    from src.eval.core.judge_wiring import run_graded_many

    cases = [_case(f"c{i}", "AAA", 2024) for i in range(3)]
    reports = run_graded_many(cases, [_Recorder(0, "a"), _Recorder(0, "b")],
                              config=RunnerConfig(cache_groups="filing"))
    assert [r.system for r in reports] == ["a", "b"]
    for report in reports:
        assert report.metrics.n == 3
        assert "keyfree" in report.config and "judge_summary" in report.config


def _run_suite_module():
    spec = importlib.util.spec_from_file_location(
        "run_suite_under_test", ROOT / "scripts" / "eval" / "run_suite.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("argv", [
    ["--system", "long_context_multi", "--with-system", "long_context_oracle"],
    ["--system", "long_context_oracle", "--with-system", "long_context_oracle"],
    ["--system", "long_context_oracle", "--with-system", "long_context_resolved",
     "--perturb-twins", "2", "--max-cost-usd", "1"],
    ["--system", "long_context_oracle", "--with-system", "html_rag"],
])
def test_run_suite_refuses_a_bad_with_system(argv):
    with pytest.raises(SystemExit):
        _run_suite_module().main([*argv, "--no-store"])


def test_a_companion_report_gets_its_own_out_file():
    module = _run_suite_module()
    assert module._out_path("runs/x.json", None) == "runs/x.json"
    assert module._out_path("runs/x.json", "long_context_resolved") == (
        "runs/x.long_context_resolved.json")
    assert module._out_path(None, "long_context_resolved") is None


def test_run_suite_groups_the_long_context_systems_by_default():
    module = _run_suite_module()
    for system in ("long_context_oracle", "long_context_resolved", "long_context_multi",
                   "long_context"):
        assert module._cache_groups(None, system) == "filing"
        assert module._cache_groups("none", system) is None
    assert module._cache_groups(None, "xbrlrag_full") is None
    assert module._cache_groups("filing", "html_rag") == "filing"
