"""q4-lookup C3: without both sides on one fiscal-year label, Q4 is refused with a reason.

docs/specs/q4-lookup.md R1, R2, R4, R5, R7, over MSFT's FY2026 revenue store rows.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.q4_lookup import select_q4

_NINE_START = "2025-07-01"


def _scenario(fx, name: str) -> tuple[list[dict], int, str | None, str | None]:
    """(rows, fy, unit, a word the reason must contain)."""
    rows = fx.store(fx.REVENUE)
    if name == "no_10q":
        return fx.store(fx.REVENUE, accessions=(fx.MSFT_10K,)), 2026, None, "nine-month"
    if name == "discrete_q3_only":
        kept = [r for r in rows if not (r["accession_no"] == fx.MSFT_10Q
                                         and r["period_start"] == _NINE_START)]
        return kept, 2026, None, "nine-month"
    if name == "label_without_rows":
        return rows, 2025, None, None
    if name == "nine_months_other_start":
        for r in rows:
            if r["accession_no"] == fx.MSFT_10Q and r["period_start"] == _NINE_START:
                r["period_start"] = "2025-07-02"
        return rows, 2026, None, "nine-month"
    if name == "instants_only":
        for r in rows:
            r["period_start"] = None
            r["period_type"] = "instant"
        return rows, 2026, None, "instant"
    if name == "dimensional_only":
        for r in rows:
            r["segment_axis"] = "srt:ProductOrServiceAxis"
            r["segment_member"] = "msft:ServerProductsMember"
        return rows, 2026, None, None
    if name == "absent_unit":
        return rows, 2026, "EUR", None
    raise AssertionError(name)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("name", ["no_10q", "discrete_q3_only", "label_without_rows",
                                  "nine_months_other_start", "instants_only",
                                  "dimensional_only", "absent_unit"])
def test_refusals_name_their_reason(fx, name):
    # oracle: R1-R5 by hand: each case lacks the year's row or its same-label, same-start
    # nine-month row (the 10-Q's 82,886M discrete quarter is not nine months)
    rows, fy, unit, word = _scenario(fx, name)
    out = select_q4(rows, fy, unit=unit)
    fx.assert_refused(out)
    if word is not None:
        assert word in out["reason"].lower()


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(data=st.data())
def test_any_subset_gives_the_answer_or_a_refusal(fx, data):
    # oracle: C1's hand value 90,007,000,000 (FSDS 331,839M - 241,832M): taking rows away
    # never produces another number
    rows = fx.store(fx.REVENUE)
    keep = data.draw(st.lists(st.booleans(), min_size=len(rows), max_size=len(rows)))
    subset = [r for r, k in zip(rows, keep, strict=True) if k]
    out = select_q4(subset, 2026)
    if out["status"] == "refused":
        fx.assert_refused(out)
    else:
        assert out["status"] == "derived"
        assert out["value"] == 90_007_000_000
