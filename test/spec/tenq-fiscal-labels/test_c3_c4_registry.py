"""C3, C4: one quarter label in the registry, the calendar, the manifest and the MVs.

Oracle: FSDS ``sub`` rows (fiscal year and quarter derived from ``period`` and ``fye``, with
SEC's ``fy`` offsets handled in conftest.py), the ORCL filing text, and the MV DDL source.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.ingest.layer0_populator import apply_fiscal_labels

ROOT = Path(__file__).resolve().parents[3]
MV_FLAT = ROOT / "scripts" / "ddl" / "mv" / "10_mv_financial_facts.sql"
MV_MULTI = ROOT / "scripts" / "ddl" / "mv" / "11_mv_financial_facts_multidim.sql"


def _keys(records) -> dict[str, tuple[int, str]]:
    return {r.accession_no: (r.fiscal_year, r.fiscal_period) for r in records}


def _expected(kit) -> dict[str, tuple[int, str]]:
    """FSDS-derived (year, quarter) of the 9 mismatched 10-Qs; SEC's fy and FY for 10-Ks."""
    out = {}
    for acc, m in kit.MANIFESTS.items():
        if m["form"] == "10-Q":
            year, quarter = kit.fsds_label(kit.SUB[acc])
            out[acc] = (year, f"Q{quarter}")
        else:
            out[acc] = (kit.OWN_FY[acc], "FY")
    return out


# ------------------------------------------------------------------ C3: the records


@pytest.mark.criterion("C3")
def test_the_nine_mismatched_10qs_take_the_fsds_quarter(kit):
    # oracle: FSDS sub period/fye/fp for each 10-Q (sub.fy never used: it repeats ORCL's DEI
    # error), and SEC's fy for every 10-K, which these filers' own year names match
    out = apply_fiscal_labels(kit.RECORDS, kit.DEI)
    got = _keys(out)
    assert {acc: got[acc] for acc in kit.MISMATCHED} == {
        acc: _expected(kit)[acc] for acc in kit.MISMATCHED}
    assert got == _expected(kit)


@pytest.mark.criterion("C3")
def test_each_10q_named_by_hand(kit):
    # oracle: the filers' own names for these quarters (companyfacts fp; ORCL's text for -389274)
    got = _keys(apply_fiscal_labels(kit.RECORDS, kit.DEI))
    assert {acc: got[acc] for acc in kit.MISMATCHED} == {
        "0000320193-26-000013": (2026, "Q2"), "0000320193-26-000020": (2026, "Q3"),
        "0001193125-26-191507": (2026, "Q3"), "0000723125-26-000006": (2026, "Q2"),
        "0000723125-26-000015": (2026, "Q3"), "0001045810-26-000052": (2027, "Q1"),
        "0001045810-26-000075": (2027, "Q2"), "0001193125-26-101045": (2026, "Q3"),
        "0001193125-26-389274": (2027, "Q1"),
    }


@pytest.mark.criterion("C3")
def test_orcl_first_quarters_of_two_years_key_apart(kit):
    # oracle: report G2 — Oracle's fiscal 2026 Q1 (Jun-Aug 2025) and fiscal 2027 Q1 (Jun-Aug
    # 2026, by its own text) are two supersession groups, not one
    records = [r for r in kit.RECORDS if r.ticker == "ORCL"] + [kit.orcl_q1_fy2026_record()]
    dei = {**kit.DEI, kit.ORCL_Q1_FY2026: kit.BY_ACCESSION[kit.ORCL_Q1_FY2026]["fy"]}
    got = _keys(apply_fiscal_labels(records, dei))
    assert got[kit.ORCL_Q1_FY2026] == (2026, "Q1")
    assert got[kit.ORCL_Q1_FY2027] == (2027, "Q1")
    groups = [(r.cik, *got[r.accession_no]) for r in records]
    assert len(groups) == len(set(groups))


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(data=st.data())
def test_10q_labels_do_not_depend_on_the_manifest_quarter_or_year(kit, data):
    # oracle: FSDS sub as above — metamorphic: whatever quarter (1-3) and calendar year a 10-Q's
    # manifest holds, the record's label is the same
    garbled = [
        dataclasses.replace(r, quarter=data.draw(st.integers(1, 3)),
                            fiscal_year=data.draw(st.integers(2020, 2030)))
        if r.form_type == "10-Q" else r
        for r in kit.RECORDS
    ]
    assert _keys(apply_fiscal_labels(garbled, kit.DEI)) == _expected(kit)


# ------------------------------------------------------------------ C4: the MVs


def _select_expression(sql: str) -> str:
    """The expression selected ``AS fiscal_period`` in a MV definition, whitespace-normalized."""
    body = re.sub(r"--[^\n]*", "", sql)
    found = re.findall(r"^\s*(\S.*?)\s+AS\s+fiscal_period\s*,?\s*$", body, flags=re.M | re.I)
    assert len(found) == 1, f"expected one fiscal_period column, found {found}"
    return " ".join(found[0].split())


@pytest.mark.criterion("C4")
def test_the_flat_mv_reads_the_registry_quarter():
    # oracle: requirement 4, read from the DDL source — the registry's quarter, not DEI's
    assert _select_expression(MV_FLAT.read_text(encoding="utf-8")) == "fr.fiscal_period"


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_both_fact_mvs_select_one_quarter_expression():
    # oracle: requirement 4, read from the DDL source — flat and multidim agree, and the
    # multidim MV's GROUP BY names the same expression instead of the DEI tag
    flat = MV_FLAT.read_text(encoding="utf-8")
    multi = MV_MULTI.read_text(encoding="utf-8")
    expr = _select_expression(multi)
    assert expr == _select_expression(flat) == "fr.fiscal_period"
    group_by = " ".join(re.sub(r"--[^\n]*", "", multi).split("GROUP BY", 1)[1].split())
    assert expr in group_by
    assert "dei.fiscal_period_focus" not in group_by
