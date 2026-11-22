"""C9: the perturbation runner answers each pair once, bounded, and applies both checks."""

from __future__ import annotations

import pytest

from src.eval.core.system import SystemAnswer
from src.eval.keyfree.runner import run_perturbation_checks
from src.llm.run_budget import RunBudgetExceeded, RunHalt

CHECKS = ("plan_perturbation", "answer_perturbation")


def _never_equal(_original, _twin) -> bool:
    return False


def _answer(make_response, ticker, fy, concept, value, cost=0.1):
    plan = [{"id": "s1", "op": "lookup", "ticker": ticker, "fy": fy,
             "concept_family": concept}]
    steps = [{"step_id": "s1", "op": "lookup", "status": "OK", "value": value,
              "unit": "USD"}]
    raw = make_response(steps=steps, plan_steps=plan)
    return SystemAnswer(text="t", raw=raw, numeric_values=[value], cost_usd=cost)


def _setup(make_case, make_response):
    o1 = make_case("o1", question="What was AAA's revenue in 2024?", ticker="AAA", fy=2024,
                   concept="Revenue")
    o2 = make_case("o2", question="What was CCC's revenue in 2024?", ticker="CCC", fy=2024,
                   concept="Revenue")
    t1 = make_case("o1~ticker:BBB", question="What was BBB's revenue in 2024?",
                   ticker="BBB", fy=2024, concept="Revenue")
    t2 = make_case("o1~year:2023", question="What was AAA's revenue in 2023?",
                   ticker="AAA", fy=2023, concept="Revenue")
    t3 = make_case("o2~ticker:AAA", question="What was AAA's revenue in 2024?",
                   ticker="AAA", fy=2024, concept="Revenue")
    twins = [
        {"original": o1, "twin": t1, "perturbation": {"kind": "ticker", "from": "AAA",
                                                      "to": "BBB"}},
        {"original": o1, "twin": t2, "perturbation": {"kind": "year", "from": 2024,
                                                      "to": 2023}},
        {"original": o2, "twin": t3, "perturbation": {"kind": "ticker", "from": "CCC",
                                                      "to": "AAA"}},
    ]
    answers = {
        "o1": _answer(make_response, "AAA", 2024, "Revenue", 100.0),
        # only the ticker changed, and the value with it
        "o1~ticker:BBB": _answer(make_response, "BBB", 2024, "Revenue", 300.0),
        # the year changed, but so did the concept; and the value did not change
        "o1~year:2023": _answer(make_response, "AAA", 2023, "NetIncome", 100.0),
        "o2": _answer(make_response, "CCC", 2024, "Revenue", 200.0),
    }
    return twins, answers


# oracle: requirement 13 and the keyfree-checks closed forms, by hand —
#   o1~ticker:BBB: plan differs only in ticker AAA -> BBB (match); 100 -> 300 with lookups
#                  not equal (match) -> consistent on both
#   o1~year:2023: fy 2024 -> 2023 as expected, but concept_family also changed
#                 (unexpected_change); 100 -> 100 with lookups not equal (unchanged)
#   o2~ticker:AAA: the system raised -> an errored answer, no plan (no_plan) and no
#                  values (missing_value) -> not_applicable on both
EXPECTED = {
    "plan_perturbation": {"consistent": ["o1~ticker:BBB"], "inconsistent": ["o1~year:2023"],
                          "not_applicable": ["o2~ticker:AAA"]},
    "answer_perturbation": {"consistent": ["o1~ticker:BBB"],
                            "inconsistent": ["o1~year:2023"],
                            "not_applicable": ["o2~ticker:AAA"]},
}


def _counts(checks: dict) -> dict:
    return {c: {v: len(ids) for v, ids in by.items()} for c, by in checks.items()}


@pytest.mark.criterion("C9")
def test_hand_built_pairs(make_case, make_response, fake_system):
    # oracle: EXPECTED above (worked by hand)
    twins, answers = _setup(make_case, make_response)
    system = fake_system(answers, raises={"o2~ticker:AAA": RuntimeError("pipeline down")})
    out = run_perturbation_checks(twins, system, lookups_equal=_never_equal, max_workers=1)

    assert out["checks"] == EXPECTED
    assert out["counts"] == _counts(EXPECTED)
    assert sorted(system.answered) == sorted(["o1", "o1~ticker:BBB", "o1~year:2023", "o2",
                                              "o2~ticker:AAA"])
    assert system.answered.count("o1") == 1
    assert out["skipped"] == []
    assert out["n_pairs"] == 3
    # tolerance: four answers of 0.1 each; float summation error only
    assert out["cost_usd"] == pytest.approx(0.4, abs=1e-12)

    by_key = {(v["check"], v["case_id"]): v for v in out["verdicts"]}
    assert len(out["verdicts"]) == 6
    assert by_key[("plan_perturbation", "o1~year:2023")]["reason"] == "unexpected_change"
    assert by_key[("answer_perturbation", "o1~year:2023")]["reason"] == "unchanged"
    assert by_key[("plan_perturbation", "o2~ticker:AAA")]["reason"] == "no_plan"
    assert by_key[("answer_perturbation", "o2~ticker:AAA")]["reason"] == "missing_value"
    for (_, cid), v in by_key.items():
        assert v["related_case_ids"] == [cid.split("~")[0]]


