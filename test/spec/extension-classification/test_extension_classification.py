"""Spec tests for extension-classification (docs/specs/extension-classification.md).

Point cases run the classifier over 16 real filings (fixtures/, hand-checked against each
filing's rendered statement and its own calculation linkbase); properties perturb those
inputs or build small synthetic filings from the same members. Every comparison is exact
(strings, tuples, Decimals), so no tolerance applies.
"""

from __future__ import annotations

import copy
import random
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from src.ingest.sec_edgar.xbrl.extension_classifier import (
    classify_extensions,
    default_identities,
    extension_mapping_rows,
)

MCD, PG, ORCL, TSLA = "0000063908", "0000080424", "0001341439", "0001318605"
TSM, NVDA, LMT, AMD = "0001046179", "0001045810", "0000936468", "0000002488"
INTC, LLY, AVGO = "0000050863", "0000059478", "0001730168"

MCD_24 = "0000063908-25-000012"
PG_23 = "0000080424-23-000073"
ORCL_26 = "0001193125-26-277521"
TSLA_24 = "0001628280-25-003063"
TSM_23, TSM_24, TSM_25 = "0001193125-24-099840", "0001193125-25-083423", "0001628280-26-025362"
NVDA_23, NVDA_25, NVDA_26 = "0001045810-23-000017", "0001045810-25-000023", "0001045810-26-000021"
LMT_24, LMT_25 = "0000936468-25-000009", "0001628280-26-004195"
AMD_26Q2 = "0000002488-26-000123"
INTC_24 = "0000050863-25-000009"
LLY_25 = "0000059478-26-000013"
AVGO_25 = "0001730168-25-000121"

PRETAX = "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
ORCL_PRETAX = ("IncomeLossFromContinuingOperationsIncludingNoncontrollingInterest"
               "BeforeIncomeTaxesExtraordinaryItems")
ORCL_NONOP = ("NonoperatingIncomeExpenseIncludingEliminationOf"
              "NetIncomeLossAttributableToNoncontrollingInterests")
LMT_DA = "DepreciationAndAmortizationIncludingDiscontinuedOperations"
NVDA_MS = "MarketableSecuritiesAndEquitySecuritiesFVNI"
NVDA_CAPEX = "PurchasesOfPropertyAndEquipmentAndIntangibleAssets"

RESULT_KEYS = {
    "cik", "local_name", "family", "role", "substitution_code", "rules", "evidence",
    "component_of", "contains", "reason", "accessions",
}
IT, ITOT, LV = "identity_term", "identity_total", "label_value"

# (cik, local name) -> (family, role, substitution_code, rules, evidence): every subject of
# the fixtures that is classified, each hand-checked (oracle comments in the C2/C4 tests).
CLASSIFIED = {
    (MCD, PRETAX): ("PretaxIncome", "primary", None, (IT, ITOT), (MCD_24,)),
    (PG, PRETAX): ("PretaxIncome", "primary", None, (IT, ITOT), (PG_23,)),
    (ORCL, ORCL_PRETAX): ("PretaxIncome", "primary", None, (IT,), (ORCL_26,)),
    (TSM, "NonOperatingIncomeExpense1"): ("OtherNonOperatingIncome", "primary", None, (IT,),
                                          (TSM_23, TSM_24)),
    (TSM, "NonOperatingIncomeExpenseNet"): ("OtherNonOperatingIncome", "primary", None, (IT,),
                                            (TSM_25,)),
    (NVDA, NVDA_MS): ("ShortTermInvestments", "primary", None, (LV,), (NVDA_23, NVDA_25, NVDA_26)),
    (NVDA, NVDA_CAPEX): ("Capex", "primary", None, (LV,), (NVDA_23, NVDA_25, NVDA_26)),
    (TSM, "InvestmentIncomeInterest"): ("InterestIncome", "primary", None, (LV,),
                                        (TSM_23, TSM_25)),
    (TSM, "InvestmentsIncomeInterest"): ("InterestIncome", "primary", None, (LV,),
                                         (TSM_24, TSM_25)),
    (TSM, "PaymentForAcquireOfTreasuryShares"): ("StockRepurchases", "primary", None, (LV,),
                                                 (TSM_23, TSM_24, TSM_25)),
    (LMT, LMT_DA): ("DepreciationAndAmortization", "substitute", "includes_accretion", (LV,),
                    (LMT_24, LMT_25)),
}

FIXTURE_SETTINGS = settings(max_examples=25,
                            suppress_health_check=[HealthCheck.function_scoped_fixture])


# --------------------------------------------------------------------------- helpers
def _index(results: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(r["cik"], r["local_name"]): r for r in results}


def _filing(filings: list[dict[str, Any]], accession_no: str) -> dict[str, Any]:
    return next(f for f in filings if f["accession_no"] == accession_no)


def _local(concept: str) -> str:
    return concept.rpartition(":")[2]


_STANDARD = ("us-gaap:", "srt:", "dei:", "ifrs-full:", "http://fasb.org/", "http://xbrl.sec.gov/",
             "http://www.xbrl.org/", "http://xbrl.ifrs.org/", "https://xbrl.ifrs.org/")


