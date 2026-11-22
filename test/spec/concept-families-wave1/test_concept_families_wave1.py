"""Spec tests for concept-families-wave1: a reviewed, validated override list for the
concept families (docs/specs/concept-families-wave1.md).

Pure: synthetic QName sets and override dicts; one committed config file; no DB.
"""

from __future__ import annotations

import copy
import re

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.ingest.layer0_populator import filer_ranking
from src.ingest.util.generate_concept_mapping import (
    FAMILY_ANCHOR_QNAMES,
    FAMILY_COMPONENT_QNAMES,
    family_ranking,
    load_family_overrides,
    merge_family_overrides,
    reviewed_filer_ranking,
    validate_family_overrides,
)

COMPONENT_BASE = 50  # the component band starts here (concept-rank-integrity)
IFRS_BASE = 100  # IFRS rows keep their own band, which stays eligible

EXCL = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
INCL = "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax"
REVS = "us-gaap:Revenues"
SRN = "us-gaap:SalesRevenueNet"
PRETAX = (
    "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
    "ExtraordinaryItemsNoncontrollingInterest"
)
IPRD = "us-gaap:ResearchAndDevelopmentInProcess"
IPRD_FAMILY = "AcquiredInProcessResearchAndDevelopment"
WMT = "0000104169"  # Walmart Inc., SEC EDGAR CIK 104169
COST = "0000909832"  # Costco Wholesale Corp., SEC EDGAR CIK 909832

# The 15 filers in the corpus before wave 1 (spec Notes).
CORPUS_CIKS = {
    "0000002488", "0000050863", "0000320193", "0000723125", "0000789019",
    "0001018724", "0001045810", "0001046179", "0001318605", "0001326801",
    "0001341439", "0001652044", "0001692819", "0001730168", "0001868275",
}
# concept-key-integrity requirement 6: Vistra and Constellation, Revenue only.
PRE_WAVE_ALLOWED = {("0001692819", "Revenue"), ("0001868275", "Revenue")}

# ----------------------------------------------------------------- C1 synthetic base
BASE_P = {
    "Revenue": [EXCL, REVS, SRN],
    "OperatingIncome": ["us-gaap:OperatingIncomeLoss"],
    "PretaxIncome": [PRETAX],
    "Debt": ["us-gaap:LongTermDebt"],
}
BASE_C = {"Debt": ["us-gaap:DebtCurrent"]}

VALID = {
    "version": 1,
    "families": {
        "Revenue": {"primaries": [INCL], "reason": "total revenue gross of assessed taxes"},
        "Debt": {"components": ["us-gaap:ShortTermBorrowings"], "reason": "part of debt"},
        IPRD_FAMILY: {
            "label": "Acquired in-process research and development",
            "primaries": [IPRD],
            "reason": "pharma acquired IPR&D",
        },
    },
    "filers": [
        {"cik": WMT, "family": "Revenue", "preferred": [REVS], "reason": "total revenues"},
    ],
}

VALID_NORMALIZED = {
    "families": {
        "Revenue": {"label": None, "primaries": [INCL], "components": []},
        "Debt": {"label": None, "primaries": [], "components": ["us-gaap:ShortTermBorrowings"]},
        IPRD_FAMILY: {
            "label": "Acquired in-process research and development",
            "primaries": [IPRD],
            "components": [],
        },
    },
    "filers": {(WMT, "Revenue"): [REVS]},
}

EMPTY = {"families": {}, "filers": {}}


def _valid():
    return copy.deepcopy(VALID)


def _validate(raw):
    return validate_family_overrides(raw, BASE_P, BASE_C)


# One mutation per rule; each breaks exactly that rule of a copy of VALID.
def _v1_top_key(r):
    r["famillies"] = {}


def _v1_version(r):
    r["version"] = 2


def _v1_family_key(r):
    r["families"]["Revenue"]["primary"] = [INCL]


def _v1_filer_extra_key(r):
    r["filers"][0]["rank"] = 1


