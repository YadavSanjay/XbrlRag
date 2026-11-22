"""C2: year-over-year on one basis (docs/specs/restatement-s1.md, requirement 5).

The prior value is the current filing's own comparative column (the filer's comparison
basis), not the prior filing's current column. Expected growth is computed in the test from
the current filing's FSDS ``num`` values.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.restatement import yoy_pairs

REPO = Path(__file__).resolve().parents[3]
YOY_DDL = REPO / "scripts" / "ddl" / "mv" / "33_mv_yoy_comparison.sql"
# Growth to 0.1 percentage point, on the fraction.
PP = 1e-3

# ticker, tag, fy, current filing, current value, prior value (both FSDS num of that filing),
# the mixed-basis growth mv_yoy_comparison stores today, the prior filing, its own prior value
CASES = [
    ("NVDA", "EarningsPerShareDiluted", 2025, "0001045810-25-000023", 2.94, 1.19, -0.754,
     "0001045810-24-000029", 11.93),
    ("WMT", "EarningsPerShareDiluted", 2024, "0000104169-24-000056", 1.91, 1.42, -0.553,
     "0000104169-23-000020", 4.27),
    ("INTC", "NetCashProvidedByUsedInFinancingActivities", 2023, "0000050863-24-000010",
     8_505e6, 1_115e6, 5.249, "0000050863-23-000006", 1_361e6),
]
IDS = [f"{c[0]}-FY{c[2]}" for c in CASES]


def _pair(kit, pairs, tag):
    mine = [p for p in pairs if kit.local(p["current"]["concept"]) == tag]
    assert len(mine) == 1, mine
    return mine[0]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_growth_is_computed_within_the_current_filing(kit, case):
    # oracle: FSDS num of the current filing — NVDA 0001045810-25-000023 EPS 2.94 (ddate
    # 20250131) and 1.19 (20240131): 2.94 / 1.19 - 1 = +147.1%; WMT 1.91 / 1.42 - 1 = +34.5%;
    # INTC 8,505 / 1,115 - 1 = +662.8%
    ticker, tag, fy, filing, cur, prior, mixed, _prev_filing, _prev_value = case
    pair = _pair(kit, yoy_pairs(kit.store_rows(ticker, [tag]), fy), tag)
    expected = cur / prior - 1
    assert pair["yoy_pct_change"] == pytest.approx(expected, abs=PP)
    assert pair["yoy_pct_change"] != pytest.approx(mixed, abs=PP)
    assert pair["same_filing"] is True
    assert pair["current"]["accession_no"] == filing
    assert pair["prior"]["accession_no"] == filing
    assert pair["current"]["value"] == pytest.approx(cur, rel=1e-9)
    assert pair["prior"]["value"] == pytest.approx(prior, rel=1e-9)


@pytest.mark.criterion("C2")
def test_the_yoy_view_pairs_on_the_current_accession():
    # oracle: requirement 5 — the view's prior side is joined on the current side's accession
    # and says which basis it used
    sql = YOY_DDL.read_text(encoding="utf-8")
    code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    same = re.compile(r"prev\.accession_no\s*=\s*curr\.accession_no"
                      r"|curr\.accession_no\s*=\s*prev\.accession_no")
    assert same.search(code), "the prior side is not joined on the current accession"
    assert re.search(r"\bsame_filing\b", code), "the view does not expose same_filing"


@pytest.mark.criterion("C2")
@given(case=st.sampled_from(CASES), data=st.data())
def test_other_filings_never_change_a_same_filing_pair(kit, case, data):
    # oracle: metamorphic — when the current filing has the prior column, the pair depends on
    # that filing only: dropping any other filings or permuting the rows changes nothing
    ticker, tag, fy, filing, cur, prior, _mixed, _prev_filing, _prev_value = case
    rows = kit.store_rows(ticker, [tag])
    others = sorted(kit.accessions(rows) - {filing})
    dropped = set(data.draw(st.lists(st.sampled_from(others), unique=True)))
    kept = data.draw(st.permutations([r for r in rows if r["accession_no"] not in dropped]))
    pair = _pair(kit, yoy_pairs(kept, fy), tag)
    assert pair["same_filing"] is True
    assert pair["prior"]["accession_no"] == filing
    assert pair["yoy_pct_change"] == pytest.approx(cur / prior - 1, abs=PP)


@pytest.mark.criterion("C2")
@given(case=st.sampled_from(CASES), shuffle=st.randoms(use_true_random=False))
def test_without_the_prior_column_the_prior_filing_is_used_and_flagged(kit, case, shuffle):
    # oracle: requirement 5's fallback — remove the current filing's prior column and the prior
    # is the prior-labelled filing's own column (FSDS-checked originals: NVDA 11.93, WMT 4.27,
    # INTC 1,361M), flagged same_filing false
    ticker, tag, fy, filing, cur, _prior, _mixed, prev_filing, prev_value = case
    rows = kit.store_rows(ticker, [tag])
    own_end = next(r["period_end"] for r in rows
                   if r["accession_no"] == filing and r["is_current_period"])
    kept = [r for r in rows if r["accession_no"] != filing or r["period_end"] == own_end]
    shuffle.shuffle(kept)
    pair = _pair(kit, yoy_pairs(kept, fy), tag)
    assert pair["same_filing"] is False
    assert pair["current"]["accession_no"] == filing
    assert pair["prior"]["accession_no"] == prev_filing
    assert pair["yoy_pct_change"] == pytest.approx(cur / prev_value - 1, abs=PP)