def _is_ext(concept: str) -> bool:
    """R1: a concept is an extension unless it is in a standard prefix form or namespace."""
    return not concept.startswith(_STANDARD)


def _ext_concept(f: dict[str, Any], local: str) -> str:
    """The concept string an extension line of this filing uses (its namespace form)."""
    return next(ln["concept"] for ln in f["lines"]
                if _local(ln["concept"]) == local and _is_ext(ln["concept"]))


def _families_of(members: list[dict[str, Any]], qname: str) -> set[str]:
    return {m["family"] for m in members if m["qname"] == qname}


def _line(f: dict[str, Any], local: str, stmt: str) -> dict[str, Any]:
    return next(ln for ln in f["lines"] if _local(ln["concept"]) == local and ln["stmt"] == stmt)


def _classification(r: dict[str, Any]) -> tuple:
    return (r["family"], r["role"], r["substitution_code"], r["rules"], r["evidence"])


def _assert_unclassified(r: dict[str, Any], reason: str) -> None:
    assert r["reason"] == reason
    assert (r["family"], r["role"], r["substitution_code"]) == (None, None, None)
    assert (r["rules"], r["evidence"]) == ((), ())


def _rename_filer_namespace(filings: list[dict[str, Any]], cik: str, new_ns: str,
                            rename: dict[str, str] | None = None) -> None:
    """Give every extension concept of a filer the namespace `new_ns` (and new local names)."""
    rename = rename or {}

    def fix(c: str) -> str:
        if not _is_ext(c):
            return c
        local = _local(c)
        return f"{new_ns}:{rename.get(local, local)}"

    for f in filings:
        if f["cik"] != cik:
            continue
        for ln in f["lines"]:
            ln["concept"] = fix(ln["concept"])
        for v in f["values"]:
            v["concept"] = fix(v["concept"])
        for a in f["arcs"]:
            a["parent"], a["child"] = fix(a["parent"]), fix(a["child"])


def _synthetic(cik: str, lines: list[tuple], arcs: list[tuple], values: list[tuple],
               accession_no: str = "9999999999-26-000001") -> dict[str, Any]:
    return {
        "accession_no": accession_no,
        "cik": cik,
        "lines": [{"stmt": s, "report": rep, "line": n, "concept": c, "plabel": lab}
                  for s, rep, n, c, lab in lines],
        "arcs": [{"link_role": "http://example.com/role/IS", "parent": p, "child": ch,
                  "weight": Decimal(w)} for p, ch, w in arcs],
        "values": [{"concept": c, "period": per, "unit": u, "value": Decimal(v)}
                   for c, per, u, v in values],
    }


# =========================================================================== C1
@pytest.mark.criterion("C1")
def test_c1_subjects_are_the_custom_pre_rows(fsds_tables, filings, members):
    # oracle: FSDS readme (pre/tag): a tag is custom when its version equals the filing's
    # adsh. The expected subjects are read straight from the committed pre rows here.
    cik_of = {s["adsh"]: s["cik"].zfill(10) for s in fsds_tables["sub"]}
    expected: dict[tuple[str, str], set[str]] = {}
    for p in fsds_tables["pre"]:
        if p["version"] == p["adsh"] and p["stmt"] in ("IS", "BS", "CF"):
            expected.setdefault((cik_of[p["adsh"]], p["tag"]), set()).add(p["adsh"])
    results = classify_extensions(filings, members)
    assert len(expected) == 156
    assert [(r["cik"], r["local_name"]) for r in results] == sorted(expected)
    for r in results:
        assert set(r) == RESULT_KEYS
        assert r["accessions"] == tuple(sorted(expected[(r["cik"], r["local_name"])]))
    per_filer: dict[str, int] = {}
    for cik, _ in expected:
        per_filer[cik] = per_filer.get(cik, 0) + 1
    assert per_filer == {MCD: 14, PG: 3, ORCL: 14, TSLA: 15, TSM: 82, NVDA: 6, LMT: 4,
                         AMD: 4, INTC: 5, LLY: 2, AVGO: 7}
    # TSMC's extension shares the local name of us-gaap:InvestmentIncomeInterest and is
    # still its own subject (R1 Identity).
    assert (TSM, "InvestmentIncomeInterest") in _index(results)


@pytest.mark.criterion("C1")
def test_c1_ext_member_row_raises(filings, members):
    # oracle: R2 (members): the hand list is not an input; an ext: row raises ValueError.
    members.append({"family": "PretaxIncome", "qname": f"ext:{PRETAX}", "role": "primary",
                    "substitution_code": None, "cik": MCD})
    with pytest.raises(ValueError):
        classify_extensions(filings, members)


