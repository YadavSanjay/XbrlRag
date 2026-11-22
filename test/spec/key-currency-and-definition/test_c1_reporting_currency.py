"""C1: a filer's reporting currency for a fiscal year, read from its annual report's facts.

Oracle: requirement 1 of docs/specs/key-currency-and-definition.md (the system's
reporting-currency rule, screen-reporting-currency R1, applied to every currency fact of the
filing) and the conftest's FACTS table. The rule is restated by hand in ``_hand_rule``.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import reporting_currency

A, B = "0000839999-24-000001", "0000839999-23-000001"  # labelled FY2023 and FY2022
LABELS = {A: 2023, B: 2022}
_CURRENCY = re.compile(r"^[A-Z]{3}$")


def _doc(entries):
    """A companyfacts document from ``(concept, unit, end, accession)`` entries."""
    facts: dict = {}
    for concept, unit, end, accn in entries:
        tax, tag = concept.split(":")
        node = facts.setdefault(tax, {}).setdefault(tag, {"units": {}})
        node["units"].setdefault(unit, []).append(
            {"end": end, "val": 1.0, "accn": accn, "fy": LABELS[accn], "fp": "FY",
             "form": "20-F", "filed": "2024-04-01"})
    return {"cik": 839999, "entityName": "Synthetic", "facts": facts}


def _hand_rule(entries, year):
    # oracle: requirement 1 written out — currency units only, the filing labelled `year`,
    # the latest end's only currency, else the unique one at the most distinct ends
    obs = {(u, e) for _c, u, e, a in entries if _CURRENCY.match(u) and LABELS[a] == year}
    if not obs:
        return None
    latest = max(e for _u, e in obs)
    at_latest = {u for u, e in obs if e == latest}
    if len(at_latest) == 1:
        return next(iter(at_latest))
    cover = {u: len({e for v, e in obs if v == u}) for u in at_latest}
    best = max(cover.values())
    top = [u for u, n in cover.items() if n == best]
    return top[0] if len(top) == 1 else None


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(("ticker", "expected"), [
    # oracle: FACTS — TWDC and TWDK tag TWD at FY2022 and FY2023 ends, USD at FY2023 only
    ("TWDC", "TWD"), ("TWDK", "TWD"),
    # oracle: FACTS — USDA, USDB, AMDX (us-gaap) and IFRU (ifrs-full) tag USD only
    ("USDA", "USD"), ("USDB", "USD"), ("IFRU", "USD"), ("AMDX", "USD"),
])
def test_fixture_filers(kit, ticker, expected):
    assert reporting_currency(kit.companyfacts(ticker), kit.labels(ticker), 2023) == expected


@pytest.mark.criterion("C1")
def test_no_filing_for_the_year_is_none(kit):
    # oracle: requirement 1 — no annual report labelled FY2022, so no observation
    assert reporting_currency(kit.companyfacts("TWDC"), kit.labels("TWDC"), 2022) is None


@pytest.mark.criterion("C1")
def test_hand_written_documents():
    # oracle: requirement 1, by hand on each document
    g = "us-gaap:X"
    # both currencies only at the latest end, nowhere else: no single currency
    assert reporting_currency(_doc([(g, "TWD", "2023-12-31", A), (g, "USD", "2023-12-31", A)]),
                              LABELS, 2023) is None
    # per-share, share and pure units are not currencies: TWD is the only one
    assert reporting_currency(_doc([(g, "TWD", "2023-12-31", A),
                                    (g, "USD/shares", "2021-12-31", A),
                                    (g, "USD/shares", "2022-12-31", A),
                                    (g, "USD/shares", "2023-12-31", A),
                                    (g, "shares", "2023-12-31", A),
                                    (g, "pure", "2023-12-31", A)]), LABELS, 2023) == "TWD"
    # facts of another filing never count: B's EUR at three ends is not FY2023's
    assert reporting_currency(_doc([("ifrs-full:Y", "TWD", "2022-12-31", A),
                                    ("ifrs-full:Y", "TWD", "2023-12-31", A),
                                    ("ifrs-full:Z", "USD", "2023-12-31", A),
                                    (g, "EUR", "2020-12-31", B), (g, "EUR", "2021-12-31", B),
                                    (g, "EUR", "2023-12-31", B)]), LABELS, 2023) == "TWD"
    # the filing decides across concepts: TWD on one concept at two ends beats USD on another
    assert reporting_currency(_doc([("ifrs-full:Y", "TWD", "2022-12-31", A),
                                    ("ifrs-full:Z", "TWD", "2023-12-31", A),
                                    ("ifrs-full:Z", "USD", "2023-12-31", A)]),
                              LABELS, 2023) == "TWD"
    assert reporting_currency(_doc([]), LABELS, 2023) is None


_ENTRY = st.tuples(st.sampled_from(["us-gaap:X", "us-gaap:Y", "ifrs-full:Z"]),
                   st.sampled_from(["USD", "TWD", "EUR", "USD/shares", "shares", "pure"]),
                   st.sampled_from(["2021-12-31", "2022-12-31", "2023-12-31", "2024-02-15"]),
                   st.sampled_from([A, B]))


@pytest.mark.criterion("C1")
@given(entries=st.lists(_ENTRY, max_size=14), year=st.sampled_from([2022, 2023, 2024]))
def test_matches_the_rule_written_out(entries, year):
    # oracle: _hand_rule, requirement 1 restated in this test
    assert reporting_currency(_doc(entries), LABELS, year) == _hand_rule(entries, year)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(entries=st.lists(_ENTRY, min_size=1, max_size=12), data=st.data())
def test_order_and_repeats_do_not_matter(entries, data):
    # oracle: requirement 1 — the observations are a set: reordering or repeating an entry
    # changes nothing, and an earlier end for the chosen currency keeps it chosen
    want = reporting_currency(_doc(entries), LABELS, 2023)
    shuffled = data.draw(st.permutations(entries), label="order")
    assert reporting_currency(_doc(shuffled + entries[:1]), LABELS, 2023) == want
    if want is not None:
        earlier = [*entries, ("us-gaap:W", want, "2019-12-31", A)]
        assert reporting_currency(_doc(earlier), LABELS, 2023) == want
