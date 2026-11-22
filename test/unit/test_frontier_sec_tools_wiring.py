"""Wiring for the frontier_sec_tools arm that the locked spec tests don't reach.

docs/specs/frontier-sec-tools.md, Notes ("Not in spec tests"): ``run_suite.py``'s
``--system frontier_sec_tools``, ``--model``, ``--sec-as-of`` and ``--sec-universes``, the
report's ``config["frontier"]``; ``build_system``; ``.env.example``; the publication doc; the
moved ``filing_text``; the prompt registry entry. No network, database or LLM.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from prompts.prompt_registry import get_xbrl_registry
from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer
from src.eval.sec_tools.client import SecApiClient, SecSnapshot
from src.eval.sec_tools.filing_text import filing_text as moved_filing_text
from src.eval.sec_tools.toolbox import tool_specs
from src.eval.systems import MODEL_NAMES, build_system
from src.eval.systems.frontier_sec_tools import FrontierSecToolsSystem
from src.eval.systems.long_context import filing_text as kept_filing_text
from src.llm.config import XBRL_STAGE_TIERS

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "eval" / "run_suite.py"


@pytest.fixture(scope="module")
def run_suite():
    spec = importlib.util.spec_from_file_location("run_suite_frontier_wiring", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_suite_frontier_wiring"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def suite_file(tmp_path: Path) -> Path:
    suite = tmp_path / "suite.json"
    suite.write_text(
        json.dumps({
            "name": "unit",
            "version": "v1",
            "cases": [{"id": f"c{i}", "question": f"Question {i}?", "expected_value": 1.0}
                      for i in range(2)],
        }),
        encoding="utf-8",
    )
    return suite


class _FakeArm:
    """Stands in for the arm: records the as-of date it saw, answers with a record."""

    name = "frontier_sec_tools"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or "gpt-5.6-sol"
        self.seen_as_of: list[str | None] = []

    def answer(self, case: EvalCase) -> SystemAnswer:
        self.seen_as_of.append(os.environ.get("XBRLRAG_SEC_AS_OF"))
        record = {"stop": "steps", "budget_hit": True, "steps": 3, "tool_calls": 2,
                  "tool_errors": 1, "snapshot_misses": 0, "unresolved_citations": ["S9"]}
        return SystemAnswer(refused=True, agent=record)

    def run_config(self) -> dict[str, Any]:
        return {"model": self.model, "prompt_version": "v1", "tools_fingerprint": "f" * 64,
                "max_steps": 20, "max_tool_calls": 60, "max_cost_usd": 2.0,
                "max_input_tokens": 250000,
                "sec_snapshot": {"as_of": os.environ.get("XBRLRAG_SEC_AS_OF"), "urls": 0}}


def _argv(suite_file: Path, out: Path, *extra: str) -> list[str]:
    return ["--suite-path", str(suite_file), "--no-store", "--workers", "1", "--out", str(out),
            *extra]


def test_the_system_needs_an_as_of_date(run_suite, suite_file, tmp_path):
    with pytest.raises(SystemExit):
        run_suite.main(_argv(suite_file, tmp_path / "r.json", "--system", "frontier_sec_tools"))


@pytest.mark.parametrize("as_of", ["2026/10/29", "20261029", "2026-W44-4", "2026-02-30"])
def test_a_malformed_as_of_date_is_refused(run_suite, suite_file, tmp_path, as_of):
    # date.fromisoformat accepts "20261029" and week dates; the toolbox does not, so neither
    # does the parser (frontier-sec-tools review).
    with pytest.raises(SystemExit):
        run_suite.main(_argv(suite_file, tmp_path / "r.json", "--system", "frontier_sec_tools",
                             "--sec-as-of", as_of))


def test_the_pacing_lock_is_free_while_a_request_runs(tmp_path):
    # frontier-sec-tools review: the lock paces starts, so a slow response must not hold it.
    seen = []

    class Transport:
        def get(self, url, headers):
            seen.append(client._pace_lock.locked())
            return 200, b"{}"

    snapshot = SecSnapshot(tmp_path / "snap", as_of="2026-10-29")
    client = SecApiClient(snapshot, transport=Transport(), identity="test test@example.com")
    client.get("https://data.sec.gov/submissions/CIK0000000001.json")
    assert seen == [False]


@pytest.mark.parametrize("flag", [["--sec-as-of", "2026-10-29"], ["--sec-universes", "x.yaml"],
                                  ["--max-input-tokens", "120000"]])
def test_the_sec_flags_apply_only_to_the_arm(run_suite, suite_file, tmp_path, flag):
    with pytest.raises(SystemExit):
        run_suite.main(_argv(suite_file, tmp_path / "r.json", "--system", "closed_book", *flag))


def test_a_run_records_the_frontier_config(run_suite, suite_file, tmp_path, monkeypatch,
                                           capsys):
    built: list[tuple[str, str | None]] = []
    arm = _FakeArm("claude-opus-5-5")

    def build(system: str, *, persist_audit: bool, model: str | None) -> _FakeArm:
        built.append((system, model))
        return arm

    monkeypatch.setattr(run_suite, "build_system", build)
    monkeypatch.delenv("XBRLRAG_SEC_AS_OF", raising=False)
    out = tmp_path / "report.json"
    rc = run_suite.main(_argv(suite_file, out, "--system", "frontier_sec_tools",
                              "--model", "claude-opus-5-5", "--sec-as-of", "2026-10-29"))
    assert rc == 0
    assert built == [("frontier_sec_tools", "claude-opus-5-5")]
    # The date is set for the run, and restored afterwards.
    assert arm.seen_as_of == ["2026-10-29", "2026-10-29"]
    assert "XBRLRAG_SEC_AS_OF" not in os.environ
    frontier = json.loads(out.read_text(encoding="utf-8"))["config"]["frontier"]
    assert frontier["model"] == "claude-opus-5-5"
    assert frontier["sec_snapshot"]["as_of"] == "2026-10-29"
    assert frontier["agent_summary"] == {
        "cases": 2, "answered": 0, "budget_hit": {"steps": 2}, "tool_calls": 4,
        "tool_errors": 2, "snapshot_misses": 0, "unresolved_citations": 2, "steps_max": 3,
        "prompt_tokens_max": 0,
    }
    assert "frontier_sec_tools:" in capsys.readouterr().out


def test_sec_universes_reach_the_arm(run_suite, suite_file, tmp_path, monkeypatch):
    universes = tmp_path / "universes.yaml"
    universes.write_text(
        "covered:\n  - {ticker: ZZZ, cik: '0000000042'}\ngroups: {}\nas_of: '2026-10-29'\n",
        encoding="utf-8",
    )
    made: list[dict[str, Any]] = []

    def fake_arm(**kwargs: Any) -> _FakeArm:
        made.append(kwargs)
        return _FakeArm(kwargs.get("model"))

    monkeypatch.setattr(run_suite, "FrontierSecToolsSystem", fake_arm)
    rc = run_suite.main(_argv(suite_file, tmp_path / "r.json", "--system", "frontier_sec_tools",
                              "--sec-as-of", "2026-10-29", "--sec-universes", str(universes)))
    assert rc == 0
    assert made[0]["universes"]["covered"] == [{"ticker": "ZZZ", "cik": "0000000042"}]


def test_max_input_tokens_reach_the_arm(run_suite, suite_file, tmp_path, monkeypatch):
    made: list[dict[str, Any]] = []

    def fake_arm(**kwargs: Any) -> _FakeArm:
        made.append(kwargs)
        return _FakeArm(kwargs.get("model"))

    monkeypatch.setattr(run_suite, "FrontierSecToolsSystem", fake_arm)
    rc = run_suite.main(_argv(suite_file, tmp_path / "r.json", "--system", "frontier_sec_tools",
                              "--model", "deepseek-flash", "--sec-as-of", "2026-10-29",
                              "--max-input-tokens", "120000"))
    assert rc == 0
    assert made == [{"model": "deepseek-flash", "max_input_tokens": 120000}]


@pytest.mark.parametrize("tokens", ["0", "-5", "250001"])
def test_an_out_of_range_window_is_refused(run_suite, suite_file, tmp_path, tokens):
    with pytest.raises(SystemExit):
        run_suite.main(_argv(suite_file, tmp_path / "r.json", "--system", "frontier_sec_tools",
                             "--sec-as-of", "2026-10-29", "--max-input-tokens", tokens))


def test_the_window_flag_builds_the_real_arm(run_suite, monkeypatch, tmp_path):
    # run_config builds the toolbox, which needs an as-of date, an identity and a snapshot
    # directory; none of them may come from the machine running the test.
    monkeypatch.setenv("XBRLRAG_SEC_AS_OF", "2026-10-29")
    monkeypatch.setenv("SEC_EDGAR_IDENTITY", "test test@example.com")
    monkeypatch.setenv("XBRLRAG_SEC_SNAPSHOT_DIR", str(tmp_path / "snap"))
    args = run_suite.argparse.Namespace(system="frontier_sec_tools", model="deepseek-flash",
                                        sec_universes=None, max_input_tokens=120000,
                                        max_steps=None, max_tool_calls=None,
                                        max_case_cost_usd=None, persist_audit=False)
    arm = run_suite._build(args)
    assert isinstance(arm, FrontierSecToolsSystem)
    assert arm.max_input_tokens == 120000
    assert arm.run_config()["max_input_tokens"] == 120000


@pytest.mark.parametrize("persist_audit", [True, False])
def test_the_budget_branch_honours_persist_audit(run_suite, monkeypatch, persist_audit):
    # The budget flags build the arm directly, not through build_system; --persist-audit
    # must still register the audit sink there, and only when given.
    calls: list[bool] = []
    monkeypatch.setattr(run_suite, "enable_audit_persistence", lambda: calls.append(True))
    args = run_suite.argparse.Namespace(system="frontier_sec_tools", model="deepseek-flash",
                                        sec_universes=None, max_input_tokens=None,
                                        max_steps=5, max_tool_calls=None,
                                        max_case_cost_usd=None, persist_audit=persist_audit)
    arm = run_suite._build(args)
    assert isinstance(arm, FrontierSecToolsSystem)
    assert calls == ([True] if persist_audit else [])


@pytest.fixture
def offline(monkeypatch):
    """No provider key and no LLM call allowed, so a test that slips through spends nothing."""
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "JINA_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("XBRLRAG_RUN_MAX_LLM_CALLS", "0")


_BUDGET_FLAGS = ["--max-steps", "7", "--max-tool-calls", "15", "--max-case-cost-usd", "0.75",
                 "--max-input-tokens", "120000"]


class _BudgetArm(_FakeArm):
    """A fake arm that keeps the budgets it was built with and reports them as the arm does."""

    def __init__(self, model: str | None = None, *, max_steps: int = 20,
                 max_tool_calls: int = 60, max_cost_usd: float = 2.0,
                 max_input_tokens: int = 250000) -> None:
        super().__init__(model)
        self.budgets = {"max_steps": max_steps, "max_tool_calls": max_tool_calls,
                        "max_cost_usd": max_cost_usd, "max_input_tokens": max_input_tokens}

    def run_config(self) -> dict[str, Any]:
        return {**super().run_config(), **self.budgets}


@pytest.mark.parametrize("flag", [["--max-steps", "5"], ["--max-tool-calls", "10"],
                                  ["--max-case-cost-usd", "1.5"]])
def test_the_budget_flags_apply_only_to_the_arm(run_suite, tmp_path, offline, capsys, flag):
    # A nonexistent suite: if the refusal did not happen, the run still could not start.
    with pytest.raises(SystemExit):
        run_suite.main(_argv(tmp_path / "missing.json", tmp_path / "r.json",
                             "--system", "closed_book", *flag))
    assert "apply only to --system frontier_sec_tools" in capsys.readouterr().err


@pytest.mark.parametrize("flag", [["--max-steps", "0"], ["--max-steps", "-3"],
                                  ["--max-tool-calls", "0"], ["--max-case-cost-usd", "0"],
                                  ["--max-case-cost-usd", "-1"], ["--max-case-cost-usd", "nan"],
                                  ["--max-case-cost-usd", "inf"], ["--max-steps", "2.5"]])
def test_a_non_positive_budget_is_refused(run_suite, tmp_path, offline, flag):
    with pytest.raises(SystemExit):
        run_suite.main(_argv(tmp_path / "missing.json", tmp_path / "r.json",
                             "--system", "frontier_sec_tools", "--sec-as-of", "2026-10-29",
                             *flag))


def test_the_budgets_reach_the_arm_and_the_recorded_config(run_suite, suite_file, tmp_path,
                                                           monkeypatch, offline):
    made: list[dict[str, Any]] = []

    def fake_arm(**kwargs: Any) -> _BudgetArm:
        made.append(kwargs)
        return _BudgetArm(**kwargs)

    monkeypatch.setattr(run_suite, "FrontierSecToolsSystem", fake_arm)
    out = tmp_path / "r.json"
    rc = run_suite.main(_argv(suite_file, out, "--system", "frontier_sec_tools",
                              "--model", "deepseek-flash", "--sec-as-of", "2026-10-29",
                              *_BUDGET_FLAGS))
    assert rc == 0
    assert made == [{"model": "deepseek-flash", "max_steps": 7, "max_tool_calls": 15,
                     "max_cost_usd": 0.75, "max_input_tokens": 120000}]
    config = json.loads(out.read_text(encoding="utf-8"))["config"]
    frontier = config["frontier"]
    assert (frontier["max_steps"], frontier["max_tool_calls"], frontier["max_cost_usd"],
            frontier["max_input_tokens"]) == (7, 15, 0.75, 120000)
    # The resume identity holds them as it holds the window (eval-run-checkpoint req. 7).
    settings = config["resume_identity"]["settings"]
    assert (settings["max_steps"], settings["max_tool_calls"], settings["max_case_cost_usd"],
            settings["max_input_tokens"]) == (7, 15, 0.75, 120000)


def test_without_the_flags_the_arm_keeps_its_defaults(run_suite, suite_file, tmp_path,
                                                      monkeypatch, offline):
    built: list[str] = []

    def build(system: str, *, persist_audit: bool, model: str | None) -> _BudgetArm:
        built.append(system)
        return _BudgetArm(model)

    monkeypatch.setattr(run_suite, "build_system", build)
    out = tmp_path / "r.json"
    rc = run_suite.main(_argv(suite_file, out, "--system", "frontier_sec_tools",
                              "--sec-as-of", "2026-10-29"))
    assert rc == 0
    assert built == ["frontier_sec_tools"]
    config = json.loads(out.read_text(encoding="utf-8"))["config"]
    assert config["frontier"]["max_steps"] == 20
    settings = config["resume_identity"]["settings"]
    assert settings["max_steps"] is None and settings["max_case_cost_usd"] is None


def test_a_resume_with_other_budgets_is_refused(run_suite, suite_file, tmp_path, monkeypatch,
                                                offline):
    from src.eval.core.checkpoint.run import ResumeRefused, check_resume

    monkeypatch.setattr(run_suite, "FrontierSecToolsSystem",
                        lambda **kwargs: _BudgetArm(**kwargs))
    stored = tmp_path / "stored.json"
    assert run_suite.main(_argv(suite_file, stored, "--system", "frontier_sec_tools",
                                "--sec-as-of", "2026-10-29", *_BUDGET_FLAGS)) == 0
    again = tmp_path / "again.json"
    assert run_suite.main(_argv(suite_file, again, "--system", "frontier_sec_tools",
                                "--sec-as-of", "2026-10-29", *_BUDGET_FLAGS[:-4],
                                "--max-case-cost-usd", "1.25", "--max-input-tokens",
                                "120000")) == 0
    stored_config = json.loads(stored.read_text(encoding="utf-8"))["config"]
    same = stored_config["resume_identity"]
    other = json.loads(again.read_text(encoding="utf-8"))["config"]["resume_identity"]
    check_resume(stored_config, same)  # the same budgets resume
    with pytest.raises(ResumeRefused, match="settings differs"):
        check_resume(stored_config, other)


def test_the_budget_flags_build_the_real_arm(run_suite, monkeypatch, tmp_path, offline):
    monkeypatch.setenv("XBRLRAG_SEC_AS_OF", "2026-10-29")
    monkeypatch.setenv("SEC_EDGAR_IDENTITY", "test test@example.com")
    monkeypatch.setenv("XBRLRAG_SEC_SNAPSHOT_DIR", str(tmp_path / "snap"))
    args = run_suite.argparse.Namespace(system="frontier_sec_tools", model="deepseek-flash",
                                        sec_universes=None, max_input_tokens=None,
                                        max_steps=7, max_tool_calls=15,
                                        max_case_cost_usd=0.75, persist_audit=False)
    arm = run_suite._build(args)
    assert isinstance(arm, FrontierSecToolsSystem)
    config = arm.run_config()
    assert (config["max_steps"], config["max_tool_calls"], config["max_cost_usd"],
            config["max_input_tokens"]) == (7, 15, 0.75, 250000)


def test_no_other_run_records_a_frontier_config(run_suite, suite_file, tmp_path, monkeypatch):
    from src.eval.core.system import CallableSystem

    monkeypatch.setattr(
        run_suite, "build_system",
        lambda system, **_kw: CallableSystem(system, lambda _q: SystemAnswer(refused=True)),
    )
    out = tmp_path / "report.json"
    run_suite.main(_argv(suite_file, out, "--system", "closed_book"))
    assert "frontier" not in json.loads(out.read_text(encoding="utf-8"))["config"]


def test_build_system_passes_the_model_and_refuses_it_elsewhere(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-unit-offline")
    assert "frontier_sec_tools" in MODEL_NAMES
    arm = build_system("frontier_sec_tools", model="claude-opus-5-5")
    assert isinstance(arm, FrontierSecToolsSystem)
    assert arm.model == "claude-opus-5-5"
    with pytest.raises(ValueError, match="--model"):
        build_system("closed_book", model="gpt-5.6-sol")


def test_env_example_names_the_settings():
    text = (_ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ("XBRLRAG_SEC_SNAPSHOT_DIR", "XBRLRAG_SEC_AS_OF", "XBRLRAG_SEC_FROZEN",
                 "XBRLRAG_SEC_WITHHELD_CIKS"):
        assert name in text


def test_the_publication_doc_lists_the_tools_and_the_prompt():
    text = (_ROOT / "docs" / "eval" / "frontier_sec_tools.md").read_text(encoding="utf-8")
    for spec in tool_specs():
        assert f"`{spec.name}`" in text
    assert "prompts/templates/xbrl/_base/frontier_sec_tools/v2/system.txt" in text


def test_filing_text_moved_and_long_context_keeps_the_name():
    assert kept_filing_text is moved_filing_text
    html = '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>a</p><p>b</p></body></html>'
    assert moved_filing_text(html) == "a\nb"


def test_the_stage_is_registered_without_a_tier():
    assert "frontier_sec_tools" not in XBRL_STAGE_TIERS
    prompt = get_xbrl_registry().resolve("frontier_sec_tools", provider="openai",
                                         series="gpt-5.6")
    assert prompt.version == "v2"
    rendered = prompt.user.format(as_of="2024-06-30", scope="{}", question="Q {x}?")
    assert "2024-06-30" in rendered
    assert "Q {x}?" in rendered
