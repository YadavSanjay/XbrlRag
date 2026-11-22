"""C3: per-arm cells by group, with exclusions, errors, declines and missing cases.

The scenario is built in conftest.py (``scenario_arms``); its table is repeated here:
X answers all 12 cases (c1 errored), F has no row for k1, O excludes s1-s4, p1, k1 and d1.
"""

from __future__ import annotations

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.r1_report import build_r1

# Rational counts over small n; 1e-12 only absorbs float division.
TOL = 1e-12

# Hand counts: (n_cases, n_missing, n_excluded, n, n_value_correct, n_confident_wrong)
EXPECTED = {
    "cross_filer": {"X": (5, 0, 0, 5, 4, 1), "F": (5, 0, 0, 5, 1, 0), "O": (5, 0, 5, 0, 0, 0)},
    "cross_period": {"X": (2, 0, 0, 2, 1, 0), "F": (2, 0, 0, 2, 2, 0), "O": (2, 0, 0, 2, 1, 0)},
    "compound": {"X": (1, 0, 0, 1, 1, 0), "F": (0, 1, 0, 0, 0, 0), "O": (1, 0, 1, 0, 0, 0)},
    "line1": {"X": (8, 0, 0, 8, 6, 1), "F": (7, 1, 0, 7, 3, 0), "O": (8, 0, 6, 2, 1, 0)},
    "single_filer_numeric": {"X": (4, 0, 0, 4, 3, 1), "F": (4, 0, 0, 4, 2, 1),
                             "O": (4, 0, 1, 3, 3, 0)},
    "lookup_compute": {"X": (4, 0, 0, 4, 2, 1), "F": (4, 0, 0, 4, 3, 0),
                       "O": (4, 0, 0, 4, 4, 0)},
    "all": {"X": (12, 0, 0, 12, 9, 2), "F": (11, 1, 0, 11, 5, 1), "O": (12, 0, 7, 5, 4, 0)},
}
GROUP_SIZE = {"cross_filer": 5, "cross_period": 2, "compound": 1, "line1": 8,
              "single_filer_numeric": 4, "lookup_compute": 4, "all": 12}
# X's grounding: mean of its non-null grounding_precision per group (s1 1.0, l1 1.0,
# l2 0.5, g1 0.0).
X_GROUNDING = {"cross_filer": 1.0, "cross_period": None, "compound": None, "line1": 1.0,
               "single_filer_numeric": 0.5, "lookup_compute": 0.5, "all": 0.625}


def _r1(arms):
    return build_r1(arms, "X", "F", oracle="O")


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("group", sorted(EXPECTED))
def test_counts_and_rates_match_hand_counts(scenario, group):
    # oracle: hand counts of the scenario (requirements 4 and 5)
    r1 = _r1(scenario())
    for label, (n_cases, n_missing, n_excl, n, k, cw) in EXPECTED[group].items():
        cell = r1["groups"][group][label]
        assert (cell["n_cases"], cell["n_missing"], cell["n_excluded"], cell["n"]) == \
            (n_cases, n_missing, n_excl, n), (group, label)
        assert (cell["n_value_correct"], cell["n_confident_wrong"]) == (k, cw), (group, label)
        if n == 0:
            assert cell["value_accuracy"] is None
            assert cell["confident_wrong_rate"] is None
            assert cell["mean_cost_usd"] is None
        else:
            # tolerance: k / n of small integers
            assert cell["value_accuracy"] == pytest.approx(k / n, abs=TOL)
            assert cell["confident_wrong_rate"] == pytest.approx(cw / n, abs=TOL)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("group", sorted(EXPECTED))
def test_grounding_is_reference_only(scenario, group):
    # oracle: requirement 5 grounding bullet and R1 ("not applicable" for the others);
    # X's values are the hand means above
    r1 = _r1(scenario())
    expected = X_GROUNDING[group]
    got = r1["groups"][group]["X"]["grounding_precision"]
    if expected is None:
        assert got is None
    else:
        # tolerance: mean of at most four binary-exact values
        assert got == pytest.approx(expected, abs=TOL)
    assert r1["groups"][group]["F"]["grounding_precision"] == "not applicable"
    assert r1["groups"][group]["O"]["grounding_precision"] == "not applicable"