def _v1_filer_missing_family(r):
    del r["filers"][0]["family"]


def _v2_custom_prefix(r):
    r["families"]["Revenue"]["primaries"].append("cost:MembershipFees")


def _v2_no_prefix(r):
    r["families"]["Revenue"]["primaries"].append("RevenuesTotal")


def _v2_bad_local_name(r):
    r["families"]["Revenue"]["primaries"].append("us-gaap:Revenues-Total")


def _v3_family_reason_missing(r):
    del r["families"]["Debt"]["reason"]


def _v3_family_reason_empty(r):
    r["families"]["Revenue"]["reason"] = ""


def _v3_filer_reason_empty(r):
    r["filers"][0]["reason"] = ""


def _v4_new_family_no_label(r):
    del r["families"][IPRD_FAMILY]["label"]


def _v4_new_family_empty_label(r):
    r["families"][IPRD_FAMILY]["label"] = ""


def _v4_new_family_no_primary(r):
    r["families"][IPRD_FAMILY]["primaries"] = []
    r["families"][IPRD_FAMILY]["components"] = ["us-gaap:ResearchAndDevelopmentInProcessX"]


def _v4_new_family_bad_name(r):
    r["families"]["acquiredIprd"] = r["families"].pop(IPRD_FAMILY)


def _v4_label_on_existing(r):
    r["families"]["Revenue"]["label"] = "Revenue"


def _v5_empty_entry(r):
    r["families"]["OperatingIncome"] = {"reason": "nothing added"}


def _v5_empty_lists(r):
    r["families"]["OperatingIncome"] = {"primaries": [], "components": [], "reason": "x"}


def _v6_base_primary_again(r):
    r["families"]["Revenue"]["primaries"].append(REVS)


def _v6_twice_in_file(r):
    r["families"]["Revenue"]["primaries"].append(INCL)


def _v6_base_component_again(r):
    r["families"]["Debt"]["components"].append("us-gaap:DebtCurrent")


def _v6_base_component_as_primary(r):
    r["families"]["Debt"]["primaries"] = ["us-gaap:DebtCurrent"]


def _v7_same_rank_two_families(r):
    # Revenue and OperatingIncome have no base components: both land at rank 50.
    r["families"]["Revenue"]["components"] = ["us-gaap:RevenueFromRelatedParties"]
    r["families"]["OperatingIncome"] = {
        "components": ["us-gaap:RevenueFromRelatedParties"], "reason": "x"}


def _v8_pretax_as_operating_income(r):
    r["families"]["OperatingIncome"] = {"primaries": [PRETAX], "reason": "no op-income line"}


def _v9_primaries_reach_band(r):
    # 3 base + 47 added = 50 primaries: rank 50 is the component band.
    r["families"]["Revenue"]["primaries"] = [f"us-gaap:RevenueX{i}" for i in range(47)]


def _v9_components_reach_ifrs(r):
    # 1 base + 50 added = 51 components: rank 100 is the IFRS band.
    r["families"]["Debt"]["components"] = [f"us-gaap:DebtX{i}" for i in range(50)]


def _v10_short_cik(r):
    r["filers"][0]["cik"] = "104169"


def _v10_int_cik(r):
    r["filers"][0]["cik"] = 104169


def _v10_unknown_family(r):
    r["filers"][0]["family"] = "Revenu"


def _v10_empty_preferred(r):
    r["filers"][0]["preferred"] = []


def _v10_repeated_preferred(r):
    r["filers"][0]["preferred"] = [REVS, REVS]


def _v10_component_promoted(r):
    r["filers"][0]["family"] = "Debt"
    r["filers"][0]["preferred"] = ["us-gaap:DebtCurrent"]


def _v10_not_in_family(r):
    r["filers"][0]["preferred"] = ["us-gaap:OperatingIncomeLoss"]


def _v10_duplicate_entry(r):
    r["filers"].append(dict(r["filers"][0], preferred=[EXCL]))


