"""Spec tests for screen-universe C1-C5: the pure disclosure helpers
(docs/specs/screen-universe.md, `src/retriever/xbrl/screen_disclosure.py`).
"""

from __future__ import annotations

import datetime as dt
import re
import string

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.screen_disclosure import (
    append_screen_disclosures,
    build_screen_disclosure,
    resolve_universe_members,
    screen_disclosure_text,
    stated_period_end,
)

ONE_DAY = dt.timedelta(days=1)

# The group memberships of configs/eval/v2/groups.yaml (written out; C2 is pure).
GROUPS = {
    "semiconductors": ["NVDA", "AMD", "TXN", "AVGO", "INTC", "MU", "QCOM", "TSM"],
    "retailers": ["WMT", "AMZN", "COST", "HD"],
    "big_tech": ["MSFT", "AAPL", "AMZN", "GOOGL", "META", "NVDA", "TSLA"],
}
SEMIS = ["AMD", "AVGO", "INTC", "MU", "NVDA", "QCOM", "TSM", "TXN"]

# A 33-filer roster shaped like the corpus (synthetic beyond the group members).
ROSTER_33 = sorted(set(SEMIS) | {
    "AAPL", "AMZN", "GOOGL", "META", "MSFT", "TSLA", "ORCL", "COST", "HD", "WMT",
    "ADBE", "CRM", "CSCO", "IBM", "JPM", "BAC", "V", "MA", "UNH", "JNJ", "PG", "KO",
    "PEP", "XOM", "CVX",
})

# The Notes' semiconductors FY2023 example.
SEMI_RANKED_ROWS = [
    {"ticker": "AVGO", "value": 14.0e9, "period_end": dt.date(2023, 10, 30)},
    {"ticker": "QCOM", "value": 7.2e9, "period_end": dt.date(2023, 9, 25)},
    {"ticker": "TXN", "value": 6.5e9, "period_end": dt.date(2024, 1, 1)},
    {"ticker": "AMD", "value": 0.9e9, "period_end": dt.date(2023, 12, 31)},
    {"ticker": "MU", "value": 0.5e9, "period_end": dt.date(2023, 9, 1)},
]
INTC_EXCL = {"ticker": "INTC", "reason": "period_not_filed", "latest_fiscal_year": 2022}
NVDA_EXCL = {"ticker": "NVDA", "reason": "metric_not_tagged"}
TSM_EXCL = {"ticker": "TSM", "reason": "currency", "currency": "TWD"}
ORCL_EXCL = {"ticker": "ORCL", "reason": "period_not_filed", "latest_fiscal_year": 2025}

SEMI_DISCLOSURE = {
    "universe": "semiconductors",
    "fiscal_year": 2023,
    "members": 8,
    "ranked": ["AVGO", "QCOM", "TXN", "AMD", "MU"],
    "exclusions": [INTC_EXCL, NVDA_EXCL, TSM_EXCL],
    "period_ends": {"AVGO": "2023-10-29", "QCOM": "2023-09-24", "TXN": "2023-12-31",
                    "AMD": "2023-12-30", "MU": "2023-08-31"},
    "period_ends_differ": True,
}
SEMI_TEXT = (
    "Universe: the semiconductors group (8 companies; 5 ranked for FY2023). "
    "Not ranked: INTC (no FY2023 figure; latest FY2022); NVDA (metric not reported); "
    "TSM (reports in TWD, not USD). Fiscal years end on different dates: AVGO 2023-10-29, "
    "QCOM 2023-09-24, TXN 2023-12-31, AMD 2023-12-30, MU 2023-08-31."
)
COVERED_DISCLOSURE = {
    "universe": "covered", "fiscal_year": 2024, "members": 2, "ranked": ["AAPL", "MSFT"],
    "exclusions": [], "period_ends": {"AAPL": "2024-09-28", "MSFT": "2024-06-30"},
    "period_ends_differ": False,
}
COVERED_TEXT = "Universe: the covered companies (2 companies; 2 ranked for FY2024)."
ONE_MEMBER_DISCLOSURE = {
    "universe": "big_tech", "fiscal_year": 2025, "members": 1, "ranked": ["NVDA"],
    "exclusions": [], "period_ends": {"NVDA": "2025-01-26"}, "period_ends_differ": False,
}
ONE_MEMBER_TEXT = "Universe: the big tech group (1 company; 1 ranked for FY2025)."

