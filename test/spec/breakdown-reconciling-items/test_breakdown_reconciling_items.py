"""Spec tests for breakdown-reconciling-items (docs/specs/breakdown-reconciling-items.md).

Real-data cases come from committed fixtures (see conftest.py): every value is checked there
against the filer's own iXBRL element, and the expected answers below are hand sums of those
values. `_rule` restates the spec's definition by brute force over every subset; it is the
oracle for the random cases. The suite builder gets a fake connection (the DB is the I/O
boundary); everything else is pure.
"""

from __future__ import annotations

import itertools

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.sql_derived import SqlDerivedBuilder
from src.retriever.xbrl.xbrl_factstore_breakdown import (
    XbrlFactstoreBreakdown,
    choose_breakdown,
    choose_partition,
    member_parents,
    reconciling_candidates,
)

REL_TOL = 1e-4
CONSOLIDATION = {
    "OperatingSegmentsMember", "IntersegmentEliminationMember", "ConsolidationEliminationsMember",
    "CorporateNonSegmentMember", "MaterialReconcilingItemsMember", "SegmentReconcilingItemsMember",
}
SEG_AXIS = "StatementBusinessSegmentsAxis"
GEO_AXIS = "StatementGeographicalAxis"
PRODUCT_AXIS = "ProductOrServiceAxis"

# Read by hand from the filers' linkbases (fixtures' `arcs`).
_OTHER = ["TotalOtherRevenuesMember"]
VST_PARENTS = {  # RevenueDisaggregationofRevenueDetails: children of Total Other Revenues
    "HedgingRevenueUnrealizedMember": _OTHER,
    "HedgingRevenueRealizedMember": _OTHER,
    "TransferableProductionTaxCreditRevenuesNuclearAndSolarAssetsMember": _OTHER,
    "IntersegmentSalesMember": _OTHER,
    "IntangibleAmortizationAndOtherRevenuesMember": _OTHER,
}
# GOOGL segment-table line items (both segment roles present StatementBusinessSegmentsAxis and
# RevenueFromContractWithCustomerExcludingAssessedTax); undimensioned FY2024 facts of them:
# costs, operating income, hedging x3, operating income.
GOOGL_SEG_LINES = [32644, 32647, 32941, 32974, 34085, 34103]
GOOGL_GEO_LINES = [32941, 32974, 34085]  # the geography role: hedging only
# WMT FY2025 segment role (...ScheduleofSegmentReportingInformationbySegmentDetails) line items
WMT25_LINE_CONCEPTS = {
    "OtherIncome", "CostOfRevenue", "SellingGeneralAndAdministrativeExpense", "OperatingIncomeLoss",
    "InterestIncomeExpenseNonoperatingNet", "NonoperatingIncomeExpense",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "DepreciationAmortizationAndAccretionNet", "SegmentExpenditureAdditionToLongLivedAssets",
    "CostOfRevenueSellingGeneralAndAdministrativeExpensesForFuelSales", "Assets",
}


# ===================================================================== the rule, by brute force
def _ident(m):
    return m.get("member") or m["label"]


def _is_cons(m):
    name = (m.get("member") or "").split(":")[-1]
    low = name.lower()
    return name in CONSOLIDATION or "elimination" in low or "intersegment" in low


def _distinct(members):
    seen, out = set(), []
    for i, m in enumerate(members):
        if m["value"] is None or m["value"] == 0 or _is_cons(m):
            continue
        if (m["label"], m["value"]) not in seen:
            seen.add((m["label"], m["value"]))
            out.append(i)
    return out


def _ancestors(members, i):
    by_name: dict[str, list[int]] = {}
    for j, m in enumerate(members):
        by_name.setdefault(_ident(m), []).append(j)
    seen, stack = set(), list(members[i].get("parents") or [])
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        for j in by_name.get(p, []):
            stack.extend(members[j].get("parents") or [])
    return seen


def _valid_sets(members, distinct):
    anc = {i: _ancestors(members, i) for i in distinct}
    out = []
    for k in range(1, len(distinct) + 1):
        for c in itertools.combinations(distinct, k):
            names = [_ident(members[i]) for i in c]
            if len(set(names)) == len(names) and not any(anc[i] & set(names) for i in c):
                out.append(c)
    return out


def _rule(members, total, lines=(), rel_tol=REL_TOL):
    """The C1/C2 definition written out over every subset."""
    if total is None or total <= 0:
        return None
    distinct = _distinct(members)
    if len(distinct) > 20:
        return None
    tol = rel_tol * total
    valid = _valid_sets(members, distinct)

    def total_of(c):
        return sum(members[i]["value"] for i in c)

    def key(c, extra=0.0):
        dev = abs(total_of(c) + extra - total)
        return (-len(c), dev, sorted(members[i]["label"] for i in c), c)

    exact = [c for c in valid if len(c) >= 2 and abs(total_of(c) - total) <= tol]
    if exact:
        chosen = min(exact, key=key)
        rest = [c for c in exact if c != chosen and not set(c) <= set(chosen)]
        aside = sorted((c for c in rest if not any(set(c) < set(o) for o in rest)), key=key)
        return {"members": list(chosen), "line": None, "set_aside": [list(c) for c in aside]}
    complete = [c for c in valid if len(c) >= 2 and not any(set(c) < set(o) for o in valid)]
    seen, line_idx = set(), []
    for j, ln in enumerate(lines):
        if ln["value"] is None or ln["value"] == 0 or (ln["label"], ln["value"]) in seen:
            continue
        seen.add((ln["label"], ln["value"]))
        line_idx.append(j)
    pairs = []
    for c in complete:
        for j in line_idx:
            if abs(total_of(c) + lines[j]["value"] - total) <= tol:
                k = key(c, lines[j]["value"])
                pairs.append(((k[0], k[1], k[2], lines[j]["label"], j, c), c, j))
    if not pairs:
        return None
    pairs.sort(key=lambda p: p[0])
    _, chosen, line = pairs[0]
    aside: list[list[int]] = []
    for _, c, _ in pairs[1:]:
        if list(c) != list(chosen) and list(c) not in aside:
            aside.append(list(c))
    return {"members": list(chosen), "line": line, "set_aside": aside}


