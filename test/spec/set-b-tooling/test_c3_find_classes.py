"""C3: ``find_classes`` — matching one written amount and grouping matches into classes.

Oracle: requirements 3 and 6 of docs/specs/set-b-tooling.md. Candidates are built by hand
(conftest ``cand``); each expected class list is read off them with the spec's rules:
agree when ``|v - value| <= max(half_unit, 1e-9 x |value|)``, then filter on dimensional,
unit, concept and member, then group by (concept, unit, period_start, period_end,
member_label), sorted.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.core import find_classes

M = 1e6
AMOUNT = (391035 * M, 0.5 * M)  # "391,035" in millions, as worked in requirement 2


@pytest.mark.criterion("C3")
def test_agrees_within_half_unit_not_just_outside(kit):
    # oracle: requirement 3 — |391035.4e6 - 391035e6| = 0.4e6 <= 0.5e6 agrees;
    # 0.6e6 > 0.5e6 (and > 1e-9 x 391035e6 = 391) does not; 391034.7e6 is 0.3e6 below, agrees
    c = kit.cand
    cands = [c(1, "Revenues", 391035.4 * M), c(2, "SalesNet", 391035.6 * M),
             c(3, "CostOfRevenue", 391034.7 * M), c(4, "OtherIncome", 391034.4 * M)]
    assert find_classes(cands, AMOUNT, "USD", False) == [[1], [3]]


@pytest.mark.criterion("C3")
def test_relative_floor_for_an_exact_amount(kit):
    # oracle: requirement 3 — with half_unit 0 the floor is 1e-9 x 5e12 = 5000: a fact
    # 4000 away agrees, one 6000 away does not
    c = kit.cand
    cands = [c(1, "Assets", 5e12 + 4000.0, instant=True),
             c(2, "Liabilities", 5e12 + 6000.0, instant=True)]
    assert find_classes(cands, (5e12, 0.0), "USD", False) == [[1]]


@pytest.mark.criterion("C3")
def test_unit_filter(kit):
    # oracle: requirement 6 — a candidate matches only when its unit equals `unit`; the
    # TWD fact has the same value but is excluded, and asking for TWD keeps only it
    c = kit.cand
    cands = [c(1, "Revenues", 391035 * M), c(2, "Revenue", 391035 * M, unit="TWD", ns=kit.IFRS)]
    assert find_classes(cands, AMOUNT, "USD", False) == [[1]]
    assert find_classes(cands, AMOUNT, "TWD", False) == [[2]]


@pytest.mark.criterion("C3")
def test_dimensional_filter(kit):
    # oracle: requirement 6 — `dimensional` must equal the candidate's flag; the flat and
    # the dimensional fact share a value and only one of them matches each way
    c = kit.cand
    cands = [c(1, "Revenues", 391035 * M), c(2, "Revenues", 391035 * M, member="Cloud")]
    assert find_classes(cands, AMOUNT, "USD", False) == [[1]]
    assert find_classes(cands, AMOUNT, "USD", True) == [[2]]


@pytest.mark.criterion("C3")
def test_concept_hint_by_family_or_local_name_substring(kit):
    # oracle: requirement 6 — hint "Revenue": fact 1 by family equality ("Revenue"), fact 2
    # by case-insensitive local-name substring ("RevenueFromContract..."), fact 3 neither
    # (local "OperatingIncomeLoss", family "OperatingIncome"); the namespace does not count,
    # so hint "fasb" matches nothing
    c = kit.cand
    cands = [c(1, "SalesNet", 391035 * M, family="Revenue"),
             c(2, "RevenueFromContractWithCustomerExcludingAssessedTax", 391035 * M),
             c(3, "OperatingIncomeLoss", 391035 * M, family="OperatingIncome")]
    assert find_classes(cands, AMOUNT, "USD", False, concept="Revenue") == [[1], [2]]
    assert find_classes(cands, AMOUNT, "USD", False, concept="revenuefromcontract") == [[2]]
    assert find_classes(cands, AMOUNT, "USD", False, concept="fasb") == []
    assert find_classes(cands, AMOUNT, "USD", False) == [[1], [2], [3]]


@pytest.mark.criterion("C3")
def test_member_hint_case_insensitive_substring(kit):
    # oracle: requirement 6 — member "cloud" is a case-insensitive substring of
    # "Intelligent Cloud" only
    c = kit.cand
    cands = [c(1, "Revenues", 391035 * M, member="Intelligent Cloud"),
             c(2, "Revenues", 391035 * M, member="Productivity and Business Processes")]
    assert find_classes(cands, AMOUNT, "USD", True, member="cloud") == [[1]]
    assert find_classes(cands, AMOUNT, "USD", True) == [[1], [2]]


@pytest.mark.criterion("C3")
def test_same_concept_period_member_is_one_class_different_concepts_two(kit):
    # oracle: requirement 6 — facts 7 and 3 share (concept, unit, period, member): one class
    # [3, 7]; fact 5 has another concept: its own class; sorted by first id
    c = kit.cand
    cands = [c(7, "Revenues", 391035 * M), c(5, "SalesNet", 391035 * M),
             c(3, "Revenues", 391035.2 * M)]
    assert find_classes(cands, AMOUNT, "USD", False) == [[3, 7], [5]]


@pytest.mark.criterion("C3")
def test_period_and_member_split_classes(kit):
    # oracle: requirement 6 — the class key includes period_start/period_end and
    # member_label: an instant vs a duration of the same concept, and two members of the
    # same concept, are separate classes
    c = kit.cand
    flat = [c(1, "Cash", 391035 * M), c(2, "Cash", 391035 * M, instant=True)]
    assert find_classes(flat, AMOUNT, "USD", False) == [[1], [2]]
    dims = [c(4, "Revenues", 391035 * M, member="Alpha"),
            c(3, "Revenues", 391035 * M, member="Beta"),
            c(6, "Revenues", 391035 * M, member="Alpha")]
    assert find_classes(dims, AMOUNT, "USD", True) == [[3], [4, 6]]


@pytest.mark.criterion("C3")
def test_classes_and_ids_sorted_numerically(kit):
    # oracle: requirement 6 — ids are ints sorted numerically: class {100, 3} -> [3, 100];
    # class {20} -> [20]; classes ordered by first id: [3, 100] before [20]
    c = kit.cand
    cands = [c(20, "SalesNet", 391035 * M), c(100, "Revenues", 391035 * M),
             c(3, "Revenues", 391035 * M)]
    assert find_classes(cands, AMOUNT, "USD", False) == [[3, 100], [20]]


_LOCALS = ["Revenues", "SalesNet", "CostOfRevenue", "GrossProfit"]


@pytest.mark.criterion("C3")
@given(
    base=st.lists(st.tuples(st.sampled_from(_LOCALS), st.sampled_from([100, 101, 250]),
                            st.booleans()), min_size=0, max_size=8),
    extra=st.lists(st.tuples(st.sampled_from(_LOCALS), st.sampled_from([100, 101, 250]),
                             st.booleans(), st.sampled_from(["TWD", "shares", "pure"])),
                   min_size=1, max_size=8),
    seed_pos=st.integers(min_value=0, max_value=20),
)
def test_other_unit_candidates_never_change_the_result(kit, base, extra, seed_pos):
    # oracle: requirement 6 — the unit filter drops every non-USD candidate, so adding
    # them (anywhere in the list) leaves the classes unchanged; a guaranteed USD match
    # (id 1, Revenues, 100e6, flat) keeps the result non-empty
    c = kit.cand
    usd = [c(1, "Revenues", 100 * M)]
    usd += [c(10 + i, loc, v * M, member="Seg" if dim else None)
            for i, (loc, v, dim) in enumerate(base)]
    others = [c(100 + i, loc, v * M, unit=u, member="Seg" if dim else None)
              for i, (loc, v, dim, u) in enumerate(extra)]
    pos = seed_pos % (len(usd) + 1)
    mixed = usd[:pos] + others + usd[pos:]
    amount = (100 * M, 0.5 * M)
    before = find_classes(usd, amount, "USD", False)
    after = find_classes(mixed, amount, "USD", False)
    assert before
    assert before[0][0] == 1
    assert after == before
