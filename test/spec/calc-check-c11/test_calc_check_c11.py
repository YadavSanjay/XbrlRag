"""Spec tests for calc-check-c11: the sum-of-parts check per XBRL Calculations 1.1
(round-to-nearest), counted per filing (docs/specs/calc-check-c11.md).

Point cases use real excerpts from xbrl_db (fixtures/, hand-checked against each filing's
rendered statement and cross-checked with Arelle 2.41.0 `--calc c11r`); properties use
small synthetic filings. Every bound is an exact Decimal, so values compare exactly.
"""

from __future__ import annotations

import csv
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.xbrl.calc_check import (
    calc_gate_decision,
    check_calculations,
    load_calc_exceptions,
)

M = Decimal(10) ** 6  # the fixtures' statements are in millions
SUM_2003 = "http://www.xbrl.org/2003/arcrole/summation-item"
SUM_2023 = "https://xbrl.org/2023/arcrole/summation-item"
PARENT_CHILD = "http://www.xbrl.org/2003/arcrole/parent-child"
PASS, FAIL, PARTIAL, INAPPLICABLE = "pass", "fail", "partial", "inapplicable"
STATUSES = {PASS, FAIL, PARTIAL, INAPPLICABLE}
RESULT_KEYS = {
    "link_role", "parent", "entity", "period_start", "period_end", "dimensions", "unit",
    "status", "dimensional", "reported_low", "reported_high", "computed_low", "computed_high",
    "parent_fact_ids", "child_fact_ids", "missing_children",
}
DECISION_KEYS = {
    "accession_no", "held_back", "strict_failures", "excepted_failures", "new_failures",
    "partial", "dimensional_failures", "inapplicable",
}

MCD_ACC = "0000063908-25-000012"
MCD_ROLE = "http://www.mcdonalds.com/role/ConsolidatedBalanceSheet"
MCD_ASSETS = "http://fasb.org/us-gaap/2024:Assets"
AMD_ACC = "0000002488-24-000012"
AMD_ROLE = "http://www.amd.com/role/ConsolidatedStatementsofCashFlows"
AMD_FIN = "http://fasb.org/us-gaap/2023:NetCashProvidedByUsedInFinancingActivities"


def _d(x) -> Decimal:
    return Decimal(str(x))


def _bounds(r: dict) -> tuple:
    return (r["reported_low"], r["reported_high"], r["computed_low"], r["computed_high"])


def _ids(r: dict) -> tuple:
    return (r["parent_fact_ids"], r["child_fact_ids"])


def _by_period(results) -> dict:
    return {(r["period_start"], r["period_end"]): r for r in results}


def _key(x: dict) -> tuple:
    return (x["entity"], x["period_start"], x["period_end"], frozenset(x["dimensions"]), x["unit"])


# ---------------------------------------------------------------- synthetic building blocks
E = "0000000001"
ROLE = "http://example.com/role/Statement"
ROLE2 = "http://example.com/role/Detail"
P, A, B, C = "t:P", "t:A", "t:B", "t:C"
SEG = frozenset({("t:SegmentAxis", "t:XMember")})
INSTANT = (E, None, "2025-01-01", frozenset(), "USD")


def _f(fid, concept, value, decimals=0, ctx=INSTANT) -> dict:
    entity, start, end, dims, unit = ctx
    return {"fact_id": fid, "concept": concept, "entity": entity, "period_start": start,
            "period_end": end, "dimensions": sorted(dims), "unit": unit,
            "value": None if value is None else _d(value), "decimals": decimals}


def _arc(child, weight=1, parent=P, role=ROLE, arcrole=SUM_2003) -> dict:
    return {"arcrole": arcrole, "link_role": role, "parent": parent, "child": child,
            "weight": _d(weight)}


TREE = [_arc(A), _arc(B), _arc(C)]


def _well_formed(results, facts) -> None:
    """Invariants any correct result list has (Requirement 1, Fields)."""
    assert results, "the generated filing always binds the Statement tree at its base context"
    by_id = {f["fact_id"]: f for f in facts}
    for r in results:
        assert set(r) == RESULT_KEYS
        assert r["status"] in STATUSES
        assert r["dimensional"] == bool(r["dimensions"])
        assert r["parent_fact_ids"] and list(r["parent_fact_ids"]) == sorted(r["parent_fact_ids"])
        for fid in r["parent_fact_ids"]:
            assert by_id[fid]["concept"] == r["parent"] and _key(by_id[fid]) == _key(r)