MUTATIONS = [
    _v1_top_key, _v1_version, _v1_family_key, _v1_filer_extra_key, _v1_filer_missing_family,
    _v2_custom_prefix, _v2_no_prefix, _v2_bad_local_name,
    _v3_family_reason_missing, _v3_family_reason_empty, _v3_filer_reason_empty,
    _v4_new_family_no_label, _v4_new_family_empty_label, _v4_new_family_no_primary,
    _v4_new_family_bad_name, _v4_label_on_existing,
    _v5_empty_entry, _v5_empty_lists,
    _v6_base_primary_again, _v6_twice_in_file, _v6_base_component_again,
    _v6_base_component_as_primary,
    _v7_same_rank_two_families,
    _v8_pretax_as_operating_income,
    _v9_primaries_reach_band, _v9_components_reach_ifrs,
    _v10_short_cik, _v10_int_cik, _v10_unknown_family, _v10_empty_preferred,
    _v10_repeated_preferred, _v10_component_promoted, _v10_not_in_family,
    _v10_duplicate_entry,
]


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_valid_file_normalizes():
    # oracle: Requirement 2 applied by hand to VALID (VALID_NORMALIZED written out)
    assert _validate(_valid()) == VALID_NORMALIZED
    assert _validate({}) == EMPTY
    assert _validate({"version": 1}) == EMPTY


@pytest.mark.criterion("C1")
def test_band_boundaries_are_valid():
    # oracle: V9 by hand: 3 + 46 = 49 primaries end at rank 49; 1 + 49 = 50 components
    # end at rank 99. Both stay inside their bands.
    raw = _valid()
    added_p = [f"us-gaap:RevenueX{i}" for i in range(46)]
    added_c = [f"us-gaap:DebtX{i}" for i in range(49)]
    raw["families"]["Revenue"]["primaries"] = added_p
    raw["families"]["Debt"]["components"] = added_c
    out = _validate(raw)
    assert out["families"]["Revenue"]["primaries"] == added_p
    assert out["families"]["Debt"]["components"] == added_c


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("mutate", MUTATIONS, ids=lambda f: f.__name__)
def test_each_broken_rule_raises(mutate):
    # oracle: Requirement 2, rules V1-V10; each mutation breaks exactly one rule
    raw = _valid()
    mutate(raw)
    with pytest.raises(ValueError):
        _validate(raw)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_filer_order_and_repetition_do_not_matter():
    # oracle: Requirement 2 (filers become a map keyed by (cik, family))
    raw = _valid()
    raw["filers"].append(
        {"cik": COST, "family": "Revenue", "preferred": [REVS, EXCL], "reason": "total"})
    flipped = copy.deepcopy(raw)
    flipped["filers"].reverse()
    expected = copy.deepcopy(VALID_NORMALIZED)
    expected["filers"][(COST, "Revenue")] = [REVS, EXCL]
    assert _validate(raw) == expected
    assert _validate(flipped) == expected
    assert _validate(copy.deepcopy(raw)) == _validate(raw)


# ---------------------------------------------------- generated valid files (C1, C2)
CIKS = st.integers(min_value=1, max_value=9_999_999_999).map(lambda n: f"{n:010d}")