@pytest.mark.criterion("C1")
@FIXTURE_SETTINGS
@given(st.integers(min_value=0, max_value=2**32 - 1))
def test_c1_input_order_never_matters(filings, members, seed):
    rnd = random.Random(seed)
    # oracle: R5: nothing depends on the order of filings, lines, arcs, values or members
    # (metamorphic).
    base = classify_extensions(copy.deepcopy(filings), copy.deepcopy(members))
    assert _classification(_index(base)[(MCD, PRETAX)])[0] == "PretaxIncome"
    shuffled = copy.deepcopy(filings)
    rnd.shuffle(shuffled)
    for f in shuffled:
        for key in ("lines", "arcs", "values"):
            rnd.shuffle(f[key])
    m = copy.deepcopy(members)
    rnd.shuffle(m)
    assert classify_extensions(shuffled, m) == base


@pytest.mark.criterion("C1")
@FIXTURE_SETTINGS
@given(st.sampled_from([MCD, PG, ORCL, TSM, NVDA, LMT, INTC]),
       st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz", min_size=1,
               max_size=12))
def test_c1_namespace_and_local_name_renaming(filings, members, cik, suffix):
    # oracle: R1 Identity: an extension is (cik, local name); its namespace URI never
    # matters, and a consistent rename of a local name changes only that name (metamorphic).
    base = _index(classify_extensions(copy.deepcopy(filings), members))
    assert len(CLASSIFIED) == sum(1 for r in base.values() if r["family"] is not None)
    new_local = "Zz" + suffix
    assume(all(local != new_local for (c, local) in base if c == cik))
    # The filer's classified subject if it has one, else its first subject.
    target = sorted((r["family"] is None, local)
                    for (c, local), r in base.items() if c == cik)[0][1]
    renamed = copy.deepcopy(filings)
    _rename_filer_namespace(renamed, cik, "http://www.renamed.example/2030", {target: new_local})
    after = _index(classify_extensions(renamed, members))
    expected = dict(base)
    moved = dict(expected.pop((cik, target)))
    moved["local_name"] = new_local
    expected[(cik, new_local)] = moved
    assert after == expected


# =========================================================================== C2
@pytest.mark.criterion("C2")
def test_c2_identity_rule_classifies_pretax_and_nonoperating(filings, members):
    # oracle: the income statements, by hand, and each filing's calculation linkbase:
    #   MCD FY2023 11,647 - 1,361 + 236 = 10,522 (pre-tax); 10,522 - 2,053 = 8,469 (net income)
    #   PG FY2023 18,134 - 756 + 307 + 668 = 18,353; 18,353 - 3,615 = 14,738
    #   ORCL FY2026 20,606 - 4,599 + 3,547 = 19,554; 19,554 - 2,467 = 17,087 (millions USD;
    #     Oracle's pre-tax tree holds its own non-operating extension, so only the
    #     NetIncome identity applies)
    #   TSMC FY2025 1,936,091.7 + 105,563.0 = 2,041,654.7 (millions TWD)
    # and decisions D27 (pre-tax) and D14 (non-operating) of XbrlRag_Concept_Decisions.md.
    got = _index(classify_extensions(filings, members))
    for key in [(MCD, PRETAX), (PG, PRETAX), (ORCL, ORCL_PRETAX),
                (TSM, "NonOperatingIncomeExpense1"), (TSM, "NonOperatingIncomeExpenseNet")]:
        assert _classification(got[key]) == CLASSIFIED[key], key
        assert got[key]["reason"] is None
    assert got[(MCD, PRETAX)]["component_of"] == ("NetIncome",)
    assert got[(MCD, PRETAX)]["contains"] == ("InterestExpense", "OperatingIncome",
                                              "OtherNonOperatingIncome")
    assert got[(TSM, "NonOperatingIncomeExpenseNet")]["component_of"] == ("PretaxIncome",)


def _perturb_orcl(filings: list[dict[str, Any]], how: str) -> None:
    f = _filing(filings, ORCL_26)
    e = _ext_concept(f, ORCL_PRETAX)
    trees = [a for a in f["arcs"] if _local(a["parent"]) == "NetIncomeLoss" and a["child"] == e]
    assert trees
    roles = {a["link_role"] for a in trees}
    parent = trees[0]["parent"]
    if how == "drop_tax":
        f["arcs"] = [a for a in f["arcs"]
                     if not (a["link_role"] in roles
                             and _local(a["child"]) == "IncomeTaxExpenseBenefit")]
    elif how == "extra_standard":
        for r in roles:
            f["arcs"].append({"link_role": r, "parent": parent,
                              "child": "us-gaap:PreferredStockDividendsIncomeStatementImpact",
                              "weight": Decimal(-1)})
    elif how == "extra_extension":
        for r in roles:
            f["arcs"].append({"link_role": r, "parent": parent,
                              "child": e.rpartition(":")[0] + ":ExpenseAllocationsAndOtherNet",
                              "weight": Decimal(-1)})
    elif how == "flip_weight":
        for a in trees:
            a["weight"] = -a["weight"]
    elif how == "total_above":
        net = _line(f, "NetIncomeLoss", "IS")
        net["line"] = _line(f, ORCL_PRETAX, "IS")["line"] - 1
        net["report"] = _line(f, ORCL_PRETAX, "IS")["report"]


