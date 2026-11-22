"""Unit tests for key-free check behaviour beyond docs/specs/keyfree-checks.md."""

from __future__ import annotations

import pytest

from src.eval.keyfree.metamorphic import check_breakdown_sum, check_growth, check_screen_order
from src.eval.keyfree.perturbation import check_plan_perturbation, perturb_ticker, plan_slots


def _amt(value, unit="USD"):
    return {"value": value, "unit": unit}


def test_too_few_rows_verdict_carries_null_numbers():
    v = check_breakdown_sum([_amt(1.0)], _amt(1.0))
    assert (v["observed"], v["expected"], v["tolerance"]) == (None, None, None)


def test_not_meaningful_growth_carries_the_stated_value():
    v = check_growth(_amt(0.5, "pure"), _amt(50.0), _amt(-10.0))
    assert v["observed"] == 0.5
    assert v["expected"] is None


def test_screen_row_without_a_string_ticker_is_skipped():
    rows = [{"ticker": None, "unit": "USD"}, {"ticker": "A", "unit": "USD"},
            {"ticker": "B", "unit": None}]
    v = check_screen_order(rows, {"A": _amt(1.0), "B": _amt(2.0)})
    assert v["skipped"] == [None, "B"]
    assert v["reason"] == "too_few_rows"


def test_expr_keeps_scientific_numbers_whole():
    plan = {"steps": [{"id": "e5", "op": "lookup"},
                      {"id": "c", "op": "compute", "expr": "e5 * 1e5 + 2.5E-3"}]}
    assert plan_slots(plan)[1]["expr"] == "$0*1e5+2.5E-3"


def test_duplicate_step_ids_resolve_to_the_first_step():
    plan = {"steps": [{"id": "a", "op": "lookup"}, {"id": "a", "op": "lookup"},
                      {"id": "c", "op": "compute", "expr": "a + a"}]}
    assert plan_slots(plan)[2]["expr"] == "$0+$0"


def test_non_dict_step_raises():
    with pytest.raises(ValueError):
        plan_slots({"steps": [{"op": "lookup"}, "lookup"]})


def test_non_dict_step_is_no_plan_in_the_perturbation_check():
    plan = {"steps": [{"op": "lookup", "ticker": "AAPL"}]}
    v = check_plan_perturbation(plan, {"steps": ["x"]}, {"kind": "year", "from": 1, "to": 2})
    assert (v["verdict"], v["reason"]) == ("not_applicable", "no_plan")


def test_negative_and_bool_ciks_are_kept():
    plan = {"steps": [{"op": "lookup", "cik": -5}, {"op": "lookup", "cik": True}]}
    assert [s["cik"] for s in plan_slots(plan)] == [-5, True]


def test_ticker_perturbation_with_one_cik_raises():
    plan = {"steps": [{"op": "lookup", "ticker": "AAPL"}]}
    with pytest.raises(ValueError):
        check_plan_perturbation(plan, plan, {"kind": "ticker", "from": "AAPL", "to": "MSFT",
                                             "from_cik": "1"})


def test_ciks_given_as_ints_are_normalised_before_comparing():
    orig = {"steps": [{"op": "lookup", "ticker": "AAPL", "cik": "0000320193"}]}
    pert = {"steps": [{"op": "lookup", "ticker": "MSFT", "cik": "0000789019"}]}
    v = check_plan_perturbation(orig, pert, {"kind": "ticker", "from": "AAPL", "to": "MSFT",
                                             "from_cik": 320193, "to_cik": 789019})
    assert (v["verdict"], v["reason"]) == ("consistent", "match")


def test_non_int_fy_is_not_shifted():
    orig = {"steps": [{"op": "lookup", "fy": None}, {"op": "lookup", "fy": 2024}]}
    pert = {"steps": [{"op": "lookup", "fy": None}, {"op": "lookup", "fy": 2025}]}
    v = check_plan_perturbation(orig, pert, {"kind": "year", "from": 2024, "to": 2025})
    assert (v["verdict"], v["reason"]) == ("consistent", "match")


def test_name_with_trailing_punctuation_matches_at_end_of_question():
    got = perturb_ticker("Tell me about Apple Inc.", "AAPL", "MSFT",
                         {"AAPL": ["Apple Inc.", "Apple"], "MSFT": ["Microsoft"]})
    assert got["question"] == "Tell me about Microsoft"
