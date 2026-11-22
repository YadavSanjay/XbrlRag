"""The reviewed concept-family overrides wired into the generator and the populator
(docs/specs/concept-families-wave1.md). No DB: psycopg2 is faked at the connection.

The locked spec tests cover the validator, merge and ranking; these cover the CSV rows
the generator emits and how the populator keeps reviewed rows away from the
facts-count override pass.
"""

from __future__ import annotations

import pytest

from src.ingest.sec_edgar.ingest import layer0_populator
from src.ingest.sec_edgar.ingest.layer0_populator import (
    ConceptMappingRecord,
    Layer0Populator,
)
from src.ingest.util.generate_concept_mapping import (
    FAMILY_ANCHOR_QNAMES,
    concept_mapping_rows,
    load_family_overrides,
)

WMT = "0000104169"
REVS = "us-gaap:Revenues"
EXCL = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
OTHER_PPE = "us-gaap:PaymentsToAcquireOtherPropertyPlantAndEquipment"
NO_OVERRIDES = {"families": {}, "filers": {}}


def _rows(rows, source, cik=""):
    return [(r["concept_family"], r["qname"], r["preference_rank"])
            for r in rows if r["source"] == source and r["cik_override"] == cik]


class TestConceptMappingRows:
    OVERRIDES = {
        "families": {"Capex": {"label": None, "primaries": [OTHER_PPE], "components": []}},
        "filers": {(WMT, "Revenue"): [REVS]},
    }

    def test_additions_follow_the_base_and_nothing_else_moves(self):
        base, _ = concept_mapping_rows({}, {}, NO_OVERRIDES)
        merged, stats = concept_mapping_rows({}, {}, self.OVERRIDES)
        base_hifi, merged_hifi = _rows(base, "hifi_kpi"), _rows(merged, "hifi_kpi")
        n_capex = len(FAMILY_ANCHOR_QNAMES["Capex"])
        assert set(merged_hifi) - set(base_hifi) == {("Capex", OTHER_PPE, n_capex + 1)}
        assert set(base_hifi) <= set(merged_hifi)
        assert stats["Capex"] == n_capex + 1
        assert not _rows(base, "reviewed_override", WMT)

    def test_a_filer_entry_writes_its_reviewed_ranking(self):
        rows, _ = concept_mapping_rows({}, {}, self.OVERRIDES)
        reviewed = _rows(rows, "reviewed_override", WMT)
        assert [(q, r) for _, q, r in reviewed] == [
            (REVS, 1), (EXCL, 2), ("us-gaap:SalesRevenueNet", 3),
            ("us-gaap:SalesRevenueGoodsNet", 4), ("us-gaap:SalesRevenueServicesNet", 5),
        ]
        assert {f for f, _, _ in reviewed} == {"Revenue"}
        # Only the reviewed pair gets filer rows.
        assert {(r["cik_override"], r["concept_family"]) for r in rows
                if r["cik_override"]} == {(WMT, "Revenue")}

    def test_committed_file_generates(self):
        rows, _ = concept_mapping_rows({}, {}, load_family_overrides())
        assert _rows(rows, "reviewed_override", WMT)[0] == ("Revenue", REVS, 1)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_family_overrides(tmp_path / "absent.yaml")


class _FakeCursor:
    """Answers the populator's SELECTs from canned rows and records every write."""

    def __init__(self, db):
        self.db = db
        self._rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.db.log.append((text, params))
        self._rows = []
        for marker, rows in self.db.answers.items():
            if marker in text:
                self._rows = rows
                break

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, answers):
        self.answers = answers
        self.log = []
        self.batches = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.log.append(("COMMIT", None))


@pytest.fixture
def fake_db(monkeypatch):
    def install(answers):
        conn = _FakeConn(answers)
        monkeypatch.setattr(layer0_populator.psycopg2, "connect", lambda **_: conn)
        monkeypatch.setattr(
            layer0_populator.psycopg2.extras, "execute_batch",
            lambda cur, sql, rows, page_size=100: conn.batches.append(
                (" ".join(sql.split()), list(rows))))
        return conn
    return install


def _populator():
    return Layer0Populator(sec_edgar_root=".", db_conn_params={})


class TestOverridePassSkipsReviewedPairs:
    def test_reviewed_pair_is_neither_deleted_nor_overridden(self, fake_db):
        revenue = [(EXCL, 1), (REVS, 2)]
        conn = fake_db({
            "WHERE cik_override IS NULL": [("Revenue", q, r) for q, r in revenue]
                                          + [("NetIncome", "us-gaap:NetIncomeLoss", 1)],
            "WHERE source = %s": [("Revenue", WMT)],
            "array_agg(DISTINCT fr.fiscal_year)": [
                ("Revenues", "http://fasb.org/us-gaap/2025", [2025]),
                ("NetIncomeLoss", "http://fasb.org/us-gaap/2025", [2025]),
            ],
        })
        stats = _populator().populate_concept_mapping_overrides(targets=[(WMT, 2025)])
        assert stats["reviewed_kept"] == 1
        assert [d["concept_family"] for d in stats["detail"]] == ["NetIncome"]
        deletes = [p for sql, p in conn.log if sql.startswith("DELETE")]
        # The last delete is the ns_expanded re-derivation (docs/specs/ns-expand-rank-sync.md).
        assert deletes == [("NetIncome", WMT), ("ns_expanded",)]
        assert not conn.batches  # NetIncome is already optimal; Revenue untouched

    def test_without_a_reviewed_row_the_facts_rule_still_decides(self, fake_db):
        conn = fake_db({
            "WHERE cik_override IS NULL": [("Revenue", EXCL, 1), ("Revenue", REVS, 2)],
            "WHERE source = %s": [],
            "array_agg(DISTINCT fr.fiscal_year)": [
                ("Revenues", "http://fasb.org/us-gaap/2025", [2025])],
        })
        stats = _populator().populate_concept_mapping_overrides(targets=[(WMT, 2025)])
        assert stats["reviewed_kept"] == 0 and stats["overrides_inserted"] == 1
        assert conn.batches[0][1] == [("Revenue", REVS, 1, WMT), ("Revenue", EXCL, 2, WMT)]


class TestReplaceSourceWithReviewedRows:
    def _record(self, qname, rank, cik=None, source="hifi_kpi"):
        return ConceptMappingRecord("Revenue", qname, rank, cik, False, source)

    def test_reviewed_rows_replace_old_ones_and_their_auto_rows(self, fake_db):
        conn = fake_db({"WHERE source = %s": [("Revenue", "0000909832")]})
        records = [self._record(EXCL, 1), self._record(REVS, 2),
                   self._record(REVS, 1, WMT, "reviewed_override"),
                   self._record(EXCL, 2, WMT, "reviewed_override")]
        n = _populator()._replace_source_concept_mapping(conn, records)
        assert n == 4
        deletes = [p for sql, p in conn.log if sql.startswith("DELETE")]
        # The new (WMT) and old (COST) reviewed pairs lose auto/ns_expanded rows, then
        # each source in the CSV is replaced.
        assert deletes == [("Revenue", WMT), ("Revenue", "0000909832"),
                           ("hifi_kpi",), ("reviewed_override",)]
        assert len(conn.batches[0][1]) == 4

    def test_a_hifi_only_csv_leaves_reviewed_rows_alone(self, fake_db):
        conn = fake_db({"WHERE source = %s": [("Revenue", WMT)]})
        _populator()._replace_source_concept_mapping(conn, [self._record(EXCL, 1)])
        deletes = [p for sql, p in conn.log if sql.startswith("DELETE")]
        assert deletes == [("hifi_kpi",)]