CONTEXTS = [
    INSTANT,
    (E, "2024-01-01", "2025-01-01", frozenset(), "USD"),  # a year
    (E, "2024-10-01", "2025-01-01", frozenset(), "USD"),  # a quarter, same end
    (E, None, "2025-01-01", SEG, "USD"),
    (E, None, "2025-01-01", frozenset(), "EUR"),
]
NEW_CONTEXT = (E, None, "2031-01-01", frozenset(), "USD")


@st.composite
def filings(draw):
    """A small filing: two trees (P <- A, B, C and A <- B, C), facts in five contexts.

    Values are whole millions at decimals -6, -5 or exact, sometimes nil, sometimes
    repeated, so every status occurs. P and A always have a non-nil fact at INSTANT, so
    the Statement tree binds there.
    """
    w = [draw(st.sampled_from([1, -1])) for _ in range(3)]
    arcs = [_arc(c, wt, arcrole=draw(st.sampled_from([SUM_2003, SUM_2023])))
            for c, wt in zip((A, B, C), w, strict=True)]
    arcs += [_arc(B, parent=A, role=ROLE2), _arc(C, parent=A, role=ROLE2)]
    facts: list[dict] = []
    for ci, ctx in enumerate(CONTEXTS):
        for concept in (P, A, B, C):
            base = ci == 0 and concept in (P, A)
            n = draw(st.integers(min_value=1 if base else 0, max_value=3))
            first = None
            for i in range(n):
                if i > 0 and draw(st.booleans()):
                    value, dec = first  # a repeated iXBRL fact
                else:
                    nil = not (base and i == 0) and draw(st.integers(0, 9)) == 0
                    value = None if nil else Decimal(draw(st.integers(-500, 500))) * M
                    dec = draw(st.sampled_from([-6, -5, None]))
                first = first or (value, dec)
                facts.append(_f(len(facts) + 1, concept, value, dec, ctx))
    return facts, arcs


# =========================================================================== C1 arcs
@pytest.mark.criterion("C1")
def test_apple_2023_arcrole_tree_is_checked(case):
    # oracle: Apple 10-Q 0000320193-26-000013 balance sheet, by hand: 147,957 + 211,284
    # = 359,241 and 144,114 + 226,968 = 371,082 (millions, decimals -6, so +-0.5 each);
    # Arelle 2.41.0 --calc c11r reports no inconsistency in this tree.
    c = case("apple_assets_2023_arcrole")
    assert {a["arcrole"] for a in c.arcs} == {SUM_2023}
    got = _by_period(check_calculations(c.facts, c.arcs))
    assert set(got) == {(None, "2025-09-28"), (None, "2026-03-29")}
    old, new = got[(None, "2025-09-28")], got[(None, "2026-03-29")]
    assert old["status"] == new["status"] == PASS
    assert _bounds(old) == (_d("359240.5") * M, _d("359241.5") * M, 359240 * M, 359242 * M)
    assert _bounds(new) == (_d("371081.5") * M, _d("371082.5") * M, 371081 * M, 371083 * M)
    assert _ids(old) == ((15619,), (15607, 15617))
    assert _ids(new) == ((15618,), (15606, 15616))
    assert old["missing_children"] == () and old["dimensional"] is False


@pytest.mark.criterion("C1")
def test_nvda_arcs_fanned_out_by_reused_role_count_once(case):
    # oracle: the 24 mv_calculation_network rows are each of the 8 balance-sheet arcs three
    # times (one per custom_role_type row of the reused URI); counted once, the FY2026
    # children sum to 206,803 = Assets (balance sheet, by hand), +-0.5M per child.
    c = case("nvda_assets")
    assert len(c.mv_arcs) == 24 and len(c.arcs) == 8
    once = check_calculations(c.facts, c.arcs)
    assert check_calculations(c.facts, c.mv_arcs) == once
    fy26 = _by_period(once)[(None, "2026-01-26")]
    assert fy26["status"] == PASS
    assert (fy26["computed_low"], fy26["computed_high"]) == (206799 * M, 206807 * M)


