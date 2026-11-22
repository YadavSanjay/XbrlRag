"""Spec tests for ns-expand-rank-sync: every namespace-expanded concept_mapping row carries
its base row's rank and deprecation flag, and drift fails loud
(docs/specs/ns-expand-rank-sync.md).

Pure: concept_mapping rows and qname pairs are written by hand or generated; no DB.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.ingest.layer0_populator import (
    ConceptMappingRecord,
    NsExpandedDriftError,
    assert_ns_expanded_in_sync,
    expand_concept_mapping,
    ns_expanded_drift,
)

EXP = "ns_expanded"
TSMC = "0001046179"

GAAP_VERSIONS = ("2021-01-31", "2022", "2023", "2024", "2025", "2026")
GAAP_NS = tuple(f"http://fasb.org/us-gaap/{v}" for v in GAAP_VERSIONS)
IFRS_NS = tuple(f"https://xbrl.ifrs.org/taxonomy/{d}/ifrs-full"
                for d in ("2022-03-24", "2023-03-23", "2024-03-27", "2025-03-27"))

# Requirement 1's table (the rule of concept_mapping_ns_expand.sql), written out here as the
# oracle for the generated cases.
NS_BASES = {
    "us-gaap": ("http://fasb.org/us-gaap/",),
    "srt": ("http://fasb.org/srt/",),
    "dei": ("http://xbrl.sec.gov/dei/",),
    "ifrs-full": ("http://xbrl.ifrs.org/taxonomy/", "https://xbrl.ifrs.org/taxonomy/"),
}


def base(family, qname, rank, cik=None, dep=False, source="hifi_kpi"):
    return ConceptMappingRecord(family, qname, rank, cik, dep, source)


def copy(family, ns, local, rank, cik=None, dep=False):
    return ConceptMappingRecord(family, f"{ns}:{local}", rank, cik, dep, EXP)


def key(r):
    return (r.concept_family, r.qname, r.cik_override or "")


def reference_copies(rows, known):
    """Requirement 1 applied directly: one copy per (base row, matching known QName)."""
    out = []
    for b in rows:
        if b.source == EXP or b.qname.startswith(("http://", "https://")):
            continue
        prefix, _, local = b.qname.partition(":")
        for ns, ln in sorted(set(known)):
            if ln == local and ns.startswith(NS_BASES.get(prefix, ())):
                out.append(copy(b.concept_family, ns, local, b.preference_rank,
                                b.cik_override, b.is_deprecated))
    return sorted(out, key=key)


# --- the 2026-11-05 state (read-only queries on xbrl_db; xbrl_heldout is the same) -----------
G = "us-gaap:"
IF = "ifrs-full:"

BASE_ROWS = [
    base("SharesOutstanding", G + "CommonStockSharesOutstanding", 1),
    base("SharesOutstanding", G + "CommonStockSharesIssued", 50),
    base("AccountsPayable", G + "AccountsPayableCurrent", 1),
    base("AccountsPayable", G + "AccountsPayableTradeCurrent", 2),
    base("AccountsPayable", G + "AccountsPayableAndAccruedLiabilitiesCurrent", 50),
    base("OtherNonOperatingIncome", G + "OtherNonoperatingIncomeExpense", 1),
    base("OtherNonOperatingIncome", G + "NonoperatingIncomeExpense", 2),
    base("OtherNonOperatingIncome", G + "OtherNonoperatingIncome", 3),
    base("OtherNonOperatingIncome", G + "OtherNonoperatingExpense", 4),
    base("NetIncome", G + "ProfitLoss", 2),
    base("NetIncome", IF + "ProfitLoss", 100, source="ifrs"),
    base("NetIncome", IF + "ProfitLoss", 1, cik=TSMC, source="auto_override"),
    base("NetIncome", G + "ProfitLoss", 3, cik=TSMC, source="auto_override"),
]

# (family, local, base rank, rank found on the expanded copies) for the us-gaap base rows.
GAAP_FOUND = [
    ("SharesOutstanding", "CommonStockSharesOutstanding", 1, 1),
    ("SharesOutstanding", "CommonStockSharesIssued", 50, 2),
    ("AccountsPayable", "AccountsPayableCurrent", 1, 1),
    ("AccountsPayable", "AccountsPayableTradeCurrent", 2, 2),
    ("AccountsPayable", "AccountsPayableAndAccruedLiabilitiesCurrent", 50, 2),
    ("OtherNonOperatingIncome", "OtherNonoperatingIncomeExpense", 1, 2),
    ("OtherNonOperatingIncome", "NonoperatingIncomeExpense", 2, 1),
    ("OtherNonOperatingIncome", "OtherNonoperatingIncome", 3, 3),
    ("OtherNonOperatingIncome", "OtherNonoperatingExpense", 4, 4),
]
DRIFTED_LOCALS = {"CommonStockSharesIssued", "AccountsPayableAndAccruedLiabilitiesCurrent",
                  "OtherNonoperatingIncomeExpense", "NonoperatingIncomeExpense"}

# NetIncome ProfitLoss copies, all in sync: (namespaces, cik, rank).
PROFIT_LOSS = [(GAAP_NS, None, 2), (GAAP_NS, TSMC, 3), (IFRS_NS, None, 100), (IFRS_NS, TSMC, 1)]


def found_copies():
    """The expanded rows as found: 24 of them carry the stale rank."""
    rows = [copy(f, ns, local, found) for f, local, _, found in GAAP_FOUND for ns in GAAP_NS]
    rows += [copy("NetIncome", ns, "ProfitLoss", r, cik)
             for nss, cik, r in PROFIT_LOSS for ns in nss]
    return rows


def synced_copies():
    """The expanded rows as they should be: every copy at its base row's rank."""
    rows = [copy(f, ns, local, rank) for f, local, rank, _ in GAAP_FOUND for ns in GAAP_NS]
    rows += [copy("NetIncome", ns, "ProfitLoss", r, cik)
             for nss, cik, r in PROFIT_LOSS for ns in nss]
    return rows