TICKERS = st.text(alphabet=string.ascii_uppercase, min_size=1, max_size=5)
DATES = st.dates(min_value=dt.date(1990, 1, 2), max_value=dt.date(2100, 12, 31))


def _stated(d: dt.date) -> str:
    return (d - ONE_DAY).isoformat()


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_stated_period_end_sec_examples():
    # oracle: SEC companyfacts `end` dates (spec Notes): MSFT FY2022 2022-06-30, AAPL FY2023
    # 2023-09-30, NVDA FY2023 2023-01-29, AVGO FY2023 2023-10-29, AMZN FY2022 2022-12-31;
    # the calendar for the leap day
    cases = {
        dt.date(2022, 7, 1): "2022-06-30",
        dt.date(2023, 10, 1): "2023-09-30",
        dt.date(2023, 1, 30): "2023-01-29",
        dt.date(2023, 10, 30): "2023-10-29",
        dt.date(2023, 1, 1): "2022-12-31",
        dt.date(2024, 3, 1): "2024-02-29",
    }
    for stored, stated in cases.items():
        assert stated_period_end(stored) == stated
        assert stated_period_end(stored.isoformat()) == stated
    assert stated_period_end(None) is None


@pytest.mark.criterion("C1")
@given(DATES)
def test_stated_period_end_is_the_day_before(stored):
    # oracle: the calendar — the result plus one day is the input; date and ISO string agree
    result = stated_period_end(stored)
    assert isinstance(result, str)
    assert dt.date.fromisoformat(result) + ONE_DAY == stored
    assert stated_period_end(stored.isoformat()) == result


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_resolve_universe_members_point_cases():
    # oracle: Requirement 2 — covered is the roster (sorted, de-duplicated); a group is its
    # tickers (sorted), TSM included although this roster lacks it
    roster = ["MSFT", "AAPL", "MSFT", "NVDA", "AMD"]
    assert resolve_universe_members("covered", roster, GROUPS) == ["AAPL", "AMD", "MSFT", "NVDA"]
    assert resolve_universe_members("semiconductors", roster, GROUPS) == SEMIS
    assert resolve_universe_members("retailers", roster, GROUPS) == ["AMZN", "COST", "HD", "WMT"]
    assert resolve_universe_members("big_tech", roster, GROUPS) == [
        "AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"]


@pytest.mark.criterion("C2")
def test_resolve_universe_members_rejects_an_unknown_id():
    # oracle: Requirement 2 — an id that is neither covered nor a group raises, naming it
    with pytest.raises(ValueError, match="megacaps"):
        resolve_universe_members("megacaps", ["AAPL"], GROUPS)
    with pytest.raises(ValueError, match="Covered"):
        resolve_universe_members("Covered", ["AAPL"], GROUPS)


@pytest.mark.criterion("C2")
@given(st.lists(st.sampled_from(ROSTER_33), min_size=1, max_size=20),
       st.lists(st.sampled_from(ROSTER_33), max_size=20),
       st.text(alphabet=string.ascii_lowercase + "_", min_size=1, max_size=12))
def test_resolve_universe_members_for_any_roster(roster, other_roster, unknown):
    # oracle: Requirement 2 written out — covered is sorted(set(roster)); a group's members
    # do not depend on the roster
    assert resolve_universe_members("covered", roster, GROUPS) == sorted(set(roster))
    for gid, tickers in GROUPS.items():
        assert resolve_universe_members(gid, roster, GROUPS) == sorted(tickers)
        assert resolve_universe_members(gid, other_roster, GROUPS) == sorted(tickers)
    if unknown != "covered" and unknown not in GROUPS:
        with pytest.raises(ValueError):
            resolve_universe_members(unknown, roster, GROUPS)


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_build_disclosure_semiconductors_example():
    # oracle: spec Notes' semiconductors FY2023 example — ORCL is not a member and is
    # dropped; stated ends 2023-08-31 .. 2023-12-31 span 122 days > 92
    result = build_screen_disclosure("semiconductors", 2023, SEMIS, ROSTER_33,
                                     SEMI_RANKED_ROWS, [TSM_EXCL, ORCL_EXCL, NVDA_EXCL, INTC_EXCL])
    assert result == SEMI_DISCLOSURE


