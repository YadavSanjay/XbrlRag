"""How the concept-family coverage reaches the database and the view
(docs/specs/concept-family-coverage.md). Pure: no DB.

- The view gives each fact one family. Oracle's cik-scoped ServiceRevenue ranking lists
  the total-revenue elements (rank 2 and 3 there); they must stay Revenue facts.
- ``ext:`` rows expand to the filer's extension namespaces before the view matches them.
- The CSV's role and substitution columns load, and bad ones are refused.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from src.ingest.sec_edgar.ingest.layer0_populator import (
    ConceptMappingRecord,
    Layer0Populator,
    expand_concept_mapping,
    ns_expanded_drift,
)
from src.ingest.util.generate_concept_mapping import committed_mapping_rows, fact_family

REPO = Path(__file__).resolve().parents[2]
ORCL = "0001341439"
MSFT = "0000789019"
# The namespaces of Oracle's FY2025 10-K (accession 0000950170-25-087926).
ORCL_GAAP = "http://fasb.org/us-gaap/2024"
ORCL_EXT = "http://www.oracle.com/20250531"
KNOWN = [
    (ORCL_GAAP, "RevenueFromContractWithCustomerExcludingAssessedTax"),
    (ORCL_GAAP, "Revenues"),
    (ORCL_EXT, "SalesRevenueServicesNet1"),
    # Another filer's extension, and a us-gaap element, with an extension's local name.
    ("http://www.cisco.com/20250726", "DepreciationAmortizationAndOther"),
    ("http://www.microsoft.com/20260630", "DepreciationAmortizationAndOther"),
    ("http://fasb.org/us-gaap/2025", "SalesRevenueServicesNet1"),
]


def _records(rows):
    return [
        ConceptMappingRecord(
            r["concept_family"], r["qname"], r["preference_rank"], r["cik_override"] or None,
            r["is_deprecated"], r["source"], r["concept_role"],
            r["substitution_code"] or None, r["substitution_note"] or None,
        )
        for r in rows
    ]


@pytest.fixture(scope="module")
def loaded():
    """The committed rows plus their namespace copies, as the reload leaves them."""
    base = _records(committed_mapping_rows())
    copies = expand_concept_mapping(base, KNOWN)
    return [dataclasses.asdict(r) for r in base + copies]


def _old_rule(rows, cik, qname):
    """The view's join before the change: any filer row first, then the lowest rank."""
    mine = [r for r in rows if r["qname"] == qname and (r["cik_override"] or "") in ("", cik)]
    return min(mine, key=lambda r: (0 if r["cik_override"] else 1, r["preference_rank"]))


class TestOneFamilyPerFact:
    @pytest.mark.parametrize("local", ["RevenueFromContractWithCustomerExcludingAssessedTax",
                                       "Revenues"])
    def test_oracle_total_revenue_stays_revenue(self, loaded, local):
        qname = f"{ORCL_GAAP}:{local}"
        assert fact_family(loaded, ORCL, qname)["concept_family"] == "Revenue"
        # The old join let Oracle's ServiceRevenue ranking take the fact.
        assert _old_rule(loaded, ORCL, qname)["concept_family"] == "ServiceRevenue"

    def test_oracle_services_line_is_service_revenue(self, loaded):
        row = fact_family(loaded, ORCL, f"{ORCL_EXT}:SalesRevenueServicesNet1")
        assert (row["concept_family"], row["preference_rank"]) == ("ServiceRevenue", 1)

    def test_another_filer_keeps_the_generic_family(self, loaded):
        qname = f"{ORCL_GAAP}:RevenueFromContractWithCustomerExcludingAssessedTax"
        assert fact_family(loaded, "0000000001", qname)["concept_family"] == "Revenue"
        assert fact_family(loaded, "0000000001", f"{ORCL_EXT}:SalesRevenueServicesNet1") is None

    def test_a_primary_beats_a_component_of_another_family(self):
        rows = [
            {"concept_family": "A", "qname": "q", "preference_rank": 50, "cik_override": "c"},
            {"concept_family": "B", "qname": "q", "preference_rank": 7, "cik_override": ""},
        ]
        assert fact_family(rows, "c", "q")["concept_family"] == "B"
        rows[1]["concept_role"] = "substitute"
        rows[1]["preference_rank"] = 60
        assert fact_family(rows, "c", "q")["concept_family"] == "B"  # substitute > component

    def test_the_view_orders_its_join_by_the_same_rule(self):
        sql = (REPO / "scripts/ddl/mv/10_mv_financial_facts.sql").read_text(encoding="utf-8")
        outer = re.search(r"\) per_family\s+ORDER BY(.*?)LIMIT 1", sql, re.S).group(1)
        terms = [t.strip() for t in re.sub(r"CASE.*?END", "ROLE", outer, flags=re.S).split(",")]
        assert terms == ["ROLE", "per_family.preference_rank ASC",
                         "per_family.is_filer_row DESC", "per_family.concept_family ASC"]
        assert "WHEN 'primary'    THEN 0" in outer and "WHEN 'substitute' THEN 1" in outer