def known_qnames():
    pairs = [(ns, local) for _, local, _, _ in GAAP_FOUND for ns in GAAP_NS]
    pairs += [(ns, "ProfitLoss") for ns in GAAP_NS + IFRS_NS]
    return pairs


def expected_drift():
    """The 24 drifted copies, each with its base row, from the hand-written table."""
    by_qname = {b.qname: b for b in BASE_ROWS if b.cik_override is None}
    pairs = [(copy(f, ns, local, found), by_qname[G + local])
             for f, local, _, found in GAAP_FOUND if local in DRIFTED_LOCALS for ns in GAAP_NS]
    return sorted(pairs, key=lambda p: key(p[0]))


# --- generated corpora -------------------------------------------------------------------------
FAMILIES = ("Alpha", "Beta", "Gamma")
LOCALS = ("X", "Y", "Z")
CIKS = (None, "0000000001", "0000000002")
PREFIXES = ("us-gaap", "srt", "dei", "ifrs-full", "country", "abc")
SOURCES = ("hifi_kpi", "ifrs", "auto_override", "reviewed_override")
NAMESPACES = ("http://fasb.org/us-gaap/2024", "http://fasb.org/us-gaap/2025",
              "http://fasb.org/srt/2025", "http://fasb.org/srt-types/2025",
              "http://xbrl.sec.gov/dei/2025", "http://xbrl.sec.gov/country/2025",
              "https://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full",
              "http://xbrl.ifrs.org/taxonomy/2021-03-24/ifrs-full",
              "http://www.example.com/abc/2025")

row_parts = st.tuples(st.sampled_from(FAMILIES), st.sampled_from(PREFIXES),
                      st.sampled_from(LOCALS), st.sampled_from(CIKS),
                      st.integers(min_value=1, max_value=120), st.booleans(),
                      st.sampled_from(SOURCES))


@st.composite
def corpora(draw):
    """Base rows with distinct keys, and known QNames; the first row always has a copy."""
    first = draw(row_parts.map(lambda t: (t[0], "us-gaap") + t[2:]))
    rest = draw(st.lists(row_parts, max_size=12))
    rows, seen = [], set()
    for fam, prefix, local, cik, rank, dep, source in [first] + rest:
        k = (fam, f"{prefix}:{local}", cik or "")
        if k not in seen:
            seen.add(k)
            rows.append(base(fam, f"{prefix}:{local}", rank, cik, dep, source))
    known = set(draw(st.lists(st.tuples(st.sampled_from(NAMESPACES), st.sampled_from(LOCALS)),
                              max_size=15)))
    known.add(("http://fasb.org/us-gaap/2025", first[2]))
    return rows, sorted(known)


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_the_four_drifted_rows_expand_at_their_base_ranks():
    # oracle: the base rows, written by hand (50, 50, 1, 2), copied to the six us-gaap versions
    drifted_base = [b for b in BASE_ROWS if b.qname.split(":")[1] in DRIFTED_LOCALS]
    stale = [r for r in found_copies() if r.qname.split(":")[-1] in DRIFTED_LOCALS]
    known = [(ns, local) for local in sorted(DRIFTED_LOCALS) for ns in GAAP_NS]
    want = sorted([copy("SharesOutstanding", ns, "CommonStockSharesIssued", 50) for ns in GAAP_NS]
                  + [copy("AccountsPayable", ns, "AccountsPayableAndAccruedLiabilitiesCurrent", 50)
                     for ns in GAAP_NS]
                  + [copy("OtherNonOperatingIncome", ns, "OtherNonoperatingIncomeExpense", 1)
                     for ns in GAAP_NS]
                  + [copy("OtherNonOperatingIncome", ns, "NonoperatingIncomeExpense", 2)
                     for ns in GAAP_NS], key=key)
    assert len(want) == 24
    assert expand_concept_mapping(drifted_base + stale, known) == want


