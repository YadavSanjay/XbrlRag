"""Spec tests C6 for eval-paraphrases: the deterministic meaning check
(docs/specs/eval-paraphrases.md)."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.paraphrases import ParaphraseSource, check_meaning


def _sources(kit):
    return {kw["case_id"]: ParaphraseSource(**kw) for kw in kit.source_kwargs()}


def _fixture_records(kit):
    """(case_id, persona, paraphrase) of every record line of both reply files, read with json."""
    out = []
    for path in (kit.reply_a, kit.reply_b):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("{"):
                r = json.loads(line)
                out.append((r["case_id"], r["persona"], r["paraphrase"].strip()))
    return out


@pytest.mark.criterion("C6")
def test_c6_fixture_records_point(kit):
    # oracle: the meaning rules worked by hand for each fixture line (conftest REVIEW holds
    # the failing ones; accepted and duplicate lines pass the meaning check).
    src = _sources(kit)
    recs = _fixture_records(kit)
    assert len(recs) == 16
    for cid, persona, text in recs:
        expected = kit.review.get(f"{cid}:{persona}", [])
        assert check_meaning(src[cid], text) == expected, (cid, persona)


@pytest.mark.criterion("C6")
def test_c6_each_reason_point(kit):
    # oracle: one hand-made paraphrase per rule, and the spec's reason order.
    s = _sources(kit)
    s1, s3, s4 = s[kit.s1], s[kit.s3], s[kit.s4]
    assert check_meaning(s1, "What was the net sales in fiscal 2024?") == ["company_missing"]
    assert check_meaning(s1, "Applesauce net sales in fiscal 2024") == ["company_missing"]
    assert check_meaning(s1, "Apple net sales in 24") == ["period_missing"]
    assert check_meaning(s3, "Microsoft sales FY22 to FY24") == ["metric_missing"]
    assert check_meaning(s3, "Microsoft revenue FY24") == ["period_missing"]   # 2022 missing
    assert check_meaning(s4, "Top 3 companies by revenue FY24") == ["universe_missing"]
    assert check_meaning(s4, "Top 3 big tech by revenue in 2024") == ["period_missing"]
    assert check_meaning(s4, "Top 3 big tech by revenue among the S&P 100, FY24") == [
        "semantic_drift"]
    assert check_meaning(s4, "What did firms sell in calendar year?") == [
        "period_missing", "metric_missing", "universe_missing", "semantic_drift"]
    assert check_meaning(s1, "Firms sold stuff last year") == [
        "company_missing", "period_missing", "metric_missing", "semantic_drift"]
    two = ParaphraseSource("c-2", "AAPL gross profit to revenue, fiscal 2024", intent_id="i",
                           intent_axis="dev", template_axis="dev", company_forms=["AAPL"],
                           years=[2024], metric_forms=[["gross profit"], ["revenue"]])
    assert check_meaning(two, "AAPL gross profit FY24") == ["metric_missing"]
    assert check_meaning(two, "AAPL gross profit over revenue FY24") == []


@pytest.mark.criterion("C6")
def test_c6_period_forms_point(kit):
    # oracle: the accepted period forms for 2024 (YYYY=2024, YY=24), the period-end date form,
    # and the bare year only for a single-filer question.
    s1 = _sources(kit)[kit.s1]
    for period in ("FY 2024", "FY2024", "FY 24", "FY24", "FY'24", "FY-24", "fiscal 2024",
                   "fiscal '24", "fiscal year 2024", "Fiscal-Year 24", "2024 fiscal year",
                   "the year ended September 28, 2024", "2024"):
        assert check_meaning(s1, f"Apple net sales, {period}") == [], period
    for period in ("24", "FY 2023", "fiscal 2025", "20 24", "FY2O24"):
        assert check_meaning(s1, f"Apple net sales, {period}") == ["period_missing"], period
    odd = ParaphraseSource("c-3", "Apple revenue in fiscal 2024", intent_id="i",
                           intent_axis="dev", template_axis="dev", company_forms=["Apple"],
                           years=[2024], period_ends={2024: "2023-12-03"},
                           metric_forms=[["revenue"]])
    assert check_meaning(odd, "Apple revenue for the year ended December 3, 2023") == []
    assert check_meaning(odd, "Apple revenue for the year ended December 03, 2023") == [
        "period_missing"]
    assert check_meaning(odd, "Apple revenue for the year ended December 30, 2023") == [
        "period_missing"]


DRIFT = ("calendar", "quarter", "quarterly", "Q1", "q2", "Q3", "Q4", "TTM", "LTM", "trailing",
         "YTD", "year-to-date", "last year", "this year", "last fiscal year", "latest",
         "most recent", "CY24", "CY 2024", "cy2024", "CY")


@pytest.mark.criterion("C6")
def test_c6_drift_point(kit):
    # oracle: every built-in drift phrase triggers semantic_drift; whole tokens only; a phrase
    # the source question already contains does not.
    s1 = _sources(kit)[kit.s1]
    base = "Apple net sales, fiscal 2024"
    assert check_meaning(s1, base) == []
    for phrase in DRIFT:
        assert check_meaning(s1, f"{base} ({phrase})") == ["semantic_drift"], phrase
    for harmless in ("quarterbacks", "cycle", "lately", "calendars", "q5", "ytds"):
        assert check_meaning(s1, f"{base} {harmless}") == [], harmless
    assert check_meaning(s1, f"{base} among big tech") == ["semantic_drift"]   # forbidden form
    has_latest = ParaphraseSource("c-4", "Apple's revenue in the latest fiscal 2024 filing",
                                  intent_id="i", intent_axis="dev", template_axis="dev",
                                  company_forms=["Apple"], years=[2024],
                                  metric_forms=[["revenue"]], forbidden_forms=["big tech"])
    assert check_meaning(has_latest, "Latest Apple revenue, FY24") == []
    assert check_meaning(has_latest, "Latest Apple revenue, FY24, big tech") == ["semantic_drift"]


_PERIODS = ("FY 2024", "FY2024", "FY 24", "FY24", "FY'24", "fiscal 2024", "fiscal 24",
            "fiscal year 2024", "fiscal year 24", "2024 fiscal year", "September 28, 2024",
            "2024")
_FLIP = st.sampled_from([str, str.upper, str.lower, str.swapcase, str.title])


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(st.sampled_from(("AAPL", "Apple", "Apple Inc.")), st.booleans(),
       st.sampled_from(("Revenues", "net sales", "revenue", "top line")),
       st.sampled_from(_PERIODS), _FLIP, st.sampled_from(DRIFT))
def test_c6_metamorphic_property(kit, company, possessive, metric, period, flip, drift):
    # oracle: matching is case-blind, possessive-blind and form-blind, so any company form,
    # metric form and accepted period form passes; adding a drift phrase the question lacks
    # adds only semantic_drift; dropping the period adds only period_missing.
    s1 = ParaphraseSource(**kit.source_kwargs()[0])
    who = f"{company}'s" if possessive else company
    text = flip(f"How about {who} {metric} for {period}?")
    assert check_meaning(s1, text) == []
    assert check_meaning(s1, f"{text} {drift}") == ["semantic_drift"]
    assert check_meaning(s1, flip(f"How about {who} {metric}?")) == ["period_missing"]
