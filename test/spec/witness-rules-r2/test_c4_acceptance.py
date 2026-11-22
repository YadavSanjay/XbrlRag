"""C4: acceptance applies the pre-registered marks exactly, and names no family.

Oracle: the table of requirement 5 of docs/specs/witness-rules-r2.md, applied by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.witness_measure import acceptance

R1_BIG = {"hit": 900, "none": 100}


def _s(kit, **families):
    return kit.score({f: kit.counts(**c) for f, c in families.items()})


def _verdict(kit, r2_big, **small):
    r1 = _s(kit, A=R1_BIG, **{f: r1c for f, (r1c, _) in small.items()})
    r2 = _s(kit, A=r2_big, **{f: r2c for f, (_, r2c) in small.items()})
    return acceptance(r1, r2)


@pytest.mark.criterion("C4")
@pytest.mark.parametrize(("r2_big", "expected"), [
    ({"hit": 992, "wrong": 8}, {"accept": True, "failed": [], "regressed_families": 0}),
    ({"hit": 991, "wrong": 9}, {"accept": False, "failed": ["precision"],
                                "regressed_families": 0}),
    ({"hit": 930, "none": 70}, {"accept": True, "failed": [], "regressed_families": 0}),
    ({"hit": 929, "none": 71}, {"accept": False, "failed": ["recall"], "regressed_families": 0}),
    ({"hit": 950, "ambiguous": 25, "none": 25},
     {"accept": True, "failed": [], "regressed_families": 0}),
    ({"hit": 950, "ambiguous": 26, "none": 24},
     {"accept": False, "failed": ["ambiguity"], "regressed_families": 0}),
    ({"hit": 900, "ambiguous": 30, "wrong": 70},
     {"accept": False, "failed": ["ambiguity", "precision", "recall"],
      "regressed_families": 0}),
])
def test_global_marks_at_their_boundaries(kit, r2_big, expected):
    # oracle: requirement 5 — precision 992/1000 passes, 991/1000 fails; recall 930/1000
    # passes, 929/1000 fails; ambiguity 25/1000 passes, 26/1000 fails; codes sorted
    assert _verdict(kit, r2_big) == expected


GOOD_BIG = {"hit": 990, "none": 10}


@pytest.mark.criterion("C4")
@pytest.mark.parametrize(("small", "expected_regressed"), [
    ({"B": ({"hit": 10}, {"hit": 9, "none": 1})}, 0),          # n=10, loses one filing
    ({"B": ({"hit": 10}, {"hit": 8, "none": 2})}, 1),          # n=10, loses two
    ({"B": ({"hit": 9}, {"none": 9})}, 0),                     # n=9: not judged per family
    ({"B": ({"hit": 40}, {"hit": 38, "none": 2})}, 0),         # n=40, loss 2 = 0.05 exactly
    ({"B": ({"hit": 40}, {"hit": 37, "none": 3})}, 1),         # n=40, loss 3 > 0.05
    ({"B": ({"hit": 10}, {"hit": 8, "none": 2}),
      "C": ({"hit": 40}, {"hit": 37, "none": 3})}, 2),
])
def test_family_regression_rule(kit, small, expected_regressed):
    # oracle: requirement 5 — a family with n >= 10 regresses when loss > 1 and
    # loss * 100 > 5 * n; the large family A keeps every global mark passing
    v = _verdict(kit, GOOD_BIG, **small)
    assert v["regressed_families"] == expected_regressed
    assert v["accept"] is (expected_regressed == 0)
    assert v["failed"] == ([] if expected_regressed == 0 else ["family_regression"])


@pytest.mark.criterion("C4")
def test_verdict_names_no_family(kit):
    # oracle: requirement 5 — the verdict holds codes and a count only
    v = _verdict(kit, GOOD_BIG, Zebrafamily=({"hit": 10}, {"hit": 7, "none": 3}))
    assert set(v) == {"accept", "failed", "regressed_families"}
    assert v["regressed_families"] == 1
    assert "Zebrafamily" not in repr(v)
    assert "A" not in v["failed"]


@pytest.mark.criterion("C4")
def test_mismatched_scores_are_refused(kit):
    # oracle: requirement 5 — different family sets or per-family evaluable counts mean the
    # scores do not come from one sample and oracle
    r1 = _s(kit, A=R1_BIG, B={"hit": 5})
    with pytest.raises(ValueError):
        acceptance(r1, _s(kit, A=R1_BIG))
    with pytest.raises(ValueError):
        acceptance(r1, _s(kit, A=R1_BIG, B={"hit": 5, "none": 1}))
    with pytest.raises(ValueError):
        acceptance(r1, _s(kit, A=R1_BIG, C={"hit": 5}))
    assert acceptance(r1, _s(kit, A={"hit": 1000}, B={"hit": 4, "no_oracle": 9, "none": 1}))[
        "accept"] is True


def _expected(r1f, r2f):
    """Requirement 5 applied in the test, in integer arithmetic."""
    t = {o: sum(c.get(o, 0) for c in r2f.values()) for o in ("hit", "none", "ambiguous", "wrong")}
    ev = sum(t.values())
    failed = []
    if t["ambiguous"] * 1000 > 25 * ev:
        failed.append("ambiguity")
    reg = 0
    for f, c1 in r1f.items():
        n = sum(c1.get(o, 0) for o in ("hit", "none", "ambiguous", "wrong"))
        loss = c1.get("hit", 0) - r2f[f].get("hit", 0)
        if n >= 10 and loss > 1 and loss * 100 > 5 * n:
            reg += 1
    if reg:
        failed.append("family_regression")
    if not (t["hit"] + t["wrong"] > 0 and t["hit"] * 1000 >= 992 * (t["hit"] + t["wrong"])):
        failed.append("precision")
    if not (ev > 0 and t["hit"] * 1000 >= 930 * ev):
        failed.append("recall")
    return {"accept": not failed, "failed": failed, "regressed_families": reg}


_fam = st.fixed_dictionaries({"hit": st.integers(0, 60), "none": st.integers(0, 6),
                              "ambiguous": st.integers(0, 3), "wrong": st.integers(0, 2)})


@pytest.mark.criterion("C4")
@given(base=st.dictionaries(st.sampled_from(["A", "B", "C"]), _fam, min_size=1),
       data=st.data())
def test_acceptance_matches_the_table_and_is_monotone(kit, base, data):
    # oracle: requirement 5 computed in the test; monotonicity — turning one r2 `none` into a
    # `hit` never turns accept into reject
    r2f = {}
    for f, c in base.items():
        n = sum(c.values())
        hit = data.draw(st.integers(0, n), label=f"r2 hit {f}")
        wrong = data.draw(st.integers(0, n - hit), label=f"r2 wrong {f}")
        amb = data.draw(st.integers(0, n - hit - wrong), label=f"r2 amb {f}")
        r2f[f] = {"hit": hit, "wrong": wrong, "ambiguous": amb, "none": n - hit - wrong - amb}
    r1 = _s(kit, **base)
    r2 = _s(kit, **r2f)
    v = acceptance(r1, r2)
    assert v == _expected(base, r2f)
    movable = [f for f, c in r2f.items() if c["none"] > 0]
    if movable:
        f = data.draw(st.sampled_from(movable), label="moved family")
        better = {**r2f, f: {**r2f[f], "none": r2f[f]["none"] - 1, "hit": r2f[f]["hit"] + 1}}
        w = acceptance(r1, _s(kit, **better))
        assert w == _expected(base, better)
        if v["accept"]:
            assert w["accept"] is True
