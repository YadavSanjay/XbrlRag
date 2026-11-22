"""Unit tests for the identity-and-stand-in wiring (docs/specs/identity-and-stand-in.md).

The spec locks the two pure functions; these cover what joins them to the system: the
lookup's stand-in refusal in ``_build_result``, the axis/label helpers, and the
``entity_master`` identity populator against a fake DB cursor (the DB boundary).
"""

from __future__ import annotations

import pytest

from src.ingest.sec_edgar.ingest import entity_identity
from src.retriever.xbrl.xbrl_factstore_lookup import (
    XbrlFactstoreLookup,
    _axis_local_names,
)

SEG = "us-gaap:StatementBusinessSegmentsAxis"
REV = "http://fasb.org/us-gaap/2025:RevenueFromContractWithCustomerExcludingAssessedTax"
COMBINED = "Client Computing Group, Datacenter And AI And Network And Edge [Member]"
INTC_CATALOG = ["Network and Edge [Member]", COMBINED, "Intel Foundry [Member]"]


def _row(fact_id, value, label, axis=SEG, dimension_labels=None):
    return {"fact_id": fact_id, "value": value, "unit": "USD", "period_type": "duration",
            "period_start": None, "period_end": "2025-12-27", "concept": REV,
            "concept_family": "Revenue", "concept_pref_rank": 1, "segment_axis": axis,
            "segment_member": None, "segment_label": label, "html_anchor": None,
            "page_num": None, "section_path": None, "accession_no": None,
            "linked_chunk_ids": [], "decimals": -6, "dimension_labels": dimension_labels}


def _result(hint, rows, catalog):
    lk = XbrlFactstoreLookup()
    notes: list[str] = []
    dims = {"segment": hint}
    specs, status = lk._parse_dimensions(dims, notes)
    assert status is None
    step = {"id": "s1", "op": "lookup", "dimensions": dims, "section_hint": None}
    return lk._build_result("s1", step, "0000050863", 2025, "Revenue", "duration", specs,
                            rows, notes, catalog_labels=catalog)


class TestStandInRefusal:
    def test_combined_member_is_refused_when_the_member_exists(self):
        r = _result("network and edge", [_row(157299, 49_147e6, COMBINED)], INTC_CATALOG)
        assert r.status.value == "UNRESOLVED_DIMENSION"
        assert r.value is None and r.citation is None
        assert any("stand-in" in n for n in r.resolution_notes)

    def test_neutral_retry_rows_are_judged_by_their_dimension_labels(self):
        row = _row(157299, 49_147e6, None, axis=None, dimension_labels={
            "http://fasb.org/srt/2025:ConsolidationItemsAxis": "Operating Segments [Member]",
            "http://fasb.org/us-gaap/2025:StatementBusinessSegmentsAxis": COMBINED})
        r = _result("network and edge", [row], INTC_CATALOG)
        assert r.status.value == "UNRESOLVED_DIMENSION"

    def test_exact_member_still_resolves(self):
        r = _result("intel foundry", [_row(157300, 17_826e6, "Intel Foundry [Member]")],
                    INTC_CATALOG)
        assert r.status.value == "OK"
        assert r.citation.fact_id == 157300

    def test_no_catalog_keeps_todays_containment_behaviour(self):
        # Catalog not consulted (e.g. a QName member): no refusal, the row is judged as today.
        r = _result("network and edge", [_row(157299, 49_147e6, COMBINED)], None)
        assert r.status.value != "UNRESOLVED_DIMENSION"
        assert r.value == pytest.approx(49_147e6, rel=1e-12)  # stored value, unrounded


def test_axis_local_names_reads_prefixed_uri_and_clark_forms():
    rows = [{"segment_axis": "srt:ProductOrServiceAxis", "dimension_labels": None},
            {"segment_axis": None, "dimension_labels": {
                "http://fasb.org/us-gaap/2025:StatementBusinessSegmentsAxis": "x",
                "{http://xbrl.sec.gov/dei/2025}LegalEntityAxis": "y"}}]
    assert _axis_local_names(rows) == {
        "ProductOrServiceAxis", "StatementBusinessSegmentsAxis", "LegalEntityAxis"}


