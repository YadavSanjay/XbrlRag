"""src/eval/core/key_audit.py and scripts/eval/key_consensus_audit.py.

Synthetic dev runs and suites only: hand-built cases and answer payloads, an in-memory run
reader at the store boundary, JSON reports in tmp_path. No DB, no LLM, no network.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from src.eval.core.case import EvalCase, EvalSuite
from src.eval.core.key_audit import (
    ArmRun,
    AuditRefused,
    agree,
    arm_run_from_report,
    asserted_values,
    check_split,
    find_consensus,
    format_report,
    load_arm_run,
)

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval" / "key_consensus_audit.py"
_spec = importlib.util.spec_from_file_location("key_consensus_audit", _SCRIPT)
cli = importlib.util.module_from_spec(_spec)
sys.modules["key_consensus_audit"] = cli
_spec.loader.exec_module(cli)

HELDOUT = ("heldout_filers", "external", "temporal")


def _case(case_id: str, value: float | None, *, kind: str = "lookup", split: str = "dev",
          concept: str = "CommonStockSharesOutstanding", **extra: Any) -> EvalCase:
    return EvalCase(
        id=case_id,
        question=f"How many shares did {case_id} have outstanding?",
        kind=kind,
        expected_value=value,
        ticker=case_id.upper(),
        fy=2024,
        concept=concept,
        unit="shares",
        tags=[f"split:{split}"],
        **extra,
    )


def _result(case: EvalCase, values: list[float], **flags: Any) -> dict[str, Any]:
    return {
        "case_id": case.id,
        "question": case.question,
        "kind": case.kind,
        "tags": list(case.tags),
        "produced": {"numeric_values": values, "fact_ids": [], **flags},
    }


def _run(system: str, results: list[dict[str, Any]], split: str = "dev") -> ArmRun:
    return ArmRun(run_id=f"eval-{system}", system=system, split=split, results=tuple(results))


# PG: the key is wrong (2.35e9), three arms say 2.4e9 (one within 1e-4 of the others).
PG = _case("pg", 2_350_000_000.0)
# OK: the key is right; two arms match it, one is off on its own.
OK = _case("ok", 100.0)
# SPLIT: two arms are wrong, but each in its own way.
SPLIT = _case("split", 500.0)
CASES = {c.id: c for c in (PG, OK, SPLIT)}


def _three_arms() -> list[ArmRun]:
    return [
        _run("xbrlrag_full", [_result(PG, [2_400_000_000.0]), _result(OK, [100.0]),
                              _result(SPLIT, [600.0])]),
        _run("long_context_oracle", [_result(PG, [2_400_100_000.0]), _result(OK, [100.0]),
                                     _result(SPLIT, [612.0])]),
        _run("closed_book", [_result(PG, [2_400_000_000.0, 7.0]), _result(OK, [90.0]),
                             _result(SPLIT, [], refused=True)]),
    ]


# --- the consensus rule ------------------------------------------------------------------


def test_finds_the_case_whose_key_the_arms_overrule():
    report = find_consensus(_three_arms(), CASES)
    assert [f.case_id for f in report.findings] == ["pg"]
    f = report.findings[0]
    assert f.kind == "lookup"
    assert f.concept == "CommonStockSharesOutstanding"
    assert f.key_value == pytest.approx(2_350_000_000.0)
    assert f.consensus_value == pytest.approx(2_400_000_000.0)
    assert f.consensus_arms == ["xbrlrag_full", "long_context_oracle", "closed_book"]
    assert f.key_arms == []
    assert f.arm_values["closed_book"] == [2_400_000_000.0, 7.0]
    assert report.n_compared == 3


def test_arms_that_match_the_key_are_not_a_consensus_against_it():
    report = find_consensus(_three_arms(), CASES)
    assert "ok" not in {f.case_id for f in report.findings}


def test_arms_wrong_in_different_ways_are_not_a_consensus():
    # 600 vs 612 is 2% apart: far outside the key's 1e-4 tolerance.
    report = find_consensus(_three_arms(), CASES)
    assert "split" not in {f.case_id for f in report.findings}
    # With a loose enough override they do agree.
    loose = find_consensus(_three_arms(), CASES, rel_tol=0.05)
    assert "split" in {f.case_id for f in loose.findings}


def test_the_keys_own_tolerance_decides_agreement():
    wide = _case("wide", 1000.0, rel_tol=0.01)
    runs = [_run("a", [_result(wide, [1100.0])]), _run("b", [_result(wide, [1105.0])])]
    assert [f.case_id for f in find_consensus(runs, {"wide": wide}).findings] == ["wide"]
    narrow = _case("wide", 1000.0, rel_tol=1e-4)
    assert find_consensus(runs, {"wide": narrow}).findings == []


def test_one_arm_matching_the_key_is_reported_beside_the_consensus():
    runs = [
        _run("a", [_result(PG, [2_400_000_000.0])]),
        _run("b", [_result(PG, [2_400_000_000.0])]),
        _run("c", [_result(PG, [2_350_000_000.0])]),
    ]
    (f,) = find_consensus(runs, CASES).findings
    assert f.consensus_arms == ["a", "b"]
    assert f.key_arms == ["c"]


def test_alt_values_count_as_the_key():
    case = _case("alt", 0.25, alt_values=[25.0])
    runs = [_run("a", [_result(case, [25.0])]), _run("b", [_result(case, [25.0])])]
    assert find_consensus(runs, {"alt": case}).findings == []


@pytest.mark.parametrize("flags", [{"refused": True}, {"hedged": True}, {"error": "Boom()"}])
def test_refused_hedged_and_errored_answers_assert_nothing(flags):
    runs = [
        _run("a", [_result(PG, [2_400_000_000.0])]),
        _run("b", [_result(PG, [2_400_000_000.0], **flags)]),
    ]
    assert find_consensus(runs, CASES).findings == []
    assert asserted_values(_result(PG, [1.0], **flags)) is None


def test_min_arms_raises_the_bar():
    report = find_consensus(_three_arms(), CASES, min_arms=4)
    assert report.findings == []
    with pytest.raises(AuditRefused):
        find_consensus(_three_arms(), CASES, min_arms=1)


def test_non_scalar_and_unmatched_cases_are_skipped_and_counted():
    screen = _case("scr", None, kind="screen", expected_rows=[{"ticker": "A", "value": 1.0}])
    moved = EvalCase.from_dict({**PG.to_dict(), "question": "A different question?"})
    runs = [
        _run("a", [_result(screen, []), _result(PG, [2_400_000_000.0]),
                   {**_result(OK, [100.0]), "case_id": "gone"}]),
        _run("b", [_result(screen, []), _result(PG, [2_400_000_000.0])]),
    ]
    report = find_consensus(runs, {"scr": screen, "pg": moved})
    assert report.findings == []
    assert report.skipped["not_scalar"] == ["scr"]
    assert report.skipped["question_changed"] == ["a:pg", "b:pg"]
    assert report.skipped["not_in_suite"] == ["a:gone"]


def test_findings_are_ordered_by_agreement_then_id():
    two = _case("b2", 10.0)
    three = _case("a3", 10.0)
    runs = [
        _run("x", [_result(two, [20.0]), _result(three, [30.0])]),
        _run("y", [_result(two, [20.0]), _result(three, [30.0])]),
        _run("z", [_result(two, [99.0]), _result(three, [30.0])]),
    ]
    report = find_consensus(runs, {"b2": two, "a3": three})
    assert [f.case_id for f in report.findings] == ["a3", "b2"]


def test_agreement_is_symmetric():
    assert agree(100.0, 100.009, 1e-4) and agree(100.009, 100.0, 1e-4)
    assert not agree(100.0, 101.0, 1e-4)


def test_repeated_systems_refuse():
    runs = [_run("a", [_result(PG, [1.0])]), _run("a", [_result(PG, [1.0])])]
    with pytest.raises(AuditRefused, match="different system"):
        find_consensus(runs, CASES)


# --- dev only ----------------------------------------------------------------------------


@pytest.mark.parametrize("split", [*HELDOUT, "validation", "legacy", None])
def test_check_split_refuses_everything_but_dev(split):
    with pytest.raises(AuditRefused, match="split"):
        check_split("eval-x", split)
    check_split("eval-x", "dev")


@pytest.mark.parametrize("split", [*HELDOUT, "validation"])
def test_a_non_dev_arm_run_refuses_in_find_consensus(split):
    runs = [_run("a", [_result(PG, [1.0])], split=split), _run("b", [_result(PG, [1.0])])]
    with pytest.raises(AuditRefused, match=split):
        find_consensus(runs, CASES)


@pytest.mark.parametrize("split", HELDOUT)
def test_a_heldout_suite_refuses(split):
    with pytest.raises(AuditRefused, match=split):
        find_consensus(_three_arms(), {"x": _case("x", 1.0, split=split)})


class _Reader:
    """An in-memory run reader (the store boundary) that records what was read."""

    def __init__(self, rows: dict[str, dict], cases: dict[str, list[dict]]) -> None:
        self.rows, self.cases, self.case_reads = rows, cases, []

    def run_row(self, run_id: str):
        return self.rows.get(run_id)

    def case_rows(self, run_id: str):
        self.case_reads.append(run_id)
        return self.cases.get(run_id, [])


@pytest.mark.parametrize("split", [*HELDOUT, "validation", "legacy"])
def test_a_stored_non_dev_run_is_refused_before_its_cases_are_read(split):
    reader = _Reader({"eval-h": {"run_id": "eval-h", "system": "a", "split": split}},
                     {"eval-h": [_result(PG, [1.0])]})
    with pytest.raises(AuditRefused, match=split):
        load_arm_run("eval-h", reader)
    assert reader.case_reads == []


def test_a_stored_dev_run_loads_and_the_config_split_is_a_fallback():
    reader = _Reader(
        {"eval-d": {"run_id": "eval-d", "system": "a", "split": None,
                    "config": json.dumps({"split": "dev"})}},
        {"eval-d": [_result(PG, [2_400_000_000.0])]},
    )
    run = load_arm_run("eval-d", reader)
    assert (run.system, run.split, len(run.results)) == ("a", "dev", 1)
    with pytest.raises(AuditRefused, match="unknown"):
        load_arm_run("eval-none", reader)


def test_a_dev_run_holding_a_heldout_case_refuses():
    bad = {**_result(PG, [1.0]), "tags": ["split:heldout_filers"]}
    with pytest.raises(AuditRefused, match="heldout_filers"):
        arm_run_from_report({"run_id": "eval-r", "system": "a", "split": "dev",
                             "results": [bad]})


def test_a_report_split_falls_back_to_its_config():
    report = {"run_id": "eval-r", "system": "a", "config": {"split": "temporal"},
              "results": [_result(PG, [1.0])]}
    with pytest.raises(AuditRefused, match="temporal"):
        arm_run_from_report(report)


# --- output and CLI ----------------------------------------------------------------------


def test_report_dict_and_table_name_ids_kind_values_key_and_concept():
    report = find_consensus(_three_arms(), CASES)
    data = json.loads(json.dumps(report.to_dict()))
    assert data["n_findings"] == 1
    (f,) = data["findings"]
    assert set(f) >= {"case_id", "kind", "arm_values", "key_value", "concept",
                      "consensus_value", "consensus_arms"}
    table = format_report(report)
    for text in ("pg", "[lookup]", "CommonStockSharesOutstanding", "2.35e+09", "2.4e+09",
                 "long_context_oracle"):
        assert text in table


def _write_report(path: Path, system: str, split: str, results: list[dict]) -> str:
    path.write_text(json.dumps({"run_id": f"eval-{system}", "system": system, "split": split,
                                "results": results}), encoding="utf-8")
    return str(path)


def _write_suite(path: Path, cases: list[EvalCase]) -> str:
    EvalSuite(name="xbrl_v2_dev", version="1", cases=cases).save(str(path))
    return str(path)


def test_cli_writes_the_findings(tmp_path, capsys):
    suite = _write_suite(tmp_path / "suite.json", [PG, OK])
    a = _write_report(tmp_path / "a.json", "a", "dev", [_result(PG, [2_400_000_000.0])])
    b = _write_report(tmp_path / "b.json", "b", "dev", [_result(PG, [2_400_000_000.0])])
    out = tmp_path / "audit.json"
    assert cli.main(["--suite", suite, "--runs", a, b, "--out", str(out)]) == 0
    assert "pg" in capsys.readouterr().out
    assert [f["case_id"] for f in json.loads(out.read_text())["findings"]] == ["pg"]


@pytest.mark.parametrize("split", HELDOUT)
def test_cli_refuses_a_heldout_run_without_printing_its_cases(tmp_path, capsys, split):
    suite = _write_suite(tmp_path / "suite.json", [PG])
    a = _write_report(tmp_path / "a.json", "a", "dev", [_result(PG, [2_400_000_000.0])])
    h = _write_report(tmp_path / "h.json", "h", split, [_result(PG, [2_400_000_000.0])])
    assert cli.main(["--suite", suite, "--runs", a, h]) == 2
    captured = capsys.readouterr()
    assert split in captured.err
    assert "pg" not in captured.out and "pg" not in captured.err
