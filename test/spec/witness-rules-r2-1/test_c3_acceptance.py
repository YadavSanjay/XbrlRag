"""C3: acceptance_r2_1 applies absolute precision/ambiguity marks and no regression against r2.

Oracle: the marks table of requirement 4 of docs/specs/witness-rules-r2-1.md, applied by hand
(and computed in the property test in integer arithmetic).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.witness_measure import acceptance_r2_1

BASE_BIG = {"hit": 900, "none": 100}
OK = {"accept": True, "failed": [], "regressed_families": 0}


def _s(kit, **families):
    return kit.score({f: kit.counts(**c) for f, c in families.items()})


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(("cand", "expected"), [
    ({"hit": 992, "wrong": 8}, OK),
    ({"hit": 991, "wrong": 9}, {"accept": False, "failed": ["precision"],
                                "regressed_families": 0}),
    ({"hit": 929, "none": 71}, OK),
    ({"hit": 900, "none": 100}, OK),
    ({"hit": 899, "none": 101},
     {"accept": False, "failed": ["recall_regression"], "regressed_families": 0}),
    ({"hit": 950, "ambiguous": 25, "none": 25}, OK),
    ({"hit": 950, "ambiguous": 26, "none": 24},
     {"accept": False, "failed": ["ambiguity"], "regressed_families": 0}),
    ({"hit": 900, "ambiguous": 30, "wrong": 70},
     {"accept": False, "failed": ["ambiguity", "precision"], "regressed_families": 0}),
])
def test_marks_at_their_boundaries(kit, cand, expected):
    # oracle: requirement 4 — precision 992/1000 passes, 991 fails; ambiguity 25/1000 passes,
    # 26 fails (both absolute); no absolute recall mark: 929/1000 and 900/1000 pass against a
    # base of 900 hits, 899 fails only as recall_regression
    assert acceptance_r2_1(_s(kit, A=BASE_BIG), _s(kit, A=cand)) == expected


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("hit", [0, 1, 500])
def test_no_absolute_recall_mark(kit, hit):
    # oracle: requirement 4 — recall has no absolute mark: a candidate equal to r2 is accepted
    # at any recall, provided precision and ambiguity pass (here no wrong, no ambiguous; with
    # 0 hits precision has no decided case and fails)
    base = _s(kit, A={"hit": hit, "none": 1000 - hit})
    v = acceptance_r2_1(base, _s(kit, A={"hit": hit, "none": 1000 - hit}))
    if hit == 0:
        assert v == {"accept": False, "failed": ["precision"], "regressed_families": 0}
    else:
        assert v == OK


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(("base", "cand", "expected"), [
    # equal hits: no regression, whatever else moves
    ({"A": {"hit": 960, "none": 40}}, {"A": {"hit": 960, "none": 40}}, OK),
    ({"A": {"hit": 960, "wrong": 5, "none": 35}}, {"A": {"hit": 960, "none": 40}}, OK),
    # one hit fewer than r2: recall_regression alone
    ({"A": {"hit": 960, "none": 40}}, {"A": {"hit": 959, "none": 41}},
     {"accept": False, "failed": ["recall_regression"], "regressed_families": 0}),
    # B (n=20) loses 2 > 5%: family_regression; total 969 < 970: recall_regression too
    ({"A": {"hit": 950, "none": 50}, "B": {"hit": 20}},
     {"A": {"hit": 951, "none": 49}, "B": {"hit": 18, "none": 2}},
     {"accept": False, "failed": ["family_regression", "recall_regression"],
      "regressed_families": 1}),
    # the same B loss offset in A: total 970 = 970, only family_regression
    ({"A": {"hit": 950, "none": 50}, "B": {"hit": 20}},
     {"A": {"hit": 952, "none": 48}, "B": {"hit": 18, "none": 2}},
     {"accept": False, "failed": ["family_regression"], "regressed_families": 1}),
    # B (n=20) loses exactly 1: allowed by the per-family rule, but the total falls
    ({"A": {"hit": 950, "none": 50}, "B": {"hit": 20}},
     {"A": {"hit": 950, "none": 50}, "B": {"hit": 19, "none": 1}},
     {"accept": False, "failed": ["recall_regression"], "regressed_families": 0}),
])
def test_no_regression_against_r2(kit, base, cand, expected):
    # oracle: requirement 4 — recall_regression when the candidate's hits (same evaluable
    # count) are fewer than r2's; family_regression is witness-rules-r2 requirement 5's rule
    # with r2 as the baseline (n >= 10, loss > 1, loss * 100 > 5 * n); codes sorted
    assert acceptance_r2_1(_s(kit, **base), _s(kit, **cand)) == expected


@pytest.mark.criterion("C3")
def test_verdict_names_no_family_and_mismatches_are_refused(kit):
    # oracle: requirement 4 — codes and a count only; scores of different samples refused
    v = acceptance_r2_1(_s(kit, A={"hit": 990, "none": 10}, Zebrafamily={"hit": 10}),
                        _s(kit, A={"hit": 990, "none": 10}, Zebrafamily={"hit": 7, "none": 3}))
    assert set(v) == {"accept", "failed", "regressed_families"}
    assert v["regressed_families"] == 1
    assert "Zebrafamily" not in repr(v)
    base = _s(kit, A=BASE_BIG, B={"hit": 5})
    with pytest.raises(ValueError):
        acceptance_r2_1(base, _s(kit, A=BASE_BIG))
    with pytest.raises(ValueError):
        acceptance_r2_1(base, _s(kit, A=BASE_BIG, B={"hit": 5, "none": 1}))


def _expected(bf, cf):
    """Requirement 4 applied in the test, in integer arithmetic."""
    ev_keys = ("hit", "none", "ambiguous", "wrong")
    t = {o: sum(c.get(o, 0) for c in cf.values()) for o in ev_keys}
    base_hit = sum(c.get("hit", 0) for c in bf.values())
    ev = sum(t.values())
    failed = []
    if t["ambiguous"] * 1000 > 25 * ev:
        failed.append("ambiguity")
    reg = 0
    for f, c1 in bf.items():
        n = sum(c1.get(o, 0) for o in ev_keys)
        loss = c1.get("hit", 0) - cf[f].get("hit", 0)
        if n >= 10 and loss > 1 and loss * 100 > 5 * n:
            reg += 1
    if reg:
        failed.append("family_regression")
    if not (t["hit"] + t["wrong"] > 0 and t["hit"] * 1000 >= 992 * (t["hit"] + t["wrong"])):
        failed.append("precision")
    if t["hit"] < base_hit:
        failed.append("recall_regression")
    return {"accept": not failed, "failed": failed, "regressed_families": reg}


_fam = st.fixed_dictionaries({"hit": st.integers(0, 60), "none": st.integers(0, 6),
                              "ambiguous": st.integers(0, 3), "wrong": st.integers(0, 2)})


@pytest.mark.criterion("C3")
@given(base=st.dictionaries(st.sampled_from(["A", "B", "C"]), _fam, min_size=1),
       data=st.data())
def test_acceptance_matches_the_table_and_is_monotone(kit, base, data):
    # oracle: requirement 4 computed in the test; monotonicity — turning one candidate `none`
    # into a `hit` never turns accept into reject
    cf = {}
    for f, c in base.items():
        n = sum(c.values())
        hit = data.draw(st.integers(0, n), label=f"hit {f}")
        wrong = data.draw(st.integers(0, n - hit), label=f"wrong {f}")
        amb = data.draw(st.integers(0, n - hit - wrong), label=f"amb {f}")
        cf[f] = {"hit": hit, "wrong": wrong, "ambiguous": amb, "none": n - hit - wrong - amb}
    b = _s(kit, **base)
    v = acceptance_r2_1(b, _s(kit, **cf))
    assert v == _expected(base, cf)
    movable = [f for f, c in cf.items() if c["none"] > 0]
    if movable:
        f = data.draw(st.sampled_from(movable), label="moved family")
        better = {**cf, f: {**cf[f], "none": cf[f]["none"] - 1, "hit": cf[f]["hit"] + 1}}
        w = acceptance_r2_1(b, _s(kit, **better))
        assert w == _expected(base, better)
        if v["accept"]:
            assert w["accept"] is True