@pytest.mark.criterion("C1")
def test_other_arcroles_are_ignored(case):
    # oracle: Requirement 1 (Arcs): only the two summation-item arcroles count.
    c = case("apple_assets_2023_arcrole")
    relabelled = [{**a, "arcrole": PARENT_CHILD} for a in c.arcs]
    assert check_calculations(c.facts, relabelled) == []
    assert check_calculations(c.facts, c.arcs + relabelled) == check_calculations(c.facts, c.arcs)
    assert len(check_calculations(c.facts, c.arcs)) == 2


@pytest.mark.criterion("C1")
@given(filings(), st.lists(st.integers(1, 3), min_size=5, max_size=5),
       st.lists(st.sampled_from([SUM_2003, SUM_2023]), min_size=15, max_size=15))
def test_repeating_arcs_changes_nothing(filing, times, roles):
    # oracle: Requirement 1 (Arcs): each (link_role, parent, child) counts once, under
    # either summation arcrole; an arc under another arcrole adds nothing (metamorphic).
    facts, arcs = filing
    before = check_calculations(facts, arcs)
    _well_formed(before, facts)
    repeated, k = [], 0
    for arc, n in zip(arcs, times, strict=True):
        for _ in range(n):
            repeated.append({**arc, "arcrole": roles[k]})
            k += 1
    repeated.append(_arc("t:Z", parent=P, arcrole=PARENT_CHILD))
    extra = [_f(10_000, "t:Z", 999 * M, -6)]
    assert check_calculations(facts + extra, repeated) == before


# =========================================================================== C2 context
@pytest.mark.criterion("C2")
def test_intel_quarter_never_mixes_with_year_to_date(case):
    # oracle: Intel 10-Q 0000050863-26-000157 statement of operations, by hand (millions):
    # Q2-2026 16,128 - 9,619 = 6,509; H1-2026 29,705 - 17,849 = 11,856;
    # Q2-2025 12,859 - 9,317 = 3,542; H1-2025 25,526 - 17,312 = 8,214. Revenue is tagged
    # twice per period (same value), so each period binds exactly three child facts.
    c = case("intel_10q_gross_profit")
    got = _by_period(check_calculations(c.facts, c.arcs))
    expected = {
        ("2026-03-29", "2026-06-28"): (6509, (159879,), (159871, 159875, 160278)),
        ("2025-12-28", "2026-06-28"): (11856, (159881,), (159873, 159877, 160324)),
        ("2025-03-30", "2025-06-29"): (3542, (159880,), (159872, 159876, 160301)),
        ("2024-12-29", "2025-06-29"): (8214, (159882,), (159874, 159878, 160347)),
    }
    assert set(got) == set(expected)
    for period, (gp, pids, cids) in expected.items():
        r = got[period]
        assert r["status"] == PASS
        half = _d("0.5")
        assert _bounds(r) == ((gp - half) * M, (gp + half) * M, (gp - 1) * M, (gp + 1) * M)
        assert _ids(r) == (pids, cids)


@pytest.mark.criterion("C2")
def test_context_and_unit_match_exactly():
    # oracle: Requirement 1 (Context), by hand: only A's fact in P's exact context binds
    # (100 = 100); each other A fact would break the sum if it were bound.
    other_ctx = [
        (E, None, "2025-01-01", SEG, "USD"),  # superset of P's (empty) dimensions
        (E, None, "2025-01-01", frozenset(), "EUR"),  # other unit
        ("0000000002", None, "2025-01-01", frozenset(), "USD"),  # other entity
        (E, "2024-01-01", "2025-01-01", frozenset(), "USD"),  # a duration with that end
    ]
    two_dims = frozenset({("t:SegmentAxis", "t:XMember"), ("t:GeoAxis", "t:UsMember")})
    facts = [_f(1, P, 100), _f(2, A, 100)]
    facts += [_f(10 + i, A, 7 * (i + 1), 0, ctx) for i, ctx in enumerate(other_ctx)]
    facts += [_f(20, P, 50, 0, (E, None, "2025-01-01", two_dims, "USD"))]  # child has a subset
    got = check_calculations(facts, TREE)
    assert len(got) == 1
    r = got[0]
    assert (r["status"], _ids(r)) == (PASS, ((1,), (2,)))
    assert _bounds(r) == (_d("99.5"), _d("100.5"), _d("99.5"), _d("100.5"))
    assert r["missing_children"] == (B, C)
    assert r["dimensions"] == frozenset()