def _gap(members, total):
    """total minus the distinct eligible members with no ancestor among them."""
    distinct = _distinct(members)
    names = {_ident(members[i]) for i in distinct}
    top = [i for i in distinct if not (_ancestors(members, i) & names)]
    return total - sum(members[i]["value"] for i in top)


def _labels(members, idx):
    return sorted(members[i]["label"] for i in idx)


def _step(axis="segment", fy=2024, **extra):
    return {"id": "b1", "op": "breakdown", "cik": "0000000001", "fy": fy,
            "concept_family": "Revenue", "axis": axis, **extra}


def _facts(case, members, lines):
    t = case["total"]
    return {"total": {"value": float(t["value"]), "fact_id": t["fact_id"], "unit": t["unit"],
                      "concept": t["concept"]},
            "members": members, "lines": lines}


# =========================================================================== strategies
LABELS = "ABCDEFGH"


@st.composite
def axis_members(draw, max_size=7, allow_special=True):
    """Unique labels; values may be 0 or negative; a random forest of parents; some
    consolidation items."""
    n = draw(st.integers(min_value=1, max_value=max_size))
    labels = draw(st.permutations(LABELS))[:n]
    out = []
    for i, lbl in enumerate(labels):
        value = draw(st.integers(min_value=-20, max_value=60) if allow_special
                     else st.integers(min_value=1, max_value=60))
        member = f"{lbl}Member"
        if allow_special and draw(st.integers(min_value=0, max_value=9)) == 0:
            member = draw(st.sampled_from([
                "IntersegmentEliminationMember", "CorporateNonSegmentMember",
                f"{lbl}IntersegmentSalesMember", "OperatingSegmentsMember"]))
        parents = []
        if allow_special and i and draw(st.booleans()):
            parents = [f"{draw(st.sampled_from(labels[:i]))}Member"]
        out.append({"label": lbl, "value": float(value), "member": member, "parents": parents,
                    "fact_id": 100 + i, "unit": "USD"})
    return out


@st.composite
def case_with_total(draw, allow_special=True, with_lines=True):
    members = draw(axis_members(allow_special=allow_special))
    pick = draw(st.lists(st.sampled_from(range(len(members))), unique=True))
    lines = []
    if with_lines:
        for k in range(draw(st.integers(min_value=0, max_value=3))):
            value = float(draw(st.integers(min_value=-30, max_value=60)))
            lines.append({"label": "xyz"[k], "value": value, "fact_id": 900 + k, "unit": "USD",
                          "concept": f"Line{k}"})
    base = sum(members[i]["value"] for i in pick)
    if lines and draw(st.booleans()):
        base += lines[draw(st.sampled_from(range(len(lines))))]["value"]
    total = float(base) if base > 0 else float(draw(st.integers(min_value=1, max_value=200)))
    return members, total, lines


# =========================================================================== C1
@pytest.mark.criterion("C1")
def test_orcl_countries_win_regions_set_aside(fx):
    # oracle: ORCL FY2024 10-K iXBRL (FSDS 2024q2 for the regions), by hand: countries US 29,055
    # + Other 18,027 + UK 2,423 + Germany 1,794 + Japan 1,662 = 52,961; regions Americas 33,122
    # + EMEA 13,030 + Asia Pacific 6,809 = 52,961 ($M). Both exact; five members beat three.
    case = fx.load("orcl_fy2024")
    assert fx.fsds(case, "RevenueFromContractWithCustomerExcludingAssessedTax",
                   "Geographical=Americas;") == 33_122_000_000
    members = fx.members(case, "geography")
    got = choose_breakdown(members, fx.total(case))
    assert _labels(members, got["members"]) == sorted(
        ["UNITED STATES", "Other Countries [Member]", "UNITED KINGDOM", "GERMANY", "JAPAN"])
    assert got["line"] is None
    assert [_labels(members, s) for s in got["set_aside"]] == [
        sorted(["Americas [Member]", "EMEA [Member]", "Asia Pacific [Member]"])]


