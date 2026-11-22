"""C7-C9: the label reaches the registries and both fact MVs (requirements 7-9).

Oracle: the same filings as C1-C2, by hand. The MV definitions need Postgres and the registries
need a connection, so C9 and the two wiring tests read committed source; the spec's Notes say why.
"""

from __future__ import annotations

import copy
import inspect
import re
from datetime import date
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.ingest.layer0_populator import (
    Layer0Populator,
    apply_fiscal_labels,
    stale_fiscal_calendar_keys,
)

REPO = Path(__file__).resolve().parents[3]
MV_FLAT = REPO / "scripts" / "ddl" / "mv" / "10_mv_financial_facts.sql"
MV_MULTI = REPO / "scripts" / "ddl" / "mv" / "11_mv_financial_facts_multidim.sql"


def _hd(kit) -> list:
    return [kit.manifest(r, kit.HD_CIK, "HD") for r in kit.HD]


def _crm(kit) -> list:
    return [kit.manifest(r, kit.CRM_CIK, "CRM") for r in kit.CRM]


def _years(records) -> dict[str, int]:
    return {r.accession_no: r.fiscal_year for r in records}


# -------------------------------------------------------------------- C7: the registry


@pytest.mark.criterion("C7")
def test_hd_reads_its_dei_and_crm_keeps_2026(kit):
    # oracle: requirement 7 — manifests say 2024-2026 for HD and 2023-2026 for CRM; the labels
    # are HD 2023, 2024, 2025 and CRM 2023, 2024, 2025, 2026
    out = apply_fiscal_labels([*_hd(kit), *_crm(kit)], kit.dei_of([*kit.HD, *kit.CRM]))
    assert _years(out) == {kit.H24: 2023, kit.H25: 2024, kit.H26: 2025, kit.C23: 2023,
                           kit.C24: 2024, kit.C25: 2025, kit.C26: 2026}


@pytest.mark.criterion("C7")
def test_a_filers_10q_takes_its_dei(kit):
    # oracle: requirement 7 — a January filer's 10-Q with manifest 2026, DEI 2027; its
    # calendar-derived fiscal_period is out of scope and stays
    row = kit.filing("0000900202-26-000052", date(2026, 4, 26), 2027, form="10-Q")
    record = kit.manifest(row, kit.JAN_CIK, "JANF", quarter=2)
    (out,) = apply_fiscal_labels([record], kit.dei_of([row]))
    assert out.fiscal_year == 2027
    assert out.fiscal_period == "Q2"


@pytest.mark.criterion("C7")
def test_a_filing_with_no_dei_entry_keeps_its_calendar_year(kit):
    # oracle: requirement 5 — not in the mapping counts as no DEI
    records = [kit.manifest(kit.HD[0], kit.HD_CIK, "HD")]
    (out,) = apply_fiscal_labels(records, {})
    assert out.fiscal_year == 2024


@pytest.mark.criterion("C7")
def test_supersession_groups_keep_their_shape(kit):
    # oracle: _resolve_superseded_by groups on (cik, fiscal_year, fiscal_period): a 10-K and its
    # 10-K/A stay one group, and CRM's two January filings are two
    orig = kit.filing("o", date(2024, 12, 31), 2024, filed=date(2025, 1, 30))
    amend = kit.filing("a", date(2024, 12, 31), 2024, form="10-K/A", filed=date(2025, 4, 30))
    pair = [kit.manifest(r, kit.SYN_CIK, "SYNF") for r in (orig, amend)]
    a, b = apply_fiscal_labels(pair, kit.dei_of([orig, amend]))
    assert (a.cik, a.fiscal_year, a.fiscal_period) == (b.cik, b.fiscal_year, b.fiscal_period)
    crm = apply_fiscal_labels(_crm(kit), kit.dei_of(kit.CRM))
    keys = {(r.cik, r.fiscal_year, r.fiscal_period) for r in crm}
    assert len(keys) == len(kit.CRM)


@pytest.mark.criterion("C7")
def test_the_populator_applies_the_labels():
    # oracle: requirement 7 — the populator is the only writer of both tables. Read from source:
    # the method needs a live connection (spec Notes)
    assert "apply_fiscal_labels(" in inspect.getsource(Layer0Populator)


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
def test_nothing_else_changes_and_inputs_are_left_alone(kit):
    # oracle: requirement 7 — the manifests, and so the downloader's calendar-year paths, are
    # never touched: only fiscal_year differs, on copies, in input order
    records = _hd(kit)
    frozen = copy.deepcopy(records)
    out = apply_fiscal_labels(records, kit.dei_of(kit.HD))
    assert records == frozen
    assert [r.accession_no for r in out] == [r.accession_no for r in frozen]
    assert all(a is not b for a, b in zip(out, records, strict=True))
    for new, old in zip(out, frozen, strict=True):
        assert new.fiscal_year == old.fiscal_year - 1
        new.fiscal_year = old.fiscal_year
        assert new == old