@st.composite
def bases_and_files(draw):
    """Base families with unique QNames, plus a valid override file for them."""
    n = draw(st.integers(min_value=2, max_value=4))
    base_p = {f"Fam{i}": [f"us-gaap:F{i}P{j}" for j in range(draw(st.integers(1, 3)))]
              for i in range(n)}
    base_c = {f"Fam{i}": [f"us-gaap:F{i}C{j}" for j in range(draw(st.integers(1, 2)))]
              for i in range(n)}
    families: dict = {}
    for i in range(n):
        if not draw(st.booleans()):
            continue
        ap = [f"us-gaap:F{i}AP{j}" for j in range(draw(st.integers(0, 2)))]
        # Added components get distinct ranks per family (50 + base count + j), and their
        # names are unique, so V7 never fires.
        ac = [f"us-gaap:F{i}AC{j}" for j in range(draw(st.integers(0 if ap else 1, 2)))]
        families[f"Fam{i}"] = {"primaries": ap, "components": ac, "reason": "reviewed"}
    for k in range(draw(st.integers(0, 2))):
        families[f"NewFam{k}"] = {
            "label": f"New family {k}",
            "primaries": [f"us-gaap:N{k}P{j}" for j in range(draw(st.integers(1, 2)))],
            "reason": "reviewed",
        }
    merged_p = {f: list(q) for f, q in base_p.items()}
    for f, e in families.items():
        merged_p.setdefault(f, []).extend(e["primaries"])
    keys = draw(st.lists(st.tuples(CIKS, st.sampled_from(sorted(merged_p))),
                         max_size=4, unique=True))
    filers = []
    for cik, fam in keys:
        pref = draw(st.lists(st.sampled_from(merged_p[fam]), min_size=1, unique=True))
        filers.append({"cik": cik, "family": fam, "preferred": pref, "reason": "reviewed"})
    raw = {"version": 1, "families": families, "filers": filers}
    return base_p, base_c, raw


def _normalized_oracle(base_p, raw):
    """Requirement 2's normalized form, written out for a valid generated file."""
    fams = {}
    for f, e in raw["families"].items():
        fams[f] = {
            "label": None if f in base_p else e["label"],
            "primaries": list(e.get("primaries", [])),
            "components": list(e.get("components", [])),
        }
    filers = {(e["cik"], e["family"]): list(e["preferred"]) for e in raw["filers"]}
    return {"families": fams, "filers": filers}


@st.composite
def broken_files(draw):
    """A generated valid file with exactly one rule broken."""
    base_p, base_c, raw = draw(bases_and_files())
    fam = draw(st.sampled_from(sorted(base_p)))
    kind = draw(st.sampled_from([
        "top_key", "version", "custom_prefix", "dup_base_primary", "base_component_promoted",
        "short_cik", "missing_reason", "new_family_no_label", "primary_of_other_family",
    ]))
    other = next(f for f in sorted(base_p) if f != fam)
    entry = raw["families"].setdefault(fam, {"primaries": [], "components": [],
                                             "reason": "reviewed"})
    if kind == "top_key":
        raw["extra"] = 1
    elif kind == "version":
        raw["version"] = 0
    elif kind == "custom_prefix":
        entry["primaries"] = [*entry["primaries"], "acme:Sales"]
    elif kind == "dup_base_primary":
        entry["primaries"] = [*entry["primaries"], base_p[fam][0]]
    elif kind == "base_component_promoted":
        # CIKS never draws 0, so this (cik, family) is new and only the component is wrong.
        raw["filers"].append({"cik": "0000000000", "family": fam,
                              "preferred": [base_c[fam][0]], "reason": "reviewed"})
    elif kind == "short_cik":
        raw["filers"].append({"cik": "12345", "family": fam,
                              "preferred": [base_p[fam][0]], "reason": "reviewed"})
    elif kind == "missing_reason":
        del entry["reason"]
    elif kind == "new_family_no_label":
        raw["families"]["BrandNewFamily"] = {"primaries": ["us-gaap:BrandNewQ"],
                                             "reason": "reviewed"}
    else:  # primary_of_other_family
        entry["primaries"] = [*entry["primaries"], base_p[other][0]]
    if not entry["primaries"] and not entry["components"] and kind != "missing_reason":
        entry["components"] = [f"us-gaap:Pad{fam}"]
    return base_p, base_c, raw


@pytest.mark.criterion("C1")
@given(bases_and_files())
def test_generated_valid_files_normalize(case):
    # oracle: Requirement 2's normalized form, written out in _normalized_oracle
    base_p, base_c, raw = case
    expected = _normalized_oracle(base_p, raw)
    assert validate_family_overrides(raw, base_p, base_c) == expected
    flipped = copy.deepcopy(raw)
    flipped["filers"].reverse()
    assert validate_family_overrides(flipped, base_p, base_c) == expected