@pytest.mark.criterion("C1")
def test_vst_product_lines_parent_never_with_its_child(fx):
    # oracle: VST FY2024 10-K iXBRL and presentation linkbase, by hand. Top level: Retail ERCOT
    # 8,064 + Retail NE/MW 3,595 + Wholesale ERCOT 1,978 + Other wholesale 1,050 + Capacity 74
    # = 14,761; Total Other Revenues 2,463 = PTC (nuclear+solar) 556 + hedging realized 895 +
    # unrealized 1,013 + intangible amortization -1 + intersegment 0. Nuclear 545 and solar 11
    # sit under the domain in the government-assistance role, not under 556. Exact (17,224):
    # C = 14,761 + 1,013 + 895 + 545 + 11 - 1 (10 members); B = 14,761 + 556 + 895 + 1,013 - 1
    # (9); A = 14,761 + 2,463 (6). Intersegment 0 is never a member.
    case = fx.load("vst_fy2024")
    names = [m["member"] for m in case["members"]["product"]]
    parents = member_parents(names, case["arcs"])
    assert {k: v for k, v in parents.items() if v} == VST_PARENTS
    assert set(parents) == set(names)
    members = fx.members(case, "product", parents=VST_PARENTS)
    got = choose_breakdown(members, fx.total(case))
    by = {m["member"]: i for i, m in enumerate(members)}
    top = ["RetailEnergyChargeInERCOTMember", "RetailEnergyChargeInNortheastMidwestMember",
           "WholesaleGenerationRevenueFromERCOTMember", "RevenueFromOtherWholesaleContractsMember",
           "CapacityRevenueMember"]
    c = top + ["HedgingRevenueUnrealizedMember", "HedgingRevenueRealizedMember",
               "TransferableNuclearProductionTaxCreditRevenuesMember",
               "TransferableSolarProductionTaxCreditRevenuesMember",
               "IntangibleAmortizationAndOtherRevenuesMember"]
    b = top + ["TransferableProductionTaxCreditRevenuesNuclearAndSolarAssetsMember",
               "HedgingRevenueRealizedMember", "HedgingRevenueUnrealizedMember",
               "IntangibleAmortizationAndOtherRevenuesMember"]
    a = top + ["TotalOtherRevenuesMember"]
    assert got["members"] == sorted(by[n] for n in c)
    assert got["line"] is None
    assert got["set_aside"] == [sorted(by[n] for n in b), sorted(by[n] for n in a)]
    assert sum(members[i]["value"] for i in got["members"]) == pytest.approx(
        17_224_000_000, rel=REL_TOL)  # spec tolerance for sums


@pytest.mark.criterion("C1")
def test_hierarchy_is_transitive_and_beats_size():
    # oracle: hand arithmetic. Total 600: {R 300, W 180, P 120} and {R, W, c1 140, c2 -20} are
    # exact; {R, W, P, c2, Adj 20} also sums to 600 but holds P with its own child c2, so the
    # four-member set wins and {R, W, P} is set aside.
    m = [{"label": "R", "value": 300.0, "member": "R", "parents": []},
         {"label": "W", "value": 180.0, "member": "W", "parents": []},
         {"label": "P", "value": 120.0, "member": "P", "parents": []},
         {"label": "c1", "value": 140.0, "member": "c1", "parents": ["P"]},
         {"label": "c2", "value": -20.0, "member": "c2", "parents": ["P"]},
         {"label": "Adj", "value": 20.0, "member": "Adj", "parents": []}]
    assert choose_breakdown(m, 600.0) == {
        "members": [0, 1, 3, 4], "line": None, "set_aside": [[0, 1, 2]]}
    # Total 150: {P 100, Q 50} is exact; {P, g 40, Q, N -40} sums to 150 but g is P's
    # grandchild (g -> c -> P), so only {P, Q} qualifies.
    g = [{"label": "P", "value": 100.0, "member": "P", "parents": []},
         {"label": "c", "value": 60.0, "member": "c", "parents": ["P"]},
         {"label": "g", "value": 40.0, "member": "g", "parents": ["c"]},
         {"label": "Q", "value": 50.0, "member": "Q", "parents": []},
         {"label": "N", "value": -40.0, "member": "N", "parents": []}]
    assert choose_breakdown(g, 150.0) == {"members": [0, 3], "line": None, "set_aside": []}


@pytest.mark.criterion("C1")
def test_zero_and_consolidation_members_are_never_chosen(fx):
    # oracle: the definition. VST's 0-valued Intersegment Sales and a consolidation item added
    # to ORCL's members change nothing; a lone member, no total or a non-positive total is None.
    case = fx.load("orcl_fy2024")
    members = fx.members(case, "geography")
    base = choose_breakdown(members, fx.total(case))
    extra = members + [
        {"label": "Eliminations", "value": -5_000_000.0,
         "member": "us-gaap:IntersegmentEliminationMember", "parents": []},
        {"label": "Corporate", "value": 0.0, "member": "CorporateAndSupportMember", "parents": []}]
    assert choose_breakdown(extra, fx.total(case)) == base
    assert base["members"] == [1, 2, 5, 6, 7]
    assert choose_breakdown(members[:1], 33_122_000_000.0) is None
    assert choose_breakdown(members, None) is None
    assert choose_breakdown(members, 0.0) is None


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(case_with_total(with_lines=False), st.randoms(use_true_random=False))
def test_partition_rule_for_any_members(data, rnd):
    # oracle: brute force over every subset (`_rule`); reordering the members changes indices,
    # not the chosen labels or the set-aside label sets
    members, total, _ = data
    got = choose_breakdown(members, total)
    assert got == _rule(members, total)
    shuffled = list(members)
    rnd.shuffle(shuffled)
    again = choose_breakdown(shuffled, total)
    if got is None:
        assert again is None
    else:
        assert _labels(shuffled, again["members"]) == _labels(members, got["members"])
        assert sorted(_labels(shuffled, s) for s in again["set_aside"]) == sorted(
            _labels(members, s) for s in got["set_aside"])


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(case_with_total(), st.sampled_from([10, 1000, 1_000_000]))
def test_scaling_and_unrelated_members_change_nothing(data, k):
    # oracle: the rule is relative: scaling every value and the total by 10^n keeps the choice;
    # appending a zero member, a consolidation item and a line no sum can reach keeps it too
    members, total, lines = data
    got = choose_breakdown(members, total, lines)
    scaled = [{**m, "value": m["value"] * k} for m in members]
    scaled_lines = [{**ln, "value": ln["value"] * k} for ln in lines]
    assert choose_breakdown(scaled, total * k, scaled_lines) == got
    noise = members + [
        {"label": "Z0", "value": 0.0, "member": "ZeroMember", "parents": []},
        {"label": "Z1", "value": 7.0, "member": "srt:ConsolidationEliminationsMember",
         "parents": []}]
    far = lines + [{"label": "far", "value": 1e9, "fact_id": 999, "unit": "USD", "concept": "Far"}]
    assert choose_breakdown(noise, total, far) == got
    assert got == _rule(members, total, lines)