@pytest.mark.criterion("C7")
@given(split=st.integers(min_value=0, max_value=7))
def test_filers_are_labelled_independently(kit, split):
    # oracle: requirement 7 — metamorphic: CRM's collision is CRM's, wherever its records fall
    # among HD's, and one call over both equals one call each
    hd, crm = _hd(kit), _crm(kit)
    mixed = [*crm[:split], *hd, *crm[split:]]
    out = apply_fiscal_labels(mixed, kit.dei_of([*kit.HD, *kit.CRM]))
    assert _years(out) == {**_years(apply_fiscal_labels(hd, kit.dei_of(kit.HD))),
                           **_years(apply_fiscal_labels(crm, kit.dei_of(kit.CRM)))}
    assert len(out) == len(mixed)


# -------------------------------------------------------------------- C8: fiscal_calendar


@pytest.mark.criterion("C8")
def test_hd_leaves_one_stale_row(kit):
    # oracle: requirement 8 — HD's rows 2024, 2025, 2026 FY, labelled 2023, 2024, 2025: only
    # 2026 is no longer produced. WMT is not in the run, so its row is not touched
    labelled = apply_fiscal_labels(_hd(kit), kit.dei_of(kit.HD))
    existing = [(kit.HD_CIK, 2024, "FY"), (kit.HD_CIK, 2025, "FY"), (kit.HD_CIK, 2026, "FY"),
                (kit.WMT_CIK, 2020, "FY")]
    assert stale_fiscal_calendar_keys(existing, labelled) == {(kit.HD_CIK, 2026, "FY")}


@pytest.mark.criterion("C8")
def test_a_quarter_row_is_keyed_by_its_period(kit):
    # oracle: requirement 8 — a 10-Q moved from (2026, Q2) to (2027, Q2) leaves the old row, and
    # the filer's FY row of 2026 is still produced
    q = kit.manifest(kit.filing("q", date(2026, 4, 26), 2027, form="10-Q"), kit.JAN_CIK, "JANF",
                     manifest_year=2027, quarter=2)
    y = kit.manifest(kit.filing("y", date(2026, 1, 25), 2026), kit.JAN_CIK, "JANF")
    existing = [(kit.JAN_CIK, 2026, "Q2"), (kit.JAN_CIK, 2026, "FY")]
    assert stale_fiscal_calendar_keys(existing, [q, y]) == {(kit.JAN_CIK, 2026, "Q2")}


@pytest.mark.criterion("C8")
def test_the_populator_deletes_stale_rows():
    # oracle: requirement 8. Read from source: the delete needs a live connection (spec Notes)
    assert "stale_fiscal_calendar_keys(" in inspect.getsource(Layer0Populator)


@pytest.mark.criterion("C8")
@given(extra=st.sets(st.integers(min_value=2000, max_value=2022), min_size=1, max_size=5))
def test_stale_is_exactly_what_the_run_does_not_produce(kit, extra):
    # oracle: requirement 8 — metamorphic: any existing year of the filer that no record
    # produces is stale; every produced key survives, present or not
    labelled = apply_fiscal_labels(_hd(kit), kit.dei_of(kit.HD))
    produced = {(r.cik, r.fiscal_year, r.fiscal_period) for r in labelled}
    gone = {(kit.HD_CIK, y, "FY") for y in extra}
    assert stale_fiscal_calendar_keys([*produced, *gone], labelled) == gone


# ------------------------------------------------------------------------------ C9: MVs


def _select_expression(sql: str) -> str:
    """The expression selected ``AS fiscal_year`` in a MV definition, whitespace-normalized."""
    body = re.sub(r"--[^\n]*", "", sql)
    found = re.findall(r"^\s*(\S.*?)\s+AS\s+fiscal_year\s*,?\s*$", body, flags=re.M | re.I)
    assert len(found) == 1, f"expected one fiscal_year column, found {found}"
    return " ".join(found[0].split())


@pytest.mark.criterion("C9")
def test_neither_fact_mv_reads_bare_dei():
    # oracle: requirement 9 — CRM's collision comes from selecting the raw DEI tag. Read from the
    # DDL source (spec Notes)
    for path in (MV_FLAT, MV_MULTI):
        expr = _select_expression(path.read_text(encoding="utf-8"))
        assert expr != "dei.fiscal_year_focus", path.name


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
def test_both_fact_mvs_select_one_filing_level_expression():
    # oracle: requirement 9 — all query surfaces agree, so the flat and multidim MVs select the
    # same expression; a filing-level one, so comparatives stay under their filing's label; and
    # the multidim MV's GROUP BY names it
    flat = MV_FLAT.read_text(encoding="utf-8")
    multi = MV_MULTI.read_text(encoding="utf-8")
    expr = _select_expression(flat)
    assert expr == _select_expression(multi)
    assert expr != "dei.fiscal_year_focus"
    assert "ctx." not in expr and "period_end" not in expr
    group_by = " ".join(re.sub(r"--[^\n]*", "", multi).split("GROUP BY", 1)[1].split())
    assert expr in group_by