@pytest.mark.criterion("C1")
@given(broken_files())
def test_generated_files_with_one_broken_rule_raise(case):
    # oracle: Requirement 2; each generated mutation breaks one of V1-V10
    base_p, base_c, raw = case
    with pytest.raises(ValueError):
        validate_family_overrides(raw, base_p, base_c)


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_merge_point_case():
    # oracle: Requirement 3 by hand on the C1 base and VALID
    base_p, base_c = copy.deepcopy(BASE_P), copy.deepcopy(BASE_C)
    merged_p, merged_c = merge_family_overrides(base_p, base_c, VALID_NORMALIZED)
    assert merged_p == {
        "Revenue": [EXCL, REVS, SRN, INCL],
        "OperatingIncome": ["us-gaap:OperatingIncomeLoss"],
        "PretaxIncome": [PRETAX],
        "Debt": ["us-gaap:LongTermDebt"],
        IPRD_FAMILY: [IPRD],
    }
    assert list(merged_p) == ["Revenue", "OperatingIncome", "PretaxIncome", "Debt", IPRD_FAMILY]
    assert merged_c.get("Debt") == ["us-gaap:DebtCurrent", "us-gaap:ShortTermBorrowings"]
    assert merged_c.get("Revenue", []) == []
    assert merged_c.get("OperatingIncome", []) == []
    assert (base_p, base_c) == (BASE_P, BASE_C)  # inputs not modified


def _ranks(primaries, components):
    return dict(family_ranking(primaries, components, []))


def _winner(ranking, reported):
    """The filer's rank-1 QName: filer_ranking's override winner, else the global first."""
    filer = filer_ranking(ranking, reported)
    if filer is not None:
        return filer[0][0]
    eligible = [q for q, r in sorted(ranking, key=lambda qr: qr[1])
                if r < COMPONENT_BASE or r >= IFRS_BASE]
    return eligible[0] if any(q in reported for q in eligible) else None


def _assert_regression(base_p, base_c, merged_p, merged_c, touched):
    for fam, prims in base_p.items():
        comps = base_c.get(fam, [])
        before = _ranks(prims, comps)
        after = _ranks(merged_p[fam], merged_c.get(fam, []))
        for q, r in before.items():
            assert after[q] == r, (fam, q)
        if fam not in touched:
            assert merged_p[fam] == list(prims)
            assert list(merged_c.get(fam, [])) == list(comps)
        # A filer reporting any base primaries keeps its rank-1 QName.
        for k in range(1, len(prims) + 1):
            for reported in ({*prims[:k]}, {prims[k - 1]}):
                assert _winner(list(after.items()), reported) == _winner(
                    list(before.items()), reported), (fam, reported)


@pytest.mark.criterion("C2")
@given(bases_and_files())
def test_merge_never_moves_a_base_qname(case):
    # oracle: Requirement 3 (additions are appended, so base ranks and winners stay)
    base_p, base_c, raw = case
    normalized = _normalized_oracle(base_p, raw)
    snapshot = copy.deepcopy((base_p, base_c, normalized))
    merged_p, merged_c = merge_family_overrides(base_p, base_c, normalized)
    assert snapshot == (base_p, base_c, normalized)
    _assert_regression(base_p, base_c, merged_p, merged_c, set(raw["families"]))
    for fam, e in normalized["families"].items():
        assert merged_p[fam] == [*base_p.get(fam, []), *e["primaries"]]
        assert merged_c.get(fam, []) == [*base_c.get(fam, []), *e["components"]]
    new = [f for f in normalized["families"] if f not in base_p]
    assert list(merged_p) == [*base_p, *new]
    assert merge_family_overrides(base_p, base_c, normalized) == (merged_p, merged_c)


