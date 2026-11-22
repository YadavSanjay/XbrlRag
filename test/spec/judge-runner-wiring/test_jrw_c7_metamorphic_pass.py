"""C7: the metamorphic pass pairs a run's own answers and applies the key-free checks."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.judge_wiring import run_graded
from src.eval.core.runner import RunnerConfig
from src.eval.core.system import SystemAnswer
from src.eval.keyfree.runner import metamorphic_pass

CHECKS = ("breakdown_sum", "growth", "screen_order")
VERDICTS = ("consistent", "inconsistent", "not_applicable")


def _lookup_answer(make_response, value, unit="USD"):
    raw = make_response(
        steps=[{"step_id": "s1", "op": "lookup", "status": "OK", "value": value,
                "unit": unit, "citation": {"fact_id": 1}}],
        plan_steps=[{"id": "s1", "op": "lookup", "concept_family": "Revenue"}],
    )
    return SystemAnswer(text="t", raw=raw, numeric_values=[value], fact_ids=[1])


def _breakdown_answer(make_response, values):
    rows = [{"entity": f"m{i}", "value": v, "unit": "USD"} for i, v in enumerate(values)]
    raw = make_response(
        steps=[{"step_id": "s1", "op": "breakdown", "status": "OK", "value": None,
                "unit": "USD", "rows": rows}],
        plan_steps=[{"id": "s1", "op": "breakdown", "concept_family": "Revenue"}],
    )
    return SystemAnswer(text="t", raw=raw, numeric_values=list(values))


def _growth_answer(make_response, percent):
    raw = make_response(
        steps=[
            {"step_id": "s1", "op": "lookup", "status": "OK", "value": 100.0, "unit": "USD"},
            {"step_id": "s2", "op": "lookup", "status": "OK", "value": 80.0, "unit": "USD"},
            {"step_id": "s3", "op": "compute", "status": "OK", "value": percent,
             "unit": None},
        ],
        plan_steps=[
            {"id": "s1", "op": "lookup"},
            {"id": "s2", "op": "lookup"},
            {"id": "s3", "op": "compute", "expr": "(s1 - s2) / s2 * 100"},
        ],
    )
    return SystemAnswer(text="t", raw=raw, numeric_values=[percent])


def _screen_answer(make_response, tickers, dimensions=None):
    rows = [{"ticker": t, "value": float(10 - i), "unit": "USD"}
            for i, t in enumerate(tickers)]
    step = {"id": "s1", "op": "screen", "concept_family": "Revenue", "fy": 2024,
            "order": "DESC", "metric_kind": "value"}
    if dimensions is not None:
        step["dimensions"] = dimensions
    raw = make_response(
        steps=[{"step_id": "s1", "op": "screen", "status": "OK", "value": None,
                "unit": "USD", "rows": rows}],
        plan_steps=[step],
    )
    return SystemAnswer(text="t", raw=raw, rows=rows)


def _pairs(make_case, make_response):
    def lk(cid, ticker, fy, value):
        case = make_case(cid, kind="lookup", ticker=ticker, fy=fy, concept="Revenue")
        return case, _lookup_answer(make_response, value)

    def bd(cid, ticker, values):
        case = make_case(cid, kind="breakdown", ticker=ticker, fy=2024, concept="Revenue")
        return case, _breakdown_answer(make_response, values)

    return [
        lk("lk-aaa-24", "AAA", 2024, 100.0),
        lk("lk-aaa-23", "aaa", 2023, 80.0),
        lk("lk-bbb-24", "BBB", 2024, 300.0),
        lk("lk-ccc-24", "CCC", 2024, 200.0),
        bd("bd-aaa", "AAA", [60.0, 40.0]),
        bd("bd-bbb", "BBB", [200.0, 115.0]),
        bd("bd-ddd", "DDD", [1.0, 2.0]),
        (make_case("bd-ccc", kind="breakdown", ticker="CCC", fy=2024, concept="Revenue"),
         SystemAnswer(error="RuntimeError('down')")),
        (make_case("gr-aaa", kind="compute", ticker="AAA", fy=2024, concept="Revenue",
                   tags=["yoy", "growth"]),
         _growth_answer(make_response, 25.0)),
        (make_case("sc-24", kind="screen", fy=2024, concept="Revenue"),
         _screen_answer(make_response, ["BBB", "AAA", "CCC"])),
        (make_case("sc-geo", kind="screen", fy=2024, concept="Revenue"),
         _screen_answer(make_response, ["AAA", "BBB"],
                        dimensions={"srt:StatementGeographicalAxis": "CN"})),
    ]


# oracle: requirements 9 to 11 and the keyfree-checks closed forms, by hand —
#   bd-aaa: 60 + 40 = 100 = lk-aaa-24 -> consistent
#   bd-bbb: 200 + 115 = 315 vs 300, 5% > rel_tol 1e-3 -> inconsistent (mismatch)
#   bd-ddd: no DDD lookup -> no verdict
#   bd-ccc: its answer errored -> not_applicable, answer_error
#   gr-aaa: 25.0 from a "* 100" expr -> 0.25 = (100 - 80) / 80 -> consistent
#   sc-24: ranked BBB, AAA, CCC desc; lookups BBB 300, AAA 100, CCC 200 -> only the pair
#          (AAA, CCC) is inverted -> inconsistent (misordered)
#   sc-geo: dimensioned -> no verdict
EXPECTED_CHECKS = {
    "breakdown_sum": {"consistent": ["bd-aaa"], "inconsistent": ["bd-bbb"],
                      "not_applicable": ["bd-ccc"]},
    "growth": {"consistent": ["gr-aaa"], "inconsistent": [], "not_applicable": []},
    "screen_order": {"consistent": [], "inconsistent": ["sc-24"], "not_applicable": []},
}
EXPECTED_RELATED = {
    "bd-aaa": ["lk-aaa-24"],
    "bd-bbb": ["lk-bbb-24"],
    "bd-ccc": ["lk-ccc-24"],
    "gr-aaa": ["lk-aaa-24", "lk-aaa-23"],
    "sc-24": ["lk-aaa-24", "lk-bbb-24", "lk-ccc-24"],
}


def _counts(checks: dict) -> dict:
    return {c: {v: len(ids) for v, ids in by.items()} for c, by in checks.items()}


@pytest.mark.criterion("C7")
def test_hand_built_run(make_case, make_response):
    # oracle: EXPECTED_CHECKS and EXPECTED_RELATED above (worked by hand)
    out = metamorphic_pass(_pairs(make_case, make_response))
    assert out["checks"] == EXPECTED_CHECKS
    assert out["counts"] == _counts(EXPECTED_CHECKS)

    by_case = {(v["check"], v["case_id"]): v for v in out["verdicts"]}
    assert len(out["verdicts"]) == 5
    assert {cid: v["related_case_ids"] for (_, cid), v in by_case.items()} == EXPECTED_RELATED

    bbb = by_case[("breakdown_sum", "bd-bbb")]
    assert (bbb["verdict"], bbb["reason"]) == ("inconsistent", "mismatch")
    # tolerance: sums of two round floats are exact
    assert bbb["observed"] == pytest.approx(315.0, abs=1e-9)
    assert bbb["expected"] == pytest.approx(300.0, abs=1e-9)
    ccc = by_case[("breakdown_sum", "bd-ccc")]
    assert (ccc["verdict"], ccc["reason"]) == ("not_applicable", "answer_error")
    growth = by_case[("growth", "gr-aaa")]
    # tolerance: (100 - 80) / 80 and 25 / 100 are both exactly 0.25 in binary
    assert growth["observed"] == pytest.approx(0.25, abs=1e-12)
    assert growth["expected"] == pytest.approx(0.25, abs=1e-12)
    screen = by_case[("screen_order", "sc-24")]
    assert (screen["verdict"], screen["reason"]) == ("inconsistent", "misordered")
    assert screen["inversions"] == [["AAA", "CCC"]]
    assert screen["order"] == "desc"


@pytest.mark.criterion("C7")
def test_empty_run_has_every_check_with_zero_counts():
    # oracle: requirement 9 — every check appears, with zero counts when it never applied
    out = metamorphic_pass([])
    assert set(out["checks"]) == set(CHECKS)
    for check in CHECKS:
        assert out["checks"][check] == {v: [] for v in VERDICTS}
        assert out["counts"][check] == dict.fromkeys(VERDICTS, 0)
    assert out["verdicts"] == []


@pytest.mark.criterion("C7")
def test_run_graded_records_the_pass(make_case, make_response, fake_system):
    # oracle: requirement 7 — config["keyfree"] is the pass over the run's own answers,
    # absent with metamorphic=False
    pairs = _pairs(make_case, make_response)
    cases = [c for c, _ in pairs]
    answers = {c.id: a for c, a in pairs}
    cfg = RunnerConfig(max_workers=1, progress_every=0)
    report = run_graded(cases, fake_system(answers), config=cfg)
    assert report.config["keyfree"]["checks"] == EXPECTED_CHECKS
    assert report.config["keyfree"]["counts"] == _counts(EXPECTED_CHECKS)
    off = run_graded(cases, fake_system(answers), metamorphic=False, config=cfg)
    assert "keyfree" not in off.config
    assert len(off.results) == len(cases)


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(data=st.data())
def test_pair_order_does_not_matter(make_case, make_response, data):
    # oracle: requirement 11 — each subject has one partner set, so pairing is order-free
    pairs = _pairs(make_case, make_response)
    shuffled = data.draw(st.permutations(pairs))
    out = metamorphic_pass(shuffled)
    assert out["checks"] == EXPECTED_CHECKS
    assert out["counts"] == _counts(EXPECTED_CHECKS)
    related = {v["case_id"]: v["related_case_ids"] for v in out["verdicts"]}
    assert related == EXPECTED_RELATED