@pytest.mark.criterion("C2")
@given(filings(), st.lists(st.tuples(st.sampled_from([P, A, B, C]), st.integers(-500, 500),
                                     st.sampled_from([-6, -5, None])), min_size=1, max_size=6))
def test_unrelated_context_changes_no_other_result(filing, added):
    # oracle: Requirement 1 (Context, Binding): a result depends only on its own key
    # (metamorphic).
    facts, arcs = filing
    before = check_calculations(facts, arcs)
    _well_formed(before, facts)
    extra = [_f(5000 + i, con, Decimal(v) * M, d, NEW_CONTEXT)
             for i, (con, v, d) in enumerate(added)]
    after = check_calculations(facts + extra, arcs)
    assert [r for r in after if r["period_end"] != NEW_CONTEXT[2]] == before


# =========================================================================== C3 duplicates
@pytest.mark.criterion("C3")
def test_nvda_duplicate_facts_count_once(case):
    # oracle: NVIDIA 10-K 0001045810-26-000021 balance sheet, by hand (millions):
    # FY2026 125,605 + 10,383 + 2,867 + 20,832 + 3,306 + 13,258 + 22,251 + 8,301 = 206,803;
    # FY2025 80,126 + 6,283 + 1,793 + 5,188 + 807 + 10,979 + 3,387 + 3,038 = 111,601.
    # Goodwill and deferred tax are also tagged at -8 (20.8B, 13.3B; 5.2B, 11.0B) and PP&E
    # twice: each group's interval is its -6 fact's, so 8 children x +-0.5M = +-4M.
    c = case("nvda_assets")
    got = _by_period(check_calculations(c.facts, c.arcs))
    assert set(got) == {(None, "2026-01-26"), (None, "2025-01-27")}
    fy26, fy25 = got[(None, "2026-01-26")], got[(None, "2025-01-27")]
    assert fy26["status"] == fy25["status"] == PASS
    assert _bounds(fy26) == (_d("206802.5") * M, _d("206803.5") * M, 206799 * M, 206807 * M)
    assert _bounds(fy25) == (_d("111600.5") * M, _d("111601.5") * M, 111597 * M, 111605 * M)
    assert _ids(fy26) == ((172,), (156, 158, 160, 162, 164, 166, 168, 170, 555, 768, 1057))
    assert _ids(fy25) == ((173,), (157, 159, 161, 163, 165, 167, 169, 171, 558, 769, 1058))


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("facts", [
    # child: two facts at the same decimals with different values
    [_f(1, P, 300 * M, -6), _f(2, A, 100 * M, -6), _f(3, A, 101 * M, -6), _f(4, B, 200 * M, -6)],
    # parent: a nil and a non-nil fact
    [_f(1, P, 300 * M, -6), _f(2, P, None, -6), _f(3, A, 100 * M, -6), _f(4, B, 200 * M, -6)],
    # parent: excess digits (1,234,567 at decimals -3)
    [_f(1, P, 1234567, -3), _f(2, A, 1234000, -3)],
    # parent: 20.9B at -8 does not contain 20.832B at -6
    [_f(1, P, 20832 * M, -6), _f(2, P, 20900 * M, -8), _f(3, A, 20832 * M, -6)],
], ids=["child-same-decimals", "parent-nil-mixed", "excess-digits", "intervals-disjoint"])
def test_inconsistent_duplicates_make_the_check_inapplicable(facts):
    # oracle: Calculations 1.1 duplicate rules (Requirement 1, Fact groups), by hand.
    got = check_calculations(facts, TREE)
    assert len(got) == 1
    assert got[0]["status"] == INAPPLICABLE
    assert _bounds(got[0]) == (None, None, None, None)