# --------------------------------------------------------------------------- C3
# The production global Revenue ranking (concept_mapping, 2026-10-29).
REVENUE_GLOBAL = [
    (EXCL, 1), (REVS, 2), (SRN, 3), ("us-gaap:SalesRevenueGoodsNet", 4),
    ("us-gaap:SalesRevenueServicesNet", 5), ("ifrs-full:Revenue", 100),
]
DA_GLOBAL = [
    ("us-gaap:DepreciationDepletionAndAmortization", 1),
    ("us-gaap:DepreciationAndAmortization", 2),
    ("us-gaap:DepreciationAmortizationAndOther", 3),
    ("us-gaap:Depreciation", 50),
    ("us-gaap:AmortizationOfIntangibleAssets", 51),
]


@pytest.mark.criterion("C3")
def test_reviewed_filer_ranking_point_cases():
    # oracle: Requirement 4 by hand; the Walmart result is the ranking filer_ranking
    # wrote for NVIDIA and Tesla (auto_override rows, 2026-10-29)
    assert reviewed_filer_ranking(REVENUE_GLOBAL, [REVS]) == [
        (REVS, 1), (EXCL, 2), (SRN, 3), ("us-gaap:SalesRevenueGoodsNet", 4),
        ("us-gaap:SalesRevenueServicesNet", 5), ("ifrs-full:Revenue", 6),
    ]
    assert reviewed_filer_ranking(REVENUE_GLOBAL, [REVS, "ifrs-full:Revenue"]) == [
        (REVS, 1), ("ifrs-full:Revenue", 2), (EXCL, 3), (SRN, 4),
        ("us-gaap:SalesRevenueGoodsNet", 5), ("us-gaap:SalesRevenueServicesNet", 6),
    ]
    assert reviewed_filer_ranking(DA_GLOBAL, ["us-gaap:DepreciationAndAmortization"]) == [
        ("us-gaap:DepreciationAndAmortization", 1),
        ("us-gaap:DepreciationDepletionAndAmortization", 2),
        ("us-gaap:DepreciationAmortizationAndOther", 3),
        ("us-gaap:Depreciation", 50),
        ("us-gaap:AmortizationOfIntangibleAssets", 51),
    ]


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("preferred", [
    [], [REVS, REVS], ["us-gaap:OperatingIncomeLoss"],
], ids=["empty", "repeated", "not-in-family"])
def test_reviewed_filer_ranking_refuses(preferred):
    # oracle: Requirement 4 (ValueError cases)
    with pytest.raises(ValueError):
        reviewed_filer_ranking(REVENUE_GLOBAL, preferred)


@pytest.mark.criterion("C3")
def test_reviewed_filer_ranking_never_promotes_a_component():
    # oracle: Requirement 4; concept-rank-integrity (components are never promoted)
    with pytest.raises(ValueError):
        reviewed_filer_ranking(DA_GLOBAL, ["us-gaap:Depreciation"])


@st.composite
def rankings_and_preferred(draw):
    n_prim = draw(st.integers(1, 6))
    n_comp = draw(st.integers(0, 3))
    n_ifrs = draw(st.integers(0, 2))
    ranking = [(f"us-gaap:P{i}", i + 1) for i in range(n_prim)]
    ranking += [(f"us-gaap:C{i}", COMPONENT_BASE + i) for i in range(n_comp)]
    ranking += [(f"ifrs-full:I{i}", IFRS_BASE + i) for i in range(n_ifrs)]
    eligible = [q for q, r in ranking if r < COMPONENT_BASE or r >= IFRS_BASE]
    preferred = draw(st.lists(st.sampled_from(eligible), min_size=1, unique=True))
    shuffled = draw(st.permutations(ranking))
    return ranking, list(shuffled), preferred


