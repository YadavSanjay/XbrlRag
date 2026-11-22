"""C2: get_concept reads several filers in one call (ciks)."""

from __future__ import annotations

import json

import pytest
from ftv2_payloads import (
    AAA_CIK,
    BBB_CIK,
    BBB_DESCRIPTION,
    BBB_LABEL,
    CCC_CIK,
    HOLD_CIK,
    REVENUES_DESCRIPTION,
    REVENUES_LABEL,
)
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.sec_tools.toolbox import tool_specs

REVENUES = {"taxonomy": "us-gaap", "tag": "Revenues"}


def _canon(rows: list[dict]) -> list[str]:
    return sorted(json.dumps(r, sort_keys=True) for r in rows)


def _ok(toolbox, arguments: dict, **kwargs) -> dict:
    outcome = toolbox.execute("get_concept", arguments, **kwargs)
    assert outcome.error_code is None, outcome.payload
    return outcome.payload


@pytest.mark.criterion("C2")
def test_two_filers_in_one_call(make_toolbox):
    # oracle: the fixture, counted by hand — AAA has 4 known Revenues facts (the 150 is filed
    # after the date), BBB 4 (three USD, one EUR); label and description are AAA's, the first
    # filer given; units USD (both) then EUR (BBB's), in first-seen order
    toolbox, _ = make_toolbox()
    payload = _ok(toolbox, {"ciks": ["1", "2"], **REVENUES, "limit": 1000})
    assert payload["total"] == 8
    assert sorted(r["val"] for r in payload["rows"]) == [30, 35, 40, 50, 100, 101, 119, 120]
    assert {r["cik"] for r in payload["rows"]} == {AAA_CIK, BBB_CIK}
    assert payload["label"] == REVENUES_LABEL
    assert payload["description"] == REVENUES_DESCRIPTION
    assert payload["units"] == ["USD", "EUR"]
    assert payload["not_found"] == []


@pytest.mark.criterion("C2")
def test_label_comes_from_the_first_reporting_filer_and_non_reporters_are_listed(make_toolbox):
    # oracle: requirement 2 — CCC reports no Revenues, so BBB is the first reporting filer given
    toolbox, _ = make_toolbox()
    payload = _ok(toolbox, {"ciks": ["3", "2", "1"], **REVENUES})
    assert payload["label"] == BBB_LABEL
    assert payload["description"] == BBB_DESCRIPTION
    assert payload["units"] == ["USD", "EUR"]
    assert payload["not_found"] == [CCC_CIK]
    assert payload["total"] == 8


@pytest.mark.criterion("C2")
def test_each_filer_is_filtered_as_a_one_filer_call(make_toolbox):
    # oracle: by hand — fiscal_year 2023 and form 10-K keep AAA's 101, 120 and 119 and BBB's 40
    # (BBB's 30 is a 10-Q); end_to 2022-12-31 then keeps 101 alone; unit EUR keeps BBB's 35
    toolbox, _ = make_toolbox()
    base = {"ciks": ["1", "2"], **REVENUES}
    rows = _ok(toolbox, {**base, "fiscal_year": 2023, "form": "10-K"})["rows"]
    assert sorted(r["val"] for r in rows) == [40, 101, 119, 120]
    rows = _ok(toolbox, {**base, "fiscal_year": 2023, "form": "10-K",
                         "end_to": "2022-12-31"})["rows"]
    assert [r["val"] for r in rows] == [101]
    rows = _ok(toolbox, {**base, "unit": "EUR"})["rows"]
    assert [(r["val"], r["cik"]) for r in rows] == [(35, BBB_CIK)]
    rows = _ok(toolbox, {**base, "end_from": "2023-06-30", "end_to": "2023-09-30"})["rows"]
    assert [r["val"] for r in rows] == [30]


@pytest.mark.criterion("C2")
def test_not_found_only_when_no_filer_reports(make_toolbox):
    # oracle: requirement 2 — CCC alone reports no Revenues
    toolbox, _ = make_toolbox()
    outcome = toolbox.execute("get_concept", {"ciks": ["3"], **REVENUES})
    assert outcome.error_code == "not_found"
    payload = _ok(toolbox, {"ciks": ["3", "1"], **REVENUES})
    assert payload["not_found"] == [CCC_CIK]
    assert {r["cik"] for r in payload["rows"]} == {AAA_CIK}


