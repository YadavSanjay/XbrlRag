"""Spec tests for screen-aggregates C8 and C10: the aggregate over a screen step's rows and
its sentence (docs/specs/screen-aggregates.md, Requirements 7 and 9). Pure; the rows are the
Notes' synthetic retailers FY2024 interest-expense fixture.
"""

from __future__ import annotations

import copy
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.screen_aggregate import (
    aggregate_disclosure_text,
    aggregate_screen_rows,
    append_aggregate_disclosures,
)

REL = 1e-12  # every expected value is a short decimal; only float rounding separates them

WMT = {"ticker": "WMT", "value": 2_683_000_000.0, "unit": "USD", "fact_id": "101",
       "period_end": "2025-01-31", "rank": 1}
AMZN = {"ticker": "AMZN", "value": 2_406_000_000.0, "unit": "USD", "fact_id": "102",
        "period_end": "2024-12-31", "rank": 2}
HD = {"ticker": "HD", "value": 2_066_000_000.0, "unit": "USD", "fact_id": "103",
      "period_end": "2025-02-02", "rank": 3}
COST_ROW = {"ticker": "COST", "value": 150_000_000.0, "unit": "USD", "fact_id": "104",
            "period_end": "2024-09-01", "rank": 4}
COST_EXCL = {"ticker": "COST", "reason": "period_not_filed", "latest_fiscal_year": 2023}
DISCLOSURE = {
    "universe": "retailers", "fiscal_year": 2024, "members": 4,
    "ranked": ["WMT", "AMZN", "HD"], "exclusions": [COST_EXCL],
    "period_ends": {"WMT": "2025-01-31", "AMZN": "2024-12-31", "HD": "2025-02-02"},
    "period_ends_differ": False,
}
ENDS3 = {"WMT": "2025-01-31", "AMZN": "2024-12-31", "HD": "2025-02-02"}


def _screen(rows, disclosure=DISCLOSURE, status="OK", **extra):
    d = {"step_id": "s1", "op": "screen", "status": status, "value": None, "unit": None,
         "citation": None, "confidence": 0.0, "linked_chunk_ids": [], "error": None,
         "rows": copy.deepcopy(rows)}
    if disclosure is not None:
        d["disclosure"] = copy.deepcopy(disclosure)
    d.update(extra)
    return d


def _agg(fn, screen):
    result = aggregate_screen_rows(fn, screen)
    assert isinstance(result, dict), result
    return result


THREE = [WMT, AMZN, HD]


# ------------------------------------------------------------------------------- C8
@pytest.mark.criterion("C8")
def test_sum_of_the_fixture():
    # oracle: Notes — 2,683,000,000 + 2,406,000,000 + 2,066,000,000 = 7,155,000,000; the
    # disclosure fields copied; ends span 2024-12-31..2025-02-02 = 33 days <= 92
    result = _agg("sum", _screen(THREE))
    assert result.pop("value") == pytest.approx(7_155_000_000.0, rel=REL)  # float sum
    assert result == {
        "fn": "sum", "status": "OK", "unit": "USD", "n": 3,
        "members": ["WMT", "AMZN", "HD"], "fact_ids": ["101", "102", "103"], "middle": [],
        "universe": "retailers", "universe_members": 4, "exclusions": [COST_EXCL],
        "period_ends": ENDS3, "period_ends_differ": False, "error": None,
    }


@pytest.mark.criterion("C8")
def test_mean_count_and_medians_of_the_fixture():
    # oracle: Notes — mean 7,155,000,000 / 3 = 2,385,000,000; count 3; median of 3 = AMZN
    # 2,406,000,000; with COST 150,000,000 added, median (2,066,000,000 + 2,406,000,000) / 2
    # = 2,236,000,000, middle HD then AMZN; ends 2024-09-01..2025-02-02 = 154 days > 92
    mean = _agg("mean", _screen(THREE))
    assert mean["status"] == "OK" and mean["unit"] == "USD"
    assert mean["value"] == pytest.approx(2_385_000_000.0, rel=REL)  # float division
    count = _agg("count", _screen(THREE))
    assert count["status"] == "OK" and count["value"] == 3 and count["unit"] is None
    med3 = _agg("median", _screen(THREE))
    assert med3["value"] == pytest.approx(2_406_000_000.0, rel=REL)  # a row's own value
    assert med3["middle"] == ["AMZN"] and med3["n"] == 3
    med4 = _agg("median", _screen(THREE + [COST_ROW]))
    assert med4["value"] == pytest.approx(2_236_000_000.0, rel=REL)  # mean of two floats
    assert med4["middle"] == ["HD", "AMZN"]
    assert med4["members"] == ["WMT", "AMZN", "HD", "COST"]
    assert med4["period_ends"] == {**ENDS3, "COST": "2024-09-01"}
    assert med4["period_ends_differ"] is True
    tie = [dict(WMT), dict(AMZN, value=2_066_000_000.0), dict(HD)]
    assert _agg("median", _screen(tie))["middle"] == ["HD"]  # tie AMZN/HD broken by ticker