@pytest.mark.criterion("C2")
@FIXTURE_SETTINGS
@given(st.sampled_from(["drop_tax", "extra_standard", "extra_extension", "flip_weight",
                        "total_above"]))
def test_c2_identity_term_needs_an_exact_tree(filings, members, how):
    # oracle: R4 rule 1: the other children must fill every required term but E's exactly,
    # with matching weights, no extra or second-extension child, and the total presented
    # below E. Oracle's pre-tax line has no other evidence, so it falls to no_evidence
    # (metamorphic).
    filings = copy.deepcopy(filings)  # hypothesis reuses one fixture value across examples
    base = _index(classify_extensions(copy.deepcopy(filings), members))
    assert _classification(base[(ORCL, ORCL_PRETAX)]) == CLASSIFIED[(ORCL, ORCL_PRETAX)]
    _perturb_orcl(filings, how)
    after = _index(classify_extensions(filings, members))
    _assert_unclassified(after[(ORCL, ORCL_PRETAX)], "no_evidence")


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_c2_identity_total_needs_every_required_term(filings, members):
    # oracle: R4 rule 2: McDonald's pre-tax total loses its total evidence when its
    # OperatingIncomeLoss child (the required OperatingIncome term) is removed, and keeps
    # the term evidence from the NetIncome tree (metamorphic).
    f = _filing(filings, MCD_24)
    e = _ext_concept(f, PRETAX)
    f["arcs"] = [a for a in f["arcs"]
                 if not (a["parent"] == e and _local(a["child"]) == "OperatingIncomeLoss")]
    got = _index(classify_extensions(filings, members))[(MCD, PRETAX)]
    assert _classification(got) == ("PretaxIncome", "primary", None, (IT,), (MCD_24,))


# =========================================================================== C3
@pytest.mark.criterion("C3")
def test_c3_intel_gains_on_equity_investments_fill_no_optional_term(filings, members):
    # oracle: Intel's 10-K FY2024 income statement: -11,678 + 242 + 226 = -11,210 (millions):
    # pre-tax income = operating loss + gains on equity investments (the extension) +
    # "Interest and other, net" (NonoperatingIncomeExpense). Both required terms of
    # pretax_income are standard lines, so the extension could only fill the optional
    # InterestIncome term, which R4 forbids; gains on equity investments are not interest.
    got = _index(classify_extensions(filings, members))[(INTC, "GainsLossesOnEquityInvestments")]
    _assert_unclassified(got, "no_evidence")
    assert got["component_of"] == ("PretaxIncome",)
    assert got["contains"] == ()


@pytest.mark.criterion("C3")
@settings(max_examples=50)
@given(st.booleans(), st.booleans(), st.sampled_from(["1", "-1"]))
def test_c3_optional_terms_are_never_filled(with_interest_income, with_interest_expense, weight):
    # oracle: R4 rule 1: with OperatingIncome and OtherNonOperatingIncome filled by standard
    # children, an extension child gets no identity evidence, whatever optional terms remain.
    members = [
        {"family": "PretaxIncome", "role": "primary", "substitution_code": None, "cik": None,
         "qname": "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
                  "ExtraordinaryItemsNoncontrollingInterest"},
        {"family": "OperatingIncome", "qname": "us-gaap:OperatingIncomeLoss", "role": "primary",
         "substitution_code": None, "cik": None},
        {"family": "OtherNonOperatingIncome", "qname": "us-gaap:NonoperatingIncomeExpense",
         "role": "primary", "substitution_code": None, "cik": None},
        {"family": "InterestIncome", "qname": "us-gaap:InvestmentIncomeInterest", "role": "primary",
         "substitution_code": None, "cik": None},
        {"family": "InterestExpense", "qname": "us-gaap:InterestExpense", "role": "primary",
         "substitution_code": None, "cik": None},
    ]
    total = ("http://fasb.org/us-gaap/2024:IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
             "ExtraordinaryItemsNoncontrollingInterest")
    ext = "http://www.filer.example/20241231:GainsOnSomething"
    kids = [("http://fasb.org/us-gaap/2024:OperatingIncomeLoss", "1"),
            ("http://fasb.org/us-gaap/2024:NonoperatingIncomeExpense", "1"), (ext, weight)]
    if with_interest_income:
        kids.append(("http://fasb.org/us-gaap/2024:InvestmentIncomeInterest", "1"))
    if with_interest_expense:
        kids.append(("http://fasb.org/us-gaap/2024:InterestExpense", "-1"))
    lines = [("IS", 2, i + 1, c, f"line {i}") for i, (c, _) in enumerate(kids)]
    lines.append(("IS", 2, 20, total, "Income before taxes"))
    f = _synthetic("0000000001", lines, [(total, c, w) for c, w in kids], [])
    (r,) = classify_extensions([f], members)
    assert r["local_name"] == "GainsOnSomething"
    _assert_unclassified(r, "no_evidence")
    assert r["component_of"] == ("PretaxIncome",)


