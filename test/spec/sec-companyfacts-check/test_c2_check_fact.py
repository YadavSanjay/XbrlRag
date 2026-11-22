"""C2: ``check_fact`` matches one fact against a filer's companyfacts.

Oracle: requirements 4 and 5 of docs/specs/sec-companyfacts-check.md. One hand-built
companyfacts document (``_doc``) yields each of the seven statuses; every expected value
below is read off that document by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.sec_check.core import check_fact

ACC24 = "0000320193-24-000123"  # our filing (the FY2024 10-K)
ACC25 = "0000320193-25-000079"  # a later filing
ACC26 = "0000320193-26-000011"  # a still later filing
ACC23 = "0000320193-23-000106"  # the prior year's filing


def _doc(kit):
    """The hand-built Apple-shaped companyfacts document every C2 test reads."""
    e, (s24, e24), (s23, e23) = kit.entry, kit.FY24, kit.FY23
    return kit.companyfacts(kit.CIKS["AAPL"], "Synthetic Apple Inc.", {
        "us-gaap": {
            # match; the later accession agrees, so it is not a restatement
            "Revenues": {"USD": [e(391035e6, ACC24, e24, s24), e(391035e6, ACC25, e24, s24)]},
            # match + a restatement in ACC25; the FY2023 entry is another period
            "NetIncomeLoss": {"USD": [e(93736e6, ACC24, e24, s24),
                                      e(93700e6, ACC25, e24, s24),
                                      e(96995e6, ACC24, e23, s23)]},
            # value mismatch, the SEC value is our value negated (sign flip)
            "IncomeTaxExpenseBenefit": {"USD": [e(29749e6, ACC24, e24, s24)]},
            # value mismatch, no sign flip; two entries for our accession, first is reported
            "OperatingIncomeLoss": {"USD": [e(123000e6, ACC24, e24, s24),
                                            e(122000e6, ACC24, e24, s24)]},
            # only other accessions: ACC25 disagrees (restatement), ACC26 agrees
            "ResearchAndDevelopmentExpense": {"USD": [e(31500e6, ACC25, e24, s24),
                                                      e(31370e6, ACC26, e24, s24)]},
            # only other accessions, none agrees
            "CostOfRevenue": {"USD": [e(210000e6, ACC25, e24, s24),
                                      e(209000e6, ACC26, e24, s24)]},
            # the concept exists, but only for FY2023
            "SellingGeneralAndAdministrativeExpense": {"USD": [e(24932e6, ACC23, e23, s23)]},
            # a pure-unit rate
            "EffectiveIncomeTaxRateContinuingOperations": {"pure": [e(0.241, ACC24, e24, s24)]},
            # a duration entry and an instant entry sharing the same end date
            "CashAndCashEquivalentsAtCarryingValue": {"USD": [e(999e6, ACC24, e24, s24),
                                                              e(29943e6, ACC24, e24)]},
            # a duration entry only
            "AssetsCurrent": {"USD": [e(152987e6, ACC24, e24, s24)]},
            # our accession disagrees, another accession agrees
            "GrossProfit": {"USD": [e(180000e6, ACC24, e24, s24),
                                    e(180683e6, ACC25, e24, s24)]},
        },
    })


def _fact(kit, fact_id, local, value, *, unit="USD", instant=False, ns=None,
          accession_no=ACC24, dimensional=False):
    s24, e24 = kit.FY24
    return kit.fact(
        fact_id,
        concept=f"{ns or kit.US_GAAP}:{local}",
        value=value,
        accession_no=accession_no,
        period_end=e24,
        period_start=None if instant else s24,
        unit=unit,
        dimensional=dimensional,
    )


def _assert_restatements(got, expected):
    """Restatements: ``[{"accession_no", "value"}]`` in input order."""
    assert [r["accession_no"] for r in got] == [a for a, _ in expected]
    # tolerance: the values are copied verbatim from the document; rel=1e-12 is float idiom
    assert [r["value"] for r in got] == pytest.approx([v for _, v in expected], rel=1e-12)


@pytest.mark.criterion("C2")
def test_match_for_our_accession(kit):
    # oracle: requirement 5.4 — Revenues FY2024 USD has an ACC24 entry with val 391035e6,
    # equal to ours; the ACC25 entry agrees, so no restatement
    got = check_fact(_fact(kit, 1, "Revenues", 391035e6), _doc(kit))
    assert got["fact_id"] == 1
    assert got["status"] == "match"
    # tolerance: the SEC value is the document's val verbatim
    assert got["sec_value"] == pytest.approx(391035e6, rel=1e-12)
    assert got["sign_flip"] is False
    assert got["restatements"] == []


@pytest.mark.criterion("C2")
def test_rate_fact_matches_under_pure(kit):
    # oracle: requirement 4 — `pure` and `Rate` are one alias group; the Rate fact 0.241
    # matches the pure entry 0.241
    got = check_fact(_fact(kit, 2, "EffectiveIncomeTaxRateContinuingOperations", 0.241,
                           unit="Rate"), _doc(kit))
    assert got["status"] == "match"
    # tolerance: the SEC value is the document's val verbatim
    assert got["sec_value"] == pytest.approx(0.241, rel=1e-12)


@pytest.mark.criterion("C2")
def test_ifrs_concept_matches_under_ifrs_full(kit):
    # oracle: requirements 3 and 4 — the namespace is everything before the LAST ':'
    # (the URI has 'https:' in it), and it keys ifrs-full; TWD entry for our accession agrees
    s24, e24 = "2024-01-01", "2024-12-31"
    accn = "0001046179-25-000004"
    doc = kit.companyfacts(kit.CIKS["TSM"], "Synthetic TSMC", {"ifrs-full": {
        "Revenue": {"TWD": [kit.entry(2894307699e3, accn, e24, s24, form="20-F")]}}})
    f = kit.fact(3, concept=f"{kit.IFRS}:Revenue", value=2894307699e3, accession_no=accn,
                 period_end=e24, period_start=s24, unit="TWD", ticker="TSM")
    got = check_fact(f, doc)
    assert got["status"] == "match"
    # tolerance: the SEC value is the document's val verbatim
    assert got["sec_value"] == pytest.approx(2894307699e3, rel=1e-12)


@pytest.mark.criterion("C2")
def test_match_lists_restatements(kit):
    # oracle: requirement 5 — ACC24 agrees (93736e6) so status match; ACC25 reports 93700e6
    # for the same concept/unit/period and disagrees, so it is the one restatement; the
    # FY2023 entry is another period and is not listed
    got = check_fact(_fact(kit, 4, "NetIncomeLoss", 93736e6), _doc(kit))
    assert got["status"] == "match"
    # tolerance: the SEC value is the document's val verbatim
    assert got["sec_value"] == pytest.approx(93736e6, rel=1e-12)
    _assert_restatements(got["restatements"], [(ACC25, 93700e6)])


@pytest.mark.criterion("C2")
def test_value_mismatch_with_sign_flip(kit):
    # oracle: requirement 5.5 — our -29749e6 vs the ACC24 entry 29749e6: no agreement,
    # and 29749e6 agrees with -value, so sign_flip
    got = check_fact(_fact(kit, 5, "IncomeTaxExpenseBenefit", -29749e6), _doc(kit))
    assert got["status"] == "value_mismatch"
    # tolerance: the SEC value is the document's val verbatim
    assert got["sec_value"] == pytest.approx(29749e6, rel=1e-12)
    assert got["sign_flip"] is True


@pytest.mark.criterion("C2")
def test_value_mismatch_without_sign_flip(kit):
    # oracle: requirement 5.5 — our 123216e6 vs ACC24 entries 123000e6 then 122000e6: none
    # agrees; sec_value is the FIRST such entry's val; -123216e6 agrees with neither
    got = check_fact(_fact(kit, 6, "OperatingIncomeLoss", 123216e6), _doc(kit))
    assert got["status"] == "value_mismatch"
    # tolerance: the SEC value is the document's val verbatim
    assert got["sec_value"] == pytest.approx(123000e6, rel=1e-12)
    assert got["sign_flip"] is False
    assert got["restatements"] == []


@pytest.mark.criterion("C2")
def test_value_mismatch_wins_over_another_accession_agreeing(kit):
    # oracle: requirement 5 order — step 5 (entries for our accession, none agrees) comes
    # before step 6; GrossProfit ACC24 says 180000e6, ACC25 agrees with our 180683e6 and so
    # is not a restatement
    got = check_fact(_fact(kit, 7, "GrossProfit", 180683e6), _doc(kit))
    assert got["status"] == "value_mismatch"
    # tolerance: the SEC value is the document's val verbatim
    assert got["sec_value"] == pytest.approx(180000e6, rel=1e-12)
    assert got["sign_flip"] is False
    assert got["restatements"] == []


@pytest.mark.criterion("C2")
def test_match_in_other_accession_only(kit):
    # oracle: requirement 5.6 — no ACC24 entry; ACC25 says 31500e6 (disagrees), ACC26 says
    # 31370e6 (agrees with ours): sec_value 31370e6; ACC25 is a restatement
    got = check_fact(_fact(kit, 8, "ResearchAndDevelopmentExpense", 31370e6), _doc(kit))
    assert got["status"] == "match_other_accession"
    # tolerance: the SEC value is the document's val verbatim
    assert got["sec_value"] == pytest.approx(31370e6, rel=1e-12)
    assert got["sign_flip"] is False
    _assert_restatements(got["restatements"], [(ACC25, 31500e6)])


@pytest.mark.criterion("C2")
def test_absent_for_accession(kit):
    # oracle: requirement 5.7 — CostOfRevenue has only ACC25 (210000e6) and ACC26
    # (209000e6), neither equals our 210352e6; both are restatements, in input order
    got = check_fact(_fact(kit, 9, "CostOfRevenue", 210352e6), _doc(kit))
    assert got["status"] == "absent_for_accession"
    assert got["sign_flip"] is False
    _assert_restatements(got["restatements"], [(ACC25, 210000e6), (ACC26, 209000e6)])


@pytest.mark.criterion("C2")
def test_concept_absent_when_period_missing(kit):
    # oracle: requirement 5.3 — the concept exists but only for FY2023; nothing matches
    # concept, unit and the FY2024 period in any accession
    got = check_fact(_fact(kit, 10, "SellingGeneralAndAdministrativeExpense", 26097e6),
                     _doc(kit))
    assert got["status"] == "concept_absent"
    assert got["sign_flip"] is False
    assert got["restatements"] == []


@pytest.mark.criterion("C2")
def test_concept_absent_when_unit_differs(kit):
    # oracle: requirement 4 — the unit must be the key under `units`; Revenues has only USD
    got = check_fact(_fact(kit, 11, "Revenues", 391035e6, unit="EUR"), _doc(kit))
    assert got["status"] == "concept_absent"


@pytest.mark.criterion("C2")
def test_instant_matches_only_the_entry_without_start(kit):
    # oracle: requirement 4 — an instant matches an entry with no `start` and the same end:
    # Cash has a duration entry (999e6) and an instant entry (29943e6) ending 2024-09-28;
    # AssetsCurrent has only a duration entry, so the instant fact finds nothing
    cash = check_fact(_fact(kit, 12, "CashAndCashEquivalentsAtCarryingValue", 29943e6,
                            instant=True), _doc(kit))
    assert cash["status"] == "match"
    # tolerance: the SEC value is the document's val verbatim
    assert cash["sec_value"] == pytest.approx(29943e6, rel=1e-12)
    assets = check_fact(_fact(kit, 13, "AssetsCurrent", 152987e6, instant=True), _doc(kit))
    assert assets["status"] == "concept_absent"


@pytest.mark.criterion("C2")
def test_extension_concept(kit):
    # oracle: requirement 5.2 — a filer namespace has no taxonomy key
    got = check_fact(_fact(kit, 14, "ServicesNetSalesCustom", 96169e6,
                           ns="http://www.apple.com/20240928"), _doc(kit))
    assert got["status"] == "extension_concept"
    assert got["sign_flip"] is False
    assert got["restatements"] == []


@pytest.mark.criterion("C2")
def test_dimensional_fact_is_not_checked(kit):
    # oracle: requirement 5.1 — dimensional comes first, even when the value would match
    got = check_fact(_fact(kit, 15, "Revenues", 391035e6, dimensional=True), _doc(kit))
    assert got["fact_id"] == 15
    assert got["status"] == "dimensional"
    assert got["sign_flip"] is False
    assert got["restatements"] == []


# (fact_id, local name, value, unit, instant, namespace, dimensional, expected status):
# every row's status is read off `_doc` by hand, as in the point tests above.
_SCENARIOS = [
    (1, "Revenues", 391035e6, "USD", False, None, False, "match"),
    (2, "EffectiveIncomeTaxRateContinuingOperations", 0.241, "Rate", False, None, False,
     "match"),
    (4, "NetIncomeLoss", 93736e6, "USD", False, None, False, "match"),
    (5, "IncomeTaxExpenseBenefit", -29749e6, "USD", False, None, False, "value_mismatch"),
    (6, "OperatingIncomeLoss", 123216e6, "USD", False, None, False, "value_mismatch"),
    (7, "GrossProfit", 180683e6, "USD", False, None, False, "value_mismatch"),
    (8, "ResearchAndDevelopmentExpense", 31370e6, "USD", False, None, False,
     "match_other_accession"),
    (9, "CostOfRevenue", 210352e6, "USD", False, None, False, "absent_for_accession"),
    (10, "SellingGeneralAndAdministrativeExpense", 26097e6, "USD", False, None, False,
     "concept_absent"),
    (12, "CashAndCashEquivalentsAtCarryingValue", 29943e6, "USD", True, None, False, "match"),
    (13, "AssetsCurrent", 152987e6, "USD", True, None, False, "concept_absent"),
    (14, "ServicesNetSalesCustom", 96169e6, "USD", False, "http://www.apple.com/20240928",
     False, "extension_concept"),
    (15, "Revenues", 391035e6, "USD", False, None, True, "dimensional"),
]


@pytest.mark.criterion("C2")
@given(
    row=st.sampled_from(_SCENARIOS),
    magnitude=st.floats(min_value=1e-6, max_value=1e6, allow_nan=False, allow_infinity=False),
    negative=st.booleans(),
)
def test_scaling_every_value_keeps_the_status(kit, row, magnitude, negative):
    # oracle: requirement 4 — the agree rule is relative (1e-9 * max(|a|, |b|, 1)); every
    # pair in `_doc` is either bit-equal or >= 0.1% apart, so scaling the document and the
    # fact by the same non-zero k keeps each hand-derived status
    fact_id, local, value, unit, instant, ns, dimensional, status = row
    k = -magnitude if negative else magnitude
    doc = kit.scale_doc(_doc(kit), k)
    got = check_fact(_fact(kit, fact_id, local, value * k, unit=unit, instant=instant, ns=ns,
                           dimensional=dimensional), doc)
    assert got["status"] == status
    assert got["sign_flip"] is (fact_id == 5)