@pytest.mark.criterion("C3")
@given(rankings_and_preferred())
def test_reviewed_filer_ranking_properties(case):
    # oracle: Requirement 4 and concept-rank-integrity C2/C3, checked as invariants
    ranking, shuffled, preferred = case
    out = reviewed_filer_ranking(ranking, preferred)
    assert sorted(q for q, _ in out) == sorted(q for q, _ in ranking)
    ranks = [r for _, r in out]
    assert len(set(ranks)) == len(ranks)
    assert out[: len(preferred)] == [(q, i) for i, q in enumerate(preferred, start=1)]
    for q, r in ranking:
        if COMPONENT_BASE <= r < IFRS_BASE:
            assert (q, r) in out
    eligible_order = [q for q, r in ranking if r < COMPONENT_BASE or r >= IFRS_BASE]
    rest = [q for q in eligible_order if q not in preferred]
    assert [q for q, _ in out[len(preferred): len(preferred) + len(rest)]] == rest
    assert reviewed_filer_ranking(shuffled, preferred) == out
    if len(preferred) == 1 and preferred[0] != eligible_order[0]:
        assert out == filer_ranking(ranking, set(preferred))


# --------------------------------------------------------------------------- C4
SYNTHETIC_YAML = f"""
version: 1
families:
  Revenue:
    primaries: [{INCL}]
    reason: total revenue gross of assessed taxes
filers:
  - cik: "{WMT}"
    family: Revenue
    preferred: [{REVS}]
    reason: total revenues, not net sales
"""


@pytest.mark.criterion("C4")
def test_committed_file_has_the_retail_overrides():
    # oracle: SEC EDGAR CIKs (Walmart 104169, Costco 909832; corpus CIKs in spec Notes);
    # spec Requirement 5
    out = load_family_overrides()
    filers = out["filers"]
    assert filers[(WMT, "Revenue")][0] == REVS
    assert filers[(COST, "Revenue")][0] == REVS
    # concept-key-integrity requirement 6: the Vistra and Constellation Revenue entries are
    # the only pre-wave filers the file may name.
    assert {key for key in filers if key[0] in CORPUS_CIKS} <= PRE_WAVE_ALLOWED
    assert all(re.fullmatch(r"\d{10}", cik) for cik, _ in filers)
    assert set(out["families"]) <= set(FAMILY_ANCHOR_QNAMES)


@pytest.mark.criterion("C4")
def test_load_parses_and_validates_a_file(tmp_path):
    # oracle: Requirement 2 by hand on the synthetic file (expected form written out)
    path = tmp_path / "concept_family_overrides.yaml"
    path.write_text(SYNTHETIC_YAML, encoding="utf-8")
    assert yaml.safe_load(SYNTHETIC_YAML)["version"] == 1
    assert load_family_overrides(path) == {
        "families": {"Revenue": {"label": None, "primaries": [INCL], "components": []}},
        "filers": {(WMT, "Revenue"): [REVS]},
    }
    bad = tmp_path / "bad.yaml"
    bad.write_text(SYNTHETIC_YAML.replace(f'"{WMT}"', '"104169"'), encoding="utf-8")
    with pytest.raises(ValueError):
        load_family_overrides(bad)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_committed_file_leaves_existing_rankings_alone():
    # oracle: Requirement 3 (append-only merge) on the real file; the production base
    # families are the hardcoded dicts
    out = load_family_overrides()
    merged_p, merged_c = merge_family_overrides(
        FAMILY_ANCHOR_QNAMES, FAMILY_COMPONENT_QNAMES, out)
    assert set(merged_p) == set(FAMILY_ANCHOR_QNAMES)
    _assert_regression(FAMILY_ANCHOR_QNAMES, FAMILY_COMPONENT_QNAMES, merged_p, merged_c,
                       set(out["families"]))
    for (cik, fam), preferred in out["filers"].items():
        # concept-key-integrity requirement 6 allows exactly these pre-wave entries.
        assert cik not in CORPUS_CIKS or (cik, fam) in PRE_WAVE_ALLOWED
        assert set(preferred) <= set(merged_p[fam])
