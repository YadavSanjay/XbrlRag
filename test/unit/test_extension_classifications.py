"""The extension classifier wired into the reload (docs/specs/extension-classification.md R7).

The classifier itself is locked by test/spec/extension-classification. These tests cover
its input and output wiring: FSDS rows and calc arcs into the classifier's filings, the
committed mapping into members and the hand list, the ranking of the new rows, the
populator's write (psycopg2 faked at the connection) and the bootstrap order.

The end-to-end case reads the spec's committed fixtures (real FSDS rows and calc
linkbases of 16 filings) and expects acceptance criterion C8's rows, whose oracle is the
hand-checked statements and the hand list, not this code.
"""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from src import PROJECT_ROOT
from src.eval.sec_reference.fsds import load_tables
from src.ingest.sec_edgar.ingest import extension_classifications as ec
from src.ingest.sec_edgar.ingest import layer0_populator
from src.ingest.sec_edgar.ingest.layer0_populator import (
    ConceptMappingRecord,
    Layer0Populator,
)
from src.ingest.sec_edgar.xbrl.extension_classifier import (
    classify_extensions,
    extension_mapping_rows,
)
from src.ingest.util.generate_concept_mapping import committed_mapping_rows

FIXTURES = Path(PROJECT_ROOT) / "test" / "spec" / "extension-classification" / "fixtures"
TSM, LMT, MCD = "0001046179", "0000936468", "0000063908"
ACCRETION = "Depreciation and amortization including accretion."

# C8 of the spec: the classifier's rows over the fixture filers.
C8_ROWS = [
    {"cik": LMT, "family": "DepreciationAndAmortization",
     "qname": "ext:DepreciationAndAmortizationIncludingDiscontinuedOperations",
     "role": "substitute", "substitution_code": "includes_accretion"},
    {"cik": TSM, "family": "InterestIncome", "qname": "ext:InvestmentsIncomeInterest",
     "role": "primary", "substitution_code": None},
    {"cik": TSM, "family": "StockRepurchases",
     "qname": "ext:PaymentForAcquireOfTreasuryShares",
     "role": "primary", "substitution_code": None},
]


