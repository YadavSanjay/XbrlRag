"""Implementation tests for ifrs-family-meaning (docs/specs/ifrs-family-meaning.md).

The locked spec tests cover the answers; these cover the coverage-file rules the change
added (IFRS substitutes outside a family, ``parts``), the unit matching of a requested
currency, and the refusal that names a family's parts (D45). Pure: the only fake is the
fact-store SQL (``_connect`` / ``_query_flat``).
"""

from __future__ import annotations

import contextlib

import pytest

from src.ingest.util.generate_concept_mapping import (
    apply_family_coverage,
    committed_mapping_rows,
    fact_family,
    validate_family_coverage,
)
from src.retriever.xbrl.family_resolution import family_member, family_parts
from src.retriever.xbrl.xbrl_factstore_lookup import (
    FactCandidate,
    LookupStatus,
    XbrlFactstoreLookup,
    _requested_unit_key,
)

P = {"NetIncome": ["us-gaap:NetIncomeLoss"], "SGA": ["us-gaap:SGA"],
     "SM": ["us-gaap:SM"], "GA": ["us-gaap:GA"]}
C = {"NetIncome": ["us-gaap:OtherPart"]}
NOTES = {"nci": "Including non-controlling interests."}
OTHER_CIK = "0000000042"


def _coverage(**raw):
    return validate_family_coverage({"version": 1, "substitutions": NOTES, **raw}, P, C)


def _rows():
    return [
        {"concept_family": "NetIncome", "qname": q, "preference_rank": r, "cik_override": "",
         "is_deprecated": False, "source": "hifi_kpi"}
        for q, r in [("us-gaap:NetIncomeLoss", 1), ("us-gaap:OtherPart", 50)]
    ]


class TestCoverageRules:
    def test_an_ifrs_element_outside_the_family_may_be_a_substitute(self):
        cov = _coverage(substitutes={"NetIncome": {"ifrs-full:ProfitLoss": "nci"}})
        rows = {r["qname"]: r for r in apply_family_coverage(_rows(), cov)}
        pl = rows["ifrs-full:ProfitLoss"]
        assert (pl["preference_rank"], pl["concept_role"], pl["source"]) == (
            51, "substitute", "ifrs")
        assert pl["substitution_note"] == NOTES["nci"]

    def test_a_us_gaap_non_component_is_still_refused(self):
        with pytest.raises(ValueError, match="not a component"):
            _coverage(substitutes={"NetIncome": {"us-gaap:ProfitLoss": "nci"}})

    def test_an_ifrs_primary_of_the_family_cannot_also_be_its_substitute(self):
        with pytest.raises(ValueError, match="not a component"):
            _coverage(ifrs={"NetIncome": ["ifrs-full:ProfitLoss"]},
                      substitutes={"NetIncome": {"ifrs-full:ProfitLoss": "nci"}})

    def test_parts_are_validated(self):
        assert _coverage(parts={"SGA": ["SM", "GA"]})["parts"] == {"SGA": ["SM", "GA"]}
        for bad in ({"SGA": ["SM"]}, {"SGA": ["SM", "SM"]}, {"SGA": ["SM", "Nope"]},
                    {"SGA": ["SGA", "SM"]}, {"Nope": ["SM", "GA"]}):
            with pytest.raises(ValueError, match="parts"):
                _coverage(parts=bad)


class TestCommittedMapping:
    def test_marketing_belongs_to_selling_and_marketing_only(self):
        row = fact_family(committed_mapping_rows(), OTHER_CIK, "ifrs-full:SalesAndMarketingExpense")
        assert row is not None and row["concept_family"] == "SellingAndMarketing"
        assert family_member("SellingGeneralAndAdministrative", OTHER_CIK,
                             "ifrs-full:SalesAndMarketingExpense") is None

    @pytest.mark.parametrize("family,qname", [
        ("InterestIncome", "ifrs-full:FinanceIncome"),
        ("NetIncome", "ifrs-full:ProfitLoss"),
        ("StockholdersEquity", "ifrs-full:Equity"),
        ("ComprehensiveIncome", "ifrs-full:ComprehensiveIncome"),
    ])
    def test_the_decided_substitutes_state_themselves(self, family, qname):
        m = family_member(family, OTHER_CIK, qname)
        assert m is not None and m["role"] == "substitute"
        assert m["substitution"]["note"]
        row = fact_family(committed_mapping_rows(), OTHER_CIK, qname)
        assert row is not None and row["concept_family"] == family

    def test_sga_parts(self):
        assert family_parts("SellingGeneralAndAdministrative") == (
            "SellingAndMarketing", "GeneralAndAdministrative")
        assert family_parts("Revenue") == ()


