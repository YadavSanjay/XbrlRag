"""C3: dimensional candidates (docs/specs/set-b-restatement.md, requirement 1).

The rows are hand-written dimensional rows on two real NVDA 10-Ks (conftest ``nvda_dim_rows``).

Changed by set-b-dimensional-s1 (docs/specs/set-b-dimensional-s1.md, requirement 1): a lone
member now follows dimensional-s1 R2, so a later-labelled 10-K's row of the same member and
period (91000004) answers instead of the FY row (91000001). These tests keep only what holds
before and after that change: one candidate per member and period, cleaned labels, never a
prior-period or non-canonical row, and a later filing's row only for a member the FY filing
reports. The new rule's own tests are test/spec/set-b-dimensional-s1 (C1).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.pg_store import key_candidates

# The single-dimension member's rows that may answer FY2025: the FY row and the FY2026 10-K's
# report of the same member and period (set-b-dimensional-s1 makes the latter answer).
SAME_MEMBER = {91_000_001: 116_193_000_000.0, 91_000_004: 116_000_000_000.0}


def _dims(cands):
    return [c for c in cands if c["dimensional"]]


# ---------------------------------------------------------------------------------- points
@pytest.mark.criterion("C3")
def test_dimensional_candidates_are_the_fy_filings_current_rows(kit):
    # oracle: hand-built rows — the FY2025 10-K's current-period rows are the two members; the
    # single-dimension one answers from the FY row or (set-b-dimensional-s1) the FY2026 10-K's
    # row of the same member and period; labels cleaned of "[Member]", a two-axis label joined
    # by axis name order (srt:ProductOrServiceAxis < us-gaap:StatementBusinessSegmentsAxis)
    rows = kit.store_rows("NVDA") + kit.nvda_dim_rows()
    dims = _dims(key_candidates(rows, 2025))
    assert len(dims) == 2
    one = next(c for c in dims if c["fact_id"] in SAME_MEMBER)
    two = next(c for c in dims if c["fact_id"] == 91_000_002)
    assert one["member_label"] == "Compute & Networking"
    assert two["member_label"] == "Data Center | Compute & Networking"
    for c in dims:
        assert c["period_start"] == "2024-01-29"
        assert c["period_end"] == "2025-01-26"
        assert c["unit"] == "USD"
        assert c["concept_family"] == "Revenue"
    # whole dollars: hand-written integral values
    assert one["value"] == pytest.approx(SAME_MEMBER[one["fact_id"]], abs=0.5)
    assert two["value"] == pytest.approx(115_186_000_000.0, abs=0.5)


@pytest.mark.criterion("C3")
def test_prior_period_and_non_canonical_rows_never_answer(kit):
    # oracle: hand-built rows — 91000003 is the FY2025 10-K's prior-year column and 91000005 a
    # non-canonical copy: neither is a FY2025 candidate; FY2026 has no current-period
    # dimensional row at all (91000004 reports FY2025's period)
    rows = kit.store_rows("NVDA") + kit.nvda_dim_rows()
    ids_2025 = {c["fact_id"] for c in _dims(key_candidates(rows, 2025))}
    assert ids_2025 & set(SAME_MEMBER)
    assert ids_2025.isdisjoint({91_000_003, 91_000_005})
    assert _dims(key_candidates(rows, 2026)) == []
    assert key_candidates(rows, 2026), "FY2026 still has flat candidates"


# ------------------------------------------------------------------------------ property
@pytest.mark.criterion("C3")
@given(extra=st.lists(st.tuples(st.sampled_from(["0001045810-26-000021",
                                                   "0001045810-26-000052",
                                                   "0001045810-26-000075"]),
                                st.floats(min_value=1.0, max_value=1e12, allow_nan=False),
                                st.booleans()),
                      max_size=6),
       order=st.randoms(use_true_random=False))
def test_later_filings_never_add_a_member_or_a_foreign_row(kit, extra, order):
    # oracle: metamorphic — rows of later-labelled filings (FY2026 10-K, FY2027 10-Qs) for the
    # same member and period, with any values and flags, leave one candidate per member: the
    # two-axis row 91000002 and one row of the single-dimension member, from the FY filing or a
    # later filing reporting it (its own value); never a prior-period or non-canonical row
    rows = kit.store_rows("NVDA") + kit.nvda_dim_rows()
    added = [kit.dim_row(92_000_000 + i, accn, "2024-01-29", "2025-01-26", value, current=cur)
             for i, (accn, value, cur) in enumerate(extra)]
    mixed = rows + added
    order.shuffle(mixed)
    by_id = {r["fact_id"]: r for r in mixed}
    dims = _dims(key_candidates(mixed, 2025))
    ids = {c["fact_id"] for c in dims}
    assert 91_000_002 in ids
    assert ids.isdisjoint({91_000_003, 91_000_005})
    member = [c for c in dims if c["fact_id"] != 91_000_002]
    assert member
    assert {by_id[c["fact_id"]]["accession_no"] for c in member} <= \
        {"0001045810-25-000023"} | {a for a, _, _ in extra} | {"0001045810-26-000021"}
    assert len({by_id[c["fact_id"]]["accession_no"] for c in member}) == 1
    for c in member:
        assert c["value"] == by_id[c["fact_id"]]["value"]  # exact: the row's own value
        assert c["member_label"] == "Compute & Networking"