def _calc_arcs() -> tuple[list[dict], dict[str, str]]:
    """The fixture arcs in CALCULATION_ARCS_SQL's columns, and each filing's CIK."""
    arcs, ciks = [], {}
    for path in sorted((FIXTURES / "calc").glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        ciks[doc["accession_no"]] = doc["cik"]
        arcs += [
            {"accession_no": doc["accession_no"], "link_role_uri": a["link_role"],
             "parent_concept": a["parent"], "child_concept": a["child"],
             "weight": Decimal(a["weight"])}
            for a in doc["arcs"]
        ]
    return arcs, ciks


def _fixture_quarters() -> list[Path]:
    return sorted(p for p in (FIXTURES / "fsds").iterdir() if p.is_dir())


# ---------------------------------------------------------------------------
# Input: FSDS rows and calc arcs
# ---------------------------------------------------------------------------


class TestFsdsConcept:
    ADSH = "0000063908-25-000012"

    def test_custom_tag_is_an_extension(self):
        assert ec.fsds_concept("X", self.ADSH, self.ADSH) == f"urn:fsds:{self.ADSH}:X"

    @pytest.mark.parametrize("version, prefix", [
        ("us-gaap/2024", "us-gaap"), ("ifrs/2024", "ifrs-full"),
        ("srt/2024", "srt"), ("dei/2024", "dei"),
    ])
    def test_standard_taxonomies_take_their_prefix_form(self, version, prefix):
        assert ec.fsds_concept("NetIncomeLoss", version, self.ADSH) == f"{prefix}:NetIncomeLoss"

    def test_another_taxonomy_stays_standard_by_namespace(self):
        concept = ec.fsds_concept("X", "us-gaap-ebp/2026", self.ADSH)
        assert concept == "http://xbrl.sec.gov/us-gaap-ebp/2026:X"


def _pre(adsh, tag, stmt="IS", inpth="0", line="1", version=None):
    return {"adsh": adsh, "report": "2", "line": line, "stmt": stmt, "inpth": inpth,
            "tag": tag, "version": version or adsh, "plabel": tag}


def _num(adsh, tag, value="5", segments="", coreg="", version=None):
    return {"adsh": adsh, "tag": tag, "version": version or adsh, "ddate": "20241231",
            "qtrs": "4", "uom": "USD", "segments": segments, "coreg": coreg,
            "value": value}


class TestClassifierFilings:
    A, B = "0000000001-25-000001", "0000000002-25-000001"

    def test_keeps_primary_non_parenthetical_lines_and_consolidated_values(self):
        pre = [_pre(self.A, "Kept"), _pre(self.A, "Paren", inpth="1"),
               _pre(self.A, "Equity", stmt="EQ"), _pre(self.A, "Cover", stmt="CP"),
               _pre(self.B, "OtherFiling")]
        num = [_num(self.A, "Kept"), _num(self.A, "Kept", segments="Axis=Member;"),
               _num(self.A, "Kept", coreg="Sub"), _num(self.A, "Nil", value=""),
               _num(self.B, "OtherFiling")]
        arcs = [{"accession_no": a, "link_role_uri": "r", "parent_concept": "p:P",
                 "child_concept": "p:C", "weight": Decimal("-1")} for a in (self.A, self.B)]
        filings = ec.classifier_filings(pre, num, arcs, {self.A: "0000000001"})
        assert len(filings) == 1
        (f,) = filings
        assert f["accession_no"] == self.A and f["cik"] == "0000000001"
        assert [ln["concept"] for ln in f["lines"]] == [f"urn:fsds:{self.A}:Kept"]
        assert f["lines"][0]["report"] == 2 and f["lines"][0]["line"] == 1
        assert [(v["concept"].rsplit(":", 1)[1], v["value"]) for v in f["values"]] == [
            ("Kept", Decimal("5")), ("Nil", None)]
        assert f["values"][0]["period"] == "20241231/4" and f["values"][0]["unit"] == "USD"
        assert f["arcs"] == [{"link_role": "r", "parent": "p:P", "child": "p:C",
                              "weight": Decimal("-1")}]

    def test_a_filing_without_statement_lines_gives_no_filing(self):
        assert ec.classifier_filings([_pre(self.A, "X", stmt="EQ")], [], [],
                                     {self.A: "0000000001"}) == []


# ---------------------------------------------------------------------------
# Input: the committed mapping
# ---------------------------------------------------------------------------


def _mrow(family, qname, rank, cik="", role="primary", code="", note="", source="hifi_kpi"):
    return {"concept_family": family, "qname": qname, "preference_rank": rank,
            "cik_override": cik, "concept_role": role, "substitution_code": code,
            "substitution_note": note, "source": source}


class TestMembersAndHandList:
    ROWS = [
        _mrow("Revenue", "us-gaap:Revenues", 1),
        _mrow("ProductRevenue", "us-gaap:Revenues", 2),
        _mrow("InterestIncome", "ifrs-full:RevenueFromInterest", 100, cik=TSM),
        _mrow("DepreciationAndAmortization", "us-gaap:Depreciation", 50,
              role="substitute", code="depreciation_only", note="n"),
        _mrow("PretaxIncome", "ext:IncomeLossFromContinuingOperationsBeforeIncomeTaxes", 3,
              cik=MCD, source="family_coverage"),
        _mrow("Country", "country:US", 1),
    ]

    def test_members_are_the_standard_rows_without_the_sliced_families(self):
        assert ec.classifier_members(self.ROWS) == [
            {"family": "Revenue", "qname": "us-gaap:Revenues", "role": "primary",
             "substitution_code": None, "cik": None},
            {"family": "InterestIncome", "qname": "ifrs-full:RevenueFromInterest",
             "role": "primary", "substitution_code": None, "cik": TSM},
            {"family": "DepreciationAndAmortization", "qname": "us-gaap:Depreciation",
             "role": "substitute", "substitution_code": "depreciation_only", "cik": None},
        ]

    def test_the_hand_list_is_the_cik_scoped_ext_rows(self):
        assert ec.listed_extensions(self.ROWS) == [
            {"cik": MCD, "family": "PretaxIncome",
             "qname": "ext:IncomeLossFromContinuingOperationsBeforeIncomeTaxes",
             "role": "primary"}]

    def test_committed_members_are_accepted_by_the_classifier(self):
        # A non-standard members row would raise ValueError (spec R2).
        committed = committed_mapping_rows()
        members = ec.classifier_members(committed)
        assert members and classify_extensions([], members) == []
        assert all(r["qname"].startswith("ext:") for r in ec.listed_extensions(committed))


class TestFixtureEndToEnd:
    """The spec's fixture filings through this module's builders give C8's rows."""

    def test_c8_rows_agreements_and_no_disagreement(self):
        # oracle: spec C8 (the fixture statements checked by hand, and the hand list)
        tables = load_tables(_fixture_quarters())
        arcs, ciks = _calc_arcs()
        filings = ec.classifier_filings(tables["pre"], tables["num"], arcs, ciks)
        committed = committed_mapping_rows()
        results = classify_extensions(filings, ec.classifier_members(committed))
        out = extension_mapping_rows(results, ec.listed_extensions(committed))
        assert out["rows"] == C8_ROWS
        assert len(out["agreements"]) == 8
        assert out["disagreements"] == ()
        assert len(filings) == 16


# ---------------------------------------------------------------------------
# Output: ranking
# ---------------------------------------------------------------------------


class TestClassifierMappingRows:
    STORED = [
        _mrow("StockRepurchases", "us-gaap:PaymentsForRepurchaseOfCommonStock", 1),
        _mrow("StockRepurchases", "us-gaap:PaymentsForRepurchaseOfEquity", 2),
        _mrow("StockRepurchases", "ifrs-full:PaymentsToAcquireOrRedeemEntitysShares", 100),
        _mrow("DepreciationAndAmortization", "us-gaap:DepreciationDepletionAndAmortization", 1),
        _mrow("DepreciationAndAmortization", "us-gaap:Depreciation", 50, role="substitute",
              code="depreciation_only", note="Depreciation only."),
        _mrow("DepreciationAndAmortization", "us-gaap:DepreciationAmortizationAndAccretionNet",
              52, role="substitute", code="includes_accretion", note=ACCRETION),
        _mrow("DepreciationAndAmortization", "us-gaap:Depreciation", 53, cik=LMT,
              role="substitute", code="depreciation_only", note="Depreciation only.",
              source="auto_override"),
    ]

    def _rank(self, rows, stored=None):
        return [(r["concept_family"], r["qname"], r["preference_rank"])
                for r in ec.classifier_mapping_rows(rows, stored or self.STORED)]

    def test_primary_ranks_after_every_primary_of_the_filer_ifrs_included(self):
        # The generic ranking ends with an IFRS primary at 100.
        row = dict(C8_ROWS[2])
        assert self._rank([row]) == [("StockRepurchases", row["qname"], 101)]

    def test_primary_ranks_after_what_the_facts_count_pass_can_give(self):
        # TSMC's facts-count ranking renumbers the three generic rows 1..3; the pass
        # never gives a primary more than 3, so 4 never ties with a later run.
        stored = self.STORED + [
            _mrow("StockRepurchases", q, r, cik=TSM, source="auto_override")
            for q, r in (("ifrs-full:PaymentsToAcquireOrRedeemEntitysShares", 1),
                         ("us-gaap:PaymentsForRepurchaseOfCommonStock", 2),
                         ("us-gaap:PaymentsForRepurchaseOfEquity", 3))]
        assert self._rank([dict(C8_ROWS[2])], stored)[0][2] == 4

    def test_primary_ranks_after_a_hand_listed_extension(self):
        # TSMC's listed interest-income extension ranks after RevenueFromInterest (101).
        stored = [
            _mrow("InterestIncome", "us-gaap:InvestmentIncomeInterest", 1),
            _mrow("InterestIncome", "ifrs-full:RevenueFromInterest", 100),
            _mrow("InterestIncome", "ext:InvestmentIncomeInterest", 101, cik=TSM,
                  source="family_coverage")]
        assert self._rank([dict(C8_ROWS[1])], stored)[0][2] == 102

    def test_substitute_ranks_after_the_filers_component_band(self):
        (row,) = ec.classifier_mapping_rows([dict(C8_ROWS[0])], self.STORED)
        assert row["preference_rank"] == 54  # LMT's own band ends at 53
        assert row["concept_role"] == "substitute"
        assert row["substitution_code"] == "includes_accretion"
        assert row["substitution_note"] == ACCRETION
        assert row["source"] == "extension_classifier" and row["cik_override"] == LMT

    def test_two_rows_of_one_pair_take_successive_ranks(self):
        a = dict(C8_ROWS[2], qname="ext:A")
        b = dict(C8_ROWS[2], qname="ext:B")
        assert [r for _, _, r in self._rank([b, a])] == [101, 102]

    def test_a_code_without_a_note_raises(self):
        with pytest.raises(ValueError, match="no note"):
            ec.classifier_mapping_rows(
                [dict(C8_ROWS[0], substitution_code="unknown_code")], self.STORED)

    def test_a_full_primary_band_raises(self):
        stored = [_mrow("Revenue", f"us-gaap:R{i}", i) for i in range(1, 50)]
        with pytest.raises(ValueError, match="primary band"):
            ec.classifier_mapping_rows(
                [dict(C8_ROWS[1], family="Revenue")], stored)


# ---------------------------------------------------------------------------
# The populator's write (fake connection)
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, db):
        self.db, self._rows, self.description, self.rowcount = db, [], None, 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.db.log.append((text, params))
        self._rows, self.description = [], None
        for marker, (columns, rows) in self.db.answers.items():
            if marker in text:
                self._rows, self.description = rows, [(c,) for c in columns]
                break
        self.rowcount = 2 if text.startswith("DELETE") else len(self._rows)

    def fetchall(self):
        return list(self._rows)