@pytest.mark.criterion("C8")
def test_error_and_not_found_rules():
    # oracle: Requirement 7's rules, in order, applied by hand
    def status_error(fn, screen):
        r = _agg(fn, screen)
        return r.get("status"), str(r.get("error")), r.get("value")

    s, e, v = status_error("sum", dict(_screen(THREE), op="lookup"))
    assert s == "ERROR" and "lookup" in e and v is None
    s, e, v = status_error("max", _screen(THREE))
    assert s == "ERROR" and "max" in e and v is None
    s, e, v = status_error("sum", _screen([], status="NOT_FOUND"))
    assert s == "NOT_FOUND" and v is None
    s, e, v = status_error("sum", _screen([], status="NOT_IMPLEMENTED"))
    assert s == "ERROR" and "s1" in e and "NOT_IMPLEMENTED" in e and v is None
    s, e, v = status_error("sum", _screen(THREE, row_cap={"limit": 2, "rows_before_cap": 3,
                                                           "returned": 2}))
    assert s == "ERROR" and "row_cap" in e and v is None
    s, e, v = status_error("sum", _screen([]))
    assert s == "NOT_FOUND" and v is None
    s, e, v = status_error("sum", _screen([WMT, dict(AMZN, value=None), HD]))
    assert s == "ERROR" and "AMZN" in e and v is None
    s, e, v = status_error("median", _screen([WMT, dict(AMZN, unit="TWD"), HD]))
    assert s == "ERROR" and "USD" in e and "TWD" in e and v is None
    s, e, v = status_error("mean", _screen([dict(r, unit=None) for r in THREE]))
    assert s == "ERROR" and v is None
    assert _agg("count", _screen([dict(r, unit=None) for r in THREE]))["value"] == 3
    margins = [dict(r, value=x, unit="pure")
               for r, x in zip(THREE, (0.25, 0.5, 0.125), strict=True)]
    s, e, v = status_error("sum", _screen(margins))
    assert s == "ERROR" and "pure" in e and v is None
    assert _agg("median", _screen(margins))["value"] == pytest.approx(0.25, rel=REL)
    eps = [dict(r, value=x, unit="USD/shares") for r, x in zip(THREE, (2.0, 3.0, 7.0), strict=True)]
    s, e, v = status_error("sum", _screen(eps))
    assert s == "ERROR" and v is None
    assert _agg("mean", _screen(eps))["value"] == pytest.approx(4.0, rel=REL)  # 12 / 3


@pytest.mark.criterion("C8")
def test_without_a_disclosure_or_period_ends():
    # oracle: Requirement 7 — no disclosure: universe and its size None, no exclusions; a
    # row without a period end is left out of period_ends
    rows = [dict(WMT), dict(AMZN, period_end=None), dict(HD)]
    result = _agg("sum", _screen(rows, disclosure=None))
    assert result["value"] == pytest.approx(7_155_000_000.0, rel=REL)  # float sum
    assert result["universe"] is None and result["universe_members"] is None
    assert result["exclusions"] == []
    assert result["period_ends"] == {"WMT": "2025-01-31", "HD": "2025-02-02"}
    assert result["period_ends_differ"] is False


_TICKERS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]


@st.composite
def _rows(draw):
    n = draw(st.integers(1, 8))
    values = draw(st.lists(st.integers(-10**12, 10**12), min_size=n, max_size=n))
    rows = [{"ticker": t, "value": float(v), "unit": "USD", "fact_id": str(500 + i),
             "period_end": "2024-12-31", "rank": i + 1}
            for i, (t, v) in enumerate(zip(_TICKERS, values, strict=False))]
    return rows, values


def _median(values):
    s = sorted(values)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


@pytest.mark.criterion("C8")
@given(_rows(), st.randoms(use_true_random=False))
def test_aggregates_match_hand_arithmetic_in_any_row_order(case, rnd):
    # oracle: closed forms on the integer values — the integer sum (exact: below 2**53),
    # sum / n, the sorted middle (or mean of the two middle); row order changes no value
    rows, values = case
    shuffled = list(rows)
    rnd.shuffle(shuffled)
    n = len(values)
    for these in (rows, shuffled):
        assert _agg("sum", _screen(these))["value"] == float(sum(values))
        assert _agg("mean", _screen(these))["value"] == pytest.approx(
            sum(values) / n, rel=REL, abs=1e-6)  # one float division
        median = _agg("median", _screen(these))["value"]
        assert median == pytest.approx(_median(values), rel=REL, abs=1e-6)  # one float mean
        if n % 2:
            assert median in [float(v) for v in values]
        assert _agg("count", _screen(these))["value"] == n
        result = _agg("sum", _screen(these))
        assert result["members"] == [r["ticker"] for r in these]
        assert result["fact_ids"] == [r["fact_id"] for r in these]


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
@given(st.sampled_from(["sum", "median", "mean", "count"]),
       st.sampled_from(["metric_not_tagged", "currency", "not_in_corpus", "nil"]))