@pytest.mark.criterion("C3")
def test_nil_groups_and_conflicting_weights():
    # oracle: Requirement 1 (Binding, Status), by hand.
    nil_parent = [_f(1, P, None), _f(2, A, 5)]
    assert check_calculations(nil_parent, TREE) == []
    nil_child = [_f(1, P, 5), _f(2, A, 5), _f(3, B, None)]
    (r,) = check_calculations(nil_child, TREE)
    assert (r["status"], r["child_fact_ids"], r["missing_children"]) == (PASS, (2, 3), (C,))
    assert (r["computed_low"], r["computed_high"]) == (_d("4.5"), _d("5.5"))
    conflicting = TREE + [_arc(A, -1, arcrole=SUM_2023)]
    (r,) = check_calculations(nil_child, conflicting)
    assert r["status"] == INAPPLICABLE


@pytest.mark.criterion("C3")
@given(filings(), st.integers(min_value=0, max_value=10_000))
def test_an_exact_copy_of_a_fact_changes_nothing(filing, pick):
    # oracle: Requirement 1 (Fact groups): a repeated iXBRL fact counts once (metamorphic).
    facts, arcs = filing
    before = check_calculations(facts, arcs)
    _well_formed(before, facts)
    original = facts[pick % len(facts)]
    after = check_calculations(facts + [{**original, "fact_id": 99_999}], arcs)

    def strip(rs):
        return [{**r, "parent_fact_ids": (), "child_fact_ids": ()} for r in rs]

    assert strip(after) == strip(before)
    for b, a in zip(before, after, strict=True):  # the copy is listed exactly where its original is
        assert (99_999 in a["parent_fact_ids"] + a["child_fact_ids"]) == (
            original["fact_id"] in b["parent_fact_ids"] + b["child_fact_ids"])


# =========================================================================== C4 rounding
@pytest.mark.criterion("C4")
def test_mcd_assets_fails_by_decimals(case):
    # oracle: McDonald's 10-K 0000063908-25-000012 balance sheet, by hand (millions):
    # 2024-12-31: 4,599 + 11,950 + 13,339 + 25,295 = 55,183 vs Assets 55,182. Assets is
    # tagged at -5 and -6, every child at -5 (ROU also at -6): reported 55,182 +- 0.05,
    # computed 55,183 +- 4 x 0.05. 2023-12-31: 7,986 + 9,738 + 13,514 (-6 only, +-0.5)
    # + 24,908 = 56,146 +- 0.65 vs 56,147 +- 0.05. Arelle 2.41.0 reports both.
    c = case("mcd_assets")
    got = _by_period(check_calculations(c.facts, c.arcs))
    assert set(got) == {(None, "2025-01-01"), (None, "2024-01-01")}
    y24, y23 = got[(None, "2025-01-01")], got[(None, "2024-01-01")]
    assert y24["status"] == y23["status"] == FAIL
    assert _bounds(y24) == tuple(_d(x) * M for x in ("55181.95", "55182.05", "55182.8", "55183.2"))
    want = (_d("56146.95"), _d("56147.05"), _d("56145.35"), _d("56146.65"))
    assert _bounds(y23) == tuple(x * M for x in want)
    assert _ids(y24) == ((216379, 216905), (216361, 216369, 216371, 216377, 216952, 217018))


@pytest.mark.criterion("C4")
def test_closed_intervals_and_weights():
    # oracle: Requirement 1 (Status), by hand.
    (touching,) = check_calculations([_f(1, P, 10), _f(2, A, 9)], TREE)  # [9.5,10.5] & [8.5,9.5]
    assert touching["status"] == PASS
    (apart,) = check_calculations([_f(1, P, "10.1", 1), _f(2, A, 9)], TREE)  # [10.05,10.15]
    assert apart["status"] == PARTIAL  # B and C absent, so not a strict failure
    (exact,) = check_calculations([_f(1, P, 10, None), _f(2, A, 9, None), _f(3, B, 2, None),
                                   _f(4, C, 0, None)], TREE)
    assert (exact["status"], _bounds(exact)) == (FAIL, (10, 10, 11, 11))
    neg = [_arc(A), _arc(B, -1)]
    (r,) = check_calculations([_f(1, P, 5), _f(2, A, 8), _f(3, B, 3)], neg)
    # A [7.5, 8.5] plus -1 x B [2.5, 3.5] = [-3.5, -2.5]: computed [4, 6]
    assert (r["status"], _bounds(r)) == (PASS, (_d("4.5"), _d("5.5"), 4, 6))


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("name", ["nvda_assets", "mcd_assets", "amd_financing",
                                  "intel_10q_gross_profit", "apple_assets_2023_arcrole"])