def _norm(exclusion, given_tickers):
    """Given exclusions compare whole; generated ones by ticker and reason."""
    if exclusion.get("ticker") in given_tickers:
        return dict(exclusion)
    return {"ticker": exclusion.get("ticker"), "reason": exclusion.get("reason")}


@pytest.mark.criterion("C3")
def test_build_disclosure_generates_not_in_corpus_and_metric_not_tagged():
    # oracle: Requirement 3 — big_tech members outside the roster are not_in_corpus, the
    # other unranked members metric_not_tagged; MSFT 2024-06-30 to AAPL 2024-09-28 is 90 days
    roster = ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA"]
    rows = [{"ticker": "MSFT", "period_end": dt.date(2024, 7, 1)},
            {"ticker": "AAPL", "period_end": "2024-09-29"},
            {"ticker": "GOOGL", "period_end": None}]
    result = build_screen_disclosure("big_tech", 2024, sorted(GROUPS["big_tech"]), roster, rows, [])
    assert isinstance(result, dict)
    assert result.get("universe") == "big_tech"
    assert result.get("fiscal_year") == 2024
    assert result.get("members") == 7
    assert result.get("ranked") == ["MSFT", "AAPL", "GOOGL"]
    assert [_norm(e, set()) for e in result.get("exclusions") or []] == [
        {"ticker": "AMZN", "reason": "metric_not_tagged"},
        {"ticker": "META", "reason": "metric_not_tagged"},
        {"ticker": "NVDA", "reason": "metric_not_tagged"},
        {"ticker": "TSLA", "reason": "not_in_corpus"},
    ]
    assert result.get("period_ends") == {"MSFT": "2024-06-30", "AAPL": "2024-09-28"}
    assert result.get("period_ends_differ") is False


@pytest.mark.criterion("C3")
def test_build_disclosure_92_day_boundary():
    # oracle: Requirement 5 — 2022-12-31 to 2023-04-02 is 92 days (not more), to 2023-04-03
    # is 93; one ranked filer cannot differ
    def differ(stored_ends):
        rows = [{"ticker": t, "period_end": d}
                for t, d in zip(["AAA", "BBB"], stored_ends, strict=False)]
        out = build_screen_disclosure("covered", 2023, ["AAA", "BBB"], ["AAA", "BBB"], rows, [])
        return out.get("period_ends_differ") if isinstance(out, dict) else "no dict"

    assert differ([dt.date(2023, 1, 1), dt.date(2023, 4, 3)]) is False
    assert differ([dt.date(2023, 1, 1), dt.date(2023, 4, 4)]) is True
    assert differ([dt.date(2023, 4, 4), dt.date(2023, 1, 1)]) is True
    assert differ([dt.date(2023, 1, 1)]) is False


REASONS = st.one_of(
    st.builds(lambda y: {"reason": "period_not_filed", "latest_fiscal_year": y},
              st.one_of(st.none(), st.integers(2018, 2024))),
    st.just({"reason": "component_only", "fiscal_year": 2024, "concept": "us-gaap:Depreciation"}),
    st.just({"reason": "currency", "currency": "TWD"}),
    st.just({"reason": "metric_not_tagged"}),
)


@st.composite
def disclosure_inputs(draw):
    pool = sorted(draw(st.sets(TICKERS, min_size=1, max_size=14)))
    members = draw(st.lists(st.sampled_from(pool), unique=True, min_size=1))
    roster = draw(st.lists(st.sampled_from(pool), unique=True))
    in_corpus = [m for m in members if m in roster]
    ranked = draw(st.permutations(in_corpus))[: draw(st.integers(0, len(in_corpus)))]
    rows = [{"ticker": t, "value": float(i),
             "period_end": draw(st.one_of(st.none(), DATES, DATES.map(dt.date.isoformat)))}
            for i, t in enumerate(ranked)]
    unranked = [m for m in in_corpus if m not in ranked]
    with_reason = draw(st.lists(st.sampled_from(unranked), unique=True)) if unranked else []
    given = [{"ticker": t, **draw(REASONS)} for t in with_reason]
    outsiders = [t for t in pool if t not in members]
    dropped = draw(st.lists(st.sampled_from(outsiders), unique=True)) if outsiders else []
    given += [{"ticker": t, "reason": "period_not_filed", "latest_fiscal_year": 2020}
              for t in dropped]
    order = draw(st.permutations(given))
    return members, roster, rows, given, order