@pytest.mark.criterion("C1")
def test_profit_loss_copies_take_their_own_taxonomy_and_filer_rank():
    # oracle: NetIncome base rows by hand — global us-gaap 2, IFRS 100; TSMC IFRS 1, us-gaap 3
    pl_base = [b for b in BASE_ROWS if b.concept_family == "NetIncome"]
    known = [(ns, "ProfitLoss") for ns in GAAP_NS + IFRS_NS]
    want = sorted([copy("NetIncome", ns, "ProfitLoss", 2) for ns in GAAP_NS]
                  + [copy("NetIncome", ns, "ProfitLoss", 3, TSMC) for ns in GAAP_NS]
                  + [copy("NetIncome", ns, "ProfitLoss", 100) for ns in IFRS_NS]
                  + [copy("NetIncome", ns, "ProfitLoss", 1, TSMC) for ns in IFRS_NS], key=key)
    assert expand_concept_mapping(pl_base, known) == want


@pytest.mark.criterion("C1")
def test_only_matching_taxonomies_and_known_qnames_expand():
    # oracle: requirement 1's prefix table, applied by hand to each row below
    rows = [
        base("F", "srt:Foo", 5),
        base("F", G + "Bar", 6),
        base("F", G + "Absent", 7),
        base("F", "dei:EntityNumberOfEmployees", 8),
        base("F", IF + "Revenue", 100, source="ifrs"),
        base("F", "http://fasb.org/us-gaap/2025:Baz", 9, source="manual"),
        base("F", "country:US", 10),
        base("F", "abc:Qux", 11),
        copy("Gone", "http://fasb.org/us-gaap/2025", "Gone", 1),
    ]
    known = [
        ("http://fasb.org/srt/2025", "Foo"), ("http://fasb.org/srt-types/2025", "Foo"),
        ("http://fasb.org/us-gaap/2025", "Bar"), (IFRS_NS[3], "Bar"),
        ("http://xbrl.sec.gov/dei/2021q4", "EntityNumberOfEmployees"),
        ("http://xbrl.ifrs.org/taxonomy/2021-03-24/ifrs-full", "Revenue"),
        ("http://fasb.org/us-gaap/2025", "Baz"),
        ("http://xbrl.sec.gov/country/2025", "US"),
        ("http://www.example.com/abc/2025", "Qux"),
        ("http://fasb.org/us-gaap/2025", "Gone"),
        ("http://fasb.org/us-gaap/2025", "Bar"),  # a repeated pair gives one row
    ]
    want = sorted([
        copy("F", "http://fasb.org/srt/2025", "Foo", 5),
        copy("F", "http://fasb.org/us-gaap/2025", "Bar", 6),
        copy("F", "http://xbrl.sec.gov/dei/2021q4", "EntityNumberOfEmployees", 8),
        copy("F", "http://xbrl.ifrs.org/taxonomy/2021-03-24/ifrs-full", "Revenue", 100),
    ], key=key)
    assert expand_concept_mapping(rows, known) == want


