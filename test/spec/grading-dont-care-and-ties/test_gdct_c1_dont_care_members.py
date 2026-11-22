"""C1: which exclusions are "don't care" (docs/specs/grading-dont-care-and-ties.md, req. 1)."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.graders.v2 import dont_care_members

STRICT = ["no_fsds", "fsds_not_published", "fallback_blocked", "presented_line_not_keyed",
          "value_recast", "no_key", "ambiguous_lines", "witness_blocked", "period_not_filed",
          "metric_not_tagged", "currency", "not_in_corpus", "definition_excluded",
          "substitute:includes_cost_of_revenue"]


@pytest.mark.criterion("C1")
def test_only_a_reviewed_drop_with_a_value_is_dont_care(kit):
    # oracle: requirement 1 — resolved_drop with a numeric value only; the K1 strict reasons
    # (no_fsds, fallback_blocked, fsds_not_published, presented_line_not_keyed) never
    excluded = [kit.drop("DEVN", 450 * kit.M, "no_fsds"),
                kit.drop("DEVW", 450 * kit.M),
                kit.drop("DEVF", 1 * kit.M, "fallback_blocked"),
                kit.drop("DEVP", 2 * kit.M, "fsds_not_published"),
                kit.drop("DEVX", 3 * kit.M, "presented_line_not_keyed")]
    case = kit.screen_case([("DEVA", 500 * kit.M), ("DEVB", 400 * kit.M)], excluded)
    assert dont_care_members(case) == {"DEVW": 450 * kit.M}


@pytest.mark.criterion("C1")
def test_zero_counts_and_non_numeric_values_do_not(kit):
    # oracle: requirement 1 — numeric = int or float, not bool, finite; 0 is numeric
    excluded = [kit.drop("DEVW", 0), kit.drop("DEVY"), kit.drop("DEVZ", "450"),
                kit.drop("DEVT", True), kit.drop("DEVU", math.nan),
                kit.drop("DEVV", math.inf), kit.drop("DEVW", 7 * kit.M)]
    case = kit.screen_case([("DEVA", 500 * kit.M), ("DEVB", 400 * kit.M)], excluded)
    got = dont_care_members(case)
    assert got == {"DEVW": 0.0}  # the repeated DEVW keeps its first entry
    assert isinstance(got["DEVW"], float)


@pytest.mark.criterion("C1")
def test_hx_and_bare_cases_have_none_and_seven_raise(kit):
    # oracle: requirement 1 — H-X cases and cases without disclosures have none; > 6 raises
    hx = EvalCase.from_dict({
        "id": "hx-gdct", "suite": "xbrl_hx", "kind": "screen", "question": "Top 2?",
        "tags": ["split:external", "cross_filer", "screen_top"], "top_n": 2,
        "expected_rows": [{"ticker": "DEVA", "value": 5.0}, {"ticker": "DEVB", "value": 4.0}],
        "universe": ["DEVA", "DEVB", "DEVW"],
        "expected_exclusions": [{"ticker": "DEVW", "reason": "resolved_drop", "value": 3.0}]})
    bare = EvalCase.from_dict({"id": "bare", "suite": "s", "kind": "lookup",
                               "question": "q", "expected_value": 1.0})
    seven = kit.screen_case([("DEVA", 9.0), ("DEVB", 8.0)],
                            [kit.drop(f"DEW{c}", float(i)) for i, c in enumerate("ABCDEFG")])
    six = kit.screen_case([("DEVA", 9.0), ("DEVB", 8.0)],
                          [kit.drop(f"DEW{c}", float(i + 1)) for i, c in enumerate("ABCDEF")])
    assert dont_care_members(hx) == {}
    assert dont_care_members(bare) == {}
    assert len(dont_care_members(six)) == 6
    with pytest.raises(ValueError):
        dont_care_members(seven)


_entry = st.tuples(
    st.sampled_from(["resolved_drop", "resolved_drop", *STRICT]),
    st.one_of(st.none(), st.integers(-10**12, 10**12), st.floats(allow_nan=True),
              st.booleans(), st.text(max_size=3)),
)


@pytest.mark.criterion("C1")
@given(entries=st.lists(_entry, max_size=8),
       must=st.integers(0, 10**12))
def test_the_members_are_exactly_the_numeric_reviewed_drops(kit, entries, must):
    # oracle: requirement 1, applied by hand to each generated entry; one numeric
    # resolved_drop (DEVW) is always present so the result is never empty
    excluded = [kit.drop("DEVW", must)]
    for i, (reason, value) in enumerate(entries):
        e = {"ticker": f"DE{i:02d}", "reason": reason}
        if value is not None:
            e["value"] = value
        excluded.append(e)
    want = {"DEVW": float(must)}
    for e in excluded[1:]:
        v = e.get("value")
        numeric = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        if e["reason"] == "resolved_drop" and numeric:
            want[e["ticker"]] = float(v)
    case = kit.screen_case([("DEVA", 9.0), ("DEVB", 8.0)], excluded)
    if len(want) > 6:
        with pytest.raises(ValueError):
            dont_care_members(case)
    else:
        assert dont_care_members(case) == want