def _stated_of(value):
    d = value if isinstance(value, dt.date) else dt.date.fromisoformat(value)
    return d - ONE_DAY


@pytest.mark.criterion("C3")
@given(disclosure_inputs(), st.integers(2020, 2026))
def test_build_disclosure_rules_for_any_input(inputs, fy):
    # oracle: Requirements 3-5 written out — ranked and excluded partition the members, the
    # exclusions follow Requirement 3, the 92-day rule is computed here, and the order of
    # the given exclusions does not matter
    members, roster, rows, given, shuffled = inputs
    result = build_screen_disclosure("covered", fy, members, roster, rows, given)
    assert isinstance(result, dict)
    assert result == build_screen_disclosure("covered", fy, members, roster, rows, shuffled)
    ranked = [r["ticker"] for r in rows]
    assert result.get("ranked") == ranked
    assert result.get("members") == len(members)
    assert result.get("fiscal_year") == fy
    assert result.get("universe") == "covered"

    given_members = {e["ticker"]: e for e in given if e["ticker"] in members}
    expected = []
    for t in sorted(set(members) - set(ranked)):
        if t in given_members:
            expected.append(given_members[t])
        elif t not in roster:
            expected.append({"ticker": t, "reason": "not_in_corpus"})
        else:
            expected.append({"ticker": t, "reason": "metric_not_tagged"})
    exclusions = result.get("exclusions") or []
    assert [_norm(e, set(given_members)) for e in exclusions] == expected
    excluded = [e.get("ticker") for e in exclusions]
    assert sorted(ranked + excluded) == sorted(members)

    ends = {r["ticker"]: _stated_of(r["period_end"]) for r in rows if r["period_end"] is not None}
    assert result.get("period_ends") == {t: d.isoformat() for t, d in ends.items()}
    differ = len(ends) >= 2 and (max(ends.values()) - min(ends.values())).days > 92
    assert result.get("period_ends_differ") is differ


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
def test_disclosure_text_point_cases():
    # oracle: spec Notes' expected text; Requirement 7 for the covered (one sentence) and
    # one-member (`company`, `big tech`) cases
    assert screen_disclosure_text(SEMI_DISCLOSURE) == SEMI_TEXT
    assert screen_disclosure_text(COVERED_DISCLOSURE) == COVERED_TEXT
    assert screen_disclosure_text(ONE_MEMBER_DISCLOSURE) == ONE_MEMBER_TEXT


@pytest.mark.criterion("C4")
def test_disclosure_text_every_reason_phrase():
    # oracle: Requirement 7's phrase list, written out; exclusions sorted by ticker
    disclosure = {
        "universe": "covered", "fiscal_year": 2024, "members": 9, "ranked": ["MSFT"],
        "exclusions": [
            {"ticker": "ZZZ", "reason": "restated_after_filing"},
            {"ticker": "TSM", "reason": "currency", "currency": "TWD"},
            {"ticker": "TM", "reason": "currency"},
            {"ticker": "NVDA", "reason": "not_in_corpus"},
            {"ticker": "META", "reason": "component_only", "fiscal_year": 2024,
             "concept": "us-gaap:Depreciation"},
            {"ticker": "GOOGL", "reason": "metric_not_tagged"},
            {"ticker": "AMZN", "reason": "period_not_filed", "latest_fiscal_year": None},
            {"ticker": "AAPL", "reason": "period_not_filed", "latest_fiscal_year": 2023},
        ],
        "period_ends": {"MSFT": "2024-06-30"}, "period_ends_differ": False,
    }
    assert screen_disclosure_text(disclosure) == (
        "Universe: the covered companies (9 companies; 1 ranked for FY2024). Not ranked: "
        "AAPL (no FY2024 figure; latest FY2023); AMZN (no FY2024 figure); "
        "GOOGL (metric not reported); META (reports only components of the metric); "
        "NVDA (not in the corpus); TM (does not report in USD); TSM (reports in TWD, not USD); "
        "ZZZ (restated after filing)."
    )