@pytest.mark.criterion("C3")
def test_arm_facts_and_completeness(scenario):
    # oracle: requirement 6 (configs in conftest.py) and requirement 4 (`complete`)
    r1 = _r1(scenario())
    assert r1["arms"]["X"]["service_tier"] == "flex"
    assert r1["arms"]["X"]["max_workers"] == 8
    assert r1["arms"]["X"]["kind_concurrency"] == {"narrative": 1}
    assert r1["arms"]["F"]["service_tier"] == "standard"
    assert r1["arms"]["F"]["max_workers"] == 4
    assert r1["arms"]["O"]["service_tier"] is None
    assert r1["arms"]["O"]["max_workers"] is None
    assert r1["arms"]["X"]["system"] == "xbrlrag_full"
    assert r1["arms"]["F"]["run_id"] == "eval-f000000"
    assert (r1["arms"]["F"]["n_cases"], r1["arms"]["F"]["n_missing"],
            r1["arms"]["F"]["n_excluded"]) == (11, 1, 0)
    assert (r1["arms"]["O"]["n_cases"], r1["arms"]["O"]["n_missing"],
            r1["arms"]["O"]["n_excluded"]) == (12, 0, 7)
    assert r1["complete"] is False
    assert (r1["reference"], r1["bar_agent"], r1["oracle"]) == ("X", "F", "O")
    assert r1["heldout"] is False


@pytest.mark.criterion("C3")
def test_complete_when_no_arm_misses_a_case(row, arm):
    # oracle: requirement 4, `complete` is true exactly when no arm misses a case
    x = arm("X", [row("a1"), row("a2", "screen.top")])
    f = arm("F", [row("a1", vc=False), row("a2", "screen.top")])
    r1 = build_r1([x, f], "X", "F")
    assert r1["complete"] is True
    assert r1["groups"]["all"]["F"]["n_value_correct"] == 1


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_order_of_rows_and_arms_does_not_matter(scenario, seed):
    # oracle: requirement 5 defines every cell from sets of rows, not their order;
    # the expected value is the unshuffled report
    base = _r1(scenario())
    rng = random.Random(seed)
    arms = scenario()
    for a in arms:
        rng.shuffle(a["cases"])
    rng.shuffle(arms)
    shuffled = _r1(arms)
    assert shuffled["groups"] == base["groups"]
    assert shuffled["arms"] == base["arms"]
    assert shuffled["comparisons"] == base["comparisons"]


@pytest.mark.criterion("C3")
@given(outcomes=st.lists(st.tuples(st.sampled_from(["lookup", "screen.top", "trend",
                                                    "dimensional", "compound.x"]),
                                   st.sampled_from(["ok", "wrong", "excluded", "missing"])),
                         min_size=1, max_size=30))
def test_cell_identities(row, arm, outcomes):
    # oracle: requirement 4/5 identities: n_cases = n + n_excluded and
    # n_cases + n_missing = |U ∩ group|; counts computed here from the drawn outcomes
    x_rows, f_rows = [], []
    for i, (shape, kind) in enumerate(outcomes):
        cid = f"c{i}"
        x_rows.append(row(cid, shape))
        if kind == "missing":
            continue
        f_rows.append(row(cid, shape, vc=kind == "ok",
                          error="excluded: synthetic" if kind == "excluded" else None))
    r1 = build_r1([arm("X", x_rows), arm("F", f_rows)], "X", "F")
    cell = r1["groups"]["all"]["F"]
    assert cell["n_cases"] == cell["n"] + cell["n_excluded"]
    assert cell["n_cases"] + cell["n_missing"] == len(outcomes)
    assert cell["n_missing"] == sum(1 for _, k in outcomes if k == "missing")
    assert cell["n_excluded"] == sum(1 for _, k in outcomes if k == "excluded")
    assert cell["n_value_correct"] == sum(1 for _, k in outcomes if k == "ok")
    for group in ("cross_filer", "cross_period", "compound", "line1",
                  "single_filer_numeric", "lookup_compute"):
        c = r1["groups"][group]["F"]
        assert c["n_cases"] == c["n"] + c["n_excluded"]
        assert c["n_cases"] + c["n_missing"] == r1["groups"][group]["X"]["n_cases"]