names_st = st.lists(st.sampled_from(["A", "B", "C", "D", "E", "Dom", "X"]),
                    min_size=1, max_size=5, unique=True)
arc_st = st.fixed_dictionaries({
    "role": st.sampled_from(["r1", "r2"]),
    "arc": st.sampled_from(["presentationArc", "definitionArc", "calculationArc"]),
    "parent": st.sampled_from(["A", "B", "C", "D", "E", "Dom", "X"]),
    "child": st.sampled_from(["A", "B", "C", "D", "E", "Dom", "X"]),
    "weight": st.none(),
})


@pytest.mark.criterion("C1")
@given(names_st, st.lists(arc_st, max_size=12))
def test_member_parents_for_any_arcs(names, arcs):
    # oracle: the definition written out: parents among the given names, from presentation or
    # definition arcs only, sorted and distinct, every name present
    want = {n: sorted({a["parent"] for a in arcs
                       if a["arc"] in ("presentationArc", "definitionArc") and a["child"] == n
                       and a["parent"] in names and a["parent"] != n}) for n in names}
    assert member_parents(names, arcs) == want


# =========================================================================== C2
@pytest.mark.criterion("C2")
def test_googl_segments_plus_hedging_line(fx):
    # oracle: GOOGL FY2024 10-K iXBRL and FSDS 2025q1: Google Services 304,930 + Google Cloud
    # 43,229 + Other 1,648 = 349,807; with the hedging-gains line 211 (fact f-471) = 350,018,
    # the total ($M). Each segment is tagged twice; the first fact counts.
    case = fx.load("googl_fy2024")
    assert fx.fsds(case, "RevenueFromContractWithCustomerExcludingAssessedTax",
                   "BusinessSegments=GoogleServices;") == 304_930_000_000
    members = fx.members(case, "segment")
    lines = fx.lines_by_fact(case, GOOGL_SEG_LINES)
    got = choose_breakdown(members, fx.total(case), lines)
    assert got == {"members": [0, 2, 4], "line": 2, "set_aside": []}
    assert choose_breakdown(members, fx.total(case)) is None  # no line: no breakdown

    res = XbrlFactstoreBreakdown(db_conn_params={}).resolve(_step(), _facts(case, members, lines))
    assert res.status.value == "OK"
    assert [(r["label"], r["value"], r["fact_id"], r["rank"], r["kind"]) for r in res.rows] == [
        ("Google Services", 304_930_000_000, "32932", 1, "member"),
        ("Google Cloud", 43_229_000_000, "32935", 2, "member"),
        ("Other Operating Segment", 1_648_000_000, "32938", 3, "member"),
        ("Gain (Loss) on Hedging Activity", 211_000_000, "32941", None, "reconciling"),
    ]
    assert {r["unit"] for r in res.rows} == {"USD"}
    assert {r["fiscal_year"] for r in res.rows} == {2024}
    # spec tolerance for sums
    assert sum(r["value"] for r in res.rows) == pytest.approx(350_018_000_000, rel=REL_TOL)
    assert res.disclosure["reconciling"] == {"label": "Gain (Loss) on Hedging Activity",
                                             "value": 211_000_000, "fact_id": "32941"}
    assert res.disclosure["total"] == {"value": 350_018_000_000, "fact_id": "34088", "unit": "USD"}
    assert res.disclosure["set_aside"] == [] and res.disclosure["gap"] is None
    assert "Gain (Loss) on Hedging Activity" in " ".join(res.resolution_notes)


@pytest.mark.criterion("C2")
def test_googl_geography_plus_hedging_line(fx):
    # oracle: GOOGL FY2024 iXBRL/FSDS: US 170,447 + EMEA 102,127 + APAC 56,815 + Americas ex-US
    # 20,418 = 349,807; + hedging 211 = 350,018 ($M)
    case = fx.load("googl_fy2024")
    members = fx.members(case, "geography")
    lines = fx.lines_by_fact(case, GOOGL_GEO_LINES)
    assert choose_breakdown(members, fx.total(case), lines) == {
        "members": [0, 1, 2, 3], "line": 0, "set_aside": []}
    res = XbrlFactstoreBreakdown().resolve(_step(axis="geography"), _facts(case, members, lines))
    assert [r["fact_id"] for r in res.rows] == ["32950", "32956", "32962", "32968", "32941"]
    assert res.rows[-1]["kind"] == "reconciling"