PHRASE_REASONS = st.one_of(
    st.builds(lambda y: {"reason": "period_not_filed", "latest_fiscal_year": y},
              st.one_of(st.none(), st.integers(2018, 2024))),
    st.just({"reason": "metric_not_tagged"}),
    st.just({"reason": "component_only"}),
    st.just({"reason": "not_in_corpus"}),
    st.builds(lambda c: {"reason": "currency", "currency": c}, st.sampled_from(["TWD", "EUR"])),
    st.just({"reason": "currency"}),
    st.sampled_from(["late_filer", "x"]).map(lambda r: {"reason": r}),
)


def _phrase(e, fy):
    reason = e["reason"]
    if reason == "period_not_filed":
        latest = e.get("latest_fiscal_year")
        return f"no FY{fy} figure" + ("" if latest is None else f"; latest FY{latest}")
    if reason == "currency":
        if "currency" not in e:
            return "does not report in USD"
        return f"reports in {e['currency']}, not USD"
    return {"metric_not_tagged": "metric not reported",
            "component_only": "reports only components of the metric",
            "not_in_corpus": "not in the corpus"}.get(reason, reason.replace("_", " "))


@st.composite
def disclosures(draw):
    tickers = draw(st.lists(TICKERS, unique=True, min_size=1, max_size=12))
    n_ranked = draw(st.integers(0, len(tickers)))
    ranked, excluded = tickers[:n_ranked], tickers[n_ranked:]
    exclusions = [{"ticker": t, **draw(PHRASE_REASONS)} for t in excluded]
    ends = {t: draw(DATES).isoformat() for t in ranked if draw(st.booleans())}
    return {
        "universe": draw(st.sampled_from(["covered", "semiconductors", "big_tech", "a_b_c"])),
        "fiscal_year": draw(st.integers(2020, 2026)),
        "members": len(tickers) + draw(st.integers(0, 3)),
        "ranked": ranked,
        "exclusions": draw(st.permutations(exclusions)),
        "period_ends": ends,
        "period_ends_differ": draw(st.booleans()),
    }


@pytest.mark.criterion("C4")
@given(disclosures())
def test_disclosure_text_rule_for_any_disclosure(d):
    # oracle: Requirement 7 written out; S2/S3's checks (every excluded ticker as a whole
    # word, the counts as numerals, every listed end) and order independence
    text = screen_disclosure_text(d)
    assert isinstance(text, str)
    m, r, fy = d["members"], len(d["ranked"]), d["fiscal_year"]
    label = ("the covered companies" if d["universe"] == "covered"
             else f"the {d['universe'].replace('_', ' ')} group")
    expected = (f"Universe: {label} ({m} {'company' if m == 1 else 'companies'}; "
                f"{r} ranked for FY{fy}).")
    excl = sorted(d["exclusions"], key=lambda e: e["ticker"])
    if excl:
        expected += " Not ranked: " + "; ".join(
            f"{e['ticker']} ({_phrase(e, fy)})" for e in excl) + "."
    listed = [(t, d["period_ends"][t]) for t in d["ranked"] if t in d["period_ends"]]
    if d["period_ends_differ"]:
        expected += (" Fiscal years end on different dates: "
                     + ", ".join(f"{t} {end}" for t, end in listed) + ".")
    assert text == expected

    for e in excl:
        assert re.search(rf"\b{e['ticker']}\b", text)
    assert re.search(rf"\b{m}\b", text) and re.search(rf"\b{r} ranked\b", text)
    if d["period_ends_differ"]:
        assert all(f"{t} {end}" in text for t, end in listed)
    else:
        assert "Fiscal years end" not in text
    reordered = {**d, "exclusions": list(reversed(d["exclusions"]))}
    assert screen_disclosure_text(reordered) == text


