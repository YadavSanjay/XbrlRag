"""C1: set B's flat candidates follow S1 (docs/specs/set-b-restatement.md, requirement 1).

Expected accessions are the S1 rule applied by hand to the companyfacts entries of the corpus
filings (listed per case below: which filings report the period, when each was filed, and its
fiscal-year label); expected values are what that filing reports, and FSDS ``num`` agrees where
it holds the filing (checked in conftest).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import KeyNotFoundError, fiscal_year_key
from src.eval.set_b.pg_store import key_candidates

# Per-share values to the cent (half a cent either way); amounts are tagged in millions
# (decimals -6), so half a million either way.
CENT = 0.005
MILLION = 5e5

# ticker, tag, fy, own period (start, inclusive end = the own filing's DEI period end),
# S1 accession, S1 value, original accession, original value, tolerance.
CASES = [
    # FY2023 (2022-01-31..2023-01-29) reported by -23-000017 (1.74, FY2023), -24-000029 (1.74,
    # FY2024, filed 2024-02-21), -25-000023 (0.17 after the 10-for-1 split, FY2025, 2025-02-26).
    ("NVDA", "EarningsPerShareDiluted", 2023, ("2022-01-31", "2023-01-29"),
     "0001045810-25-000023", 0.17, "0001045810-23-000017", 1.74, CENT),
    # FY2024: -24-000029 (11.93), -25-000023 (1.19, 2025-02-26), -26-000021 (1.19, 2026-02-25).
    ("NVDA", "EarningsPerShareDiluted", 2024, ("2023-01-30", "2024-01-28"),
     "0001045810-26-000021", 1.19, "0001045810-24-000029", 11.93, CENT),
    # FY2025: -25-000023 (2.94), -26-000021 (2.94, FY2026). Same value, later filing.
    ("NVDA", "EarningsPerShareDiluted", 2025, ("2024-01-29", "2025-01-26"),
     "0001045810-26-000021", 2.94, "0001045810-25-000023", 2.94, CENT),
    # FY2026: only its own 10-K reports the year; the FY2027 10-Qs report quarters.
    ("NVDA", "EarningsPerShareDiluted", 2026, ("2025-01-27", "2026-01-25"),
     "0001045810-26-000021", 4.90, "0001045810-26-000021", 4.90, CENT),
    # FY2022: -22-000118 (26.53), -23-000096 (26.53, FY2023), -24-000139 (2.65, FY2024, after
    # the split, 2024-12-20); -25-000121 starts at FY2023.
    ("AVGO", "EarningsPerShareDiluted", 2022, ("2021-11-01", "2022-10-30"),
     "0001730168-24-000139", 2.65, "0001730168-22-000118", 26.53, CENT),
    # FY2023: -23-000096 (32.98), -24-000139 (3.30), -25-000121 (3.30, FY2025, 2025-12-18).
    ("AVGO", "EarningsPerShareDiluted", 2023, ("2022-10-31", "2023-10-29"),
     "0001730168-25-000121", 3.30, "0001730168-23-000096", 32.98, CENT),
    # At 2023-12-30: -24-000012 ($5,376M), -25-000012 ($4,323M, FY2024, reclassified).
    ("AMD", "AccountsReceivableNetCurrent", 2023, (None, "2023-12-30"),
     "0000002488-25-000012", 4_323e6, "0000002488-24-000012", 5_376e6, MILLION),
    # At 2024-12-28: -25-000012 ($6,192M), -26-000018 ($6,192M, FY2025, 2026-02-04).
    ("AMD", "AccountsReceivableNetCurrent", 2024, (None, "2024-12-28"),
     "0000002488-26-000018", 6_192e6, "0000002488-25-000012", 6_192e6, MILLION),
    # At 2025-12-27: -26-000018 (FY2025 10-K), and the FY2026 10-Qs -26-000076 (2026-05-06) and
    # -26-000123 (2026-08-05), all $6,315M: the latest later-labelled report is the Q2 10-Q.
    ("AMD", "AccountsReceivableNetCurrent", 2025, (None, "2025-12-27"),
     "0000002488-26-000123", 6_315e6, "0000002488-26-000018", 6_315e6, MILLION),
]
IDS = [f"{c[0]}-FY{c[2]}" for c in CASES]

# Every filer-concept-year the key agreement is checked on (companyfacts unit spelling).
GRID = ([("NVDA", "EarningsPerShareDiluted", "USD/shares", fy) for fy in range(2022, 2027)]
        + [("AVGO", "EarningsPerShareDiluted", "USD/shares", fy) for fy in range(2022, 2026)]
        + [("AMD", "AccountsReceivableNetCurrent", "USD", fy) for fy in range(2022, 2026)]
        + [("NVDA", "Assets", "USD", fy) for fy in range(2022, 2027)]
        + [("AMD", "NetIncomeLoss", "USD", fy) for fy in range(2022, 2026)])


def _expected_ids(kit, rows, tag, accn, end):
    return sorted(r["fact_id"] for r in kit.rows_of(rows, tag, accn, end))


# ---------------------------------------------------------------------------------- points
@pytest.mark.criterion("C1")
@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_flat_candidates_are_the_latest_report_of_the_own_period(kit, case):
    # oracle: S1 by hand over the companyfacts entries listed with each case (e.g. NVDA FY2023
    # diluted EPS: -23-000017, -24-000029, -25-000023 report it; -25-000023 is the latest filed,
    # 0.17, FSDS num ddate 20230131 qtrs 4 = 0.17)
    ticker, tag, fy, (start, end), s1_acc, s1_val, _orig_acc, orig_val, tol = case
    rows = kit.store_rows(ticker)
    cands = kit.flat(key_candidates(rows, fy), tag)
    assert sorted(c["fact_id"] for c in cands) == _expected_ids(kit, rows, tag, s1_acc, end)
    assert cands, "the S1 filing reports the period"
    for c in cands:
        assert c["value"] == pytest.approx(s1_val, abs=tol)
        assert c["period_end"] == end
        assert c["period_start"] == start
        assert c["member_label"] is None
    if orig_val != pytest.approx(s1_val, abs=tol):
        assert all(c["value"] != pytest.approx(orig_val, abs=tol) for c in cands)


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("case", [c for c in CASES if c[4] != c[6]],
                         ids=[i for c, i in zip(CASES, IDS, strict=True) if c[4] != c[6]])
def test_the_original_filings_rows_are_not_candidates(kit, case):
    # oracle: S1 by hand — the year's own filing is replaced by the later filing that reports the
    # same period, so none of the own filing's rows of the concept remains a candidate
    ticker, tag, fy, (_start, end), s1_acc, _s1, orig_acc, _orig, _tol = case
    rows = kit.store_rows(ticker)
    ids = {c["fact_id"] for c in key_candidates(rows, fy)}
    own = _expected_ids(kit, rows, tag, orig_acc, end)
    assert own, "the own filing reports its period"
    assert ids.isdisjoint(own)
    assert set(_expected_ids(kit, rows, tag, s1_acc, end)) <= ids


# ------------------------------------------------------------------------------ properties
@pytest.mark.criterion("C1")
@given(case=st.sampled_from(CASES), data=st.data())
def test_older_filings_and_row_order_never_change_the_candidates(kit, case, data):
    # oracle: metamorphic — S1 depends only on the latest filing that reports the period:
    # dropping filings filed before it (other than the year's own), adding an older filing
    # with other values, or permuting the rows leaves the hand-checked candidates unchanged
    ticker, tag, fy, (_start, end), s1_acc, s1_val, orig_acc, _orig, tol = case
    rows = kit.store_rows(ticker)
    s1_filed = next(r["filed_date"] for r in rows if r["accession_no"] == s1_acc)
    older = sorted({r["accession_no"] for r in rows
                    if r["filed_date"] < s1_filed and r["accession_no"] != orig_acc})
    dropped = set(data.draw(st.lists(st.sampled_from(older), unique=True))) if older else set()
    kept = [r for r in rows if r["accession_no"] not in dropped]
    # The older filing is labelled later than the year when a later report exists (it then
    # loses on its filing date), else earlier (an earlier label never restates).
    label = fy + 1 if s1_acc != orig_acc else fy - 1
    fake = [dict(r, accession_no="0000000001-20-000001", filed_date="2020-01-02",
                 fiscal_year=label, value=(r["value"] or 1.0) * 3,
                 fact_id=r["fact_id"] + 5_000_000)
            for r in rows if r["accession_no"] == orig_acc]
    mixed = data.draw(st.permutations(kept + fake))
    cands = kit.flat(key_candidates(mixed, fy), tag)
    assert sorted(c["fact_id"] for c in cands) == _expected_ids(kit, rows, tag, s1_acc, end)
    assert [c["value"] for c in cands] == [pytest.approx(s1_val, abs=tol)] * len(cands)
    assert cands


@pytest.mark.criterion("C1")
@given(item=st.sampled_from(GRID))
def test_candidates_agree_with_the_answer_key_on_the_same_filings(kit, item):
    # oracle: metamorphic — set B's candidate and the S1 answer key (fiscal_year_key over the
    # same filings' companyfacts entries) name one accession and one value, or both refuse
    ticker, tag, unit, fy = item
    cands = kit.flat(key_candidates(kit.store_rows(ticker), fy), tag)
    try:
        key = fiscal_year_key(kit.companyfacts(ticker, corpus_only=True), f"us-gaap:{tag}",
                              unit, fy)
    except KeyNotFoundError:
        key = None
    if key is None:
        assert cands == []
    else:
        rows = kit.store_rows(ticker)
        by_id = {r["fact_id"]: r for r in rows}
        assert {by_id[c["fact_id"]]["accession_no"] for c in cands} == {key["accession"]}
        # exact: both sides are the same filing's tagged value
        assert cands
        assert [c["value"] for c in cands] == [pytest.approx(key["value"], abs=1e-9)] * len(cands)
