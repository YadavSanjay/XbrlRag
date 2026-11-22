"""q4-lookup C4: both sides of a derived Q4 are on one basis (S1).

docs/specs/q4-lookup.md R6. A synthetic corpus-shaped filer: the FY2024 10-K's year and the
FY2024 Q3 10-Q's nine months, restated (or not) by FY2025-labelled filings.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.q4_lookup import select_q4

M = 1_000_000
_START = dt.date(2023, 7, 1)
_OWN_YEAR, _OWN_NINE, _LATER_YEAR, _LATER_NINE = 101, 102, 201, 202


def _rows(fx, year_restated: int | None, nine_restated: int | None) -> list[dict]:
    """Own rows (1,000M year, 700M nine months), plus later-labelled comparatives."""
    rows = [
        fx.synth(_OWN_YEAR, 2024, "FY", _START, 366, 1_000 * M, "0000000004-24-000010",
                 dt.date(2024, 7, 30)),
        fx.synth(_OWN_NINE, 2024, "Q3", _START, 275, 700 * M, "0000000004-24-000007",
                 dt.date(2024, 4, 30)),
    ]
    if year_restated is not None:
        rows.append(fx.synth(_LATER_YEAR, 2025, "FY", _START, 366, year_restated,
                             "0000000004-25-000010", dt.date(2025, 7, 30), own=False))
    if nine_restated is not None:
        rows.append(fx.synth(_LATER_NINE, 2025, "Q3", _START, 275, nine_restated,
                             "0000000004-25-000007", dt.date(2025, 4, 30), own=False))
    return rows


@pytest.mark.criterion("C4")
def test_year_restated_alone_derives_on_the_as_filed_basis(fx):
    # oracle: R6 by hand: only the year restated (1,030M) -> both as filed, 1,000M - 700M
    out = select_q4(_rows(fx, 1_030 * M, None), 2024)
    assert out["status"] == "derived"
    assert out["basis"] == "as_filed"
    assert out["value"] == 300 * M
    assert tuple(out["fact_ids"]) == (_OWN_YEAR, _OWN_NINE)
    assert "restated" in out["note"].lower()


@pytest.mark.criterion("C4")
def test_both_restated_derives_on_the_latest_basis(fx):
    # oracle: R6 by hand: both restated -> 1,030M - 720M = 310M from the later filings
    out = select_q4(_rows(fx, 1_030 * M, 720 * M), 2024)
    assert out["status"] == "derived"
    assert out["basis"] == "latest"
    assert out["value"] == 310 * M
    assert tuple(out["fact_ids"]) == (_LATER_YEAR, _LATER_NINE)


@pytest.mark.criterion("C4")
def test_repeated_values_are_not_restatements(fx):
    # oracle: R6 by hand: later filings repeating 1,000M and 700M change nothing
    out = select_q4(_rows(fx, 1_000 * M, None), 2024)
    assert (out["status"], out["basis"], out["value"]) == ("derived", "latest", 300 * M)


@pytest.mark.criterion("C4")
def test_as_originally_reported_reads_the_original_filings(fx):
    # oracle: R6 by hand: as originally reported -> 1,000M - 700M, own fact ids
    out = select_q4(_rows(fx, 1_030 * M, 720 * M), 2024, as_originally_reported=True)
    assert out["status"] == "derived"
    assert out["basis"] == "original"
    assert out["value"] == 300 * M
    assert tuple(out["fact_ids"]) == (_OWN_YEAR, _OWN_NINE)


@pytest.mark.criterion("C4")
@given(da=st.integers(min_value=-200, max_value=200),
       dn=st.integers(min_value=-200, max_value=200),
       original=st.booleans())
def test_basis_follows_the_table(fx, da, dn, original):
    # oracle: R6's table by hand: one side changed -> as filed; none or both -> latest;
    # as originally reported -> the original filings
    out = select_q4(_rows(fx, (1_000 + da) * M, (700 + dn) * M), 2024,
                    as_originally_reported=original)
    assert out["status"] == "derived"
    if original:
        assert (out["basis"], out["value"]) == ("original", Decimal(300 * M))
    elif (da != 0) != (dn != 0):
        assert (out["basis"], out["value"]) == ("as_filed", Decimal(300 * M))
    else:
        assert (out["basis"], out["value"]) == ("latest", Decimal((300 + da - dn) * M))
