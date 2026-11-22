"""C9: each phrased case becomes an EvalCase with the run_columns tags and the keyed
expectations.

Oracle: requirement 13 of docs/specs/v2-suite-build.md, the run_columns tag conventions and
the eval-intents split rule (restated with hashlib); values read off the payloads by hand.
"""

from __future__ import annotations

import hashlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.run_columns import run_split, slice_fields_from_tags
from src.eval.suites.intents import Intent
from src.eval.suites.v2_suite import build_v2_suites

SALT = "v2-test"


def _build(kit, share=0.5, salt=SALT):
    docs = kit.companyfacts_docs()
    return build_v2_suites(kit.inventory(), kit.universes(), kit.sec(docs), kit.store(docs),
                           kit.phrasebook, kit.lexicon, quotas={s: 100 for s in kit.SHAPES},
                           per_intent=1, share=share, salt=salt)


def _cases(build):
    return [(s, c) for s in ("dev", "validation") for c in build[s]["cases"]]


def _case_of(kit, build, intent):
    iid = kit.intent_id(intent)
    found = [c for _s, c in _cases(build) if f"intent:{iid}" in c["tags"]]
    assert len(found) == 1, intent  # per_intent = 1: one case per selected intent
    return found[0]


@pytest.mark.criterion("C9")
def test_tags_are_the_run_columns_in_order(kit):
    # oracle: requirement 13 — seven tags, in order, parsed by run_columns to the case's
    # split, intent, register (family), template, key source and key status
    build = _build(kit)
    pairs = _cases(build)
    assert pairs
    for split, case in pairs:
        tags = case["tags"]
        assert [t.partition(":")[0] for t in tags] == list(kit.TAG_PREFIXES)
        fields = slice_fields_from_tags(tags)
        assert fields["split"] == split
        assert case["suite"] == f"xbrl_v2_{split}"
        template = kit.phrasebook.get(fields["template_id"])
        assert fields["phrasing_family"] == template.register
        assert fields["key_source"] == "sec_companyfacts"
        assert fields["key_status"] == "verified"  # the default fake store agrees everywhere
        assert case["paraphrase_group"] == fields["intent_id"]
        shape = tags[6].partition(":")[2]
        assert case["origin"] == f"v2:{shape}"
        assert case["kind"] == {
            "lookup": "lookup", "point_in_time": "lookup", "trend": "breakdown",
            "screen.top": "screen", "screen.bottom": "screen", "peer_relative": "screen",
        }.get(shape, "compute")


@pytest.mark.criterion("C9")
def test_lookup_case_fields(kit):
    # oracle: payload — AAA FY2022 revenue 102bn (A24); CIK 0000900101
    build = _build(kit)
    case = _case_of(kit, build, Intent("lookup", tickers=["AAA"], fiscal_years=[2022],
                                       metrics=["Revenue"]))
    assert case["kind"] == "lookup"
    assert case["expected_value"] == 102 * kit.BN
    assert case["ticker"] == "AAA" and case["cik"] == 900101 and case["fy"] == 2022
    assert case["concept"] == "us-gaap:Revenues" and case["unit"] == "USD"
    assert "alt_values" not in case and "expected_rows" not in case
    assert case["key"]["inputs"][0]["accession"] == kit.A24
    assert case["key"]["status"] == "verified"
    loaded = EvalCase.from_dict(case)
    assert loaded.expected_value == 102 * kit.BN
    assert loaded.question


@pytest.mark.criterion("C9")
def test_not_meaningful_growth_case(kit):
    # oracle: S4 — AAA net income FY2022 -5bn -> FY2023 8bn: not meaningful, value 13/5,
    # alt 260; growth is dimensionless (no unit, no concept)
    build = _build(kit)
    case = _case_of(kit, build, Intent("compute", operation="yoy_growth", tickers=["AAA"],
                                       fiscal_years=[2022, 2023], metrics=["NetIncome"]))
    assert case["kind"] == "compute"
    assert case["expect_not_meaningful"] is True
    assert case["expected_value"] == pytest.approx(13 / 5, rel=1e-12)  # float rounding only
    assert case["alt_values"] == [pytest.approx(260, rel=1e-12)]
    assert "unit" not in case and "concept" not in case
    assert case["fy"] == 2023
    assert case["key"]["definition"] == "growth"


@pytest.mark.criterion("C9")
def test_screen_and_trend_cases(kit):
    # oracle: payload — FY2024 revenue top: BBB 220bn, AAA 121bn; AAA total-assets trend
    # 301bn, 321bn, 350bn graded as an unordered breakdown
    build = _build(kit)
    top = _case_of(kit, build, Intent("screen", operation="top", n=3, universe="covered",
                                      fiscal_years=[2024], metrics=["Revenue"]))
    assert top["kind"] == "screen" and top["top_n"] == 3
    assert [r["ticker"] for r in top["expected_rows"]] == ["BBB", "AAA"]
    assert "ticker" not in top and "cik" not in top and "concept" not in top
    assert top["unit"] == "USD"
    assert top["disclosures"]["excluded"] == [{"ticker": "CCC", "reason": "unit_mismatch"},
                                              {"ticker": "TWX", "reason": "currency"}]
    trend = _case_of(kit, build, Intent("trend", tickers=["AAA"],
                                        fiscal_years=[2022, 2023, 2024],
                                        metrics=["TotalAssets"]))
    assert trend["kind"] == "breakdown"
    assert trend["order_sensitive"] is False
    assert [r["value"] for r in trend["expected_rows"]] == [301 * kit.BN, 321 * kit.BN,
                                                             350 * kit.BN]
    assert trend["key"]["same_filing"] is False
    total = _case_of(kit, build, Intent("screen", operation="sum", universe="covered",
                                        fiscal_years=[2024], metrics=["NetIncome"]))
    assert total["kind"] == "compute" and total["expected_value"] == 37 * kit.BN


@pytest.mark.criterion("C9")
@settings(max_examples=12)
@given(share=st.floats(min_value=0.0, max_value=1.0), salt=st.text(min_size=1, max_size=8))
def test_split_is_the_phrased_cases_split(kit, share, salt):
    # oracle: eval-intents split rule restated — validation iff the intent's or the
    # template's axis hash falls under the share; each suite holds one split only
    def axis(key):
        return int(hashlib.sha1(f"{salt}:{key}".encode()).hexdigest()[:8], 16) / 2**32 < share

    build = _build(kit, share=share, salt=salt)
    pairs = _cases(build)
    assert pairs
    for split in ("dev", "validation"):
        cases = [EvalCase.from_dict(c) for c in build[split]["cases"]]
        if cases:
            assert run_split(cases) == split
    for split, case in pairs:
        fields = slice_fields_from_tags(case["tags"])
        validation = axis(fields["intent_id"]) or axis(fields["template_id"])
        assert split == ("validation" if validation else "dev")