@pytest.mark.criterion("C2")
def test_wmt_segments_plus_other_income(fx):
    # oracle: WMT 10-K iXBRL and FSDS. FY2025: Walmart US 462,415 + International 121,885 +
    # Sam's Club US 90,238 = 674,538; + other income 6,447 (fact 206419) = 680,985 total
    # revenues. FY2023: 420,553 + 100,983 + 84,345 = 605,881; + 5,408 (fact 203558) = 611,289;
    # Corporate and support is tagged 0 and is no segment ($M).
    case = fx.load("wmt_fy2025")
    members = fx.members(case, "segment")
    lines = fx.lines_by_fact(case, [f["fact_id"] for f in case["facts"]
                                    if f["axis"] is None and f["concept"] in WMT25_LINE_CONCEPTS])
    got = choose_breakdown(members, fx.total(case), lines)
    assert got["members"] == [0, 1, 2] and lines[got["line"]]["fact_id"] == 206419
    assert lines[got["line"]]["value"] == 6_447_000_000

    old = fx.load("wmt_fy2023")
    assert fx.fsds(old, "OtherIncome") == 5_408_000_000
    m23 = fx.members(old, "segment")
    l23 = fx.lines_by_fact(old, [203558])
    res = XbrlFactstoreBreakdown().resolve(_step(fy=2023), _facts(old, m23, l23))
    assert [(r["label"], r["value"], r["kind"]) for r in res.rows] == [
        ("Walmart U S", 420_553_000_000, "member"),
        ("Walmart International", 100_983_000_000, "member"),
        ("Sams Club", 84_345_000_000, "member"),
        ("Other Income", 5_408_000_000, "reconciling"),
    ]
    assert [r["fact_id"] for r in res.rows] == ["204759", "204760", "204761", "203558"]


@pytest.mark.criterion("C2")
def test_not_found_names_the_gap(fx):
    # oracle: UPS FY2023 iXBRL/FSDS: one segment alone, Supply Chain Solutions 13,169 against
    # 90,958: gap 77,789 ($M); GOOGL FY2024 segments without a line: gap 350,018 - 349,807 = 211
    ups = fx.load("ups_fy2023")
    um = fx.members(ups, "segment")
    ul = fx.lines_by_fact(ups, [251090, 251108, 251156, 253720, 253747])
    assert choose_breakdown(um, fx.total(ups), ul) is None
    res = XbrlFactstoreBreakdown().resolve(_step(fy=2023), _facts(ups, um, ul))
    assert res.status.value == "NOT_FOUND" and res.rows == []
    assert res.disclosure["gap"] == pytest.approx(77_789_000_000, rel=1e-12)  # exact integers
    assert "77,789,000,000" in " ".join(res.resolution_notes)

    googl = fx.load("googl_fy2024")
    gm = fx.members(googl, "segment")
    res = XbrlFactstoreBreakdown().resolve(_step(), _facts(googl, gm, []))
    assert res.status.value == "NOT_FOUND"
    assert res.disclosure["gap"] == pytest.approx(211_000_000, rel=1e-12)  # exact integers
    assert "211,000,000" in " ".join(res.resolution_notes)
    ok = XbrlFactstoreBreakdown().resolve(
        _step(), _facts(googl, gm, fx.lines_by_fact(googl, GOOGL_SEG_LINES)))
    assert ok.status.value == "OK"


@pytest.mark.criterion("C2")
def test_elimination_is_never_a_segment(fx):
    # oracle: ABT FY2023 iXBRL/FSDS 2024q1: ConsolidationItemsAxis OperatingSegments 40,095 +
    # CorporateNonSegment 14 = 40,109, the total, exactly; neither is a segment, so offered as
    # axis members they give no breakdown (gap = the whole 40,109). Synthetic: gross segments
    # 70 + 50 with intersegment eliminations -20 make 100 only as a named line.
    abt = fx.load("abt_fy2023")
    am = fx.members(abt, "segment")
    al = fx.lines_by_fact(abt, [235896, 236953])
    assert choose_breakdown(am, fx.total(abt), al) is None
    res = XbrlFactstoreBreakdown().resolve(_step(fy=2023), _facts(abt, am, al))
    assert res.status.value == "NOT_FOUND" and res.rows == []
    assert res.disclosure["gap"] == pytest.approx(40_109_000_000, rel=1e-12)  # exact integers

    seg = [{"label": "A", "value": 70.0, "member": "AMember", "parents": []},
           {"label": "B", "value": 50.0, "member": "BMember", "parents": []},
           {"label": "Elim", "value": -20.0, "member": "us-gaap:IntersegmentEliminationMember",
            "parents": []}]
    line = [{"label": "Intersegment eliminations", "value": -20.0, "fact_id": 7, "unit": "USD",
             "concept": "Revenues"}]
    assert choose_breakdown(seg, 100.0) is None
    assert choose_breakdown(seg, 100.0, line) == {"members": [0, 1], "line": 0, "set_aside": []}


@pytest.mark.criterion("C2")
@given(case_with_total())
def test_lines_rule_for_any_members(data):
    # oracle: brute force over every subset and line (`_rule`)
    members, total, lines = data
    assert choose_breakdown(members, total, lines) == _rule(members, total, lines)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(case_with_total(), st.sampled_from(["segment", "product", "geography"]))