# =========================================================================== C4
@pytest.mark.criterion("C4")
def test_c4_label_and_value_rule(filings, members):
    # oracle: the statements by hand (millions):
    #   NVDA FY2026 BS "Marketable securities" (extension) 34,621 at 2025-01-31 = FY2025
    #     MarketableSecuritiesCurrent 34,621 (D39)
    #   NVDA FY2023 CF "Purchases related to property and equipment and intangible assets"
    #     (extension) 1,833 = FY2025 PaymentsToAcquireProductiveAssets for FY2023 (D41)
    #   TSMC IS "Interest Income": FY2023 InvestmentIncomeInterest 60,293.9 and FY2024
    #     InvestmentsIncomeInterest 87,213.4 = FY2025 RevenueFromInterest (TWD; decision C8)
    #   TSMC CF "Treasury stock acquired" FY2024 3,089.2 = FY2025
    #     PaymentsToAcquireOrRedeemEntitysShares (TWD)
    #   LMT CF "Depreciation and amortization" FY2024 extension 1,559 and 1,430 = FY2025
    #     DepreciationAmortizationAndAccretionNet, a stated substitute (includes_accretion)
    got = _index(classify_extensions(filings, members))
    for key in [(NVDA, NVDA_MS), (NVDA, NVDA_CAPEX), (TSM, "InvestmentIncomeInterest"),
                (TSM, "InvestmentsIncomeInterest"), (TSM, "PaymentForAcquireOfTreasuryShares"),
                (LMT, LMT_DA)]:
        assert _classification(got[key]) == CLASSIFIED[key], key
    assert {k for k, r in got.items() if r["family"] is not None} == set(CLASSIFIED)


@pytest.mark.criterion("C4")
def test_c4_same_label_different_value_is_label_only(filings, members):
    # oracle: AMD 10-Q Q2 2026 CF: "Purchases of property and equipment" of discontinued
    # operations 22 against continuing 494 / 1,197 (millions); Intel's financing-section
    # "Additions to property, plant and equipment" matches the investing line's label only.
    got = _index(classify_extensions(filings, members))
    amd = got[(AMD, "PaymentsToAcquirePropertyPlantAndEquipmentFromDiscontinuedOperation")]
    _assert_unclassified(amd, "label_only")
    intc = got[(INTC, "PaymentsToAcquirePropertyPlantAndEquipmentFinancingActivities")]
    _assert_unclassified(intc, "label_only")
    assert intc["component_of"] == ("CashFromFinancing",)


@pytest.mark.criterion("C4")
@FIXTURE_SETTINGS
@given(st.sampled_from([NVDA, TSM, LMT, AMD, INTC]),
       st.integers(min_value=-1000, max_value=1000).filter(lambda k: k != 0))
def test_c4_scaling_a_filers_values_changes_nothing(filings, members, cik, k):
    # oracle: R4 rule 3 compares values for equality only, so multiplying every value of a
    # filer by one non-zero factor keeps every equal pair and every zero (metamorphic).
    filings = copy.deepcopy(filings)  # hypothesis reuses one fixture value across examples
    base = classify_extensions(copy.deepcopy(filings), members)
    assert _classification(_index(base)[(NVDA, NVDA_MS)]) == CLASSIFIED[(NVDA, NVDA_MS)]
    for f in filings:
        if f["cik"] == cik:
            for v in f["values"]:
                if v["value"] is not None:
                    v["value"] = v["value"] * k
    assert classify_extensions(filings, members) == base


@pytest.mark.criterion("C4")
@settings(max_examples=50)
@given(st.sampled_from(["equal", "zero", "unit", "period"]),
       st.integers(min_value=1, max_value=10**12))
def test_c4_value_pair_must_match_period_unit_and_be_non_zero(how, n):
    # oracle: R4 rule 3: same normalized label, same period, same unit, same non-zero value;
    # anything less leaves the extension label_only.
    members = [{"family": "Capex", "qname": "us-gaap:PaymentsToAcquireProductiveAssets",
                "role": "primary", "substitution_code": None, "cik": None}]
    ext = "http://www.filer.example/20231231:PurchasesOfThings"
    std = "us-gaap:PaymentsToAcquireProductiveAssets"
    e_val = ("2023-12-31/4", "USD", "0" if how == "zero" else str(n))
    s_val = {"equal": ("2023-12-31/4", "USD", str(n)), "zero": ("2023-12-31/4", "USD", "0"),
             "unit": ("2023-12-31/4", "EUR", str(n)),
             "period": ("2022-12-31/4", "USD", str(n))}[how]
    a = _synthetic("0000000002", [("CF", 7, 20, ext, "Purchases of things, net")], [],
                   [(ext, *e_val)], accession_no="0000000002-24-000001")
    b = _synthetic("0000000002", [("CF", 7, 21, std, "Purchases of THINGS (net)")], [],
                   [(std, *s_val)], accession_no="0000000002-25-000001")
    (r,) = classify_extensions([a, b], members)
    if how == "equal":
        assert _classification(r) == ("Capex", "primary", None, (LV,),
                                      ("0000000002-24-000001", "0000000002-25-000001"))
    else:
        _assert_unclassified(r, "label_only")


