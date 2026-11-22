"""C3: aggregate counts outcomes and forms the three ratios of requirement 4.

Oracle: hand count of the synthetic r1 outcomes (spec Notes) and the ratio definitions.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.witness_measure import aggregate

# tolerance: ratios of small integers; rel 1e-12 absorbs binary floating point only.
REL = 1e-12

SYNTH_R1 = [
    ("TotalAssets", "hit"), ("Revenue", "hit"), ("NetIncome", "hit"),
    ("TotalAssets", "none"), ("Revenue", "ambiguous"), ("NetIncome", "wrong"),
    ("TotalAssets", "no_oracle"), ("Revenue", "oracle_ambiguous"),
    ("NetIncome", "no_oracle_selected"),
    ("TotalAssets", "no_oracle_selected"), ("Revenue", "no_oracle_selected"),
    ("NetIncome", "no_oracle"),
]


@pytest.mark.criterion("C3")
def test_synthetic_r1_aggregate(kit):
    # oracle: hand count (spec Notes): hit 3, none 1, ambiguous 1, wrong 1, oracle_ambiguous 1,
    # no_oracle 2, no_oracle_selected 3; evaluable 6; recall 1/2, precision 3/4, ambiguity 1/6
    s = aggregate(SYNTH_R1)
    c = kit.counts
    assert s["families"] == {
        "TotalAssets": c(hit=1, none=1, no_oracle=1, no_oracle_selected=1),
        "Revenue": c(hit=1, ambiguous=1, oracle_ambiguous=1, no_oracle_selected=1),
        "NetIncome": c(hit=1, wrong=1, no_oracle_selected=1, no_oracle=1)}
    assert s["totals"] == c(hit=3, none=1, ambiguous=1, wrong=1, oracle_ambiguous=1,
                            no_oracle=2, no_oracle_selected=3)
    assert s["evaluable"] == 6
    assert s["recall"] == pytest.approx(1 / 2, rel=REL)
    assert s["precision"] == pytest.approx(3 / 4, rel=REL)
    assert s["ambiguity"] == pytest.approx(1 / 6, rel=REL)


@pytest.mark.criterion("C3")
def test_empty_denominators_give_none(kit):
    # oracle: requirement 4 — recall and ambiguity are None with no evaluable outcome,
    # precision None with no hit or wrong
    s = aggregate([("A", "no_oracle"), ("A", "oracle_ambiguous")])
    assert s["evaluable"] == 0
    assert s["totals"] == kit.counts(no_oracle=1, oracle_ambiguous=1)
    assert (s["recall"], s["precision"], s["ambiguity"]) == (None, None, None)
    t = aggregate([("A", "none"), ("A", "ambiguous")])
    assert t["evaluable"] == 2
    assert t["precision"] is None
    assert t["recall"] == 0
    assert t["ambiguity"] == pytest.approx(1 / 2, rel=REL)


@pytest.mark.criterion("C3")
def test_unknown_outcome_is_refused():
    # oracle: requirement 4 — only the seven outcomes are counted
    with pytest.raises(ValueError):
        aggregate([("A", "hit"), ("A", "miss")])


OUT = ["hit", "none", "ambiguous", "wrong", "oracle_ambiguous", "no_oracle",
       "no_oracle_selected"]


@pytest.mark.criterion("C3")
@given(rows=st.lists(st.tuples(st.sampled_from(["A", "B", "C"]), st.sampled_from(OUT)),
                     min_size=1, max_size=60),
       seed=st.randoms(use_true_random=False))
def test_aggregate_is_a_count(kit, rows, seed):
    # oracle: requirement 4's definitions, counted in the test; order-invariance
    s = aggregate(rows)
    shuffled = list(rows)
    seed.shuffle(shuffled)
    assert aggregate(shuffled) == s
    fams = {f for f, _ in rows}
    assert s["families"] == {f: kit.counts(**{o: sum(1 for g, p in rows if (g, p) == (f, o))
                                               for o in OUT}) for f in fams}
    tot = {o: sum(1 for _, p in rows if p == o) for o in OUT}
    assert s["totals"] == tot
    ev = tot["hit"] + tot["none"] + tot["ambiguous"] + tot["wrong"]
    assert s["evaluable"] == ev
    dec = tot["hit"] + tot["wrong"]
    assert s["recall"] == (pytest.approx(tot["hit"] / ev, rel=REL) if ev else None)
    assert s["precision"] == (pytest.approx(tot["hit"] / dec, rel=REL) if dec else None)
    assert s["ambiguity"] == (pytest.approx(tot["ambiguous"] / ev, rel=REL) if ev else None)