@pytest.mark.criterion("C2")
def test_a_withheld_filer_fails_the_call_unless_lifted(make_toolbox):
    # oracle: requirement 2 — as get_frame does with ciks; lifted, HOLD's 999 is read
    toolbox, _ = make_toolbox(withheld=(HOLD_CIK,))
    outcome = toolbox.execute("get_concept", {"ciks": ["1", "9"], **REVENUES})
    assert outcome.error_code == "withheld"
    payload = _ok(toolbox, {"ciks": ["1", "9"], **REVENUES}, lift_withheld=True)
    assert 999 in [r["val"] for r in payload["rows"]]
    assert payload["total"] == 5


@pytest.mark.criterion("C2")
def test_duplicates_are_read_once(make_toolbox):
    # oracle: requirement 2 — "1", "0000000001" and "01" are one filer; "3" twice is listed once
    toolbox, _ = make_toolbox()
    single = _ok(toolbox, {"cik": "1", **REVENUES})
    payload = _ok(toolbox, {"ciks": ["1", "0000000001", "3", "01", "3"], **REVENUES})
    assert payload["total"] == 4
    assert _canon(payload["rows"]) == _canon(single["rows"])
    assert payload["not_found"] == [CCC_CIK]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(
    "arguments",
    [
        {"cik": "1", "ciks": ["2"]},
        {},
        {"ciks": []},
        {"ciks": [str(i) for i in range(1, 52)]},
        {"ciks": "1"},
        {"ciks": ["x"]},
    ],
)
def test_both_neither_or_a_bad_list_is_bad_arguments(make_toolbox, arguments):
    # oracle: requirement 2 — exactly one of cik and ciks; ciks holds 1 to 50 CIKs. A valid ciks
    # call succeeds on the same toolbox, so the failure is the arguments'
    toolbox, _ = make_toolbox()
    outcome = toolbox.execute("get_concept", {**arguments, **REVENUES})
    assert outcome.error_code == "bad_arguments"
    assert toolbox.execute("get_concept", {"ciks": ["1"], **REVENUES}).error_code is None


@pytest.mark.criterion("C2")
def test_the_schema_offers_ciks(make_toolbox):
    # oracle: requirement 2 — required becomes taxonomy and tag; ciks is a list of CIKs
    spec = next(s for s in tool_specs() if s.name == "get_concept")
    assert spec.parameters["required"] == ["taxonomy", "tag"]
    assert spec.parameters["properties"].get("ciks", {}).get("type") == "array"
    assert "cik" in spec.parameters["properties"]


@pytest.mark.criterion("C2")
def test_a_one_filer_call_says_nothing_was_missing(make_toolbox):
    # oracle: requirement 2 — the one-filer payload adds not_found: []
    toolbox, _ = make_toolbox()
    payload = _ok(toolbox, {"cik": "2", **REVENUES})
    assert payload.get("not_found") == []
    assert payload["label"] == BBB_LABEL


_FILTERS = st.fixed_dictionaries({}, optional={
    "unit": st.sampled_from(["USD", "EUR"]),
    "form": st.sampled_from(["10-K", "10-Q"]),
    "fiscal_year": st.sampled_from([2021, 2022, 2023]),
    "end_from": st.sampled_from(["2022-06-30", "2023-06-30"]),
    "end_to": st.sampled_from(["2022-12-31", "2023-09-30"]),
})


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@settings(max_examples=30)
@given(pair=st.permutations(["1", "2", "3"]).map(lambda p: p[:2]), filters=_FILTERS)
def test_two_filers_are_the_union_of_two_one_filer_calls(make_toolbox, pair, filters):
    # oracle: metamorphic — the rows for ciks=[a, b] are exactly the rows for cik=a plus the rows
    # for cik=b, under the same filters; a filer that reports nothing adds nothing
    toolbox, _ = make_toolbox()
    union: list[dict] = []
    for cik in pair:
        alone = toolbox.execute("get_concept", {"cik": cik, **REVENUES, **filters,
                                                "limit": 1000})
        if alone.error_code is None:
            union.extend(alone.payload["rows"])
        else:
            assert alone.error_code == "not_found"
    both = toolbox.execute("get_concept", {"ciks": list(pair), **REVENUES, **filters,
                                           "limit": 1000})
    if not union:
        assert both.error_code in ("not_found", None)
        if both.error_code is None:
            assert both.payload["rows"] == []
            assert both.payload.get("not_found") is not None
        return
    assert both.error_code is None, both.payload
    assert _canon(both.payload["rows"]) == _canon(union)