# --------------------------------------------------------------------------- populator
DEI = "http://xbrl.sec.gov/dei/2025"
GAAP = "http://fasb.org/us-gaap/2025"
GOOG_NS = "http://www.abc.xyz/20250630"


class FakeCursor:
    """The DB boundary: entity_master rows and per-accession cover-fact rows."""

    def __init__(self, master, cover):
        self.master, self.cover = master, cover
        self.updates: list[dict] = []
        self._rows: list = []

    def execute(self, sql, params=None):
        if sql.lstrip().startswith("SELECT cik"):
            self._rows = list(self.master)
        elif sql.lstrip().startswith("UPDATE"):
            self.updates.append(params)
        else:
            assert "cd.is_default = FALSE" in sql   # explicit dimensions only
            self._rows = list(self.cover[(params["cik"], params["accession"])])

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, cur):
        self.cur, self.commits = cur, 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1


GOOGL_ROWS = [
    (1, "EntityRegistrantName", "Alphabet Inc.", None, None, None, None),
    (2, "TradingSymbol", "GOOGL", DEI, "EntityListingsClassOfStockAxis", GAAP,
     "CommonClassAMember"),
    (3, "TradingSymbol", "GOOG", DEI, "EntityListingsClassOfStockAxis", GOOG_NS,
     "CapitalClassCMember"),
]
CEG_ROWS = [
    (10, "EntityRegistrantName", "CONSTELLATION ENERGY CORPORATION", None, None, None, None),
    (11, "EntityRegistrantName", "CONSTELLATION ENERGY GENERATION, LLC", DEI,
     "LegalEntityAxis", "http://www.constellationenergy.com/20251231",
     "ConstellationEnergyGenerationLLCMember"),
    (12, "TradingSymbol", "CEG", None, None, None, None),
]


def test_cover_facts_maps_namespaces_to_prefixes():
    cur = FakeCursor([], {("0001652044", "acc"): GOOGL_ROWS})
    facts = entity_identity.cover_facts(cur, "0001652044", "acc")
    by_value = {f["value"]: f["dims"] for f in facts}
    assert by_value["GOOGL"] == {"dei:EntityListingsClassOfStockAxis": "us-gaap:CommonClassAMember"}
    assert by_value["GOOG"] == {"dei:EntityListingsClassOfStockAxis": "ext:CapitalClassCMember"}
    assert by_value["Alphabet Inc."] == {}


@pytest.mark.parametrize("dry_run", [False, True])
def test_populate_fixes_fresh_and_wrong_rows_and_keeps_good_ones(dry_run):
    master = [
        ("0001652044", None, None, "goog-acc"),                       # fresh row from the SQL
        ("0001868275", "CONSTELLATION ENERGY GENERATION, LLC", "CEG", "ceg-acc"),
        ("0000320193", "Apple Inc.", "AAPL", "aapl-acc"),               # no cover facts found
    ]
    cover = {("0001652044", "goog-acc"): GOOGL_ROWS, ("0001868275", "ceg-acc"): CEG_ROWS,
             ("0000320193", "aapl-acc"): []}
    conn = FakeConn(FakeCursor(master, cover))
    changes = entity_identity.populate_entity_identity(conn, dry_run=dry_run)
    assert [(c, after) for c, _, after in changes] == [
        ("0001652044", {"company_name": "Alphabet Inc.", "trading_symbol": "GOOGL"}),
        ("0001868275", {"company_name": "CONSTELLATION ENERGY CORPORATION",
                        "trading_symbol": "CEG"}),
    ]
    assert len(conn.cur.updates) == (0 if dry_run else 2)
    assert conn.commits == (0 if dry_run else 1)


def test_populate_keeps_a_listed_stored_symbol():
    master = [("0001652044", "Alphabet Inc.", "GOOG", "goog-acc")]
    conn = FakeConn(FakeCursor(master, {("0001652044", "goog-acc"): GOOGL_ROWS}))
    assert entity_identity.populate_entity_identity(conn) == []
    assert conn.cur.updates == []
