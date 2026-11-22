"""C5: ``draw_v2``.

Oracle: requirement 5 of docs/specs/heldout-draw-v2.md (aprime-draw requirement 10 with the
v2 ranking and default sizes 4 and 2). Pools and pulses are synthetic. Expected rank orders
are computed in the test with ``hashlib`` (``kit.rk2``, sorted by ``(key, cik)``).
Eligibility is set per CIK in the fake ``FilingIndexSource`` after ranking, so the hand-picked
rank positions below are the ineligible ones.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.aprime.v2 import draw_v2

STAMP = "2027-01-04T16:00:00.000Z"

S_CIKS = [str(n).zfill(10) for n in (4101, 4102, 4103, 4104, 4105, 4106, 4107, 4108, 4109)]
D_CIKS = [str(n).zfill(10) for n in (5201, 5202, 5203, 5204)]


def _order(kit, ov, ciks):
    return sorted(ciks, key=lambda c: (kit.rk2(ov, c), c))


def _pool(kit):
    cands = [kit.candidate(c, "S") for c in S_CIKS] + [kit.candidate(c, "D") for c in D_CIKS]
    cands.sort(key=lambda c: c["cik"])
    return {"candidates": cands, "excluded": [], "warnings": [],
            "counts": {"constituents": 13, "S": 9, "D": 4, "excluded": 0, "warnings": 0}}


def _setup(kit):
    """Ranks each stratum with hashlib, then sets eligibility by rank position.

    S (9 candidates): rank 2 misses FY2023, rank 4's FY2024 10-K is not inline, every other
    rank is eligible. D (4 candidates): rank 3 has no filings at all.
    """
    s_order = _order(kit, kit.OV, S_CIKS)
    d_order = _order(kit, kit.OV, D_CIKS)
    by_cik = {c: kit.eligible_filings() for c in S_CIKS + D_CIKS}
    by_cik[s_order[1]] = kit.missing_2023_filings()
    by_cik[s_order[3]] = kit.not_inline_2024_filings()
    by_cik[d_order[2]] = []
    return s_order, d_order, kit.FakeFilings(by_cik)


def _entry(kit, cik, rank):
    c = kit.candidate(cik, "S" if cik in S_CIKS else "D")
    return {"symbol": c["symbol"], "name": c["name"], "sector": c["sector"], "cik": cik,
            "rank": rank, "key": kit.rk2(kit.OV, cik)}


def _chk(kit, cik, rank, missing=(), not_inline=()):
    return {"rank": rank, "symbol": kit.candidate(cik, "S")["symbol"], "cik": cik,
            "key": kit.rk2(kit.OV, cik), "eligible": not missing and not not_inline,
            "missing_years": list(missing), "not_inline_years": list(not_inline)}


def _expected(kit, s_order, d_order):
    # S: eligible ranks 1,3,5,6,7,8,9 -> drawn 1,3,5,6; alternates 7,8; the walk stops
    #    after rank 8 (both lists full), so rank 9 is never checked; shortfall 0
    # D: ranks 1,2,4 drawn; no alternates; shortfall 4 + 2 - 3 - 0 = 3
    s = {
        "drawn": [_entry(kit, s_order[r - 1], r) for r in (1, 3, 5, 6)],
        "alternates": [_entry(kit, s_order[r - 1], r) for r in (7, 8)],
        "checked": [
            _chk(kit, s_order[0], 1),
            _chk(kit, s_order[1], 2, missing=[2023]),
            _chk(kit, s_order[2], 3),
            _chk(kit, s_order[3], 4, not_inline=[2024]),
        ] + [_chk(kit, s_order[r - 1], r) for r in (5, 6, 7, 8)],
        "shortfall": 0,
    }
    d = {
        "drawn": [_entry(kit, d_order[r - 1], r) for r in (1, 2, 4)],
        "alternates": [],
        "checked": [
            _chk(kit, d_order[0], 1),
            _chk(kit, d_order[1], 2),
            _chk(kit, d_order[2], 3, missing=[2023, 2024, 2025]),
            _chk(kit, d_order[3], 4),
        ],
        "shortfall": 3,
    }
    return {"output_value": kit.OV.upper(), "pulse_timestamp": STAMP,
            "strata": {"S": s, "D": d}}


@pytest.mark.criterion("C5")
def test_default_sizes_give_expected_draw(kit):
    # oracle: requirement 5 with hashlib v2 rank orders computed here — default sizes 4 and
    # 2; drawn/alternates filled in rank order from eligible candidates; ineligible ones in
    # checked; output_value upper-cased, pulse_timestamp = timeStamp
    s_order, d_order, src = _setup(kit)
    got = draw_v2(_pool(kit), kit.pulse(output_value=kit.OV, stamp=STAMP), src)
    assert got == _expected(kit, s_order, d_order)


@pytest.mark.criterion("C5")
def test_ineligible_skipped_but_checked(kit):
    # oracle: requirement 5 — S ranks 2 and 4 are ineligible (set in the fake): absent from
    # drawn and alternates, present in checked with eligible False and their years
    s_order, _, src = _setup(kit)
    got = draw_v2(_pool(kit), kit.pulse(output_value=kit.OV, stamp=STAMP), src)["strata"]["S"]
    picked = [e["cik"] for e in got["drawn"] + got["alternates"]]
    assert s_order[1] not in picked
    assert s_order[3] not in picked
    checked = {c["cik"]: c for c in got["checked"]}
    assert checked[s_order[1]]["eligible"] is False
    assert checked[s_order[1]]["missing_years"] == [2023]
    assert checked[s_order[3]]["eligible"] is False
    assert checked[s_order[3]]["not_inline_years"] == [2024]


@pytest.mark.criterion("C5")
def test_filings_not_called_after_both_lists_full(kit):
    # oracle: requirement 5 — each walked candidate is read exactly once; in S the lists
    # fill at rank 8, so rank 9 is never read; all 4 D candidates are walked
    s_order, d_order, src = _setup(kit)
    draw_v2(_pool(kit), kit.pulse(output_value=kit.OV, stamp=STAMP), src)
    assert len(src.calls) == len(set(src.calls))
    assert sorted(src.calls) == sorted(s_order[:8] + d_order)
    assert s_order[8] not in src.calls


@pytest.mark.criterion("C5")
def test_short_stratum_reports_shortfall_with_custom_sizes(kit):
    # oracle: requirement 5 — per_stratum=2, alternates=1: D has 3 eligible of 4 (rank 3 has
    # no filings), so D draws ranks 1,2 and alternate rank 4, shortfall 2 + 1 - 3 = 0; an S
    # with one eligible candidate gives shortfall 2 + 1 - 1 - 0 = 2
    s_one = "0000006301"
    d_order = _order(kit, kit.OV, D_CIKS)
    by_cik = {c: kit.eligible_filings() for c in D_CIKS}
    by_cik[d_order[2]] = []
    by_cik[s_one] = kit.eligible_filings()
    src = kit.FakeFilings(by_cik)
    pool = {"candidates": [kit.candidate(s_one, "S")] + [kit.candidate(c, "D") for c in D_CIKS],
            "excluded": [], "warnings": [], "counts": {}}
    got = draw_v2(pool, kit.pulse(output_value=kit.OV, stamp=STAMP), src,
                  per_stratum=2, alternates=1)
    d = got["strata"]["D"]
    assert [e["cik"] for e in d["drawn"]] == [d_order[0], d_order[1]]
    assert [e["rank"] for e in d["drawn"]] == [1, 2]
    assert [e["cik"] for e in d["alternates"]] == [d_order[3]]
    assert [e["rank"] for e in d["alternates"]] == [4]
    assert d["shortfall"] == 0
    s = got["strata"]["S"]
    assert [e["cik"] for e in s["drawn"]] == [s_one]
    assert s["alternates"] == []
    assert s["shortfall"] == 2


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("ov", ["a" * 127, "g" + "a" * 127, "a" * 129])
def test_malformed_output_value_raises(kit, ov):
    # oracle: requirement 5 — an outputValue that isn't exactly 128 hex characters raises
    _, _, src = _setup(kit)
    with pytest.raises(ValueError):
        draw_v2(_pool(kit), kit.pulse(output_value=ov, stamp=STAMP), src)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(perm=st.permutations(list(range(len(S_CIKS) + len(D_CIKS)))))
def test_shuffled_candidates_give_identical_result(kit, perm):
    # oracle: requirement 5 — the result doesn't depend on the order of pool["candidates"];
    # every permutation gives the hashlib-derived expected draw
    s_order, d_order, src = _setup(kit)
    pool = _pool(kit)
    pool["candidates"] = [pool["candidates"][i] for i in perm]
    got = draw_v2(pool, kit.pulse(output_value=kit.OV, stamp=STAMP), src)
    assert got == _expected(kit, s_order, d_order)


@st.composite
def _random_pool(draw_):
    ov = draw_(st.text(alphabet="0123456789abcdefABCDEF", min_size=128, max_size=128))
    nums = draw_(st.lists(st.integers(1, 9_999_999), min_size=0, max_size=14, unique=True))
    strata = draw_(st.lists(st.sampled_from(["S", "D"]), min_size=len(nums),
                            max_size=len(nums)))
    elig = draw_(st.lists(st.booleans(), min_size=len(nums), max_size=len(nums)))
    per = draw_(st.integers(1, 5))
    alt = draw_(st.integers(0, 3))
    ciks = [str(n).zfill(10) for n in nums]
    return ov, list(zip(ciks, strata, elig, strict=True)), per, alt


@pytest.mark.criterion("C5")
@given(case=_random_pool())
def test_drawn_then_alternates_are_first_eligible_in_v2_rank_order(kit, case):
    # oracle: requirement 5 — per stratum, drawn + alternates = the first per + alt
    # eligible candidates in (hashlib v2 key, cik) order; shortfall = per + alt - that count
    ov, rows, per, alt = case
    by_cik = {c: (kit.eligible_filings() if e else kit.missing_2023_filings())
              for c, _, e in rows}
    src = kit.FakeFilings(by_cik)
    pool = {"candidates": [kit.candidate(c, s) for c, s, _ in rows], "excluded": [],
            "warnings": [], "counts": {}}
    got = draw_v2(pool, kit.pulse(output_value=ov, stamp=STAMP), src,
                  per_stratum=per, alternates=alt)
    assert got["output_value"] == ov.upper()
    assert set(got["strata"]) == {"S", "D"}
    eligible = {c for c, _, e in rows if e}
    for stratum in ("S", "D"):
        order = _order(kit, ov, [c for c, s, _ in rows if s == stratum])
        first = [c for c in order if c in eligible][: per + alt]
        entry = got["strata"][stratum]
        assert [e["cik"] for e in entry["drawn"]] == first[:per]
        assert [e["cik"] for e in entry["alternates"]] == first[per:]
        assert [e["rank"] for e in entry["drawn"] + entry["alternates"]] == [
            order.index(c) + 1 for c in first]
        assert [e["key"] for e in entry["drawn"] + entry["alternates"]] == [
            kit.rk2(ov, c) for c in first]
        assert entry["shortfall"] == per + alt - len(first)