# --------------------------------------------------------------------------- C5
def _screen_step(sid, disclosure="__absent__"):
    step = {"step_id": sid, "op": "screen", "status": "OK", "rows": []}
    if disclosure != "__absent__":
        step["disclosure"] = disclosure
    return step


LOOKUP = {"step_id": "l1", "op": "lookup", "status": "OK", "value": 391.0}


@pytest.mark.criterion("C5")
def test_append_one_and_two_screen_steps():
    # oracle: Requirement 8 — sentences in step order, one space apart, after the summary
    # stripped of trailing whitespace
    summary = "Broadcom ranked first."
    one = [LOOKUP, _screen_step("s1", SEMI_DISCLOSURE)]
    assert append_screen_disclosures(summary, one) == f"{summary} {SEMI_TEXT}"
    assert append_screen_disclosures(summary + "  \n", one) == f"{summary} {SEMI_TEXT}"
    two = [_screen_step("s1", SEMI_DISCLOSURE), LOOKUP, _screen_step("s2", COVERED_DISCLOSURE)]
    assert append_screen_disclosures(summary, two) == f"{summary} {SEMI_TEXT} {COVERED_TEXT}"
    assert append_screen_disclosures(summary, list(reversed(two))) == (
        f"{summary} {COVERED_TEXT} {SEMI_TEXT}")


@pytest.mark.criterion("C5")
def test_append_unchanged_empty_and_duplicate_cases():
    # oracle: Requirement 8 — no screen disclosure leaves the summary unchanged; an empty
    # summary gets the sentence alone; a sentence already present is not added again
    summary = "Apple's FY2024 revenue was $391.0 billion.  "
    with_disclosure_on_lookup = {**LOOKUP, "disclosure": SEMI_DISCLOSURE}
    for steps in ([LOOKUP], [], [_screen_step("s1")], [_screen_step("s1", None)],
                  [with_disclosure_on_lookup]):
        assert append_screen_disclosures(summary, steps) == summary
    assert append_screen_disclosures("", [_screen_step("s1", SEMI_DISCLOSURE)]) == SEMI_TEXT
    already = f"Broadcom ranked first. {SEMI_TEXT}"
    assert append_screen_disclosures(already, [_screen_step("s1", SEMI_DISCLOSURE)]) == already
    twice = [_screen_step("s1", SEMI_DISCLOSURE), _screen_step("s2", SEMI_DISCLOSURE)]
    assert append_screen_disclosures("Top three:", twice) == f"Top three: {SEMI_TEXT}"


STEP_CHOICES = {
    "semi": (_screen_step("s1", SEMI_DISCLOSURE), SEMI_TEXT),
    "covered": (_screen_step("s2", COVERED_DISCLOSURE), COVERED_TEXT),
    "one": (_screen_step("s3", ONE_MEMBER_DISCLOSURE), ONE_MEMBER_TEXT),
    "lookup": (LOOKUP, None),
    "none": (_screen_step("s4", None), None),
    "absent": (_screen_step("s5"), None),
}
SUMMARIES = st.one_of(
    st.just(""),
    st.text(alphabet=string.ascii_letters + " .,$\n", max_size=40).map(lambda s: "A" + s),
    st.sampled_from([SEMI_TEXT, COVERED_TEXT]).map(lambda s: "Ranked. " + s),
)


@pytest.mark.criterion("C5")
@given(SUMMARIES, st.lists(st.sampled_from(sorted(STEP_CHOICES)), max_size=6))
def test_append_rule_for_any_steps(summary, names):
    # oracle: Requirement 8 written out; applying it twice equals applying it once; the
    # result starts with the (right-stripped) summary
    steps = [STEP_CHOICES[n][0] for n in names]
    sentences: list[str] = []
    for n in names:
        s = STEP_CHOICES[n][1]
        if s is not None and s not in summary and s not in sentences:
            sentences.append(s)
    if not sentences:
        expected = summary
    else:
        base = summary.rstrip()
        expected = (base + " " if base else "") + " ".join(sentences)
    result = append_screen_disclosures(summary, steps)
    assert result == expected
    assert append_screen_disclosures(result, steps) == result
    assert result.startswith(summary.rstrip() if sentences else summary)