def test_inconsistencies_equal_arelle(case, name):
    # oracle: Arelle 2.41.0 `--calc c11r` on the local filing, recorded in the fixture
    # (calc11e:inconsistentCalculationUsingRounding messages in this tree, with bounds).
    c = case(name)
    results = check_calculations(c.facts, c.arcs)
    assert len(results) >= 2
    got = [r for r in results if r["status"] in (FAIL, PARTIAL)]
    want = {(a["period_start"], a["period_end"]): a for a in c.arelle}
    assert {(r["period_start"], r["period_end"]) for r in got} == set(want)
    for r in got:
        a = want[(r["period_start"], r["period_end"])]
        assert _bounds(r) == tuple(_d(x) for x in a["reported"] + a["computed"])


@pytest.mark.criterion("C4")
@given(filings(), st.integers(min_value=-3, max_value=3))
def test_scaling_values_and_decimals_together_changes_nothing(filing, k):
    # oracle: Calculations 1.1 intervals are value +- 10^-d / 2, so value x 10^k with
    # decimals - k scales every bound by 10^k and keeps every comparison (metamorphic).
    facts, arcs = filing
    before = check_calculations(facts, arcs)
    _well_formed(before, facts)
    scaled = [{**f, "value": None if f["value"] is None else f["value"].scaleb(k),
               "decimals": None if f["decimals"] is None else f["decimals"] - k} for f in facts]

    def up(x):
        return None if x is None else x.scaleb(k)

    bound_keys = ("reported_low", "reported_high", "computed_low", "computed_high")
    want = [{**r, **{b: up(r[b]) for b in bound_keys}} for r in before]
    assert check_calculations(scaled, arcs) == want


# =========================================================================== C5 status, scope
@pytest.mark.criterion("C5")
def test_amd_every_column_is_checked(case):
    # oracle: AMD 10-K 0000002488-24-000012 cash flow statement, by hand (millions):
    # FY2021 0 + 0 - 237 + 104 - 1,762 - 0 = -1,895, but fact 18008 is tagged +1,895 (no
    # sign attribute; the page shows "(1,895)"). FY2022 -2 + 991 - 406 + 167 - 3,702 - 312
    # = -3,264 and FY2023 -2 + 0 - 427 + 268 - 985 - 0 = -1,146 reconcile. Six children at
    # -6: +-3M. Arelle 2.41.0 reports the FY2021 context only.
    c = case("amd_financing")
    assert c.by_id(18008)["value"] == 1895 * M
    got = _by_period(check_calculations(c.facts, c.arcs))
    assert set(got) == {("2020-12-27", "2021-12-26"), ("2021-12-26", "2023-01-01"),
                        ("2023-01-01", "2023-12-31")}
    fy21 = got[("2020-12-27", "2021-12-26")]
    assert (fy21["status"], fy21["dimensional"]) == (FAIL, False)
    assert _bounds(fy21) == (_d("1894.5") * M, _d("1895.5") * M, -1898 * M, -1892 * M)
    assert _ids(fy21) == ((18008,), (17990, 17993, 17996, 17999, 18002, 18005))
    assert got[("2021-12-26", "2023-01-01")]["status"] == PASS
    assert got[("2023-01-01", "2023-12-31")]["status"] == PASS


