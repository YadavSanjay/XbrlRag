"""C6: a defined-family key (Debt, DeferredRevenue) reads the S1 column: the latest annual
report that presents the fiscal year's balance-sheet date, not only the original report.

Oracle: requirement 6 of docs/specs/key-currency-and-definition.md; S1 of
docs/design/XbrlRag_Eval_Semantics.md (the latest filing's figure for a period is the key);
the conftest's RSTD filings, summed by hand: FY2023 total debt is 5,713 in its own FY2023 10-K
(one line) and 75 + 5,713 = 5,788 in the FY2024 10-K's comparative column; FY2024 is
80 + 6,000 = 6,080 (no later report).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import (
    load_note_concepts,
    load_resolutions,
    load_statement_rules,
)
from src.eval.suites.v2_witness import KeyWitness

M = 1_000_000
REL = 1e-12  # tolerance: whole-million fixture amounts; only binary floating point differs


def _key(kit, year, facts=None, fsds=None, filed=None):
    """The Debt key of RSTD's ``year``: FSDS rows of ``fsds``, companyfacts of ``filed``."""
    facts = kit.rstd_facts() if facts is None else facts
    fsds = (kit.R23, kit.R24) if fsds is None else fsds
    filed = (kit.R23, kit.R24) if filed is None else filed
    w = KeyWitness(kit.rstd_tables(facts, fsds), load_statement_rules(kit.RULES),
                   load_note_concepts(kit.RULES.parent / "note_concepts.yaml"),
                   load_resolutions(kit.RULES.parent / "key_resolutions.yaml"))
    key, _adsh, _value = w.fy_key(kit.rstd_companyfacts(facts, filed), kit.rstd_labels(filed),
                                  kit.RSTD_CIK, "Debt",
                                  kit.spec(kit.G + "LongTermDebtNoncurrent"), year)
    return key


@pytest.mark.criterion("C6")
def test_each_year_reads_the_latest_column(kit):
    # oracle: RSTD - the FY2024 10-K presents FY2023 as 75 + 5,713 = 5,788 (S1: the latest
    # filing that presents the date), and the key still labels the year FY2023; FY2024 has no
    # later report: 80 + 6,000 = 6,080 from its own column
    key = _key(kit, 2023)
    assert key["value"] == pytest.approx(5_788 * M, rel=REL)
    assert (key["accession"], key["period_end"], key["fiscal_year"], key["concept"]) == (
        kit.R24, "2023-12-31", 2023, "s4:debt_total")
    own = _key(kit, 2024)
    assert own["value"] == pytest.approx(6_080 * M, rel=REL)
    assert (own["accession"], own["period_end"]) == (kit.R24, "2024-12-31")


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
def test_without_a_readable_later_column_the_own_column_stands(kit):
    # oracle: requirement 6 - with both reports the key is the later column (5,788); with the
    # FY2024 10-K absent (neither FSDS nor companyfacts) it is the own report's single line,
    # 5,713; with the FY2024 10-K in companyfacts but not in the FSDS data its column cannot
    # be read (no_fsds), so the own column stands too
    assert _key(kit, 2023)["value"] == pytest.approx(5_788 * M, rel=REL)
    only = _key(kit, 2023, fsds=(kit.R23,), filed=(kit.R23,))
    assert (only["value"], only["accession"]) == (pytest.approx(5_713 * M, rel=REL), kit.R23)
    unread = _key(kit, 2023, fsds=(kit.R23,))
    assert (unread["value"], unread["accession"]) == (pytest.approx(5_713 * M, rel=REL),
                                                      kit.R23)


@pytest.mark.criterion("C6")
@given(current=st.integers(min_value=1, max_value=900),
       noncurrent=st.integers(min_value=1, max_value=9_000))
def test_the_key_follows_the_later_column(kit, current, noncurrent):
    # oracle: requirement 6 - whatever the FY2024 10-K presents for FY2023 (both lines filed in
    # companyfacts with the same values), the FY2023 key is that column's hand sum
    key = _key(kit, 2023, facts=kit.rstd_facts(current, noncurrent))
    assert key["value"] == pytest.approx((current + noncurrent) * M, rel=REL)
    assert key["accession"] == kit.R24
