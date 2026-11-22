"""C1: ``screen_summary``, a screen step's derived facts (Requirement 1)."""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.screen_evidence import screen_summary

KEYS = {"step_id", "universe", "fiscal_year", "members", "ranked_count", "ranked",
        "rows_returned", "exclusions", "period_ends", "period_ends_differ"}


@pytest.mark.criterion("C1")
def test_retailers_screen_summary(vd):
    # oracle: Requirement 1 applied by hand to the stored ap_screen step
    assert screen_summary(vd.ap_screen()) == {
        "step_id": "ap_screen", "universe": "retailers", "fiscal_year": 2023, "members": 4,
        "ranked_count": 4, "ranked": ["AMZN", "WMT", "COST", "HD"], "rows_returned": 4,
        "exclusions": [],
        "period_ends": {"AMZN": "2023-12-31", "WMT": "2023-01-31", "COST": "2023-09-03",
                        "HD": "2024-01-28"},
        "period_ends_differ": True,
    }


@pytest.mark.criterion("C1")
def test_bottom_three_counts_every_ranked_member(vd):
    # oracle: Requirement 1: ranked_count is the disclosure's 23 ranked, not the 3 rows
    s = screen_summary(vd.ppe_screen())
    assert s["ranked_count"] == 23 and s["rows_returned"] == 3 and s["members"] == 33
    assert s["ranked"] == vd.PPE_RANKED
    assert s["exclusions"] == vd.PPE_EXCLUSIONS
    assert [e["ticker"] for e in s["exclusions"]] == [
        "CRM", "GOOGL", "HD", "INTC", "META", "MU", "TSLA", "TSM", "UNP", "UPS"]
    assert s["period_ends"]["ADBE"] == "2025-11-28" and len(s["period_ends"]) == 23
    assert s["universe"] == "covered" and s["fiscal_year"] == 2025
    assert s["period_ends_differ"] is True


@pytest.mark.criterion("C1")
def test_screen_without_disclosure(vd):
    # oracle: Requirement 1 "without one", by hand: tickers and ends from the rows (a row
    # with no ticker skipped), ranked_count from row_cap.rows_before_cap, exclusions from
    # excluded
    rows = [vd.screen_row("AAA", "1", 11, 5e9, 1, "2024-12-31", 2024, "Revenue"),
            vd.screen_row("BBB", "2", 12, 4e9, 2, None, 2024, "Revenue"),
            {**vd.screen_row("CCC", "3", 13, 3e9, 3, "2024-06-30", 2024, "Revenue"),
             "ticker": None}]
    excluded = [{"ticker": "DDD", "reason": "period_not_filed", "latest_fiscal_year": 2023}]
    step = {"op": "screen", "step_id": "s9", "status": "OK", "value": None, "rows": rows,
            "excluded": excluded,
            "row_cap": {"limit": 3, "rows_before_cap": 12, "returned": 3}}
    assert screen_summary(step) == {
        "step_id": "s9", "universe": None, "fiscal_year": None, "members": None,
        "ranked_count": 12, "ranked": ["AAA", "BBB"], "rows_returned": 3,
        "exclusions": [{"ticker": "DDD", "reason": "period_not_filed",
                        "latest_fiscal_year": 2023}],
        "period_ends": {"AAA": "2024-12-31"}, "period_ends_differ": None,
    }
    uncapped = {k: v for k, v in step.items() if k != "row_cap"}
    assert screen_summary(uncapped)["ranked_count"] == 3


@pytest.mark.criterion("C1")
def test_other_steps_have_no_summary(vd):
    # oracle: Requirement 1: None for any op other than "screen"
    assert screen_summary(vd.wmt_lookup()) is None
    for op in ("compute", "aggregate", "breakdown", "narrative", "cagr"):
        assert screen_summary({**vd.ap_screen(), "op": op}) is None


_ticker = st.from_regex(r"[A-Z]{2,5}", fullmatch=True)


@st.composite
def _screens(draw):
    ranked = draw(st.lists(_ticker, unique=True, max_size=12))
    others = draw(st.lists(_ticker.filter(lambda t: t not in ranked), unique=True,
                           max_size=6))
    n_rows = draw(st.integers(0, len(ranked)))
    exclusions = [{"ticker": t, "reason": draw(st.sampled_from(
        ["metric_not_tagged", "currency", "period_not_filed"]))} for t in others]
    ends = {t: draw(st.sampled_from(["2024-12-31", "2024-06-30", "2025-01-31"]))
            for t in ranked}
    rows = [{"ticker": t, "rank": i, "value": float(10 ** 9 * (20 - i)), "unit": "USD",
             "fact_id": str(100 + i), "period_end": ends[t], "fiscal_year": 2024}
            for i, t in enumerate(ranked[:n_rows], start=1)]
    disclosure = {"universe": draw(st.sampled_from(["covered", "retailers", "big_tech"])),
                  "fiscal_year": 2024, "members": len(ranked) + len(others),
                  "ranked": list(ranked), "exclusions": exclusions, "period_ends": ends,
                  "period_ends_differ": draw(st.booleans())}
    return {"op": "screen", "step_id": draw(st.sampled_from(["s1", "scr", "ppe"])),
            "status": "OK", "value": None, "rows": rows, "disclosure": disclosure}


@pytest.mark.criterion("C1")
@given(step=_screens())
def test_summary_follows_any_disclosure_and_never_mutates(step):
    # oracle: Requirement 1: the disclosure's own fields; counts are lengths; step unchanged
    before = copy.deepcopy(step)
    s = screen_summary(step)
    d = step["disclosure"]
    assert set(s) == KEYS
    assert s["step_id"] == step["step_id"]
    assert (s["universe"], s["fiscal_year"], s["members"]) == (
        d["universe"], d["fiscal_year"], d["members"])
    assert s["ranked"] == d["ranked"] and s["ranked_count"] == len(d["ranked"])
    assert s["rows_returned"] == len(step["rows"])
    assert s["exclusions"] == d["exclusions"] and s["period_ends"] == d["period_ends"]
    assert s["period_ends_differ"] is d["period_ends_differ"]
    assert step == before


@pytest.mark.criterion("C1")
@given(step=_screens())
def test_summary_never_aliases_the_step(step):
    # oracle: Requirement 1 ("a new dict", copies): changing the summary leaves the step as it was
    before = copy.deepcopy(step)
    s = screen_summary(step)
    s["ranked"].append("ZZZZZ")
    s["period_ends"]["ZZZZZ"] = "2020-01-01"
    for e in s["exclusions"]:
        e["reason"] = "changed"
    s["exclusions"].append({"ticker": "ZZZZZ"})
    assert step == before