class _Conn:
    def __init__(self, answers):
        self.answers, self.log, self.batches = answers, [], []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.log.append(("COMMIT", None))


@pytest.fixture
def populated(monkeypatch, tmp_path):
    """Run populate_extension_classifications over the fixtures against a fake DB."""
    arcs, ciks = _calc_arcs()
    arc_cols = ["accession_no", "link_role_uri", "parent_concept", "child_concept", "weight"]
    map_cols = ["concept_family", "qname", "preference_rank", "cik_override",
                "substitution_code", "substitution_note"]
    stored = [tuple(r[c] for c in map_cols) for r in committed_mapping_rows()]
    conn = _Conn({
        "FROM filing_registry fr": (["accession_no", "cik"], sorted(ciks.items())),
        "FROM network n": (arc_cols, [tuple(a[c] for c in arc_cols) for a in arcs]),
        "FROM concept_mapping WHERE source NOT IN": (map_cols, stored),
    })
    monkeypatch.setattr(layer0_populator.psycopg2, "connect", lambda **_: conn)
    monkeypatch.setattr(
        layer0_populator.psycopg2.extras, "execute_batch",
        lambda cur, sql, rows, page_size=100: conn.batches.append(
            (" ".join(sql.split()), list(rows))))
    synced = []
    pop = Layer0Populator(sec_edgar_root=".", db_conn_params={})
    monkeypatch.setattr(pop, "_sync_ns_expanded", lambda c: synced.append(c) or {"n": 1})
    fsds = tmp_path / "fsds"
    for q in _fixture_quarters():
        shutil.copytree(q, fsds / q.name)

    def run(dry_run):
        stats = pop.populate_extension_classifications(
            fsds, report_path=tmp_path / "report.json", dry_run=dry_run)
        return stats, conn, synced, tmp_path / "report.json"
    return run


