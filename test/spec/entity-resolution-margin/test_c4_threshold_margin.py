"""C4: fuzzy threshold and runner-up margin (docs/specs/entity-resolution-margin.md, R3).

Scores are C2's hand counts (each also read from PostgreSQL pg_trgm), so every expected
status follows from R3's rule: resolve iff s1 >= 0.75 and s1 - s2 >= 0.10.
"""

from __future__ import annotations

import pytest
from erm_data import CORPUS, SEC_CIK, sec_row
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.entity_resolution import resolve_company

MU, MCHP, TXN, CEG = SEC_CIK["MU"], SEC_CIK["MCHP"], SEC_CIK["TXN"], SEC_CIK["CEG"]
A, B, C = "0000000001", "0000000002", "0000000003"


@pytest.mark.criterion("C4")
def test_a_typo_clears_the_threshold_alone_but_not_beside_a_lookalike():
    # oracle: hand trigram counts — "micrn technology" vs Micron's key "micron technology":
    # micrn 6 + technology 11 = 17, micron 7 + 11 = 18, shared 4 + 11 = 15 -> 15/20 = 0.75;
    # vs Microsoft's "microsoft" (10): shared 4 -> 4/23; vs Microchip's "microchip
    # technology" (21): shared 15 -> 15/23. Corpus: 0.75 - 4/23 >= 0.10 -> Micron.
    # With Microchip (SEC row): 15/20 - 15/23 = 0.098 < 0.10 -> ambiguous (MU, MCHP).
    # tolerance: exact ratios, float rounding only
    alone = resolve_company("micrn technology", CORPUS, {})
    assert (alone.status, alone.cik, alone.candidates) == ("fuzzy_name", MU, ())
    assert alone.score == pytest.approx(15 / 20, abs=1e-9)

    beside = resolve_company("micrn technology", [*CORPUS, sec_row("MCHP")], {})
    assert (beside.status, beside.cik) == ("ambiguous", None)
    assert beside.candidates[:2] == (MU, MCHP)
    assert beside.score == pytest.approx(15 / 20, abs=1e-9)


@pytest.mark.criterion("C4")
def test_texas_instrument_resolves_and_constellation_alone_does_not():
    # oracle: C2 — "texas instrument" vs "texas instruments" = 16/19 >= 0.75, and no other
    # corpus key shares more than "  t", " te", "tex"-like scraps (far below 16/19 - 0.10);
    # "constellation" vs "constellation energy" = 14/21 < 0.75 -> ambiguous naming CEG
    # tolerance: exact ratios, float rounding only
    ti = resolve_company("Texas Instrument", CORPUS, {})
    assert (ti.status, ti.cik, ti.candidates) == ("fuzzy_name", TXN, ())
    assert ti.score == pytest.approx(16 / 19, abs=1e-9)

    ceg = resolve_company("Constellation", CORPUS, {})
    assert (ceg.status, ceg.cik, ceg.candidates) == ("ambiguous", None, (CEG,))
    assert ceg.score == pytest.approx(14 / 21, abs=1e-9)


@pytest.mark.criterion("C4")
def test_threshold_is_inclusive_at_0_75_and_rejects_8_of_11():
    # oracle: C2 hand counts — 12/16 = 0.75 exactly clears ">= 0.75"; 8/11 = 0.727 does not
    # tolerance: exact ratios, float rounding only
    at = resolve_company("abcdefghijkl", [(A, None, "abcdefghijklmn")], {})
    assert (at.status, at.cik) == ("fuzzy_name", A)
    assert at.score == pytest.approx(0.75, abs=1e-9)

    below = resolve_company("abcdefgh", [(A, None, "abcdefghx")], {})
    assert (below.status, below.cik, below.candidates) == ("ambiguous", None, (A,))
    assert below.score == pytest.approx(8 / 11, abs=1e-9)