def test_resolve_rows_for_any_members(data, axis):
    # oracle: `_rule` and `_gap`; OK rows are the chosen members value-descending, ranked, then
    # the line, and sum to the total; NOT_FOUND states the gap
    members, total, lines = data
    facts = {"total": {"value": total, "fact_id": 1, "unit": "USD", "concept": "Revenues"},
             "members": members, "lines": lines}
    res = XbrlFactstoreBreakdown().resolve(_step(axis=axis), facts)
    want = _rule(members, total, lines)
    if want is None:
        assert res.status.value == "NOT_FOUND" and res.rows == []
        # integer-valued sums: exact up to float noise
        assert res.disclosure["gap"] == pytest.approx(_gap(members, total), abs=1e-9)
        assert f"{_gap(members, total):,.0f}" in " ".join(res.resolution_notes)
        return
    assert res.status.value == "OK"
    member_rows = [r for r in res.rows if r["kind"] == "member"]
    assert sorted(r["label"] for r in member_rows) == _labels(members, want["members"])
    values = [r["value"] for r in member_rows]
    assert values == sorted(values, reverse=True)
    assert [r["rank"] for r in member_rows] == list(range(1, len(member_rows) + 1))
    by_label = {m["label"]: m for m in members}
    assert not any(_is_cons(by_label[r["label"]]) for r in member_rows)
    if want["line"] is None:
        assert len(res.rows) == len(member_rows)
    else:
        assert res.rows[-1]["kind"] == "reconciling"
        assert res.rows[-1]["fact_id"] == str(lines[want["line"]]["fact_id"])
    assert sum(r["value"] for r in res.rows) == pytest.approx(total, rel=REL_TOL)  # spec tolerance


@pytest.mark.criterion("C2")
def test_resolve_rejects_malformed_steps(fx):
    # oracle: requirement, as breakdown() does: a non-breakdown op or an unknown axis is ERROR;
    # no total is NOT_FOUND; a good step over the same facts is OK
    case = fx.load("googl_fy2024")
    facts = _facts(case, fx.members(case, "segment"), fx.lines_by_fact(case, GOOGL_SEG_LINES))
    bd = XbrlFactstoreBreakdown()
    assert bd.resolve(_step(axis="customer"), facts).status.value == "ERROR"
    assert bd.resolve(_step(op="lookup"), facts).status.value == "ERROR"
    assert bd.resolve(_step(), {**facts, "total": None}).status.value == "NOT_FOUND"
    assert bd.resolve(_step(), facts).status.value == "OK"


# =========================================================================== C3
def _cand_ids(out):
    return [c["fact_id"] for c in out]


@pytest.mark.criterion("C3")
def test_googl_candidates_from_the_segment_table(fx):
    # oracle: GOOGL FY2024 presentation linkbase read by hand: the two roles presenting
    # StatementBusinessSegmentsAxis and RevenueFromContractWithCustomerExcludingAssessedTax list
    # the line items hedging gains, operating income, labor, operating expenses and costs and
    # expenses; the facts of those undimensioned in FY2024 are costs 237,628, operating income
    # 112,390 (x2) and hedging 211 (x3). The geography role lists hedging only.
    case = fx.load("googl_fy2024")
    total = case["total"]
    concepts = {"RevenueFromContractWithCustomerExcludingAssessedTax"}
    seg = reconciling_candidates(total, concepts, SEG_AXIS, case["arcs"], case["facts"])
    assert _cand_ids(seg) == GOOGL_SEG_LINES
    hedging = [c for c in seg if c["fact_id"] == 32941][0]
    assert hedging == {"label": "Gain (Loss) on Hedging Activity", "value": 211_000_000,
                       "fact_id": 32941, "unit": "USD",
                       "concept": "GainLossOnOilAndGasHedgingActivity"}
    geo = reconciling_candidates(total, concepts, GEO_AXIS, case["arcs"], case["facts"])
    assert _cand_ids(geo) == GOOGL_GEO_LINES


@pytest.mark.criterion("C3")
def test_wmt_candidates_from_calculation_and_table(fx):
    # oracle: WMT linkbases read by hand. FY2023: no role presents both the segment axis and
    # Revenues, but the calculation Revenues = RevenueFromContract... + OtherIncome (weight 1)
    # gives other income 5,408 alone. FY2025: the segment table presents Revenues with its line
    # items; their undimensioned facts are the candidates, other income 6,447 among them; the
    # members' own concept (net sales 674,538) is not.
    old = fx.load("wmt_fy2023")
    net_sales = {"RevenueFromContractWithCustomerExcludingAssessedTax"}
    got = reconciling_candidates(old["total"], net_sales, SEG_AXIS, old["arcs"], old["facts"])
    assert got == [{"label": "Other Income", "value": 5_408_000_000, "fact_id": 203558,
                    "unit": "USD", "concept": "OtherIncome"}]
    new = fx.load("wmt_fy2025")
    got = reconciling_candidates(new["total"], net_sales, SEG_AXIS, new["arcs"], new["facts"])
    want = [f["fact_id"] for f in new["facts"]
            if f["axis"] is None and f["concept"] in WMT25_LINE_CONCEPTS and float(f["value"]) != 0]
    assert _cand_ids(got) == want
    assert 206419 in want and 207570 not in _cand_ids(got)


