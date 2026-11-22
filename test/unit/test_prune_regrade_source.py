"""scripts/eval/prune_regrade_source.py: keep the stored results a re-grade would accept.

Constructed source reports and suites graded by the real graders; no DB, LLM or network.
The acceptance oracle is the re-grade itself (``regrade_report``): it refuses the unpruned
source and accepts the pruned one.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from src.eval.core.case import EvalCase, EvalSuite
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.regrade import RegradeRefused, regrade_report
from src.eval.core.report import CaseResult, RunReport
from src.eval.core.system import SystemAnswer

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval" / "prune_regrade_source.py"
_spec = importlib.util.spec_from_file_location("prune_regrade_source", _SCRIPT)
prune = importlib.util.module_from_spec(_spec)
sys.modules["prune_regrade_source"] = prune
_spec.loader.exec_module(prune)

SOURCE_ID = "eval-aaaaaaaaaaaa"
IDS = ["a1", "a2", "a3", "a4", "a5"]


def _case(case_id: str, question: str | None = None) -> EvalCase:
    return EvalCase(id=case_id, question=question or f"What was revenue for {case_id}?",
                    kind="lookup", expected_value=100.0, rel_tol=1e-4, tags=["split:dev"])


def _source_dict() -> dict[str, Any]:
    cases = [_case(i) for i in IDS]
    answers = [SystemAnswer(numeric_values=[100.0 + n], fact_ids=[n + 1], query_id=f"q-{n}")
               for n in range(len(IDS))]
    results = [CaseResult.build(c, a, grade_case(c, a))
               for c, a in zip(cases, answers, strict=True)]
    report = RunReport(
        run_id=SOURCE_ID, suite="xbrl_v2_dev", suite_version="1", system="xbrlrag_full",
        status="OK", results=results, n_planned=len(IDS),
        config={"split": "dev", "resume_identity": {"suite_hash": "sha256:x"}},
    )
    return json.loads(json.dumps(report.to_dict(), default=str))


def _rebuilt_suite() -> list[EvalCase]:
    """a2 and a4 dropped, a3 re-worded by one character, a6 new."""
    return [_case("a1"), _case("a3", "What was revenue for a3 ?"), _case("a5"), _case("a6")]


def _regrade(source: dict[str, Any], cases: list[EvalCase]) -> RunReport:
    return regrade_report(source, cases, suite="xbrl_v2_dev", suite_version="2",
                          corpus_fingerprint=None, run_id="eval-bbbbbbbbbbbb")


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True)


def test_kept_and_dropped_split_per_reason():
    pruned, dropped = prune.prune_source(_source_dict(), _rebuilt_suite())
    assert [r["case_id"] for r in pruned["results"]] == ["a1", "a5"]
    assert dropped == [
        {"case_id": "a2", "reason": "missing_from_suite"},
        {"case_id": "a3", "reason": "question_changed"},
        {"case_id": "a4", "reason": "missing_from_suite"},
    ]
    assert prune.reason_counts(dropped) == {"missing_from_suite": 2, "question_changed": 1}


def test_kept_cases_and_run_keys_are_unchanged():
    source = _source_dict()
    original = _canon(source)
    pruned, _ = prune.prune_source(source, _rebuilt_suite())
    assert _canon(source) == original  # the source itself is not mutated
    kept = {r["case_id"]: r for r in source["results"]}
    for result in pruned["results"]:
        assert _canon(result) == _canon(kept[result["case_id"]])
    assert set(pruned) == set(source)
    assert all(_canon(pruned[k]) == _canon(source[k]) for k in source if k != "results")


def test_pruning_is_idempotent():
    once, _ = prune.prune_source(_source_dict(), _rebuilt_suite())
    twice, dropped = prune.prune_source(once, _rebuilt_suite())
    assert dropped == []
    assert _canon(twice) == _canon(once)


def test_round_trip_unpruned_refused_pruned_accepted():
    source = _source_dict()
    cases = _rebuilt_suite()
    with pytest.raises(RegradeRefused):
        _regrade(source, cases)
    pruned, _ = prune.prune_source(source, cases)
    report = _regrade(pruned, cases)
    assert [r.case.id for r in report.results] == ["a1", "a5"]


def test_nothing_to_drop_keeps_everything():
    source = _source_dict()
    pruned, dropped = prune.prune_source(source, [_case(i) for i in IDS])
    assert dropped == []
    assert _canon(pruned) == _canon(source)


@pytest.mark.parametrize("mutate", [
    lambda s: s.update(status="RUNNING"),
    lambda s: s.update(results=[]),
    lambda s: s.pop("results"),
    lambda s: s["results"].append(dict(s["results"][0])),  # a repeated case id
    lambda s: s["results"][0].pop("produced"),  # the re-grade would refuse it
])
def test_refuses_sources_pruning_cannot_fix(mutate):
    source = _source_dict()
    mutate(source)
    with pytest.raises(RegradeRefused):
        prune.prune_source(source, [_case(i) for i in IDS])


def test_refuses_when_no_case_matches():
    with pytest.raises(RegradeRefused):
        prune.prune_source(_source_dict(), [_case("zz")])


def test_cli_writes_pruned_source_and_report(tmp_path, capsys):
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(_source_dict()), encoding="utf-8")
    suite_path = tmp_path / "suite.json"
    EvalSuite(name="xbrl_v2_dev", version="2", cases=_rebuilt_suite()).save(str(suite_path))
    out = tmp_path / "out" / "pruned.json"
    report = tmp_path / "out" / "dropped.json"

    def no_store():
        raise AssertionError("a JSON source never touches the store")

    code = prune.main(["--source", str(source_path), "--suite", str(suite_path),
                       "--out", str(out), "--report", str(report)], store=no_store)
    assert code == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert [r["case_id"] for r in written["results"]] == ["a1", "a5"]
    dropped = json.loads(report.read_text(encoding="utf-8"))
    assert dropped["counts"] == {"missing_from_suite": 2, "question_changed": 1}
    assert dropped["n_source"] == 5 and dropped["n_kept"] == 2
    printed = capsys.readouterr().out
    assert "kept 2 of 5" in printed and "a2" not in printed


def test_cli_refuses_running_source_with_exit_2(tmp_path, capsys):
    source = _source_dict()
    source["status"] = "RUNNING"
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    suite_path = tmp_path / "suite.json"
    EvalSuite(name="xbrl_v2_dev", version="2", cases=_rebuilt_suite()).save(str(suite_path))
    out = tmp_path / "pruned.json"
    code = prune.main(["--source", str(source_path), "--suite", str(suite_path),
                       "--out", str(out)])
    assert code == 2
    assert not out.exists()
    assert "RUNNING" in capsys.readouterr().err