def test_an_extra_exclusion_changes_no_value_and_is_named(fn, reason):
    # oracle: Requirement 7 — exclusions are carried, never aggregated
    base = _agg(fn, _screen(THREE))
    extra = {"ticker": "ZZZ", "reason": reason}
    widened = dict(DISCLOSURE, members=5, exclusions=[COST_EXCL, extra])
    result = _agg(fn, _screen(THREE, disclosure=widened))
    assert base.get("status") == "OK" and result.get("value") == base.get("value")
    assert result["exclusions"] == [COST_EXCL, extra]
    assert result["universe_members"] == 5


# ------------------------------------------------------------------------------- C10
SUM3 = {"fn": "sum", "status": "OK", "value": 7_155_000_000.0, "unit": "USD", "n": 3,
        "members": ["WMT", "AMZN", "HD"], "fact_ids": ["101", "102", "103"], "middle": [],
        "universe": "retailers", "universe_members": 4, "exclusions": [COST_EXCL],
        "period_ends": ENDS3, "period_ends_differ": False, "error": None}
MED4 = dict(SUM3, fn="median", value=2_236_000_000.0, n=4,
            members=["WMT", "AMZN", "HD", "COST"], middle=["HD", "AMZN"])
MED3 = dict(SUM3, fn="median", value=2_406_000_000.0, middle=["AMZN"])
SCREEN_SENTENCE = "Universe: the retailers group (4 companies; 3 ranked for FY2024)."


@pytest.mark.criterion("C10")
def test_aggregate_disclosure_text_points():
    # oracle: Requirement 9's template filled by hand
    assert aggregate_disclosure_text(SUM3) == "Total of 3 companies: WMT, AMZN, HD."
    assert aggregate_disclosure_text(dict(SUM3, fn="mean")) == (
        "Average of 3 companies: WMT, AMZN, HD.")
    assert aggregate_disclosure_text(dict(SUM3, fn="count", unit=None, value=3)) == (
        "Count of 3 companies: WMT, AMZN, HD.")
    assert aggregate_disclosure_text(MED3) == (
        "Median of 3 companies: WMT, AMZN, HD (middle: AMZN).")
    assert aggregate_disclosure_text(MED4) == (
        "Median of 4 companies: WMT, AMZN, HD, COST (middle: HD and AMZN, averaged).")
    assert aggregate_disclosure_text(dict(SUM3, n=1, members=["NVDA"], fact_ids=["9"])) == (
        "Total of 1 company: NVDA.")
    for status in ("NOT_FOUND", "ERROR"):
        assert aggregate_disclosure_text(dict(SUM3, status=status, value=None)) == ""


@pytest.mark.criterion("C10")
def test_append_aggregate_disclosures_points():
    # oracle: Requirement 9 — appended in step order after the text, one space apart
    steps = [{"step_id": "s1", "op": "screen", "disclosure": DISCLOSURE},
             {"step_id": "a1", "op": "aggregate", "status": "OK", "aggregate": SUM3},
             {"step_id": "a2", "op": "aggregate", "status": "OK", "aggregate": MED4}]
    summary = "The retailers' combined interest expense was $7.155 billion. " + SCREEN_SENTENCE
    assert append_aggregate_disclosures(summary + "  ", steps) == (
        summary + " Total of 3 companies: WMT, AMZN, HD."
        " Median of 4 companies: WMT, AMZN, HD, COST (middle: HD and AMZN, averaged).")
    assert append_aggregate_disclosures("", steps[:2]) == "Total of 3 companies: WMT, AMZN, HD."
    assert append_aggregate_disclosures("Unchanged.", steps[:1]) == "Unchanged."
    failed = {"step_id": "a3", "op": "aggregate", "status": "ERROR",
              "aggregate": dict(SUM3, status="ERROR", value=None)}
    assert append_aggregate_disclosures("Unchanged.", [failed, steps[0]]) == "Unchanged."
    assert append_aggregate_disclosures("Answer.", [steps[1], steps[1]]) == (
        "Answer. Total of 3 companies: WMT, AMZN, HD.")


@pytest.mark.criterion("C10")
@given(st.lists(st.sampled_from(_TICKERS), min_size=1, max_size=8, unique=True),
       st.sampled_from(["sum", "mean", "count", "median"]),
       st.sampled_from(["", "Answer.", "Answer. More text.  "]))
def test_text_names_every_member_and_append_is_idempotent(members, fn, summary):
    # oracle: Requirement 9 — every member a whole word, n a numeral; appending twice is
    # appending once
    n = len(members)
    middle = [members[(n - 1) // 2]] if n % 2 else [members[n // 2 - 1], members[n // 2]]
    agg = dict(SUM3, fn=fn, n=n, members=members, middle=middle if fn == "median" else [])
    text = aggregate_disclosure_text(agg)
    assert re.search(rf"\b{n}\b", text)
    for t in members:
        assert re.search(rf"\b{t}\b", text)
    steps = [{"step_id": "a1", "op": "aggregate", "status": "OK", "aggregate": agg}]
    once = append_aggregate_disclosures(summary, steps)
    assert once.endswith(text)
    assert append_aggregate_disclosures(once, steps) == once
