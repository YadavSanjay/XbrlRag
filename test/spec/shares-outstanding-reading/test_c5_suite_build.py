"""C5: the v2 build keys shares outstanding from the balance sheet and emits cover readings.

Oracle: requirements 1, 2, 4 and 5 of docs/specs/shares-outstanding-reading.md and the
synthetic payload's values, read by hand (filer SSS copies Union Pacific's FY2023-25 counts;
filer PPP, like Procter & Gamble, tags issued shares only).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.suites.v2_suite import build_v2_suites

SALT = "shares-test"
SHARES = "SharesOutstanding"


def _build(kit, inventory=None, salt=SALT):
    inv = kit.inventory() if inventory is None else inventory
    return build_v2_suites(inv, {"covered": ["SSS", "PPP"]}, kit.sec(), kit.store(),
                           kit.phrasebook, kit.lexicon, quotas={"lookup": 100, "trend": 100},
                           per_intent=1, share=0.5, salt=salt)


def _cases(build):
    return [c for split in ("dev", "validation") for c in build[split]["cases"]]


def _find(build, shape, ticker, metric_concept, fy):
    found = [c for c in _cases(build)
             if f"shape:{shape}" in c["tags"] and c.get("ticker") == ticker
             and c.get("concept") == metric_concept and c.get("fy") == fy]
    assert len(found) == 1, (shape, ticker, metric_concept, fy)
    return found[0]


def _reading(kit, year):
    return {"reading": "cover_page", "value": kit.COVER[year], "concept": kit.COVER_CONCEPT,
            "period_end": kit.COVER_DATE[year], "accession": kit.OWN_10K[year]}


@pytest.mark.criterion("C5")
def test_lookup_case_carries_its_years_cover_reading(kit):
    # oracle: payload — SSS FY2025 key 593,245,884 at 2025-12-31 (balance sheet); the FY2025
    # 10-K's cover 593,391,460 on 2026-01-30 is the reading; the 10-K/A's is not
    build = _build(kit)
    case = _find(build, "lookup", "SSS", "us-gaap:CommonStockSharesOutstanding", 2025)
    assert case["expected_value"] == kit.KEY[2025]
    assert case.get("alt_readings") == [_reading(kit, 2025)]
    assert "alt_values" not in case
    alternates = case["key"].get("alternates", [])
    assert [(a["value"], a["accession"], a["selection"]) for a in alternates] == [
        (kit.COVER[2025], kit.S25, "cover_page")]
    loaded = EvalCase.from_dict(case)
    assert loaded.alt_readings == [_reading(kit, 2025)]
    assert loaded.to_dict()["alt_readings"] == [_reading(kit, 2025)]


@pytest.mark.criterion("C5")
def test_trend_rows_each_read_their_own_10k(kit):
    # oracle: payload — the FY2023 key comes from the FY2024 10-K (latest filed, S1), but the
    # FY2023 row's reading is the FY2023 10-K's cover, 609,777,914
    build = _build(kit)
    case = _find(build, "trend", "SSS", "us-gaap:CommonStockSharesOutstanding", 2025)
    rows = case["expected_rows"]
    assert [r["value"] for r in rows] == [kit.KEY[y] for y in (2023, 2024, 2025)]
    assert [r.get("alt_readings") for r in rows] == [[_reading(kit, y)] for y in (2023, 2024, 2025)]
    assert case["key"]["inputs"][0]["accession"] == kit.S24
    alternates = case["key"].get("alternates", [])
    assert [a["accession"] for a in alternates] == [kit.S23, kit.S24, kit.S25]


@pytest.mark.criterion("C5")
def test_other_metrics_are_emitted_unchanged(kit):
    # oracle: requirement 4 — a Revenue lookup of the same filer has no readings and no
    # alternates; the shares lookup of the same build does
    build = _build(kit)
    revenue = _find(build, "lookup", "SSS", "us-gaap:Revenues", 2025)
    assert "alt_readings" not in revenue
    assert "alternates" not in revenue["key"]
    shares = _find(build, "lookup", "SSS", "us-gaap:CommonStockSharesOutstanding", 2025)
    assert shares.get("alt_readings")


@pytest.mark.criterion("C5")
def test_issued_shares_never_key_shares_outstanding(kit):
    # oracle: requirements 1-2 — PPP's inventory lists CommonStockSharesIssued for the metric:
    # no SharesOutstanding case, a concept_mismatch drop, and its Revenue lookup still built
    build = _build(kit)
    ppp = [c for c in _cases(build) if c.get("ticker") == "PPP"]
    assert all(c.get("concept") != "us-gaap:CommonStockSharesIssued" for c in ppp)
    assert all(c.get("concept") != "dei:EntityCommonStockSharesOutstanding" for c in ppp)
    assert any(c.get("concept") == "us-gaap:Revenues" for c in ppp)
    assert build["manifest"]["intents"]["dropped_by_shape"]["lookup"]["concept_mismatch"] == 1


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@settings(max_examples=5)
@given(salt=st.sampled_from(["a", "b", "c", "shares-test"]))
def test_readings_do_not_depend_on_the_split(kit, salt):
    # oracle: requirement 4 — readings come from the payload, not from the phrasing or split:
    # every SSS shares lookup carries its own year's cover, whatever the salt
    build = _build(kit, salt=salt)
    lookups = [c for c in _cases(build) if "shape:lookup" in c["tags"]
               and c.get("ticker") == "SSS"
               and c.get("concept") == "us-gaap:CommonStockSharesOutstanding"]
    assert sorted(c["fy"] for c in lookups) == [2023, 2024, 2025]
    for case in lookups:
        assert case.get("alt_readings") == [_reading(kit, case["fy"])]