@pytest.mark.criterion("C1")
def test_a_deprecated_base_row_gives_deprecated_copies():
    # oracle: requirement 1 — each copy carries its base row's is_deprecated
    rows = [base("Revenue", G + "SalesRevenueNet", 4, dep=True),
            base("Revenue", G + "Revenues", 1, cik="0000000001", source="reviewed_override")]
    known = [(ns, local) for local in ("SalesRevenueNet", "Revenues") for ns in GAAP_NS[:2]]
    want = sorted([copy("Revenue", ns, "SalesRevenueNet", 4, dep=True) for ns in GAAP_NS[:2]]
                  + [copy("Revenue", ns, "Revenues", 1, "0000000001") for ns in GAAP_NS[:2]],
                  key=key)
    assert expand_concept_mapping(rows, known) == want


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(st.lists(st.integers(min_value=1, max_value=120), min_size=24, max_size=24))
def test_stale_copies_of_any_rank_do_not_change_the_expansion(ranks):
    # oracle: the same hand-written ranks (50, 50, 1, 2); input ns_expanded rows are ignored
    drifted_base = [b for b in BASE_ROWS if b.qname.split(":")[1] in DRIFTED_LOCALS]
    stale = [ConceptMappingRecord(r.concept_family, r.qname, n, None, False, EXP)
             for (r, _), n in zip(expected_drift(), ranks, strict=True)]
    known = [(ns, local) for local in sorted(DRIFTED_LOCALS) for ns in GAAP_NS]
    got = expand_concept_mapping(drifted_base + stale, known)
    assert len(got) == 24
    assert {(r.qname.split(":")[-1], r.preference_rank) for r in got} == {
        ("CommonStockSharesIssued", 50), ("AccountsPayableAndAccruedLiabilitiesCurrent", 50),
        ("OtherNonoperatingIncomeExpense", 1), ("NonoperatingIncomeExpense", 2)}


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_expanding_the_found_state_again_gives_the_synced_copies():
    # oracle: the hand-written base ranks; the found (stale) copies in the input are ignored
    got = expand_concept_mapping(BASE_ROWS + found_copies(), known_qnames())
    assert got == sorted(synced_copies(), key=key)
    assert expand_concept_mapping(BASE_ROWS + got, known_qnames()) == got


@pytest.mark.criterion("C2")
@given(corpora())
def test_every_copy_carries_its_base_row(corpus):
    # oracle: requirement 1 applied directly in the test (reference_copies)
    rows, known = corpus
    got = expand_concept_mapping(rows, known)
    assert got == reference_copies(rows, known)
    assert len({key(r) for r in got}) == len(got) >= 1
    assert all(r.source == EXP for r in got)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(corpora(), st.integers(min_value=1, max_value=30))
def test_expansion_is_idempotent_and_ignores_stale_copies(corpus, shift):
    # oracle: requirement 1 (reference_copies); ns_expanded input rows never reach the output
    rows, known = corpus
    want = reference_copies(rows, known)
    stale = [ConceptMappingRecord(r.concept_family, r.qname, r.preference_rank + shift,
                                  r.cik_override, not r.is_deprecated, EXP) for r in want]
    assert expand_concept_mapping(rows + want, known) == want
    assert expand_concept_mapping(stale + rows, known) == want


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(corpora())
def test_expansion_does_not_depend_on_input_order(corpus):
    # oracle: requirement 1 (reference_copies) is order-free; the output is sorted by key
    rows, known = corpus
    assert expand_concept_mapping(rows[::-1], known[::-1]) == reference_copies(rows, known)


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_drift_on_the_found_state_is_the_24_stale_copies():
    # oracle: the base rows by hand; IFRS copies at 100 / TSMC 1 and ProfitLoss 2 / 3 are in sync
    assert ns_expanded_drift(BASE_ROWS + found_copies()) == expected_drift()


@pytest.mark.criterion("C3")
def test_drift_matches_on_filer_and_taxonomy_and_flags_orphans():
    # oracle: the base rows by hand — TSMC's IFRS rank is 1, the global IFRS rank 100,
    # CommonStockSharesOutstanding is not deprecated, and family "Gone" has no base row
    tsmc_at_global = copy("NetIncome", IFRS_NS[3], "ProfitLoss", 100, TSMC)
    global_at_tsmc = copy("NetIncome", IFRS_NS[0], "ProfitLoss", 1)
    deprecated = copy("SharesOutstanding", GAAP_NS[4], "CommonStockSharesOutstanding", 1, dep=True)
    replaced = {key(r) for r in (tsmc_at_global, global_at_tsmc, deprecated)}
    orphan_family = copy("Gone", GAAP_NS[4], "Gone", 1)
    orphan_ns = copy("SharesOutstanding", "http://www.example.com/abc/2025",
                     "CommonStockSharesOutstanding", 1)
    copies = [r for r in synced_copies() if key(r) not in replaced]
    copies += [tsmc_at_global, global_at_tsmc, deprecated, orphan_family, orphan_ns]
    by = {(b.qname, b.cik_override): b for b in BASE_ROWS}
    want = sorted([
        (tsmc_at_global, by[(IF + "ProfitLoss", TSMC)]),
        (global_at_tsmc, by[(IF + "ProfitLoss", None)]),
        (deprecated, by[(G + "CommonStockSharesOutstanding", None)]),
        (orphan_family, None),
        (orphan_ns, None),
    ], key=lambda p: key(p[0]))
    assert ns_expanded_drift(BASE_ROWS + copies) == want


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(st.permutations(BASE_ROWS + found_copies()))
def test_drift_does_not_depend_on_row_order(rows):
    # oracle: the same hand-written 24 drifted copies, whatever order the rows come in
    assert ns_expanded_drift(rows) == expected_drift()


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
def test_removing_a_base_row_orphans_exactly_its_copies():
    # oracle: the hand-written state — only CommonStockSharesIssued's six copies lose their base
    rows = [b for b in BASE_ROWS if b.qname != G + "CommonStockSharesIssued"] + synced_copies()
    want = sorted([(copy("SharesOutstanding", ns, "CommonStockSharesIssued", 50), None)
                   for ns in GAAP_NS], key=lambda p: key(p[0]))
    assert ns_expanded_drift(rows) == want


