"""C3: rows that do not name the key's top three leave a generated compound not value-correct
(requirement 2).

Each test contrasts the wrong rows with the key's three names reordered, so a fix that made
every ranking right fails here just as the unfixed grader does.
"""

from __future__ import annotations

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from src.eval.core.graders.dispatch import grade_case

WRONG = {
    "DEVD for DEVC": ("DEVA", "DEVB", "DEVD"),
    "excluded DEVE for DEVC": ("DEVA", "DEVB", "DEVE"),
    "only two": ("DEVA", "DEVB"),
    "the three after another": ("DEVD", "DEVA", "DEVB", "DEVC"),
}


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("shape", ["rank_by_ratio", "leader_growth"])
@pytest.mark.parametrize("label", list(WRONG))
def test_wrong_sets_are_wrong(kit, shape, label):
    # oracle: fixture key — the top three are exactly DEVA, DEVB and DEVC (DEVD is fourth,
    # DEVE excluded). Requirement 2 compares the set of the first three row entities; each
    # WRONG list's first three differ from it (or has fewer than three). The key's names
    # reordered (DEVB, DEVC, DEVA) are right.
    case = kit.generated(shape)
    right = kit.gen_answer(shape, rows=kit.step2_rows(shape, ("DEVB", "DEVC", "DEVA")),
                           cited=False)
    assert grade_case(case, right).value_correct is True
    for cited in (True, False):
        g = grade_case(case, kit.gen_answer(shape, rows=kit.revenue_rows(WRONG[label]),
                                            cited=cited))
        assert g.value_correct is False
        assert g.passed is False


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@settings(max_examples=60)
@given(shape=st.sampled_from(["rank_by_ratio", "leader_growth"]),
       picked=st.lists(st.sampled_from(["DEVA", "DEVB", "DEVC", "DEVD", "DEVE"]),
                       min_size=1, max_size=5, unique=True),
       cited=st.booleans())
@example(shape="leader_growth", picked=["DEVC", "DEVA", "DEVB", "DEVD"], cited=False)
@example(shape="rank_by_ratio", picked=["DEVA", "DEVB", "DEVE", "DEVC"], cited=True)
def test_value_correct_exactly_when_the_first_three_are_the_key_set(kit, shape, picked, cited):
    # oracle: requirement 2 applied by hand — the ranking part is right exactly when the set
    # of the first three listed names is {DEVA, DEVB, DEVC}; the dependent is right, so the
    # compound's value-correctness is the ranking's
    case = kit.generated(shape)
    rows = kit.revenue_rows(picked)
    expected = set(picked[:3]) == {"DEVA", "DEVB", "DEVC"}
    g = grade_case(case, kit.gen_answer(shape, rows=rows, cited=cited))
    assert g.value_correct is expected