@pytest.mark.criterion("C3")
def test_consolidation_items_and_weights(fx):
    # oracle: VST FY2024 iXBRL: Revenues on ConsolidationItemsAxis IntersegmentElimination
    # -7,506 (fact 140815) and CorporateNonSegment -7,506 (142737) are candidates;
    # OperatingSegments 24,730 is the segments' own subtotal and is not; net sales 14,761 is a
    # members' concept. Synthetic: a calculation child with weight -1 enters negated.
    case = fx.load("vst_fy2024")
    concepts = {"Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"}
    got = reconciling_candidates(case["total"], concepts, PRODUCT_AXIS, case["arcs"], case["facts"])
    assert [(c["fact_id"], c["value"]) for c in got] == [
        (140815, -7_506_000_000), (142737, -7_506_000_000)]
    arcs = [{"role": "r", "arc": "calculationArc", "parent": "Revenues", "child": "Returns",
             "weight": -1.0}]
    facts = [{"fact_id": 5, "concept": "Returns", "label": "Returns", "value": "30", "unit": "USD",
              "axis": None, "member": None}]
    total = {"concept": "Revenues", "fact_id": 1, "value": "100", "unit": "USD"}
    assert reconciling_candidates(total, set(), SEG_AXIS, arcs, facts) == [
        {"label": "Returns", "value": -30, "fact_id": 5, "unit": "USD", "concept": "Returns"}]


def _brute_candidates(total, member_concepts, axis, arcs, facts):
    pres = [a for a in arcs if a["arc"] == "presentationArc"]
    ra = {a["role"] for a in pres if axis in (a["parent"], a["child"])}
    rt = {a["role"] for a in pres if a["child"] == total["concept"]}
    presented = {a["child"] for a in pres if a["role"] in ra & rt}
    calc: dict[str, float] = {}
    for a in arcs:
        if a["arc"] == "calculationArc" and a["parent"] == total["concept"]:
            calc.setdefault(a["child"], a["weight"])
    out = []
    for f in facts:
        v = None if f["value"] is None else float(f["value"])
        if not v or f["fact_id"] == total["fact_id"]:
            continue
        if f["axis"] is None:
            c = f["concept"]
            if c == total["concept"] or c in member_concepts:
                continue
            if c in calc:
                v = calc[c] * v
            elif c not in presented:
                continue
        elif not (f["axis"] == "ConsolidationItemsAxis" and f["concept"] == total["concept"]
                  and f["member"] != "OperatingSegmentsMember"):
            continue
        out.append({"label": f["label"], "value": v, "fact_id": f["fact_id"], "unit": f["unit"],
                    "concept": f["concept"]})
    return out


ELEMS = ["Revenues", "Hedge", "Other", "Cost", "Net", "SegAxis", "GeoAxis", "LineItems"]
cand_arc_st = st.fixed_dictionaries({
    "role": st.sampled_from(["r1", "r2", "r3"]),
    "arc": st.sampled_from(["presentationArc", "calculationArc", "definitionArc"]),
    "parent": st.sampled_from(ELEMS),
    "child": st.sampled_from(ELEMS),
    "weight": st.sampled_from([1.0, -1.0]),
})
cand_fact_st = st.fixed_dictionaries({
    "concept": st.sampled_from(["Revenues", "Hedge", "Other", "Cost", "Net"]),
    "value": st.sampled_from(["0", "5", "-7", "11", None]),
    "axis": st.sampled_from([None, None, "ConsolidationItemsAxis", "ProductOrServiceAxis"]),
    "member": st.sampled_from([None, "CorporateNonSegmentMember", "OperatingSegmentsMember"]),
})


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(st.lists(cand_arc_st, max_size=14), st.lists(cand_fact_st, max_size=8))
def test_candidates_for_any_linkbase(arcs, raw):
    # oracle: the C3 definition written out (`_brute_candidates`); adding a role that presents
    # an unrelated axis with its own line items and facts changes nothing
    facts = [{**f, "fact_id": 10 + i, "label": f["concept"], "unit": "USD"}
             for i, f in enumerate(raw)]
    total = {"concept": "Revenues", "fact_id": 10, "value": "100", "unit": "USD"}
    got = reconciling_candidates(total, {"Net"}, "SegAxis", arcs, facts)
    assert got == _brute_candidates(total, {"Net"}, "SegAxis", arcs, facts)
    unrelated = arcs + [
        {"role": "u", "arc": "presentationArc", "parent": p, "child": c, "weight": None}
        for p, c in (("Table", "MajorCustomersAxis"), ("LineItems", "Revenues"),
                     ("LineItems", "Unrelated"))]
    more = facts + [{"fact_id": 99, "concept": "Unrelated", "label": "Unrelated", "value": "3",
                     "unit": "USD", "axis": None, "member": None}]
    assert reconciling_candidates(total, {"Net"}, "SegAxis", unrelated, more) == got


# =========================================================================== C4
class _BuildCursor:
    def __init__(self, store):
        self._store = store
        self.description = None
        self._rows: list = []

    def execute(self, sql, params=None):
        params = params or {}
        if "publish_manifest" in sql:
            cols, rows = ["accession_no", "is_published"], [("0000000000-26-000001", True)]
        elif "array_agg" in sql:
            cols, rows = ["anchor", "members"], []
        else:
            cols = ["member_label", "value", "fact_id", "total", "member", "parents", "is_line"]
            rows = self._store[(params.get("ticker"), params.get("fy"))]
        self.description = [(c,) for c in cols]
        self._rows = rows

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _BuildConn:
    def __init__(self, store):
        self._store = store

    def cursor(self):
        return _BuildCursor(self._store)

    def rollback(self):
        pass

    def close(self):
        pass