class TestPopulate:
    def test_writes_the_rows_then_re_derives_the_copies_in_one_transaction(self, populated):
        stats, conn, synced, report = populated(dry_run=False)
        assert [(r["cik_override"], r["qname"]) for r in stats["rows"]] == [
            (r["cik"], r["qname"]) for r in C8_ROWS]
        assert stats["agreements"] == 8 and stats["disagreements"] == 0
        writes = [(sql.split()[0], p) for sql, p in conn.log
                  if sql.startswith(("DELETE", "INSERT")) or sql == "COMMIT"]
        assert writes == [("DELETE", ("extension_classifier",)), ("COMMIT", None)]
        assert [len(rows) for _, rows in conn.batches] == [3]
        assert all(r["source"] == "extension_classifier" for r in conn.batches[0][1])
        assert len(synced) == 1 and stats["ns_expanded"] == {"n": 1}
        body = json.loads(report.read_text(encoding="utf-8"))
        assert body["disagreement_list"] == []
        assert len(body["unclassified_list"]) == sum(stats["unclassified"].values())
        assert body["accessions_in_fsds"] == 16

    def test_the_arcs_are_read_for_the_filings_fsds_holds(self, populated):
        _, conn, _, _ = populated(dry_run=True)
        (params,) = [p for sql, p in conn.log if "FROM network n" in sql]
        assert params == (sorted(_calc_arcs()[1]),)

    def test_a_dry_run_writes_nothing(self, populated):
        stats, conn, synced, report = populated(dry_run=True)
        assert len(stats["rows"]) == 3 and stats["dry_run"]
        assert not [sql for sql, _ in conn.log
                    if sql.startswith(("DELETE", "INSERT")) or sql == "COMMIT"]
        assert not conn.batches and not synced
        assert report.is_file()

    def test_no_fsds_quarter_fails_loud(self, tmp_path):
        pop = Layer0Populator(sec_edgar_root=".", db_conn_params={})
        with pytest.raises(FileNotFoundError):
            pop.populate_extension_classifications(tmp_path / "absent", dry_run=True)


