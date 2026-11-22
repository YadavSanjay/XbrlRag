"""C2: trigram_similarity (docs/specs/entity-resolution-margin.md, R2)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.entity_resolution import trigram_similarity

# (a, b, shared trigrams, all trigrams). Each pair was read from PostgreSQL pg_trgm
# similarity() on xbrl_db on 2026-11-07 and counted by hand:
# - "micron technology" / "microchip technology": "technology" gives 11 trigrams to both;
#   "micron" 7, "microchip" 10, sharing "  m", " mi", "mic", "icr", "cro" -> 16 shared,
#   18 + 21 - 16 = 23 in all (pg_trgm 0.6956522).
# - "texas instrument" / "texas instruments": 17 and 18 trigrams; "nt " vs "nts", "ts "
#   differ -> 16 shared, 19 in all (pg_trgm 0.84210527).
# - "constellation" / "constellation energy": 14 trigrams, all shared; energy adds 7 -> 14/21.
# - "abcdefghijkl" (13 trigrams) / "abcdefghijklm" (14): shared "  a", " ab" and the 10
#   inner ones -> 12/15 (pg_trgm 0.8).
# - "abcdefghijkl" / "abcdefghijklmn" (15): 12 shared, 16 in all (pg_trgm 0.75).
# - "abcdefghijkl" / "abcdefghijxy" (13): shared "  a", " ab", abc..hij (8) -> 10/16 (0.625).
# - "abcdefgh" (9) / "abcdefghx" (10): all but "gh " shared -> 8/11 (pg_trgm 0.72727275).
HAND = [
    ("micron technology", "microchip technology", 16, 23),
    ("texas instrument", "texas instruments", 16, 19),
    ("constellation", "constellation energy", 14, 21),
    ("abcdefghijkl", "abcdefghijklm", 12, 15),
    ("abcdefghijkl", "abcdefghijklmn", 12, 16),
    ("abcdefghijkl", "abcdefghijxy", 10, 16),
    ("abcdefgh", "abcdefghx", 8, 11),
]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("a,b,shared,total", HAND)
def test_similarity_matches_pg_trgm_and_the_hand_count(a, b, shared, total):
    # oracle: PostgreSQL pg_trgm similarity() and the hand counts above
    # tolerance: the ratio of two small integers, so only float rounding (1e-9)
    assert trigram_similarity(a, b) == pytest.approx(shared / total, abs=1e-9)


@pytest.mark.criterion("C2")
def test_similarity_edge_values():
    # oracle: R2 — 0.0 when either string has no trigram; 1.0 for equal trigram sets;
    # disjoint words share nothing
    assert trigram_similarity("", "apple") == pytest.approx(0.0, abs=1e-12)  # empty set: 0
    assert trigram_similarity("!!!", "apple") == pytest.approx(0.0, abs=1e-12)  # no words: 0
    assert trigram_similarity("Apple", "apple") == pytest.approx(1.0, abs=1e-9)  # exact ratio
    assert trigram_similarity("xyz", "apple") == pytest.approx(0.0, abs=1e-12)  # 0 shared


WORDS = st.lists(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1,
                         max_size=8), min_size=1, max_size=4)


@pytest.mark.criterion("C2")
@given(WORDS, WORDS)
def test_similarity_is_symmetric_bounded_and_reflexive(wa, wb):
    # oracle: R2 — |A∩B|/|A∪B| is symmetric and in [0, 1]; a non-empty set against itself is 1
    # tolerance: same ratio computed twice, float rounding only
    a, b = " ".join(wa), " ".join(wb)
    s = trigram_similarity(a, b)
    assert s == pytest.approx(trigram_similarity(b, a), abs=1e-12)
    assert 0.0 <= s <= 1.0
    assert trigram_similarity(a, a) == pytest.approx(1.0, abs=1e-12)


@pytest.mark.criterion("C2")
@given(WORDS, st.randoms(use_true_random=False))
def test_similarity_ignores_case_and_word_order(words, rnd):
    # oracle: R2 — the string is lower-cased and its trigram set is a union over words,
    # so upper-casing and reordering words leave the set, hence a similarity of 1.0
    # tolerance: an exact ratio n/n, float rounding only
    a = " ".join(words)
    shuffled = list(words)
    rnd.shuffle(shuffled)
    b = "  ".join(shuffled).upper()
    assert trigram_similarity(a, b) == pytest.approx(1.0, abs=1e-12)