@pytest.mark.criterion("C4")
@given(corpora(), st.data())
def test_changing_copy_ranks_flags_exactly_those_copies(corpus, data):
    # oracle: requirement 3 — a copy is flagged iff its rank differs from its base row's
    rows, known = corpus
    copies = reference_copies(rows, known)
    assert ns_expanded_drift(rows + copies) == []
    picked = data.draw(st.sets(st.sampled_from(range(len(copies))), min_size=1))
    changed = [ConceptMappingRecord(r.concept_family, r.qname, r.preference_rank + 1,
                                    r.cik_override, r.is_deprecated, EXP) if i in picked else r
               for i, r in enumerate(copies)]
    base_of = {(b.concept_family, b.qname.split(":", 1)[0], b.qname.split(":", 1)[1],
                b.cik_override): b for b in rows}

    def owner(r):
        ns, local = r.qname.rsplit(":", 1)
        prefix = next(p for p, bases in NS_BASES.items() if ns.startswith(bases))
        return base_of[(r.concept_family, prefix, local, r.cik_override)]

    want = sorted([(changed[i], owner(changed[i])) for i in picked], key=lambda p: key(p[0]))
    assert ns_expanded_drift(rows + changed) == want


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(corpora())
def test_removing_a_base_row_orphans_its_copies_and_extra_rows_change_nothing(corpus):
    # oracle: requirements 1 and 3 — copies of the removed first row lose their base; base
    # rows with no copies ("Extra", a local name no known QName has) are never flagged
    rows, known = corpus
    copies = reference_copies(rows, known)
    gone = rows[0]
    kept = rows[1:]
    orphans = reference_copies([gone], known)
    extra = [base("Extra", G + "NotInQnameTable", 1), base("Extra", IF + "NotInQnameTable", 100)]
    want = sorted([(r, None) for r in orphans], key=lambda p: key(p[0]))
    assert len(want) >= 1
    assert ns_expanded_drift(kept + copies) == want
    assert ns_expanded_drift((extra + copies + kept)[::-1]) == want


# --------------------------------------------------------------------------- C5
@pytest.mark.criterion("C5")
def test_the_found_state_fails_loud_naming_each_drifted_row():
    # oracle: the 24 hand-written drifted copies and their families
    with pytest.raises(NsExpandedDriftError) as err:
        assert_ns_expanded_in_sync(BASE_ROWS + found_copies())
    assert isinstance(err.value, ValueError)
    message = str(err.value)
    for expanded, _ in expected_drift():
        assert expanded.qname in message
        assert expanded.concept_family in message


@pytest.mark.criterion("C5")
def test_the_synced_state_passes_and_counts_its_copies():
    # oracle: 9 us-gaap rows x 6 versions + ProfitLoss 2 x 6 + 2 x 4 = 74 copies, counted by hand
    assert assert_ns_expanded_in_sync(BASE_ROWS + synced_copies()) == 9 * 6 + 2 * 6 + 2 * 4


@pytest.mark.criterion("C5")
@given(corpora(), st.booleans())
def test_the_check_raises_iff_there_is_drift(corpus, drift):
    # oracle: requirement 4 — returns the copy count when in sync, raises otherwise
    rows, known = corpus
    copies = reference_copies(rows, known)
    if drift:
        first = copies[0]
        copies[0] = ConceptMappingRecord(first.concept_family, first.qname,
                                         first.preference_rank + 1, first.cik_override,
                                         first.is_deprecated, EXP)
        with pytest.raises(NsExpandedDriftError):
            assert_ns_expanded_in_sync(rows + copies)
    else:
        assert assert_ns_expanded_in_sync(rows + copies) == len(copies)