@pytest.mark.criterion("C9")
def test_lookups_equal_flips_the_answer_check(make_case, make_response, fake_system):
    # oracle: keyfree-checks answer perturbation — with equal lookups an unchanged value
    # is consistent and a changed one inconsistent
    twins, answers = _setup(make_case, make_response)
    out = run_perturbation_checks(twins[:2], fake_system(answers),
                                  lookups_equal=lambda _o, _t: True, max_workers=1)
    assert out["checks"]["answer_perturbation"] == {
        "consistent": ["o1~year:2023"], "inconsistent": ["o1~ticker:BBB"],
        "not_applicable": []}


@pytest.mark.criterion("C9")
def test_run_halt_propagates(make_case, make_response, fake_system):
    # oracle: requirement 13 — RunHalt from the system is never turned into an error
    twins, answers = _setup(make_case, make_response)
    system = fake_system(answers, raises={"o1~ticker:BBB": RunBudgetExceeded("cap")})
    with pytest.raises(RunHalt):
        run_perturbation_checks(twins, system, lookups_equal=_never_equal, max_workers=1)


@pytest.mark.criterion("C9")
def test_cost_cap_skips_later_pairs(make_case, make_response, fake_system):
    # oracle: requirement 13 — answers cost 0.5; three pairs with distinct originals; the
    # cap 1.5 is not reached after pair 1 (1.0) but is after pair 2 (2.0), so pair 3 is
    # skipped
    def case(cid, ticker):
        return make_case(cid, question=f"What was {ticker}'s revenue in 2024?",
                         ticker=ticker, fy=2024, concept="Revenue")

    twins, answers = [], {}
    for i, (frm, to) in enumerate([("AAA", "BBB"), ("BBB", "CCC"), ("CCC", "AAA")]):
        o, t = case(f"o{i}", frm), case(f"o{i}~ticker:{to}", to)
        twins.append({"original": o, "twin": t,
                      "perturbation": {"kind": "ticker", "from": frm, "to": to}})
        answers[o.id] = _answer(make_response, frm, 2024, "Revenue", 10.0 + i, cost=0.5)
        answers[t.id] = _answer(make_response, to, 2024, "Revenue", 20.0 + i, cost=0.5)
    system = fake_system(answers)
    out = run_perturbation_checks(twins, system, lookups_equal=_never_equal, max_workers=1,
                                  max_cost_usd=1.5)
    assert out["skipped"] == ["o2~ticker:AAA"]
    assert out["n_pairs"] == 2
    assert system.answered == ["o0", "o0~ticker:BBB", "o1", "o1~ticker:CCC"]
    # tolerance: four answers of 0.5 are exact in binary
    assert out["cost_usd"] == pytest.approx(2.0, abs=1e-12)
    assert out["counts"]["plan_perturbation"]["consistent"] == 2


@pytest.mark.criterion("C9")
def test_bad_worker_count(make_case, make_response, fake_system):
    # oracle: requirement 13 — max_workers < 1 raises
    twins, answers = _setup(make_case, make_response)
    with pytest.raises(ValueError):
        run_perturbation_checks(twins, fake_system(answers), lookups_equal=_never_equal,
                                max_workers=0)


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
def test_counts_do_not_depend_on_workers(make_case, make_response, fake_system):
    # oracle: requirement 13 — concurrency bounds the run, it does not change verdicts
    twins, answers = _setup(make_case, make_response)
    raises = {"o2~ticker:AAA": RuntimeError("pipeline down")}
    serial = run_perturbation_checks(twins, fake_system(answers, raises=raises),
                                     lookups_equal=_never_equal, max_workers=1)
    wide_system = fake_system(answers, raises=raises)
    wide = run_perturbation_checks(twins, wide_system, lookups_equal=_never_equal,
                                   max_workers=4)
    assert wide["checks"] == serial["checks"] == EXPECTED
    assert wide["counts"] == serial["counts"]
    assert wide_system.answered.count("o1") == 1
