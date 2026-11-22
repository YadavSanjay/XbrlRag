"""C4: ``rank_key_v2`` and ``rank_v2``.

Oracle: requirement 4 of docs/specs/heldout-draw-v2.md. Every expected key is recomputed in
the test with ``hashlib`` (``kit.rk2``: sha256 of ``"xbrlrag-heldout-v2|" + OV.upper() +
"|" + cik10``); ``kit.rk1`` is the A' key with prefix ``"xbrlrag-aprime-v1|"``.
"""

from __future__ import annotations

import copy
import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.aprime.v2 import rank_key_v2, rank_v2

CIKS = [str(n).zfill(10) for n in (3101, 3102, 3103, 3104, 3105, 3106, 3107)]


@pytest.mark.criterion("C4")
def test_rank_key_v2_equals_hashlib_oracle(kit):
    # oracle: requirement 4, written out literally — lower-case OV is upper-cased, CIK 3101
    # is zero-padded to 0000003101
    text = "xbrlrag-heldout-v2|" + kit.OV.upper() + "|0000003101"
    expected = hashlib.sha256(text.encode("ascii")).hexdigest()
    got = rank_key_v2(kit.OV, "3101")
    assert got == expected
    assert got == kit.rk2(kit.OV, "3101")


@pytest.mark.criterion("C4")
def test_rank_key_v2_differs_from_aprime_key(kit):
    # oracle: requirement 4 — the v2 prefix differs from A''s, so the digests differ for the
    # same pulse and CIK (both computed with hashlib in the test)
    got = rank_key_v2(kit.OV, "0000003101")
    assert got == kit.rk2(kit.OV, "0000003101")
    assert got != kit.rk1(kit.OV, "0000003101")


@pytest.mark.criterion("C4")
def test_rank_v2_orders_by_key_and_leaves_input_alone(kit):
    # oracle: requirement 4 — copies with key and 1-based rank, sorted by (v2 key, cik);
    # the expected order is sorted here with hashlib keys
    cands = [kit.candidate(c, "S") for c in CIKS]
    before = copy.deepcopy(cands)
    got = rank_v2(cands, kit.OV)
    order = sorted(CIKS, key=lambda c: (kit.rk2(kit.OV, c), c))
    assert [c["cik"] for c in got] == order
    assert [c["rank"] for c in got] == list(range(1, len(CIKS) + 1))
    assert [c["key"] for c in got] == [kit.rk2(kit.OV, c) for c in order]
    for c in got:
        base = kit.candidate(c["cik"], "S")
        assert {k: v for k, v in c.items() if k not in ("key", "rank")} == base
    assert cands == before


_HEX = "0123456789abcdefABCDEF"


@pytest.mark.criterion("C4")
@given(ov=st.text(alphabet=_HEX, min_size=128, max_size=128),
       num=st.integers(1, 9_999_999_999))
def test_rank_key_v2_matches_oracle_for_random_inputs(kit, ov, num):
    # oracle: requirement 4 — hashlib over the v2 string, for any outputValue case and any
    # CIK given unpadded or padded
    assert rank_key_v2(ov, str(num)) == kit.rk2(ov, str(num))
    assert rank_key_v2(ov, str(num).zfill(10)) == kit.rk2(ov, str(num))


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(perm=st.permutations(list(range(len(CIKS)))), extra=st.integers(4000, 9999))
def test_shuffle_and_added_candidate_keep_relative_order(kit, perm, extra):
    # oracle: requirement 4 — the order is a pure sort by (key, cik): shuffling the input
    # gives the same output, and adding a candidate keeps the others' relative order
    base = rank_v2([kit.candidate(c, "D") for c in CIKS], kit.OV)
    shuffled = rank_v2([kit.candidate(CIKS[i], "D") for i in perm], kit.OV)
    assert shuffled == base
    more = rank_v2([kit.candidate(c, "D") for c in CIKS]
                   + [kit.candidate(str(extra).zfill(10), "D")], kit.OV)
    others = [c["cik"] for c in more if c["cik"] in CIKS]
    assert others == [c["cik"] for c in base]
    assert others == sorted(CIKS, key=lambda c: (kit.rk2(kit.OV, c), c))
