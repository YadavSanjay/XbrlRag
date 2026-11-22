"""Tests for the concept-family coverage file and how the generator applies it
(docs/specs/concept-family-coverage.md). Pure: no DB.

The locked spec tests read the result through ``family_member``; these cover the
validation rules and the rows written for the load.
"""

from __future__ import annotations

import pytest

from src.ingest.util.generate_concept_mapping import (
    MAPPING_CSV_COLUMNS,
    apply_family_coverage,
    committed_mapping_rows,
    validate_family_coverage,
)

P = {"Debt": ["us-gaap:LongTermDebt", "us-gaap:DebtLongtermAndShorttermCombinedAmount"]}
C = {"Debt": ["us-gaap:DebtCurrent", "us-gaap:LongTermDebtNoncurrent"]}
NOTES = {"excludes_current_portion": "Long-term debt excluding the current portion."}
CIK = "0000000042"


def _rows():
    return [
        {"concept_family": "Debt", "qname": q, "preference_rank": r, "cik_override": "",
         "is_deprecated": False, "source": "hifi_kpi"}
        for q, r in [("us-gaap:LongTermDebt", 1),
                     ("us-gaap:DebtLongtermAndShorttermCombinedAmount", 2),
                     ("us-gaap:DebtCurrent", 50), ("us-gaap:LongTermDebtNoncurrent", 51)]
    ]


def _filer(**kw):
    entry = {"cik": CIK, "family": "Debt", "concept": "ext:TotalBorrowings",
             "role": "primary", "reason": "test"}
    entry.update(kw)
    return entry


def _coverage(**raw):
    return validate_family_coverage(
        {"version": 1, "substitutions": NOTES, **raw}, P, C)


class TestValidation:
    def test_a_substitute_must_be_a_component(self):
        with pytest.raises(ValueError, match="not a component"):
            _coverage(substitutes={"Debt": {"us-gaap:LongTermDebt": "excludes_current_portion"}})

    def test_an_unknown_code_is_refused(self):
        with pytest.raises(ValueError, match="unknown substitution code"):
            _coverage(substitutes={"Debt": {"us-gaap:DebtCurrent": "nope"}})

    def test_a_filer_entry_needs_a_reason_and_a_known_role(self):
        with pytest.raises(ValueError):
            _coverage(filers=[_filer(reason="")])
        with pytest.raises(ValueError, match="role"):
            _coverage(filers=[_filer(role="component")])

    def test_a_generic_member_cannot_be_cik_scoped_again(self):
        with pytest.raises(ValueError, match="already a member"):
            _coverage(filers=[_filer(concept="us-gaap:LongTermDebt")])

    def test_unknown_keys_are_refused(self):
        with pytest.raises(ValueError, match="unknown keys"):
            _coverage(extra={})


class TestApply:
    def test_roles_and_substitutions_are_written(self):
        cov = _coverage(substitutes={"Debt": {"us-gaap:LongTermDebtNoncurrent":
                                              "excludes_current_portion"}})
        rows = {r["qname"]: r for r in apply_family_coverage(_rows(), cov)}
        assert rows["us-gaap:LongTermDebt"]["concept_role"] == "primary"
        assert rows["us-gaap:DebtCurrent"]["concept_role"] == "component"
        ltd = rows["us-gaap:LongTermDebtNoncurrent"]
        assert (ltd["concept_role"], ltd["preference_rank"]) == ("substitute", 51)
        assert ltd["substitution_code"] == "excludes_current_portion"
        assert ltd["substitution_note"] == NOTES["excludes_current_portion"]

    def test_positions(self):
        last = apply_family_coverage(_rows(), _coverage(filers=[_filer()]))
        ext = [r for r in last if r["cik_override"] == CIK]
        assert [(r["qname"], r["preference_rank"]) for r in ext] == [("ext:TotalBorrowings", 3)]

        first = apply_family_coverage(_rows(), _coverage(filers=[_filer(position="first")]))
        mine = {r["qname"]: r["preference_rank"] for r in first if r["cik_override"] == CIK}
        assert mine == {"ext:TotalBorrowings": 1, "us-gaap:LongTermDebt": 2,
                        "us-gaap:DebtLongtermAndShorttermCombinedAmount": 3,
                        "us-gaap:DebtCurrent": 50, "us-gaap:LongTermDebtNoncurrent": 51}

        after = apply_family_coverage(_rows(), _coverage(filers=[_filer(
            position={"after": "us-gaap:DebtLongtermAndShorttermCombinedAmount"})]))
        assert [r["preference_rank"] for r in after if r["cik_override"] == CIK] == [3]
        with pytest.raises(ValueError, match="taken"):
            apply_family_coverage(_rows(), _coverage(filers=[_filer(
                position={"after": "us-gaap:LongTermDebt"})]))

    def test_a_substitute_ranks_after_the_component_band(self):
        rows = apply_family_coverage(_rows(), _coverage(filers=[_filer(
            role="substitute", substitution="excludes_current_portion")]))
        ext = next(r for r in rows if r["cik_override"] == CIK)
        assert (ext["preference_rank"], ext["concept_role"]) == (52, "substitute")


def test_committed_rows_carry_every_csv_column():
    rows = committed_mapping_rows()
    assert rows
    assert all(set(MAPPING_CSV_COLUMNS) <= set(r) for r in rows)
    roles = {r["concept_role"] for r in rows}
    assert roles == {"primary", "substitute", "component"}
    keys = [(r["concept_family"], r["qname"], r["cik_override"]) for r in rows]
    assert len(keys) == len(set(keys))
