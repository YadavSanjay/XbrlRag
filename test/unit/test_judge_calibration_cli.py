"""scripts/eval/judge_calibration.py and run_suite.py's calibration record.

The maintainer's workflow end to end: export dev text answers, fill the label sheet,
label the answers with a candidate judge, calibrate, show, and have ``run_suite.py
--judge`` report the calibrated judge's means (docs/specs/judge-calibration.md). Fakes
sit only at the I/O boundaries: the LLM provider behind a real ``LLMGateway`` and the
system under test. No network, database or paid call.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

import src.eval.core.graders.judge as judge_module
import src.eval.systems as systems_module
from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

_ROOT = Path(__file__).resolve().parents[2]
_PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cli():
    return _load("judge_calibration_cli", _ROOT / "scripts" / "eval" / "judge_calibration.py")


@pytest.fixture(scope="module")
def run_suite():
    return _load("run_suite_calibration_under_test", _ROOT / "scripts" / "eval" / "run_suite.py")


class _JudgeAdapter(ProviderAdapter):
    """A judge: a claim is correct and supported iff its text says "good"; kp0 covered."""

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
            "claims": [{"id": c["id"], "correct": "good" in c["text"],
                        "supported": "good" in c["text"]} for c in payload["claims"]],
            "key_points": [{"index": p["index"], "covered": p["index"] == 0}
                           for p in payload["key_points"]],
        })

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=raw, finish_reason="stop", prompt_tokens=1000,
                              completion_tokens=200, reasoning_tokens=0,
                              cached_prompt_tokens=0)


class _System:
    """Answers each case with a good and a bad statement, both citing one chunk."""

    name = "fake_system"

    def __init__(self) -> None:
        self.answered: list[str] = []

    def answer(self, case: EvalCase) -> SystemAnswer:
        self.answered.append(case.id)
        raw = {
            "answer_details": [
                {"statement": f"A good point about {case.id}.", "chunk_ids": ["c1"]},
                {"statement": "A wrong point.", "chunk_ids": ["c1"]},
            ],
            "execution_result": {"steps": []},
            "narrative_chunks": [{"chunk_id": "c1", "text": "Filing text, with a comma."}],
        }
        return SystemAnswer(text="Two points.", raw=raw, cost_usd=0.01)


@pytest.fixture(autouse=True)
def _no_audit_sink():
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


@pytest.fixture
def fakes(monkeypatch, run_suite):
    system = _System()
    adapter = _JudgeAdapter()
    gateway = LLMGateway(stage="eval_judge", cost_tracker=CostTracker(), rate_limiter=None,
                         run_budget=RunBudget(), max_attempts=1, sleep_fn=lambda _s: None,
                         adapters={p: adapter for p in _PROVIDERS})
    monkeypatch.setattr(systems_module, "build_system", lambda name, **kw: system)
    monkeypatch.setattr(run_suite, "build_system", lambda name, **kw: system)
    monkeypatch.setattr(judge_module, "build_xbrl_gateway", lambda stage: gateway)
    return system, adapter


def _suite(path: Path, split: str = "dev") -> Path:
    cases = [
        {"id": f"t{i}", "question": f"What did AAA say about topic {i}?", "kind": "narrative",
         "expected_text": f"Topic {i} matters.", "key_points": ["it matters", "it grew"],
         "tags": [f"split:{split}"]}
        for i in range(3)
    ]
    path.write_text(json.dumps({"name": "dev-text", "version": "v1", "cases": cases}),
                    encoding="utf-8")
    return path


def _fill_like_the_judge(sheet: Path) -> None:
    """Fills every label the way the fake judge will, so each kappa is 1."""
    rows = list(csv.reader(io.StringIO(sheet.read_text(encoding="utf-8"))))
    for row in rows[1:]:
        _, dim, item, text, _ = row
        good = item == "kp0" if dim == "covered" else "good" in text
        row[4] = "y" if good else "n"
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    sheet.write_text(buf.getvalue(), encoding="utf-8")


def test_workflow_export_judge_calibrate_show_and_run_suite(cli, run_suite, fakes,
                                                            tmp_path, capsys):
    system, adapter = fakes
    out = tmp_path / "cal"
    suite = _suite(tmp_path / "suite.json")
    base = ["--out-dir", str(out)]

    rc = cli.main([*base, "export", "--suite", str(suite), "--system", "closed_book",
                   "--llm-profile", "deepseek", "--max-cost-usd", "1"])
    assert rc == 0
    assert system.answered == ["t0", "t1", "t2"]
    answers = cli.answers_from_json((out / "answers.json").read_text(encoding="utf-8"))
    assert [a.answer_id for a in answers] == ["t0~closed_book", "t1~closed_book",
                                              "t2~closed_book"]
    # per answer: 2 correct + 2 supported (both cited) + 2 covered
    sheet = out / "labels.csv"
    assert len(sheet.read_text(encoding="utf-8").splitlines()) == 1 + 3 * 6
    assert "(no cited passages)" not in (out / "context.md").read_text(encoding="utf-8")

    # the export refuses to overwrite the sheet a person may be filling
    assert cli.main([*base, "export", "--suite", str(suite), "--system", "closed_book",
                     "--llm-profile", "deepseek", "--max-cost-usd", "1"]) == 2

    _fill_like_the_judge(sheet)
    rc = cli.main([*base, "judge", "--judge-model", "claude-haiku-5-5", "--max-cost-usd", "1"])
    assert rc == 0
    assert adapter.calls == 3
    # a re-run continues the file: nothing left to judge, no call
    assert cli.main([*base, "judge", "--judge-model", "claude-haiku-5-5",
                     "--max-cost-usd", "1"]) == 0
    assert adapter.calls == 3

    # the default --min-items 100 fails every 6-label dimension
    rc = cli.main([*base, "calibrate", "--min-answers", "3", "--out",
                   str(tmp_path / "floored.json")])
    assert rc == 0
    floored = json.loads((tmp_path / "floored.json").read_text(encoding="utf-8"))["record"]
    assert floored["candidates"][0]["min_items"] == 100
    assert floored["candidates"][0]["passed"] is False
    capsys.readouterr()

    rc = cli.main([*base, "calibrate", "--min-answers", "3", "--min-items", "6",
                   "--generator-profile", "deepseek"])
    assert rc == 0
    record = json.loads((out / "calibration.json").read_text(encoding="utf-8"))["record"]
    assert record["schema"] == "judge-calibration/2"
    (cand,) = record["candidates"]
    assert cand["judge_version"] == "v2:claude-haiku-5-5"
    assert cand["passed"] is True
    assert (cand["min_answers"], cand["min_items"]) == (3, 6)
    assert {d: v["kappa"] for d, v in cand["dimensions"].items()} == {
        "correct": 1.0, "supported": 1.0, "covered": 1.0}
    # each dimension has 3 y and 3 n: share 1/2, kappa-gated, passes
    assert {d: (v["gate"], v["human_majority_share"], v["passed"])
            for d, v in cand["dimensions"].items()} == {
        d: ("kappa", 0.5, True) for d in ("correct", "supported", "covered")}
    printed = capsys.readouterr().out
    assert "cheapest passing judge for profile 'deepseek': v2:claude-haiku-5-5" in printed
    assert "min_answers=3 min_items=6" in printed
    assert "gate=kappa" in printed and "human_share=0.500" in printed

    assert cli.main([*base, "show", "--profile", "openai"]) == 0
    shown = capsys.readouterr().out
    assert "(verified)" in shown
    assert "min kappa (kappa-gated)=1.000" in shown
    assert "min_answers=3 min_items=6" in shown and "gate=kappa" in shown

    # run_suite --judge reports the calibrated version's means
    run_suite_out = tmp_path / "report.json"
    rc = run_suite.main(["--suite-path", str(suite), "--system", "closed_book",
                         "--llm-profile", "openai", "--no-store", "--workers", "1",
                         "--out", str(run_suite_out), "--judge",
                         "--judge-calibration", str(out / "calibration.json")])
    assert rc == 0
    summary = json.loads(run_suite_out.read_text(encoding="utf-8"))["config"]["judge_summary"]
    assert summary["status"] == "calibrated"
    assert summary["means"]["correctness"] == pytest.approx(0.5, abs=1e-12)  # 1 of 2 claims


def test_run_suite_refuses_an_edited_record(cli, run_suite, fakes, tmp_path):
    system, adapter = fakes
    doc = {"record": {"schema": "judge-calibration/2", "candidates": []},
           "record_sha256": "0" * 64}
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    rc = run_suite.main(["--suite-path", str(_suite(tmp_path / "s.json")), "--system",
                         "closed_book", "--llm-profile", "openai", "--no-store",
                         "--judge", "--judge-calibration", str(path)])
    assert rc == 2
    assert system.answered == [] and adapter.calls == 0


def test_run_suite_without_a_record_is_uncalibrated(run_suite, tmp_path, monkeypatch):
    monkeypatch.setattr(run_suite, "_CALIBRATION_FILE", tmp_path / "missing.json")
    assert run_suite._judge_calibration(None) == []
    with pytest.raises(OSError):
        run_suite._judge_calibration(str(tmp_path / "missing.json"))


def test_export_refuses_a_non_dev_suite(cli, fakes, tmp_path):
    system, _ = fakes
    suite = _suite(tmp_path / "suite.json", split="external")
    rc = cli.main(["--out-dir", str(tmp_path / "cal"), "export", "--suite", str(suite),
                   "--system", "closed_book", "--max-cost-usd", "1"])
    assert rc == 2
    assert system.answered == []


def test_an_edited_answers_file_is_refused(cli, fakes, tmp_path):
    out = tmp_path / "cal"
    assert cli.main(["--out-dir", str(out), "export", "--suite",
                     str(_suite(tmp_path / "suite.json")), "--system", "closed_book",
                     "--llm-profile", "deepseek", "--max-cost-usd", "1"]) == 0
    path = out / "answers.json"
    path.write_text(path.read_text(encoding="utf-8").replace("Topic 1", "Topic 9"),
                    encoding="utf-8")
    with pytest.raises(ValueError):
        cli.answers_from_json(path.read_text(encoding="utf-8"))
