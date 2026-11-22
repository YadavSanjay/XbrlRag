"""C2: a compute's period, from the lookups it combines (Requirement 2)."""

from __future__ import annotations

import copy

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.retriever.xbrl.step_period import compute_period

# Hand-written lookup periods (Requirement 1 shapes): Apple revenue FY2024 and FY2023,
# durations with stated ends 2024-09-28 and 2023-09-30.
REV24 = {"fiscal_year": 2024, "fiscal_period": "FY", "ytd": False,
         "period_start": "2023-10-01", "period_end": "2024-09-28"}
REV23 = {"fiscal_year": 2023, "fiscal_period": "FY", "ytd": False,
         "period_start": "2022-09-25", "period_end": "2023-09-30"}


@pytest.mark.criterion("C2")
def test_current_ratio_lists_both_lookups(vpe):
    # oracle: the spec's C2 by hand: ca then cl (expression order), fiscal years {2025}
    out = compute_period([("ca", vpe.CA_PERIOD), ("cl", vpe.CL_PERIOD)])
    assert out == {"inputs": [{"step_id": "ca", **vpe.CA_PERIOD},
                              {"step_id": "cl", **vpe.CL_PERIOD}],
                   "fiscal_years": [2025]}
    assert out == vpe.CUR_RATIO_PERIOD


@pytest.mark.criterion("C2")
def test_growth_over_two_years_sorts_fiscal_years():
    # oracle: Requirement 2 by hand: inputs keep reference order (rev24, rev23); fiscal_years
    # are sorted ascending
    out = compute_period([("rev24", REV24), ("rev23", REV23)])
    assert out == {"inputs": [{"step_id": "rev24", **REV24}, {"step_id": "rev23", **REV23}],
                   "fiscal_years": [2023, 2024]}


@pytest.mark.criterion("C2")
def test_inputs_without_period_are_left_out(vpe):
    # oracle: Requirement 2 by hand: a None period is not listed; all None -> None
    out = compute_period([("x", None), ("ca", vpe.CA_PERIOD), ("y", None)])
    assert out == {"inputs": [{"step_id": "ca", **vpe.CA_PERIOD}], "fiscal_years": [2025]}
    assert compute_period([("x", None), ("y", None)]) is None
    assert compute_period([]) is None


@pytest.mark.criterion("C2")
def test_bool_fiscal_year_is_listed_but_not_a_year():
    # oracle: Requirement 2 by hand: the entry is listed; only ints (not bools) count as years
    odd = {"fiscal_year": True, "fiscal_period": "FY", "ytd": False, "period_start": None,
           "period_end": "2024-09-28"}
    out = compute_period([("odd", odd), ("rev23", REV23)])
    assert out == {"inputs": [{"step_id": "odd", **odd}, {"step_id": "rev23", **REV23}],
                   "fiscal_years": [2023]}


@pytest.mark.criterion("C2")
def test_compute_over_compute_is_flattened_and_deduplicated(vpe):
    # oracle: Requirement 2 by hand: cur_ratio's period is replaced by its inputs (ca, cl);
    # the later ca is a repeat (first wins); rev24 follows; years {2024, 2025}
    out = compute_period([("cur_ratio", vpe.CUR_RATIO_PERIOD), ("ca", vpe.CA_PERIOD),
                          ("rev24", REV24)])
    assert out == {"inputs": [{"step_id": "ca", **vpe.CA_PERIOD},
                              {"step_id": "cl", **vpe.CL_PERIOD},
                              {"step_id": "rev24", **REV24}],
                   "fiscal_years": [2024, 2025]}


@pytest.mark.criterion("C2")
def test_flattening_is_recursive(vpe):
    # oracle: Requirement 2 by hand: an inputs entry that itself has an inputs list is
    # replaced by its entries too, at every depth
    inner = {"inputs": [{"step_id": "ca", **vpe.CA_PERIOD}], "fiscal_years": [2025]}
    middle = {"inputs": [{"step_id": "wrap", **inner}, {"step_id": "rev23", **REV23}],
              "fiscal_years": [2023, 2025]}
    out = compute_period([("outer", middle), ("rev24", REV24)])
    assert out == {"inputs": [{"step_id": "ca", **vpe.CA_PERIOD},
                              {"step_id": "rev23", **REV23},
                              {"step_id": "rev24", **REV24}],
                   "fiscal_years": [2023, 2024, 2025]}


def _pool(vpe):
    return [("ca", vpe.CA_PERIOD), ("cl", vpe.CL_PERIOD), ("rev24", REV24), ("rev23", REV23),
            ("none", None)]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@settings(max_examples=60)
@given(data=st.data())
def test_repeats_and_permutations(vpe, data):
    # oracle: Requirement 2: repeating an input never changes the result; permuting inputs
    # permutes `inputs` (first-seen order) and never changes `fiscal_years`; no returned dict
    # is an input object, and mutating the result leaves the inputs unchanged
    pool = _pool(vpe)
    base = data.draw(st.lists(st.sampled_from(pool[:4]), min_size=1, max_size=4,
                              unique_by=lambda p: p[0]))
    base = base + data.draw(st.lists(st.sampled_from(pool[4:]), max_size=1))
    inputs = copy.deepcopy(base)
    before = copy.deepcopy(inputs)
    out = compute_period(inputs)
    assert out is not None
    repeated = inputs + [data.draw(st.sampled_from(inputs))]
    assert compute_period(repeated) == out
    perm = data.draw(st.permutations(inputs))
    permuted = compute_period(perm)
    assert permuted is not None
    assert permuted["fiscal_years"] == out["fiscal_years"]
    assert [e["step_id"] for e in permuted["inputs"]] == [s for s, p in perm if p is not None]
    by_id = {e["step_id"]: e for e in out["inputs"]}
    assert {e["step_id"]: e for e in permuted["inputs"]} == by_id
    periods = [p for _, p in inputs if p is not None]
    for entry in out["inputs"]:
        assert all(entry is not p for p in periods)
        entry["fiscal_year"] = 1900
        entry["extra"] = "mutated"
    out["fiscal_years"].append(1900)
    assert inputs == before