# =========================================================================== C5
@pytest.mark.criterion("C5")
def test_c5_components_and_totals(filings, members):
    # oracle: the calculation linkbases, by hand:
    #   AVGO CostOfRevenue = CostofProductsSold (extension) + ...: a part of cost of revenue
    #   MCD Assets = ... + TotalOtherAssets; TotalOtherAssets = Goodwill + investments + other
    #   LLY pre-tax = Revenue - CostOfSalesOperatingExpensesAndOtherNet, which sums cost of
    #     goods sold, SG&A, R&D, acquired IPR&D, restructuring/impairment and -nonoperating
    #     (D42: not CostOfRevenue)
    #   ORCL RestructuringAndOtherExpenses = RestructuringCharges + OtherOperatingExpensesNet,
    #     under CostsAndExpenses (no family's primary)
    got = _index(classify_extensions(filings, members))
    expect = {
        (AVGO, "CostofProductsSold"): (("CostOfRevenue",), ()),
        (MCD, "TotalOtherAssets"): (("TotalAssets",), ("Goodwill",)),
        (MCD, "Revenuesfromfranchisedrestaurants"): (("Revenue",), ()),
        (LLY, "CostOfSalesOperatingExpensesAndOtherNet"): (
            ("PretaxIncome",),
            ("CostOfRevenue", "OtherNonOperatingIncome", "ResearchAndDevelopment",
             "SellingGeneralAndAdministrative")),
        (ORCL, "RestructuringAndOtherExpenses"): ((), ("RestructuringCharges",)),
    }
    for key, (component_of, contains) in expect.items():
        r = got[key]
        _assert_unclassified(r, "no_evidence")
        assert (r["component_of"], r["contains"]) == (component_of, contains), key


@pytest.mark.criterion("C5")
def test_c5_component_with_label_evidence_is_a_conflict(filings, members):
    # oracle: R5: Broadcom's "Cost of products sold" is a child of CostOfRevenue; a
    # standard CostOfRevenue line with the same label and value would name CostOfRevenue,
    # its own total, so the result is a conflict, never a CostOfRevenue member.
    f = _filing(filings, AVGO_25)
    e = _line(f, "CostofProductsSold", "IS")
    std = "us-gaap:CostOfGoodsAndServicesSold"
    f["lines"].append({"stmt": "IS", "report": e["report"], "line": 99, "concept": std,
                       "plabel": e["plabel"]})
    v = next(x for x in f["values"] if x["concept"] == e["concept"])
    f["values"].append({**v, "concept": std})
    got = _index(classify_extensions(filings, members))[(AVGO, "CostofProductsSold")]
    _assert_unclassified(got, "conflict")


@pytest.mark.criterion("C5")
@FIXTURE_SETTINGS
@given(st.sampled_from(sorted(CLASSIFIED)), st.sampled_from(["component", "contains"]))
def test_c5_own_family_in_a_guard_is_a_conflict(filings, members, key, side):
    # oracle: R5: no result's family is in its own component_of or contains; making a
    # classified extension a child (or the parent) of a primary of its own family turns it
    # into a conflict (metamorphic).
    filings = copy.deepcopy(filings)  # hypothesis reuses one fixture value across examples
    base = _index(classify_extensions(copy.deepcopy(filings), members))
    for r in base.values():
        assert r["family"] not in r["component_of"] + r["contains"]
    cik, local = key
    family = CLASSIFIED[key][0]
    std = sorted(m["qname"] for m in members if m["family"] == family and m["role"] == "primary"
                 and m["cik"] is None)[0]
    f = next(x for x in filings if x["cik"] == cik and any(
        _local(ln["concept"]) == local and _is_ext(ln["concept"]) for ln in x["lines"]))
    e = _ext_concept(f, local)
    parent, child = (std, e) if side == "component" else (e, std)
    f["arcs"].append({"link_role": "http://www.filer.example/role/Guard", "parent": parent,
                      "child": child, "weight": Decimal(1)})
    _assert_unclassified(_index(classify_extensions(filings, members))[key], "conflict")


# =========================================================================== C6
def _add_partner(f: dict[str, Any], local: str, qname: str) -> None:
    """A standard line with the extension's label and one of its values, in the same filing."""
    e = next(ln for ln in f["lines"] if _local(ln["concept"]) == local and _is_ext(ln["concept"]))
    f["lines"].append({**e, "line": 999, "concept": qname})
    v = next(x for x in f["values"] if x["concept"] == e["concept"] and x["value"])
    f["values"].append({**v, "concept": qname})


@pytest.mark.criterion("C6")
def test_c6_two_families_are_ambiguous(filings, members):
    # oracle: R5: NVIDIA's "Marketable securities" extension with a second same-label,
    # same-value partner in another family (cash and equivalents) names two families, so it
    # is ambiguous rather than either.
    _add_partner(_filing(filings, NVDA_26), NVDA_MS,
                 "us-gaap:CashAndCashEquivalentsAtCarryingValue")
    got = _index(classify_extensions(filings, members))[(NVDA, NVDA_MS)]
    _assert_unclassified(got, "ambiguous")