class TestRequestedUnit:
    def test_a_currency_code_matches_its_per_share_unit(self):
        units = {"TWD/shares", "USD/shares"}
        assert _requested_unit_key("USD", units) == "USD/shares"
        assert _requested_unit_key("shares*USD", units) == "USD/shares"
        assert _requested_unit_key("EUR", units) == "EUR"
        assert _requested_unit_key("USD", {"USD", "TWD"}) == "USD"
        assert _requested_unit_key(None, units) is None

    def test_restrict_keeps_the_requested_per_share_unit_in_any_spelling(self):
        cands: list[FactCandidate] = [
            XbrlFactstoreLookup._row_to_candidate(
                {"fact_id": i, "value": v, "unit": u, "period_end": end})
            for i, v, u, end in [(1, 44.67, "shares*TWD", "2025-01-01"),
                                 (2, 1.36, "USD*shares", "2025-01-01"),
                                 (3, 31.0, "TWD/shares", "2024-01-01")]
        ]
        kept, status = XbrlFactstoreLookup._restrict_to_one_unit(cands, "USD", [])
        assert status is None and [c.fact_id for c in kept] == [2]
        kept, status = XbrlFactstoreLookup._restrict_to_one_unit(cands, None, [])
        assert status is None and [c.fact_id for c in kept] == [1, 3]
        _, status = XbrlFactstoreLookup._restrict_to_one_unit(cands, "EUR", [])
        assert status == LookupStatus.NOT_FOUND


def _part_row(fact_id, value, family, concept):
    return {"fact_id": fact_id, "value": value, "unit": "TWD", "period_type": "duration",
            "period_start": "2024-01-01", "period_end": "2025-01-01", "concept": concept,
            "concept_family": family, "concept_pref_rank": 1, "concept_role": "primary",
            "presented": True, "decimals": -5, "accession_no": "0000000000-25-000001"}


class TestPartsRefusal:
    STEP = {"id": "s1", "op": "lookup", "cik": OTHER_CIK, "fy": 2024,
            "concept_family": "SellingGeneralAndAdministrative", "dimensions": "NONE"}

    def _lookup(self, monkeypatch, by_family):
        lk = XbrlFactstoreLookup()
        monkeypatch.setattr(lk, "_connect", lambda: contextlib.nullcontext(object()))

        def query_flat(conn, cik, fy, family, *args, **kwargs):
            return [dict(r) for r in by_family.get(family, [])]

        monkeypatch.setattr(lk, "_query_flat", query_flat)
        monkeypatch.setattr(lk, "_retry_across_axes", lambda *a, **k: [])
        return lk.lookup(dict(self.STEP))

    def test_a_refusal_names_both_parts(self, monkeypatch):
        r = self._lookup(monkeypatch, {
            "SellingAndMarketing": [_part_row(11, 13_143_600_000.0, "SellingAndMarketing",
                                              "ifrs-full:SalesAndMarketingExpense")],
            "GeneralAndAdministrative": [_part_row(12, 83_745_000_000.0,
                                                   "GeneralAndAdministrative",
                                                   "ifrs-full:GeneralAndAdministrativeExpense")],
        })
        assert r.status == LookupStatus.NOT_FOUND and r.value is None and r.citation is None
        assert r.error is not None and r.error == r.resolution_notes[-1]
        assert "SellingAndMarketing 13,143,600,000 TWD (fact_id 11)" in r.error
        assert "GeneralAndAdministrative 83,745,000,000 TWD (fact_id 12)" in r.error

    def test_no_parts_note_when_a_part_is_missing(self, monkeypatch):
        r = self._lookup(monkeypatch, {
            "SellingAndMarketing": [_part_row(11, 1.0, "SellingAndMarketing",
                                              "ifrs-full:SalesAndMarketingExpense")],
        })
        assert r.status == LookupStatus.NOT_FOUND
        assert r.error is None
        assert not any("compute" in n for n in r.resolution_notes)

    def test_a_reported_total_is_answered_as_before(self, monkeypatch):
        total = _part_row(21, 96_888_600_000.0, "SellingGeneralAndAdministrative",
                          "us-gaap:SellingGeneralAndAdministrativeExpense")
        r = self._lookup(monkeypatch, {"SellingGeneralAndAdministrative": [total]})
        assert r.status == LookupStatus.OK and r.value == 96_888_600_000.0