@pytest.mark.criterion("C5")
def test_partial_and_dimensional_are_recorded_apart():
    # oracle: Requirement 1 (Status), by hand: decimals 0, so +-0.5 per fact.
    ctx_pass = (E, None, "2024-01-01", frozenset(), "USD")
    ctx_dim = (E, None, "2025-01-01", SEG, "USD")
    facts = [
        _f(1, P, 100), _f(2, A, 60), _f(3, B, 30),  # C absent, 90 vs 100: partial
        _f(4, P, 90, 0, ctx_pass), _f(5, A, 60, 0, ctx_pass), _f(6, B, 30, 0, ctx_pass),
        _f(7, P, 100, 0, ctx_dim), _f(8, A, 60, 0, ctx_dim), _f(9, B, 30, 0, ctx_dim),
        _f(10, C, 5, 0, ctx_dim),
    ]
    got = {(r["period_end"], r["dimensional"]): r for r in check_calculations(facts, TREE)}
    assert set(got) == {("2025-01-01", False), ("2024-01-01", False), ("2025-01-01", True)}
    partial = got[("2025-01-01", False)]
    assert (partial["status"], partial["missing_children"]) == (PARTIAL, (C,))
    assert (partial["computed_low"], partial["computed_high"]) == (89, 91)
    ok = got[("2024-01-01", False)]
    assert (ok["status"], ok["missing_children"]) == (PASS, (C,))
    dim = got[("2025-01-01", True)]
    assert (dim["status"], dim["missing_children"], dim["dimensions"]) == (FAIL, (), SEG)
    assert (dim["computed_low"], dim["computed_high"]) == (_d("93.5"), _d("96.5"))


@pytest.mark.criterion("C5")
@given(filings(), st.randoms(use_true_random=False))
def test_input_order_never_matters(filing, rnd):
    # oracle: Requirement 1 (Order): the list is sorted by key (metamorphic).
    facts, arcs = filing
    before = check_calculations(facts, arcs)
    _well_formed(before, facts)
    f2, a2 = list(facts), list(arcs)
    rnd.shuffle(f2)
    rnd.shuffle(a2)
    assert check_calculations(f2, a2) == before
    keys = [(r["link_role"], r["parent"], r["entity"], r["period_start"] or "", r["period_end"],
             sorted(r["dimensions"]), r["unit"]) for r in before]
    assert keys == sorted(keys)


# =========================================================================== C6 gate
def _result(status, period_end="2025-01-01", dims=frozenset(), parent=P, role=ROLE,
            start=None, unit="USD") -> dict:
    return {"link_role": role, "parent": parent, "entity": E, "period_start": start,
            "period_end": period_end, "dimensions": dims, "unit": unit, "status": status,
            "dimensional": bool(dims), "reported_low": None, "reported_high": None,
            "computed_low": None, "computed_high": None, "parent_fact_ids": (1,),
            "child_fact_ids": (2,), "missing_children": ()}


def _sort_key(k: tuple) -> tuple:
    return tuple("" if x is None else x for x in k)


def _mcd_key(acc, end) -> tuple:
    return (acc, MCD_ROLE, MCD_ASSETS, None, end, "USD")


@pytest.mark.criterion("C6")
def test_mcd_gate_per_filing(case):
    # oracle: Requirement 4 by hand: the fixture's two strict failures (C4) are the seed's
    # two MCD Assets rows for this accession.
    c = case("mcd_assets")
    results = check_calculations(c.facts, c.arcs)
    listed = {_mcd_key(MCD_ACC, "2025-01-01"), _mcd_key(MCD_ACC, "2024-01-01")}
    d = calc_gate_decision(MCD_ACC, results, listed)
    assert set(d) == DECISION_KEYS
    assert (d["accession_no"], d["held_back"], d["strict_failures"], d["excepted_failures"]) == (
        MCD_ACC, False, 2, 2)
    assert d["new_failures"] == ()
    d = calc_gate_decision(MCD_ACC, results, frozenset())
    assert (d["held_back"], d["strict_failures"], d["excepted_failures"]) == (True, 2, 0)
    assert d["new_failures"] == (_mcd_key(MCD_ACC, "2024-01-01"), _mcd_key(MCD_ACC, "2025-01-01"))
    next_year = {_mcd_key("0000063908-26-000035", "2025-01-01"),
                 _mcd_key("0000063908-26-000035", "2024-01-01")}
    d = calc_gate_decision(MCD_ACC, results, next_year)
    assert (d["held_back"], d["excepted_failures"], len(d["new_failures"])) == (True, 0, 2)


