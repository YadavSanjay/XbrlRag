"""C6: screens and peer-relative rank only keyed USD filers and name every exclusion
(S3, S5, S6), flagging year-ends more than 92 days apart (S2).

Oracle: requirement 9 of docs/specs/v2-suite-build.md; values read off the conftest
payloads by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.intents import Intent
from src.eval.suites.v2_suite import IntentDroppedError, key_intent


def _key(kit, intent, *, docs=None, inv=None, uni=None):
    docs = kit.companyfacts_docs() if docs is None else docs
    return key_intent(intent, kit.inventory() if inv is None else inv,
                      kit.universes() if uni is None else uni, kit.sec(docs), kit.store(docs))


def _screen(op, y, m, u="covered"):
    n = 3 if op in ("top", "bottom") else None
    return Intent("screen", operation=op, n=n, universe=u, fiscal_years=[y], metrics=[m])


ROW_BBB_24 = {"ticker": "BBB", "value": 220_000_000_000, "fiscal_year": 2024,
              "period_end": "2024-12-31"}
ROW_AAA_24 = {"ticker": "AAA", "value": 121_000_000_000, "fiscal_year": 2024,
              "period_end": "2024-09-28"}


@pytest.mark.criterion("C6")
def test_top_ranks_usd_keys_and_names_exclusions(kit):
    # oracle: payload — FY2024 revenue: BBB 220bn (B24), AAA 121bn (A24). CCC's revenue is
    # only in EUR (the inventory says USD): unit_mismatch. TWX reports TWD: currency.
    # Year ends 2024-09-28 and 2024-12-31 are 94 days apart (> 92): disclose.
    keyed = _key(kit, _screen("top", 2024, "Revenue"))
    assert keyed["shape"] == "screen.top"
    assert keyed["rows"] == [ROW_BBB_24, ROW_AAA_24]
    assert keyed["value"] is None
    assert keyed["disclosures"] == {
        "universe": "covered", "members": 4, "ranked": ["BBB", "AAA"],
        "excluded": [{"ticker": "CCC", "reason": "unit_mismatch"},
                     {"ticker": "TWX", "reason": "currency"}],
        "period_ends": {"BBB": "2024-12-31", "AAA": "2024-09-28"},
        "disclose_period_ends": True,
    }
    assert {"S3", "S5"} <= set(keyed["policies"])
    assert [k["accession"] for k in keyed["inputs"]] == [kit.B24, kit.A24]


@pytest.mark.criterion("C6")
def test_bottom_and_ninety_two_days(kit):
    # oracle: payload — FY2023 revenue: AAA 111bn (A24), BBB 206bn (B23A, latest filed).
    # Bottom ranks ascending. 2023-09-30 to 2023-12-31 is exactly 92 days: no disclosure.
    keyed = _key(kit, _screen("bottom", 2023, "Revenue"))
    assert keyed["rows"] == [
        {"ticker": "AAA", "value": 111 * kit.BN, "fiscal_year": 2023,
         "period_end": "2023-09-30"},
        {"ticker": "BBB", "value": 206 * kit.BN, "fiscal_year": 2023,
         "period_end": "2023-12-31"},
    ]
    assert keyed["disclosures"]["disclose_period_ends"] is False
    assert keyed["disclosures"]["excluded"] == [
        {"ticker": "CCC", "reason": "period_not_filed"},
        {"ticker": "TWX", "reason": "period_not_filed"}]


@pytest.mark.criterion("C6")
def test_sum_and_median(kit):
    # oracle: payload — FY2024 net income AAA 12bn, BBB 25bn: sum 37bn, median 18.5bn.
    # CCC has no NetIncomeLoss at all (no_key); TWX's inventory has no NetIncome.
    total = _key(kit, _screen("sum", 2024, "NetIncome"))
    assert total["value"] == 37 * kit.BN
    assert total["rows"] is None
    assert total["disclosures"]["excluded"] == [
        {"ticker": "CCC", "reason": "no_key"}, {"ticker": "TWX", "reason": "metric_not_tagged"}]
    median = _key(kit, _screen("median", 2024, "NetIncome"))
    assert median["value"] == pytest.approx(18.5 * kit.BN, rel=1e-12)  # exact half
    assert median["shape"] == "screen.median"


@pytest.mark.criterion("C6")
def test_one_ranked_filer_is_too_few(kit):
    # oracle: inventory — only AAA holds FY2022, so fewer than 2 filers rank
    with pytest.raises(IntentDroppedError) as err:
        _key(kit, _screen("top", 2022, "Revenue"))
    assert err.value.args[0] == "too_few_ranked"


@pytest.mark.criterion("C6")
def test_peer_relative(kit):
    # oracle: payload — peers [AAA, BBB, ZZZ]: BBB 220bn, AAA 121bn ranked; ZZZ is not in
    # the inventory. AAA ranks 2; median (220 + 121) / 2 = 170.5bn; AAA is below it
    intent = Intent("peer_relative", tickers=["AAA"], fiscal_years=[2024], metrics=["Revenue"],
                    universe="peers")
    keyed = _key(kit, intent)
    assert keyed["rows"] == [ROW_BBB_24, ROW_AAA_24]
    assert keyed["subject"]["ticker"] == "AAA"
    assert keyed["subject"]["rank"] == 2
    assert keyed["subject"]["universe_median"] == pytest.approx(170.5 * kit.BN, rel=1e-12)
    assert keyed["subject"]["position"] == "below"
    assert keyed["disclosures"]["members"] == 3
    assert keyed["disclosures"]["excluded"] == [{"ticker": "ZZZ", "reason": "not_in_corpus"}]


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(perm=st.permutations(["AAA", "BBB", "CCC", "TWX"]))
def test_member_order_does_not_matter(kit, perm):
    # oracle: requirement 9 — members are visited in ticker order, so any listing order
    # gives the same ranking and disclosures
    uni = {"covered": list(perm), "peers": ["AAA", "BBB", "ZZZ"]}
    keyed = _key(kit, _screen("top", 2024, "Revenue"), uni=uni)
    assert keyed["rows"] == [ROW_BBB_24, ROW_AAA_24]
    assert keyed["disclosures"]["excluded"] == [{"ticker": "CCC", "reason": "unit_mismatch"},
                                                {"ticker": "TWX", "reason": "currency"}]


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
def test_adding_a_non_usd_filer_adds_one_currency_exclusion(kit):
    # oracle: S6 — EEE reports in EUR: it is excluded as currency, never converted, and the
    # ranked rows do not change
    inv = kit.inventory()
    inv["EEE"] = kit.eee_inventory_entry()
    uni = {"covered": ["AAA", "BBB", "CCC", "TWX", "EEE"], "peers": ["AAA", "BBB", "ZZZ"]}
    keyed = _key(kit, _screen("top", 2024, "Revenue"), inv=inv, uni=uni)
    assert keyed["rows"] == [ROW_BBB_24, ROW_AAA_24]
    assert keyed["disclosures"]["members"] == 5
    assert keyed["disclosures"]["excluded"] == [
        {"ticker": "CCC", "reason": "unit_mismatch"}, {"ticker": "EEE", "reason": "currency"},
        {"ticker": "TWX", "reason": "currency"}]
