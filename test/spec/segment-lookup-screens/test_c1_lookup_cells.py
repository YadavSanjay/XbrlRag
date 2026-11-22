"""C1: a one-dimension lookup answers the member's full value or refuses; never a strict part.

docs/specs/segment-lookup-screens.md, Requirement 1. `XbrlFactstoreLookup.lookup` runs for
real over the fake store of conftest.py (the DB boundary).
"""

from __future__ import annotations

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from sls_store import catalog_of, synthetic_record

from src.retriever.xbrl.xbrl_factstore_lookup import FactLookupResult, LookupStatus

LLY_CIK = "0000059478"
LLY_ACC = "0000059478-24-000065"
HD_CIK = "0000354950"
HD_ACC = "0001628280-26-019436"
REFUSALS = {LookupStatus.NOT_FOUND, LookupStatus.UNRESOLVED_DIMENSION}


def _step(cik: str, ticker: str, fy: int, member: str) -> dict:
    return {"id": "l1", "op": "lookup", "cik": cik, "ticker": ticker, "fy": fy,
            "concept_family": "Revenue", "dimensions": {"product": member}}


def _fsds(fixture: dict, accession: str, tag: str, segments: str) -> float:
    """The 4-quarter value of one FSDS num line quoted in the fixture."""
    for block in fixture["fsds"]:
        header = block["header"].split("\t")
        for line in block["lines"]:
            f = dict(zip(header, line.split("\t"), strict=True))
            if (f["adsh"], f["tag"], f["segments"], f["qtrs"]) == (accession, tag, segments, "4"):
                return float(f["value"])
    raise AssertionError(f"no FSDS line {accession} {tag} {segments!r} in the fixture")


def _ids(result: FactLookupResult) -> list[int]:
    summed = result.to_dict().get("sum_of")
    if summed:
        return sorted(int(i) for i in summed)
    return [int(result.citation.fact_id)] if result.citation else []


def _full_or_refused(result: FactLookupResult, full_value: float, all_ids: list[int]) -> None:
    """Requirement 1: OK only at the member's full value, citing every fact it adds up."""
    if result.status is LookupStatus.OK:
        # Exact integers in USD; a float sum of two or four of them is exact.
        assert result.value == pytest.approx(full_value, rel=1e-12)
        assert _ids(result) == sorted(all_ids)
    else:
        assert result.status in REFUSALS, result.to_dict()
        assert result.value is None


@pytest.mark.criterion("C1")
def test_lly_trulicity_is_the_full_member_or_refused(run_lookup, lookup_fixture):
    # oracle: FSDS 2024q1 num lines of 0000059478-24-000065 (Trulicity US 5,433.3M and
    # Non-US 1,699.2M, summed here; Jardiance 2,744.7M) and FSDS 2026q1 for
    # 0001628280-26-019436 (Appliances x Primary Segment 13,987M, the only Appliances line)
    records = lookup_fixture["records"]
    cmap = lookup_fixture["concept_mapping"]
    catalog = lookup_fixture["dimension_catalog"]
    us = _fsds(lookup_fixture, LLY_ACC, "Revenues",
               "Geographical=US;ProductOrService=TrulicityMember;")
    non_us = _fsds(lookup_fixture, LLY_ACC, "Revenues",
                   "Geographical=NonUs;ProductOrService=TrulicityMember;")
    jardiance = _fsds(lookup_fixture, LLY_ACC, "Revenues", "ProductOrService=Jardiance;")
    appliances = _fsds(lookup_fixture, HD_ACC,
                       "RevenueFromContractWithCustomerExcludingAssessedTax",
                       "BusinessSegments=PrimarySegment;ProductOrService=Appliances;")

    # Control: a single-dimension member fact answers as today.
    r = run_lookup(records, _step(LLY_CIK, "LLY", 2023, "Jardiance"), cmap, catalog)
    assert r.status is LookupStatus.OK
    assert r.value == pytest.approx(jardiance, rel=1e-12)  # one exact stored integer
    assert int(r.citation.fact_id) == 219594

    # Control: HD's only Appliances fact is a cell; it may answer it, or refuse.
    r = run_lookup(records, _step(HD_CIK, "HD", 2025, "appliances"), cmap, catalog)
    _full_or_refused(r, appliances, [213903])

    # The defect: Trulicity is reported only in two cells (US 219333, Non-US 219336).
    r = run_lookup(records, _step(LLY_CIK, "LLY", 2023, "Trulicity"), cmap, catalog)
    _full_or_refused(r, us + non_us, [219333, 219336])

    # With the US cell gone, what is left is a strict part: refuse.
    partial = [x for x in records if x["fact_id"] != 219333]
    r = run_lookup(partial, _step(LLY_CIK, "LLY", 2023, "Trulicity"), cmap, catalog)
    assert r.status in REFUSALS, r.to_dict()
    assert r.value is None