@pytest.mark.criterion("C6")
@FIXTURE_SETTINGS
@given(st.sampled_from(sorted(CLASSIFIED)), st.integers(min_value=0, max_value=2**32 - 1))
def test_c6_second_family_evidence_never_picks(filings, members, key, seed):
    # oracle: R5: evidence for a second (family, role, code) makes any classified subject
    # ambiguous, unless that family is in a guard (then conflict) (metamorphic).
    rnd = random.Random(seed)
    filings = copy.deepcopy(filings)  # hypothesis reuses one fixture value across examples
    cik, local = key
    family = CLASSIFIED[key][0]
    base = _index(classify_extensions(copy.deepcopy(filings), members))[key]
    assert _classification(base) == CLASSIFIED[key]
    # A partner whose every family is new to this subject: neither its own nor a guard.
    banned = {family} | set(base["component_of"]) | set(base["contains"])
    others = sorted({m["qname"] for m in members if m["role"] == "primary" and m["cik"] is None
                     and not (_families_of(members, m["qname"]) & banned)
                     and all(x["cik"] is None for x in members if x["qname"] == m["qname"])})
    qname = rnd.choice(others)
    f = next(x for x in filings if x["cik"] == cik and any(
        _local(ln["concept"]) == local and _is_ext(ln["concept"])
        and any(v["concept"] == ln["concept"] and v["value"] for v in x["values"])
        for ln in x["lines"]))
    _add_partner(f, local, qname)
    _assert_unclassified(_index(classify_extensions(filings, members))[key], "ambiguous")


# =========================================================================== C7
@pytest.mark.criterion("C7")
def test_c7_same_name_two_filers(filings, members):
    # oracle: McDonald's and P&G income statements and linkbases (each pre-tax extension sits
    # in its own NetIncome = pre-tax - tax tree); Oracle's restructuring extension is a
    # total of RestructuringCharges, Tesla's a part of OperatingExpenses (Tesla 10-K FY2024
    # calculation linkbase).
    got = _index(classify_extensions(filings, members))
    assert got[(MCD, PRETAX)]["evidence"] == (MCD_24,)
    assert got[(PG, PRETAX)]["evidence"] == (PG_23,)
    orcl = got[(ORCL, "RestructuringAndOtherExpenses")]
    tsla = got[(TSLA, "RestructuringAndOtherExpenses")]
    _assert_unclassified(orcl, "no_evidence")
    _assert_unclassified(tsla, "no_evidence")
    assert (orcl["component_of"], orcl["contains"]) == ((), ("RestructuringCharges",))
    assert (tsla["component_of"], tsla["contains"]) == (("OperatingExpenses",), ())
    assert tsla["accessions"] == (TSLA_24,)


@pytest.mark.criterion("C7")
@FIXTURE_SETTINGS
@given(st.sampled_from([MCD, PG, ORCL, TSLA, TSM, NVDA, LMT, AMD, INTC, LLY, AVGO]))
def test_c7_other_filers_never_change_a_result(filings, members, cik):
    # oracle: R1 Scoping: removing every filing of one filer, or adding a copy of a filing
    # under a new filer, changes no other filer's results (metamorphic).
    base = classify_extensions(copy.deepcopy(filings), members)
    assert _classification(_index(base)[(PG, PRETAX)]) == CLASSIFIED[(PG, PRETAX)]
    without = classify_extensions([f for f in copy.deepcopy(filings) if f["cik"] != cik], members)
    assert without == [r for r in base if r["cik"] != cik]
    clone = copy.deepcopy(next(f for f in filings if f["cik"] == cik))
    clone["cik"], clone["accession_no"] = "0009999999", "0009999999-26-000001"
    with_clone = classify_extensions(copy.deepcopy(filings) + [clone], members)
    assert [r for r in with_clone if r["cik"] != "0009999999"] == base
    assert any(r["cik"] == "0009999999" for r in with_clone)


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
def test_c7_borrowed_namespace_changes_nothing(filings, members):
    # oracle: R1 Identity and Scoping: P&G's extensions written in McDonald's namespace URI
    # are still P&G's, classified from P&G's filing only (metamorphic).
    base = classify_extensions(copy.deepcopy(filings), members)
    mcd_ns = _ext_concept(_filing(filings, MCD_24), PRETAX).rpartition(":")[0]
    _rename_filer_namespace(filings, PG, mcd_ns)
    assert classify_extensions(filings, members) == base
    assert _classification(_index(base)[(PG, PRETAX)]) == CLASSIFIED[(PG, PRETAX)]