class TestExtensionExpansion:
    def test_ext_rows_expand_only_to_extension_namespaces(self, loaded):
        mine = {r["qname"] for r in loaded
                if r["cik_override"] == ORCL and r["source"] == "ns_expanded"
                and r["qname"].endswith(":SalesRevenueServicesNet1")}
        # The us-gaap namespace with the same local name is not the extension.
        assert mine == {f"{ORCL_EXT}:SalesRevenueServicesNet1"}

    def test_copies_stay_cik_scoped_and_carry_the_role(self, loaded):
        msft = [r for r in loaded if r["qname"].endswith(":DepreciationAmortizationAndOther")
                and r["source"] == "ns_expanded"]
        # Cisco's same-named extension gets an MSFT-scoped copy, which no Cisco fact uses.
        assert {r["cik_override"] for r in msft} == {MSFT}
        assert {r["concept_role"] for r in msft} == {"primary"}

    def test_a_generic_ext_row_never_expands(self):
        row = ConceptMappingRecord("F", "ext:X", 1, None, False, "manual")
        assert expand_concept_mapping([row], [("http://www.example.com/2025", "X")]) == []

    def test_copies_are_in_sync_and_drift_is_seen(self):
        base = ConceptMappingRecord("F", "ext:X", 3, "0000000042", False, "family_coverage",
                                    "substitute", "c", "n")
        copies = expand_concept_mapping([base], [("http://www.example.com/2025", "X")])
        assert [c.qname for c in copies] == ["http://www.example.com/2025:X"]
        assert ns_expanded_drift([base, *copies]) == []
        stale = dataclasses.replace(copies[0], substitution_note="old")
        assert ns_expanded_drift([base, stale]) == [(stale, base)]


class TestCsvColumns:
    ROW = {"concept_family": "Debt", "qname": "us-gaap:LongTermDebtNoncurrent",
           "preference_rank": "53", "cik_override": "", "is_deprecated": "False",
           "source": "hifi_kpi"}

    def test_role_and_substitution_load(self):
        rec = Layer0Populator._parse_concept_mapping_row(
            {**self.ROW, "concept_role": "substitute", "substitution_code": "c",
             "substitution_note": "n"}, 2)
        assert (rec.concept_role, rec.substitution_code, rec.substitution_note) == (
            "substitute", "c", "n")

    def test_old_csv_without_the_columns_loads(self):
        rec = Layer0Populator._parse_concept_mapping_row(dict(self.ROW), 2)
        assert rec.concept_role is None and rec.substitution_code is None

    @pytest.mark.parametrize("extra", [
        {"concept_role": "other"},
        {"concept_role": "substitute"},
        {"concept_role": "primary", "substitution_code": "c", "substitution_note": "n"},
    ])
    def test_bad_columns_are_refused(self, extra):
        with pytest.raises(ValueError):
            Layer0Populator._parse_concept_mapping_row({**self.ROW, **extra}, 2)