class TestReplaceSourceHandListWins:
    def test_a_listed_extension_deletes_the_classifiers_row_of_its_key(self, monkeypatch):
        conn = _Conn({})
        monkeypatch.setattr(
            layer0_populator.psycopg2.extras, "execute_batch",
            lambda cur, sql, rows, page_size=100: conn.batches.append((sql, list(rows))))
        records = [
            ConceptMappingRecord("InterestIncome", "ext:InvestmentsIncomeInterest", 5, TSM,
                                 False, "family_coverage"),
            ConceptMappingRecord("Revenue", "us-gaap:Revenues", 1, None, False, "hifi_kpi"),
        ]
        Layer0Populator(sec_edgar_root=".", db_conn_params={}) \
            ._replace_source_concept_mapping(conn, records)
        (params,) = [p for sql, p in conn.log
                     if sql.startswith("DELETE") and "unnest" in sql]
        assert params == ("extension_classifier", ["InterestIncome"],
                          ["ext:InvestmentsIncomeInterest"], [TSM])


def test_bootstrap_classifies_after_layer0_populate_and_before_the_mv_build():
    text = (Path(PROJECT_ROOT) / "scripts" / "bootstrap_db.py").read_text(encoding="utf-8")
    order = [text.index(f'id="{s}"') for s in
             ("concept-mapping", "layer0-populate", "extension-classify", "mv-build")]
    assert order == sorted(order)