@pytest.mark.criterion("C6")
def test_non_strict_results_never_hold_back():
    # oracle: Requirements 2 and 4 by hand.
    results = [_result(PARTIAL), _result(FAIL, dims=SEG),
               _result(INAPPLICABLE, "2024-01-01"), _result(PASS, "2023-01-01")]
    d = calc_gate_decision("acc-1", results, frozenset())
    assert d == {"accession_no": "acc-1", "held_back": False, "strict_failures": 0,
                 "excepted_failures": 0, "new_failures": (), "partial": 1,
                 "dimensional_failures": 1, "inapplicable": 1}
    d = calc_gate_decision("acc-1", results + [_result(FAIL, "2022-01-01")], frozenset())
    assert (d["held_back"], d["strict_failures"], d["partial"], d["dimensional_failures"],
            d["inapplicable"]) == (True, 1, 1, 1, 1)
    assert d["new_failures"] == (("acc-1", ROLE, P, None, "2022-01-01", "USD"),)


gate_results = st.lists(st.builds(
    _result, st.sampled_from(sorted(STATUSES)),
    st.sampled_from(["2023-01-01", "2024-01-01", "2025-01-01"]),
    st.sampled_from([frozenset(), SEG]), st.sampled_from([P, A]), st.sampled_from([ROLE, ROLE2]),
    st.sampled_from([None, "2024-01-01"])), max_size=12)


@pytest.mark.criterion("C6")
@given(gate_results, gate_results, st.data())
def test_gate_is_monotone(results, noise, data):
    # oracle: Requirement 4: held back exactly when a strict failure is not listed
    # (metamorphic: fewer exceptions never release; non-strict results never matter).
    results = results + [_result(FAIL, "2021-01-01")]
    keys = sorted({("acc-1", r["link_role"], r["parent"], r["period_start"], r["period_end"],
                    r["unit"]) for r in results if r["status"] == FAIL and not r["dimensional"]},
                  key=_sort_key)
    listed = set(data.draw(st.lists(st.sampled_from(keys), unique=True)))
    fewer = set()
    if listed:
        pool = sorted(listed, key=_sort_key)
        fewer = set(data.draw(st.lists(st.sampled_from(pool), unique=True)))
    held = calc_gate_decision("acc-1", results, listed)["held_back"]
    assert held == bool(set(keys) - listed)
    if held:
        assert calc_gate_decision("acc-1", results, fewer)["held_back"]
    assert calc_gate_decision("acc-1", results, frozenset())["held_back"]
    non_strict = [r for r in noise if not (r["status"] == FAIL and not r["dimensional"])]
    assert calc_gate_decision("acc-1", results + non_strict, listed)["held_back"] == held


# =========================================================================== C7 exception list
FIELDS = ["accession_no", "link_role", "parent", "period_start", "period_end", "unit"]


def _rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _row_key(row: dict) -> tuple:
    return (row["accession_no"], row["link_role"], row["parent"], row["period_start"] or None,
            row["period_end"], row["unit"])


@pytest.mark.criterion("C7")
def test_seed_and_committed_list(seed_path, committed_exceptions_path):
    # oracle: the investigation (data/eval/calc_check/report.md): 131 strict failures in 23
    # filings, among them MCD Assets at both dates (C4) and AMD FY2021 financing (C5).
    seed = load_calc_exceptions(seed_path)
    assert len(seed) == 131
    assert len({k[0] for k in seed}) == 23
    assert seed == {_row_key(r) for r in _rows(seed_path)}
    assert _mcd_key(MCD_ACC, "2025-01-01") in seed and _mcd_key(MCD_ACC, "2024-01-01") in seed
    assert (AMD_ACC, AMD_ROLE, AMD_FIN, "2020-12-27", "2021-12-26", "USD") in seed
    assert load_calc_exceptions(committed_exceptions_path) <= seed  # shrink-only


@pytest.mark.criterion("C7")
@given(st.data())
def test_any_subset_of_seed_rows_round_trips(seed_path, data):
    # oracle: Requirement 3 (file format), rows parsed independently with csv.DictReader.
    rows = _rows(seed_path)
    picked = data.draw(st.lists(st.sampled_from(range(len(rows))), min_size=1, unique=True))
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "exceptions.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerows(rows[i] for i in picked)
        want = {_row_key(rows[i]) for i in picked}
        assert load_calc_exceptions(path) == want
        assert load_calc_exceptions(str(path)) == want