def _build(store, structured=True):
    tickers = sorted({t for t, _ in store})
    expect = {"rows": {"entity": "member_label", "value": "value"}, "fact_ids": "fact_id",
              "total": "total"}
    if structured:
        expect.update({"line": "is_line", "member": "member", "parents": "parents"})
    spec = {
        "version": "v1", "suite_name": "sql_derived",
        "corpus": {"tickers": tickers, "fiscal_years": [2024]},
        "tiers": {"standard": {"paraphrases": 1}},
        "templates": [{
            "id": "t-bd", "source": "analyst_queries.sql#3a", "kind": "breakdown",
            "nl": ["Break down {company}'s fiscal {fy} revenue by segment."],
            "params": {"ticker": "corpus.tickers", "fy": "corpus.fiscal_years"},
            "sql": "SELECT member_label, value, fact_id, total, member, parents, is_line FROM mv "
                   "WHERE t=%(ticker)s AND y=%(fy)s AND fiscal_period = 'FY'",
            "expect": expect, "top_n": 2, "min_rows": 2,
        }],
    }
    builder = SqlDerivedBuilder(spec, _BuildConn(store), company_names={t: t for t in tickers})
    return builder.build("standard")


def _rows(members, lines, total):
    rows = [(m["label"], m["value"], m["fact_id"], total, m["member"], m["parents"], False)
            for m in members]
    return rows + [(ln["label"], ln["value"], ln["fact_id"], total, None, None, True)
                   for ln in lines]


@pytest.mark.criterion("C4")
def test_builder_ground_truth_follows_the_rule(fx):
    # oracle: the C2/C1 hand cases. GOOGL: three segments plus the hedging line (fact ids
    # 32932, 32935, 32938, 32941; top_n=2 cuts nothing); ORCL: the five countries; UPS: no case;
    # ABT's consolidation items (40,095 + 14 = 40,109) are no segments: no case; WMT FY2023:
    # the three segments and other income, never the 0-valued Corporate and support.
    googl, orcl, ups = fx.load("googl_fy2024"), fx.load("orcl_fy2024"), fx.load("ups_fy2023")
    abt, wmt = fx.load("abt_fy2023"), fx.load("wmt_fy2023")
    def rows(case, axis, line_ids):
        return _rows(fx.members(case, axis), fx.lines_by_fact(case, line_ids), fx.total(case))

    store = {
        ("ABT", 2024): rows(abt, "segment", [235896]),
        ("WMT", 2024): rows(wmt, "segment", [203558]),
        ("GOOGL", 2024): rows(googl, "segment", GOOGL_SEG_LINES),
        ("ORCL", 2024): rows(orcl, "geography", []),
        ("UPS", 2024): rows(ups, "segment", [251108]),
    }
    suite = _build(store)
    cases = {c.ticker: c for c in suite.cases}
    assert sorted(cases) == ["GOOGL", "ORCL", "WMT"]
    assert sorted((r["entity"], r["value"]) for r in cases["WMT"].expected_rows) == sorted([
        ("Walmart U S", 420_553_000_000), ("Walmart International", 100_983_000_000),
        ("Sams Club", 84_345_000_000), ("Other Income", 5_408_000_000)])
    assert sorted(int(f) for f in cases["WMT"].expected_fact_ids) == [
        203558, 204759, 204760, 204761]
    g = cases["GOOGL"]
    assert sorted((r["entity"], r["value"]) for r in g.expected_rows) == sorted([
        ("Google Services", 304_930_000_000), ("Google Cloud", 43_229_000_000),
        ("Other Operating Segment", 1_648_000_000),
        ("Gain (Loss) on Hedging Activity", 211_000_000)])
    assert sorted(int(f) for f in g.expected_fact_ids) == [32932, 32935, 32938, 32941]
    assert sorted(r["entity"] for r in cases["ORCL"].expected_rows) == sorted(
        ["UNITED STATES", "Other Countries", "UNITED KINGDOM", "GERMANY", "JAPAN"])


@pytest.mark.criterion("C4")
def test_choose_partition_is_the_plain_case(fx):
    # oracle: the C1 hand case: ORCL's five countries (indices 1, 2, 5, 6, 7) both ways
    case = fx.load("orcl_fy2024")
    members = [{"label": m["label"], "value": m["value"], "member": "", "parents": []}
               for m in fx.members(case, "geography")]
    pairs = [(m["label"], m["value"]) for m in members]
    assert choose_breakdown(members, fx.total(case))["members"] == [1, 2, 5, 6, 7]
    assert choose_partition(pairs, fx.total(case)) == [1, 2, 5, 6, 7]


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(case_with_total(allow_special=False))
def test_builder_and_partition_for_any_rows(data):
    # oracle: `_rule`; the builder's case is the rule's choice (members and line), no choice no
    # case; for plain members without lines choose_partition gives the same members
    members, total, lines = data
    want = _rule(members, total, lines)
    suite = _build({("AAPL", 2024): _rows(members, lines, total)})
    if want is None:
        assert suite.cases == []
    else:
        assert len(suite.cases) == 1
        exp = [(members[i]["label"], members[i]["value"]) for i in want["members"]]
        ids = [members[i]["fact_id"] for i in want["members"]]
        if want["line"] is not None:
            exp.append((lines[want["line"]]["label"], lines[want["line"]]["value"]))
            ids.append(lines[want["line"]]["fact_id"])
        case = suite.cases[0]
        assert sorted((r["entity"], r["value"]) for r in case.expected_rows) == sorted(exp)
        assert sorted(int(f) for f in case.expected_fact_ids) == sorted(ids)
    plain = [{"label": m["label"], "value": m["value"], "member": "", "parents": []}
             for m in members]
    got = choose_breakdown(plain, total)
    assert choose_partition([(m["label"], m["value"]) for m in members], total) == (
        None if got is None else got["members"])
    assert got == _rule(plain, total)
