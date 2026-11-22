"""C4: the appended disclosure sentences are found exactly (docs/specs/grader-synthesis-error.md,
requirement 3).

The point tests type the sentences by hand from ``screen_disclosure_text``; the property builds
them with the pipeline's own ``build_screen_disclosure`` and ``screen_disclosure_text``.
"""

from __future__ import annotations

import datetime as dt
import itertools

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import disclosure_spans
from src.retriever.xbrl.screen_disclosure import build_screen_disclosure, screen_disclosure_text

U = "Universe: the covered companies (33 companies; 14 ranked for FY2022)."
N = ("Not ranked: ABT (metric not reported); MRK (no FY2023 figure; latest FY2022); "
     "TSM (reports in TWD, not USD); DEVZ (several members match: Americas (excl. U.S.), "
     "Europe).")
F = "Fiscal years end on different dates: AMZN 2022-12-31, MSFT 2022-06-30."


def _found(text):
    return [text[s:e] for s, e in disclosure_spans(text)]


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("sentence", [
    U,
    "Universe: the big tech group (7 companies; 3 ranked for FY2023).",
    "Universe: the retailers group (1 company; 1 ranked for FY2025).",
    N,
    "Not ranked: DEVX (metric not reported).",
    F,
    "Fiscal years end on different dates: BRK.B 2024-12-31, DEVA 2024-06-30.",
])
def test_each_form_alone(sentence):
    # oracle: requirement 3, forms U, N and F, read by hand
    text = f"DEVS ranked 3rd among them. {sentence}"
    assert _found(text) == [sentence]
    start = text.index(sentence)
    assert disclosure_spans(text) == [(start, start + len(sentence))]


@pytest.mark.criterion("C4")
def test_the_three_together_as_appended(kit):
    # oracle: requirement 3 — the META answer's tail: U, N and F, one space apart
    text = kit.META_TEXT
    found = _found(text)
    assert [s.split(":")[0] for s in found] == ["Universe", "Not ranked",
                                                "Fiscal years end on different dates"]
    assert found[0] == U
    assert found[2] == "Fiscal years end on different dates: AMZN 2022-12-31, " \
                       "MSFT 2022-06-30, ORCL 2022-05-31."


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("text", [
    "33 companies were ranked for FY2022, and DEVS ranked 3rd.",
    "Universe: the covered companies (33 companies; 14 ranked for FY2022)",
    "Universe: the covered companies, 33 companies with 14 ranked.",
    "Median of 5 companies: DEVA, DEVB, DEVS, DEVC, DEVD (middle: DEVS).",
    "Total of 3 companies: DEVA, DEVB, DEVS.",
    "Not ranked: none.",
    "Fiscal years end on different dates: AMZN December 31.",
    "",
])
def test_prose_and_aggregate_sentences_are_not_disclosures(text):
    # oracle: requirement 3 — no final ".", other wording, and the aggregate sentence
    # (decision 3) are not disclosure sentences
    assert disclosure_spans(text) == []


TICKERS = ["DEVA", "DEVB", "DEVC", "DEVD", "DEVE", "DEVF", "DEVG", "DEVH", "BRK.B", "DEVZ"]
REASONS = st.one_of(
    st.just({"reason": "metric_not_tagged"}),
    st.just({"reason": "no_fsds"}),
    st.just({"reason": "nil"}),
    st.builds(lambda fy: {"reason": "period_not_filed", "latest_fiscal_year": fy},
              st.one_of(st.none(), st.integers(2015, 2025))),
    st.builds(lambda c: {"reason": "currency", "currency": c}, st.sampled_from(["TWD", "EUR"])),
    st.builds(lambda labels: {"reason": "ambiguous_member", "labels": labels},
              st.lists(st.sampled_from(["Americas", "Americas (excl. U.S.)", "U.S. and Canada",
                                        "Rest of world"]), min_size=1, max_size=3)),
)
PROSE = st.sampled_from([
    "", "DEVS ranked 3rd among 12 companies.", "The total is $1.5 billion; DEVA leads.",
    "No company reported it (see below).",
])


@st.composite
def disclosures(draw):
    tickers = draw(st.lists(st.sampled_from(TICKERS), min_size=1, max_size=8, unique=True))
    n_ranked = draw(st.integers(0, len(tickers)))
    ranked, rest = tickers[:n_ranked], tickers[n_ranked:]
    base = dt.date(2025, 1, 1)
    ranked_rows = [{"ticker": t, "period_end": base + dt.timedelta(days=draw(
        st.integers(0, 365)))} for t in ranked]
    exclusions = [{"ticker": t, **draw(REASONS)} for t in rest if draw(st.booleans())]
    roster = draw(st.sampled_from([tickers, ranked]))
    universe = draw(st.sampled_from(["covered", "big_tech", "retailers"]))
    return build_screen_disclosure(universe, draw(st.integers(2000, 2030)), tickers, roster,
                                   ranked_rows, exclusions)


@pytest.mark.criterion("C4")
@given(d=disclosures(), prose=PROSE)
def test_built_disclosures_are_found_exactly(d, prose):
    # oracle: requirement 3 — the pipeline's own sentence (screen_disclosure_text joins its
    # sentences with one space; each begins with one of the three fixed openings)
    built = screen_disclosure_text(d)
    text = f"{prose} {built}" if prose else built
    found = _found(text)
    assert " ".join(found) == built
    assert found[0].startswith("Universe: ")
    assert all(s.startswith(("Universe: ", "Not ranked: ",
                             "Fiscal years end on different dates: ")) for s in found)
    spans = disclosure_spans(text)
    assert spans == sorted(spans)
    assert all(e1 <= s2 for (_s1, e1), (s2, _e2) in itertools.pairwise(spans))
    assert spans[0][0] == text.index(built)