# ------------------------------------------------------------------ property
CIK = "0000000042"
CONCEPT = "http://fasb.org/us-gaap/2024:Revenues"
GEO_AXIS = "http://fasb.org/srt/2024:StatementGeographicalAxis"
PROD_AXIS = "http://fasb.org/srt/2024:ProductOrServiceAxis"
GEOS = [
    ("http://fasb.org/srt/2024:AmericasMember", "Americas [Member]"),
    ("http://fasb.org/srt/2024:EuropeMember", "Europe [Member]"),
    ("http://fasb.org/srt/2024:AsiaPacificMember", "Asia Pacific [Member]"),
    ("http://www.syn.example/20241231:RestOfWorldMember", "Rest of World [Member]"),
]
WIDGET = ("http://www.syn.example/20241231:WidgetMember", "Widget [Member]")
GADGET = ("http://www.syn.example/20241231:GadgetMember", "Gadget [Member]")
CMAP = [{"qname": CONCEPT, "preference_rank": 1, "cik_override": None, "is_deprecated": False}]


def _cell_store(w, x, present, widget_first, with_single):
    """A filer whose Widget revenue is reported in cells over a geographic partition.

    The undimensioned total equals the sum of the single-dimension geography facts, which
    equal Widget + Gadget per geography: the geographic members partition the whole.
    """
    n = len(w)
    rec, dims_of = [], []

    def add(fact_id, value, dims):
        rec.append(synthetic_record(fact_id=fact_id, ticker="SYN", cik=CIK, fiscal_year=2024,
                                    value=float(value), dims=dims, concept=CONCEPT))
        dims_of.append(dims)

    add(1, sum(w) + sum(x), [])
    for i in range(n):
        geo = (GEO_AXIS, *GEOS[i])
        add(10 + i, w[i] + x[i], [geo])
        add(30 + i, x[i], [(PROD_AXIS, *GADGET), geo])
        if present[i]:
            widget = (PROD_AXIS, *WIDGET)
            add(50 + i, w[i], [widget, geo] if widget_first[i] else [geo, widget])
    if with_single:
        add(90, sum(w), [(PROD_AXIS, *WIDGET)])
    return {"records": rec, "catalog": catalog_of(rec, dims_of), "truth": float(sum(w)),
            "cell_ids": [50 + i for i in range(n)], "single": 90 if with_single else None}


@st.composite
def cell_stores(draw):
    n = draw(st.integers(min_value=2, max_value=4))
    amounts = st.lists(st.integers(min_value=1, max_value=10_000).map(lambda v: v * 1_000_000),
                       min_size=n, max_size=n)
    w, x = draw(amounts), draw(amounts)
    present = draw(st.lists(st.booleans(), min_size=n, max_size=n).filter(any))
    widget_first = draw(st.lists(st.booleans(), min_size=n, max_size=n))
    with_single = draw(st.booleans())
    return w, x, present, widget_first, with_single


def _flip(args):
    w, x, present, widget_first, with_single = args
    return w, x, present, [not f for f in widget_first], with_single


@pytest.mark.criterion("C1")
@given(cell_stores())
@example(([5_000_000, 2_000_000], [1_000_000, 3_000_000], [True, True], [False, True], False))
@example(([5_000_000, 2_000_000], [1_000_000, 3_000_000], [False, True], [True, True], False))
@example(([5_000_000, 2_000_000], [1_000_000, 3_000_000], [True, True], [True, True], True))
def test_cells_answer_the_full_member_or_refuse(run_lookup, args):
    # oracle: the store's construction (the Widget total is the sum of all its cells, the
    # geography facts partition the undimensioned total) and Requirement 1
    step = {"id": "l1", "op": "lookup", "cik": CIK, "ticker": "SYN", "fy": 2024,
            "concept_family": "Revenue", "dimensions": {"product": "widget"}}
    store = _cell_store(*args)
    r = run_lookup(store["records"], step, CMAP, store["catalog"])
    if store["single"] is not None:
        assert r.status is LookupStatus.OK, r.to_dict()
        assert r.value == pytest.approx(store["truth"], rel=1e-12)  # exact stored integer
        assert int(r.citation.fact_id) == store["single"]
    else:
        _full_or_refused(r, store["truth"], store["cell_ids"])

    # Which dimension the view lists first never changes the result.
    flipped = _cell_store(*_flip(args))
    r2 = run_lookup(flipped["records"], step, CMAP, flipped["catalog"])
    assert r2.status is r.status
    assert r2.value == r.value
