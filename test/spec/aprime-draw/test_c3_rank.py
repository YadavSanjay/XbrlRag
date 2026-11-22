"""C3: ``rank_key`` and ``rank``.

Oracle: requirements 5-6 of docs/specs/aprime-draw.md. Every expected key is computed in
the test with ``hashlib.sha256`` over ``"xbrlrag-aprime-v1|" + OV.upper() + "|" + cik10``
(``kit.rk`` in conftest), and every expected order sorts by ``(key, cik)``.
"""

from __future__ import annotations

import copy
import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.aprime.core import rank, rank_key

_HEX = "0123456789abcdefABCDEF"


@pytest.mark.criterion("C3")
def test_rank_key_matches_hashlib_for_lowercase_ov_and_unpadded_cik(kit):
    # oracle: requirement 5 — sha256 of the ASCII string, computed here; the OV is
    # upper-cased and "320193" is zero-padded to "0000320193"
    ov = "ab" * 64
    expected = hashlib.sha256(
        ("xbrlrag-aprime-v1|" + ("AB" * 64) + "|0000320193").encode("ascii")).hexdigest()
    got = rank_key(ov, "320193")
    assert got == expected
    assert got == rank_key(ov.upper(), "0000320193")
    assert len(got) == 64
    assert got == got.lower()


def _cands(kit, ciks):
    return [kit.candidate(c, "S" if i % 2 else "D") for i, c in enumerate(ciks)]


@pytest.mark.criterion("C3")
def test_rank_orders_by_key_numbers_from_one_and_leaves_input_alone(kit):
    # oracle: requirement 6 — copies with "key" = sha256 (computed here) and 1-based "rank",
    # sorted by (key, cik); the input list and its dicts are unchanged
    ciks = ["0000000011", "0000000022", "0000000033", "0000000044", "0000000055"]
    cands = _cands(kit, ciks)
    before = copy.deepcopy(cands)
    got = rank(cands, kit.OV)
    order = sorted(ciks, key=lambda c: (kit.rk(kit.OV, c), c))
    by_cik = {c["cik"]: c for c in before}
    expected = [dict(by_cik[c], key=kit.rk(kit.OV, c), rank=i + 1) for i, c in enumerate(order)]
    assert got == expected
    assert [r["rank"] for r in got] == [1, 2, 3, 4, 5]
    assert cands == before
    assert all("key" not in c and "rank" not in c for c in cands)
    assert got is not cands


@st.composite
def _ranking_case(draw):
    ov = draw(st.text(alphabet=_HEX, min_size=128, max_size=128))
    nums = draw(st.lists(st.integers(1, 9_999_999_999), min_size=1, max_size=12, unique=True))
    extra = draw(st.integers(1, 9_999_999_999).filter(lambda n: n not in nums))
    return ov, [str(n).zfill(10) for n in nums], str(extra).zfill(10)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(case=_ranking_case(), data=st.data())
def test_adding_a_candidate_keeps_relative_order_and_shuffling_changes_nothing(kit, case, data):
    # oracle: requirement 6 — order is by (key, cik) with keys computed here by hashlib, so
    # adding one candidate never reorders the others and a shuffled input gives the same output
    ov, ciks, extra = case
    cands = _cands(kit, ciks)
    base = rank(cands, ov)
    assert [r["cik"] for r in base] == sorted(ciks, key=lambda c: (kit.rk(ov, c), c))
    assert [r["rank"] for r in base] == list(range(1, len(ciks) + 1))

    grown = rank(cands + [kit.candidate(extra, "S")], ov)
    assert len(grown) == len(ciks) + 1
    assert [r["cik"] for r in grown if r["cik"] != extra] == [r["cik"] for r in base]

    perm = data.draw(st.permutations(list(range(len(cands)))))
    assert rank([cands[i] for i in perm], ov) == base
