"""The ns_expanded re-derivation wired into every concept_mapping write
(docs/specs/ns-expand-rank-sync.md, requirement 4). No DB: psycopg2 is faked at the
connection by a small in-memory concept_mapping + qname table with commit/rollback.

The locked spec tests cover expand_concept_mapping, ns_expanded_drift and
assert_ns_expanded_in_sync; these cover the populator method that applies them inside
the caller's transaction, the writes that call it, bootstrap_db and the CLI.
"""

from __future__ import annotations

import copy
import dataclasses
import importlib.util
import sys
from pathlib import Path

import pytest

from src.ingest.sec_edgar.ingest import __main__ as ingest_cli
from src.ingest.sec_edgar.ingest import layer0_populator
from src.ingest.sec_edgar.ingest.layer0_populator import (
    ConceptMappingRecord,
    Layer0Populator,
    NsExpandedDriftError,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXP = "ns_expanded"
WMT = "0000104169"
NS25 = "http://fasb.org/us-gaap/2025"
NS26 = "http://fasb.org/us-gaap/2026"
ISSUED = "CommonStockSharesIssued"
OUTSTANDING = "CommonStockSharesOutstanding"
QNAMES = [(NS25, ISSUED), (NS26, ISSUED), (NS25, OUTSTANDING), (NS26, OUTSTANDING)]


def rec(family, qname, rank, cik=None, source="hifi_kpi", dep=False):
    return ConceptMappingRecord(family, qname, rank, cik, dep, source)


def key(r):
    return (r.concept_family, r.qname, r.cik_override or "")


class FakeDb:
    """concept_mapping (keyed like its unique index) and qname, with transactions."""

    def __init__(self, rows, qnames, facts=()):
        self.committed = {key(r): r for r in rows}
        self.rows = dict(self.committed)
        self.qnames = list(qnames)
        self.facts = list(facts)
        self.corrupt_inserts = False
        self.commits = 0

    def table(self):
        return sorted(self.committed.values(), key=key)

    # --- connection protocol -------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, exc_type, *exc):
        if exc_type is not None:
            self.rows = dict(self.committed)  # psycopg2's `with conn` rolls back
        return False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = dict(self.rows)
        self.commits += 1

    # --- writes ----------------------------------------------------------------
    def insert(self, sql, row):
        if isinstance(row, dict):
            # Only the columns the INSERT names are written; a value present in the
            # parameter dict but missing from the column list is NULL, as in Postgres.
            columns = sql.split("VALUES")[0]
            r = dataclasses.replace(
                rec(row["concept_family"], row["qname"], row["preference_rank"],
                    row["cik_override"], row["source"], row["is_deprecated"]),
                **{f: row.get(f) if f in columns else None
                   for f in ("concept_role", "substitution_code", "substitution_note")})
        else:  # the override pass: (family, qname, rank, cik)
            family, qname, rank, cik = row
            r = rec(family, qname, rank, cik, "auto_override")
        if self.corrupt_inserts and r.source == EXP:
            r = rec(r.concept_family, r.qname, r.preference_rank + 1, r.cik_override, EXP)
        k = key(r)
        if k in self.rows:
            if "DO NOTHING" in sql:
                return
            if "DO UPDATE" not in sql:
                raise RuntimeError(f"unique violation on {k}")
            old = self.rows[k]
            dep = r.is_deprecated if "is_deprecated = EXCLUDED" in sql else old.is_deprecated
            r = rec(r.concept_family, r.qname, r.preference_rank, r.cik_override,
                    r.source, dep)
        self.rows[k] = r


class FakeCursor:
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
        db = self.db
        rows = list(db.rows.values())
        self._rows = []
        if text.startswith("SELECT DISTINCT namespace, local_name FROM qname"):
            self._rows = sorted({q for q in db.qnames if q[1] in params[0]})
        elif text.startswith("SELECT concept_family, qname, preference_rank, cik_override,"):
            self._rows = [(r.concept_family, r.qname, r.preference_rank, r.cik_override,
                           r.is_deprecated, r.source, r.concept_role, r.substitution_code,
                           r.substitution_note) for r in rows]
        elif text.startswith("UPDATE concept_mapping AS o SET concept_role"):
            generic = {(r.concept_family, r.qname): r for r in rows
                       if r.cik_override is None and r.source != EXP}
            n = 0
            for k, r in db.rows.items():
                g = generic.get((r.concept_family, r.qname))
                fields = ("concept_role", "substitution_code", "substitution_note")
                if (r.source == "auto_override" and g is not None
                        and any(getattr(r, f) != getattr(g, f) for f in fields)):
                    db.rows[k] = dataclasses.replace(
                        r, **{f: getattr(g, f) for f in fields})
                    n += 1
            self.rowcount = n
        elif text.startswith("SELECT DISTINCT concept_family, cik_override"):
            self._rows = sorted({(r.concept_family, r.cik_override)
                                 for r in rows if r.source == params[0]})
        elif "WHERE cik_override IS NULL" in text:
            self._rows = sorted((r.concept_family, r.qname, r.preference_rank) for r in rows
                                if r.cik_override is None and not r.is_deprecated
                                and not r.qname.startswith("http"))
        elif "array_agg(DISTINCT fr.fiscal_year)" in text:
            self._rows = list(db.facts)
        elif text == "DELETE FROM concept_mapping WHERE source = %s":
            gone = [k for k, r in db.rows.items() if r.source == params[0]]
            self._delete(gone)
        elif text.startswith("DELETE FROM concept_mapping WHERE concept_family = %s"):
            family, cik = params
            gone = [k for k, r in db.rows.items()
                    if r.concept_family == family and r.cik_override == cik
                    and r.source in ("auto_override", EXP)]
            self._delete(gone)
        else:
            raise AssertionError(f"unexpected SQL: {text}")

    def _delete(self, keys):
        for k in keys:
            del self.db.rows[k]
        self.rowcount = len(keys)

    def fetchall(self):
        return list(self._rows)


@pytest.fixture
def install(monkeypatch):
    def _install(db):
        monkeypatch.setattr(layer0_populator.psycopg2, "connect", lambda **_: db)
        monkeypatch.setattr(
            layer0_populator.psycopg2.extras, "execute_batch",
            lambda cur, sql, rows, page_size=100: [cur.db.insert(sql, r) for r in rows])
        return db
    return _install


def populator():
    return Layer0Populator(sec_edgar_root=".", db_conn_params={})


def copies(family, local, rank, cik=None, namespaces=(NS25, NS26)):
    return [rec(family, f"{ns}:{local}", rank, cik, EXP) for ns in namespaces]


# The 2026-11-05 shape: the issued-shares base row moved to the component band (50)
# but its copies kept rank 2; plus an orphan copy whose base row is gone.
STALE = [
    rec("SharesOutstanding", f"us-gaap:{OUTSTANDING}", 1),
    rec("SharesOutstanding", f"us-gaap:{ISSUED}", 50),
    *copies("SharesOutstanding", OUTSTANDING, 1),
    *copies("SharesOutstanding", ISSUED, 2),
    rec("Gone", f"{NS25}:Gone", 1, source=EXP),
]
BASES = [
    rec("SharesOutstanding", f"us-gaap:{OUTSTANDING}", 1),
    rec("SharesOutstanding", f"us-gaap:{ISSUED}", 50),
]
SYNCED = sorted([
    *BASES,
    *copies("SharesOutstanding", OUTSTANDING, 1),
    *copies("SharesOutstanding", ISSUED, 50),
], key=key)


class TestSyncNsExpanded:
    def test_stale_and_orphan_copies_are_rederived_and_committed(self, install):
        db = install(FakeDb(STALE, QNAMES))
        stats = populator().sync_ns_expanded()
        assert db.table() == SYNCED
        assert stats == {"ns_expanded_before": 5, "drifted_before": 3,
                         "ns_expanded_deleted": 5, "ns_expanded_inserted": 4,
                         "ns_expanded_checked": 4, "dry_run": False}
        assert db.commits == 1

    def test_auto_override_rows_take_the_generic_role(self, install):
        # docs/specs/concept-family-coverage.md: a facts-count ranking never changes what
        # a QName is to its family; its rows and their copies carry the generic role.
        cover = dataclasses.replace(
            rec("SharesOutstanding", f"us-gaap:{ISSUED}", 50),
            concept_role="substitute", substitution_code="c", substitution_note="n")
        auto = rec("SharesOutstanding", f"us-gaap:{ISSUED}", 50, WMT, "auto_override")
        db = install(FakeDb([cover, auto], QNAMES))
        populator().sync_ns_expanded()
        rows = [r for r in db.table() if r.cik_override == WMT]
        assert rows and {(r.concept_role, r.substitution_code, r.substitution_note)
                         for r in rows} == {("substitute", "c", "n")}
        assert {r.source for r in rows} == {"auto_override", EXP}

    def test_is_idempotent(self, install):
        db = install(FakeDb(SYNCED, QNAMES))
        stats = populator().sync_ns_expanded()
        assert db.table() == SYNCED
        assert stats["drifted_before"] == 0

    def test_dry_run_reports_and_writes_nothing(self, install):
        db = install(FakeDb(STALE, QNAMES))
        stats = populator().sync_ns_expanded(dry_run=True)
        assert db.table() == sorted(STALE, key=key)
        assert db.commits == 0
        assert stats["drifted_before"] == 3 and stats["ns_expanded_would_insert"] == 4

    def test_drift_after_the_write_raises_and_rolls_back(self, install):
        db = install(FakeDb(STALE, QNAMES))
        db.corrupt_inserts = True  # the re-read sees ranks other than the base's
        with pytest.raises(NsExpandedDriftError, match=ISSUED):
            populator().sync_ns_expanded()
        assert db.table() == sorted(STALE, key=key)
        assert db.commits == 0

    def test_a_derived_row_colliding_with_a_base_row_raises_before_writing(self, install):
        manual = rec("SharesOutstanding", f"{NS25}:{ISSUED}", 7, source="manual")
        db = install(FakeDb([*BASES, manual], QNAMES))
        with pytest.raises(NsExpandedDriftError, match="collide"):
            populator().sync_ns_expanded()
        assert db.commits == 0

    def test_reads_only_qnames_for_base_local_names(self, install):
        db = install(FakeDb([rec("F", "country:US", 1)], QNAMES))
        stats = populator().sync_ns_expanded()
        assert db.table() == [rec("F", "country:US", 1)]
        assert stats["ns_expanded_inserted"] == 0


def write_csv(path, rows):
    lines = ["concept_family,qname,preference_rank,cik_override,is_deprecated,source"]
    lines += [f"{r.concept_family},{r.qname},{r.preference_rank},{r.cik_override or ''},"
              f"{r.is_deprecated},{r.source}" for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestEveryWriteRederives:
    # A reload that moves the issued-shares concept to the component band; its copies
    # sit at the old rank 2 before the load.
    BEFORE = [
        rec("SharesOutstanding", f"us-gaap:{OUTSTANDING}", 1),
        rec("SharesOutstanding", f"us-gaap:{ISSUED}", 2),
        *copies("SharesOutstanding", OUTSTANDING, 1),
        *copies("SharesOutstanding", ISSUED, 2),
    ]

    @pytest.mark.parametrize("mode", ["upsert", "replace_source"])
    def test_populate_concept_mapping_rederives(self, install, tmp_path, mode):
        db = install(FakeDb(self.BEFORE, QNAMES))
        csv = write_csv(tmp_path / "cm.csv", [
            rec("SharesOutstanding", f"us-gaap:{OUTSTANDING}", 1),
            rec("SharesOutstanding", f"us-gaap:{ISSUED}", 50),
        ])
        stats = populator().populate_concept_mapping(csv, mode=mode)
        assert db.table() == SYNCED
        assert stats["ns_expanded"]["drifted_before"] == 2

    def test_insert_new_also_rederives(self, install, tmp_path):
        # The base row keeps its rank under insert_new; the stale copies still move to it.
        stale = [*BASES, *copies("SharesOutstanding", OUTSTANDING, 1),
                 *copies("SharesOutstanding", ISSUED, 2)]
        db = install(FakeDb(stale, QNAMES))
        csv = write_csv(tmp_path / "cm.csv", [rec("SharesOutstanding", f"us-gaap:{ISSUED}", 3)])
        populator().populate_concept_mapping(csv, mode="insert_new")
        assert db.table() == SYNCED

    def test_dry_run_does_not_write_or_rederive(self, install, tmp_path):
        db = install(FakeDb(self.BEFORE, QNAMES))
        csv = write_csv(tmp_path / "cm.csv", [rec("SharesOutstanding", f"us-gaap:{ISSUED}", 50)])
        stats = populator().populate_concept_mapping(csv, dry_run=True)
        assert db.table() == sorted(self.BEFORE, key=key)
        assert "ns_expanded" not in stats

    def test_override_pass_rederives_filer_and_global_copies(self, install):
        # WMT reports only the second primary (Alt), so it gets a filer ranking with Alt
        # first; the stale global copies are re-derived in the same transaction.
        alt = "CommonStockSharesAlt"
        rows = [*STALE, rec("SharesOutstanding", f"us-gaap:{alt}", 2),
                rec("SharesOutstanding", f"{NS25}:{alt}", 2, source=EXP)]
        qnames = [*QNAMES, (NS25, alt)]
        db = install(FakeDb(rows, qnames, [(alt, NS25, [2025])]))
        stats = populator().populate_concept_mapping_overrides(targets=[(WMT, 2025)])
        assert stats["errors"] == [] and stats["overrides_inserted"] == 1
        table = db.table()
        wmt = sorted((r.qname, r.preference_rank, r.source) for r in table
                      if r.cik_override == WMT)
        # Hand-derived: filer_ranking puts Alt 1, Outstanding 2, Issued (component) 50.
        assert wmt == sorted([
            (f"us-gaap:{alt}", 1, "auto_override"), (f"{NS25}:{alt}", 1, EXP),
            (f"us-gaap:{OUTSTANDING}", 2, "auto_override"),
            (f"{NS25}:{OUTSTANDING}", 2, EXP), (f"{NS26}:{OUTSTANDING}", 2, EXP),
            (f"us-gaap:{ISSUED}", 50, "auto_override"),
            (f"{NS25}:{ISSUED}", 50, EXP), (f"{NS26}:{ISSUED}", 50, EXP),
        ])
        global_copies = sorted((r.qname, r.preference_rank) for r in table
                               if r.cik_override is None and r.source == EXP)
        assert global_copies == sorted([
            (f"{NS25}:{alt}", 2), (f"{NS25}:{OUTSTANDING}", 1), (f"{NS26}:{OUTSTANDING}", 1),
            (f"{NS25}:{ISSUED}", 50), (f"{NS26}:{ISSUED}", 50),
        ])
        assert db.commits == 1

    def test_override_dry_run_writes_nothing(self, install):
        db = install(FakeDb(STALE, QNAMES, [(ISSUED, NS25, [2025])]))
        stats = populator().populate_concept_mapping_overrides(
            targets=[(WMT, 2025)], dry_run=True)
        assert "ns_expanded" not in stats
        assert db.table() == sorted(STALE, key=key)


class TestBootstrapAndCli:
    def test_layer0_populate_rederives_after_the_populate_sql(self, monkeypatch):
        spec = importlib.util.spec_from_file_location(
            "bootstrap_db_under_test", REPO_ROOT / "scripts" / "bootstrap_db.py")
        bootstrap = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, bootstrap)  # dataclasses look it up
        spec.loader.exec_module(bootstrap)
        calls = []
        monkeypatch.setattr(bootstrap, "_exec_sql_file",
                            lambda path: calls.append(("sql", Path(path).name)))
        monkeypatch.setattr(bootstrap, "_run_module",
                            lambda module, *args: calls.append(("module", module, *args)))
        bootstrap._apply_populate()
        assert ("sql", "concept_mapping_ns_expand.sql") in calls
        assert calls[-1] == ("module", "src.ingest.sec_edgar.ingest",
                             "--sync-ns-expanded", "--skip-registries")

    @pytest.mark.parametrize(("rows", "want"), [(STALE, 1), (SYNCED, 0)])
    def test_cli_dry_run_exits_nonzero_on_drift(self, install, monkeypatch, rows, want):
        monkeypatch.setattr(ingest_cli, "load_dotenv", lambda *a, **k: None)
        monkeypatch.setattr(layer0_populator, "load_dotenv", lambda *a, **k: None)
        db = install(FakeDb(copy.deepcopy(rows), QNAMES))
        assert ingest_cli.main(["--sync-ns-expanded", "--skip-registries", "--dry-run"]) == want
        assert db.commits == 0

    def test_cli_sync_writes(self, install, monkeypatch):
        monkeypatch.setattr(ingest_cli, "load_dotenv", lambda *a, **k: None)
        monkeypatch.setattr(layer0_populator, "load_dotenv", lambda *a, **k: None)
        db = install(FakeDb(STALE, QNAMES))
        assert ingest_cli.main(["--sync-ns-expanded", "--skip-registries"]) == 0
        assert db.table() == SYNCED