# =========================================================================== C8
@pytest.mark.criterion("C8")
def test_c8_mapping_rows_next_to_the_hand_list(filings, members, listed):
    # oracle: the hand list (concept_family_coverage.yaml filers:, fixtures/listed.json) and
    # the classifications hand-checked in C2/C4: three classified extensions are not listed;
    # the eight listed-and-classified ones agree.
    results = classify_extensions(filings, members)
    out = extension_mapping_rows(results, listed)
    assert set(out) == {"rows", "agreements", "disagreements", "unclassified"}
    assert list(out["rows"]) == [
        {"cik": LMT, "family": "DepreciationAndAmortization", "qname": f"ext:{LMT_DA}",
         "role": "substitute", "substitution_code": "includes_accretion"},
        {"cik": TSM, "family": "InterestIncome", "qname": "ext:InvestmentsIncomeInterest",
         "role": "primary", "substitution_code": None},
        {"cik": TSM, "family": "StockRepurchases", "qname": "ext:PaymentForAcquireOfTreasuryShares",
         "role": "primary", "substitution_code": None},
    ]
    assert tuple(out["agreements"]) == (
        (MCD, PRETAX), (PG, PRETAX), (NVDA, NVDA_MS), (NVDA, NVDA_CAPEX),
        (TSM, "InvestmentIncomeInterest"), (TSM, "NonOperatingIncomeExpense1"),
        (TSM, "NonOperatingIncomeExpenseNet"), (ORCL, ORCL_PRETAX))
    assert tuple(out["disagreements"]) == ()
    expected_unclassified = sorted((r["cik"], r["local_name"], r["reason"]) for r in results
                                   if r["family"] is None)
    assert len(expected_unclassified) == 156 - 11
    assert tuple(out["unclassified"]) == tuple(expected_unclassified)
    assert (ORCL, "SalesRevenueServicesNet1", "no_evidence") in out["unclassified"]


@pytest.mark.criterion("C8")
@FIXTURE_SETTINGS
@given(st.integers(min_value=0, max_value=2**32 - 1))
def test_c8_listed_rows_only_remove_rows(filings, members, listed, seed):
    rnd = random.Random(seed)
    # oracle: R6: an unclassified or listed extension never yields a row, and adding listed
    # rows can only remove rows (metamorphic).
    results = classify_extensions(filings, members)
    full = extension_mapping_rows(results, listed)
    assert len(full["rows"]) == 3
    extra = [{"cik": r["cik"], "family": "Revenue", "qname": "ext:" + r["local_name"],
              "role": "primary"} for r in results if rnd.random() < 0.3]
    more = extension_mapping_rows(results, listed + extra)
    assert all(row in full["rows"] for row in more["rows"])
    listed_keys = {(x["cik"], x["qname"][4:]) for x in listed + extra}
    unclassified = {(r["cik"], r["local_name"]) for r in results if r["family"] is None}
    for row in more["rows"]:
        key = (row["cik"], row["qname"][4:])
        assert key not in listed_keys and key not in unclassified
    for x in extra:
        r = _index(results)[(x["cik"], x["qname"][4:])]
        if r["family"] is not None and (x["cik"], x["qname"][4:]) not in {
                (y["cik"], y["qname"][4:]) for y in listed}:
            assert (x["cik"], x["qname"][4:]) in more["disagreements"]


# =========================================================================== C9
@pytest.mark.criterion("C9")
def test_c9_default_identities_table():
    # oracle: R3's table (statement sums: net income = pre-tax income - tax; pre-tax income =
    # operating income + non-operating items; gross profit = revenue - cost of revenue;
    # operating income = gross profit - operating expenses).
    def t(family: str, weight: int, required: bool) -> dict[str, Any]:
        return {"family": family, "weight": Decimal(weight), "required": required}

    assert default_identities() == (
        {"id": "net_income", "statement": "IS", "total": "NetIncome",
         "terms": (t("PretaxIncome", 1, True), t("TaxExpense", -1, True))},
        {"id": "pretax_income", "statement": "IS", "total": "PretaxIncome",
         "terms": (t("OperatingIncome", 1, True), t("OtherNonOperatingIncome", 1, True),
                   t("InterestIncome", 1, False), t("InterestExpense", -1, False))},
        {"id": "gross_profit", "statement": "IS", "total": "GrossProfit",
         "terms": (t("Revenue", 1, True), t("CostOfRevenue", -1, True))},
        {"id": "operating_income", "statement": "IS", "total": "OperatingIncome",
         "terms": (t("GrossProfit", 1, True), t("OperatingExpenses", -1, True))},
    )


@pytest.mark.criterion("C9")
@FIXTURE_SETTINGS
@given(st.integers(min_value=0, max_value=2**32 - 1))
def test_c9_identities_parameter(filings, members, seed):
    rnd = random.Random(seed)
    # oracle: R2/R3: identities=None means the defaults, their order never matters, and no
    # identities leave only label_value evidence (metamorphic).
    base = classify_extensions(copy.deepcopy(filings), members)
    ids = list(default_identities())
    rnd.shuffle(ids)
    assert classify_extensions(copy.deepcopy(filings), members, ids) == base
    none = _index(classify_extensions(filings, members, ()))
    classified = {k for k, r in none.items() if r["family"] is not None}
    assert classified == {k for k, v in CLASSIFIED.items() if v[3] == (LV,)}
    assert all(r["rules"] in ((), (LV,)) for r in none.values())