@pytest.mark.criterion("C4")
def test_margin_separates_a_clear_winner_from_a_near_tie():
    # oracle: C2 hand counts — mention "abcdefghijkl": A "abcdefghijklm" 12/15 = 0.8,
    # B "abcdefghijklmn" 12/16 = 0.75 (margin 0.05 < 0.10), C "abcdefghijxy" 10/16 = 0.625
    # (margin 0.175 >= 0.10)
    near = resolve_company("abcdefghijkl", [(B, None, "abcdefghijklmn"),
                                            (A, None, "abcdefghijklm")], {})
    assert (near.status, near.cik, near.candidates) == ("ambiguous", None, (A, B))

    clear = resolve_company("abcdefghijkl", [(C, None, "abcdefghijxy"),
                                             (A, None, "abcdefghijklm")], {})
    assert (clear.status, clear.cik, clear.candidates) == ("fuzzy_name", A, ())


@pytest.mark.criterion("C4")
def test_nothing_above_the_floor_is_unresolved_without_candidates():
    # oracle: R3 step 5 — no CIK scores >= 0.30 ("zzqq" shares no trigram with any corpus
    # key: no corpus name has a "zz" or "qq" or starts with z), so unresolved, no candidates
    m = resolve_company("zzqq", CORPUS, {})
    assert (m.status, m.cik, m.candidates) == ("unresolved", None, ())


@pytest.mark.criterion("C4")
def test_candidates_are_capped_at_three_best_first():
    # oracle: R3 step 5 — at most 3 candidates, best first, ties by CIK ascending. Mention
    # "abcdefgh" (9 trigrams): "abcdefghx" 8/11, "abcdefghy" 8/11, "abcdefgxy" shares
    # "  a".."efg" (7) of 10 -> 7/12, "abcdefxyz" 6/13; all < 0.75
    filers = [("0000000004", None, "abcdefxyz"), (C, None, "abcdefgxy"),
              (B, None, "abcdefghy"), (A, None, "abcdefghx")]
    m = resolve_company("abcdefgh", filers, {})
    assert (m.status, m.cik) == ("ambiguous", None)
    assert m.candidates == (A, B, C)


# ---------------------------------------------------------------- properties
@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(st.text(alphabet="bcdfghlmnprst", min_size=12, max_size=20),
       st.sampled_from(["x", "y", "z", "q"]), st.sampled_from(["k", "j", "v", "w"]))
def test_a_mention_equidistant_to_two_filers_never_resolves(word, x, y):
    # oracle: R3 margin — filers word+x and word+y have trigram sets of the same shape
    # around the mention "word" (same size, same overlap), so s1 == s2 and the margin is 0;
    # both are named. The word never contains x, y, z, q, k, j, v or w, so no trigram of
    # word+x or word+y occurs elsewhere in it. (A word of 12+ distinct trigrams scores
    # >= 0.8 against each, above the threshold, so only the margin stops it.)
    filers = [(B, None, word + y), (A, None, word + x)]
    m = resolve_company(word, filers, {})
    assert (m.status, m.cik) == ("ambiguous", None)
    assert m.candidates == (A, B)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(st.sampled_from(["micrn technology", "Texas Instrument", "Constellation", "zzqq",
                        "Abbott Labs", "Wal-Mart", "Nvida"]),
       st.randoms(use_true_random=False))
def test_fuzzy_result_does_not_depend_on_universe_order(mention, rnd):
    # oracle: R3 — scores are per CIK and ties break by CIK, so the order of filers is
    # irrelevant; pinned to the hand-derived outcomes of the first three mentions
    universe = [*CORPUS, sec_row("MCHP")]
    shuffled = list(universe)
    rnd.shuffle(shuffled)
    first = resolve_company(mention, universe, {})
    second = resolve_company(mention, shuffled, {})
    assert (first.status, first.cik, first.candidates) == (
        second.status, second.cik, second.candidates)
    if mention == "Texas Instrument":
        assert (first.status, first.cik) == ("fuzzy_name", TXN)
    elif mention in ("micrn technology", "Constellation"):
        assert first.status == "ambiguous"
    assert first.cik is None or first.status == "fuzzy_name"
