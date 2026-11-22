"""C1-C6: ``assign_fiscal_labels``, the pure rule (requirements 1-6).

Oracle: the filings' raw iXBRL tags and the filers' own year labels for CRM, HD and WMT; the
requirement's closed forms, worked by hand, for the synthetic runs.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.fiscal_labels import assign_fiscal_labels

DEI = st.integers(min_value=2014, max_value=2024)
RUN = st.lists(DEI, min_size=2, max_size=7)


# ------------------------------------------------------------------- C1: DEI is the label


@pytest.mark.criterion("C1")
def test_home_depot_keeps_its_start_year_labels(kit):
    # oracle: Home Depot labels by start year; its DEI tags 2023, 2024, 2025 are right (S2)
    labels = assign_fiscal_labels(kit.HD)
    assert labels == {kit.H24: 2023, kit.H25: 2024, kit.H26: 2025}


@pytest.mark.criterion("C1")
def test_walmart_with_the_same_year_end_is_unchanged(kit):
    # oracle: Walmart tags 2023-2026 on the same January year-ends as CRM; no two share one
    labels = assign_fiscal_labels(kit.WMT)
    assert labels == {kit.W23: 2023, kit.W24: 2024, kit.W25: 2025, kit.W26: 2026}


@pytest.mark.criterion("C1")
@given(deis=st.lists(DEI, min_size=1, max_size=7, unique=True).map(sorted))
def test_a_strictly_increasing_dei_is_the_label(kit, deis):
    # oracle: requirement 1 — with no collision, S2 alone decides
    filings = kit.run_of(deis)
    assert kit.labels_in_order(assign_fiscal_labels(filings), filings) == deis


# ----------------------------------------------------------------- C2: collisions


@pytest.mark.criterion("C2")
def test_salesforce_later_filing_takes_the_next_label(kit):
    # oracle: Salesforce calls the year ending 2026-01-31 fiscal 2026 although DEI tags 2025;
    # the year ending 2025-01-31 is its fiscal 2025 and keeps the label
    labels = assign_fiscal_labels(kit.CRM)
    assert labels == {kit.C23: 2023, kit.C24: 2024, kit.C25: 2025, kit.C26: 2026}


@pytest.mark.criterion("C2")
def test_three_in_a_row(kit):
    # oracle: requirement 2 — 2025, 2025, 2025 walks to 2025, 2026, 2027
    filings = kit.run_of([2025, 2025, 2025], first_end_year=2025)
    assert kit.labels_in_order(assign_fiscal_labels(filings), filings) == [2025, 2026, 2027]


@pytest.mark.criterion("C2")
def test_a_dei_below_the_prior_label_is_a_collision(kit):
    # oracle: requirement 2 — labels strictly increase: 2025, 2024 gives 2025, 2026
    filings = kit.run_of([2025, 2024], first_end_year=2025)
    assert kit.labels_in_order(assign_fiscal_labels(filings), filings) == [2025, 2026]


@pytest.mark.criterion("C2")
def test_a_later_correct_tag_after_a_slip_needs_no_move(kit):
    # oracle: requirement 2 — CRM's next 10-K tagging 2027 is above the moved 2026: DEI stands
    filings = [*kit.CRM, kit.filing("0001108524-27-000001", date(2027, 1, 31), 2027)]
    labels = assign_fiscal_labels(filings)
    assert labels["0001108524-27-000001"] == 2027
    assert labels[kit.C26] == 2026


@pytest.mark.criterion("C2")
@given(deis=RUN)
def test_consecutive_years_follow_the_closed_form(kit, deis):
    # oracle: requirement 2 for one-year steps, computed here: label[i] = max(dei[i], label[i-1]+1)
    expected = [deis[0]]
    for d in deis[1:]:
        expected.append(max(d, expected[-1] + 1))
    filings = kit.run_of(deis)
    assert kit.labels_in_order(assign_fiscal_labels(filings), filings) == expected


@pytest.mark.criterion("C2")
@given(deis=RUN, cut=st.integers(min_value=1, max_value=6))
def test_a_later_filing_never_moves_an_earlier_label(kit, deis, cut):
    # oracle: requirement 2 — "the earlier filing always keeps its label"; metamorphic
    filings = kit.run_of(deis)
    earlier = filings[:cut]
    whole = assign_fiscal_labels(filings)
    assert assign_fiscal_labels(earlier) == {f["accession_no"]: whole[f["accession_no"]]
                                             for f in earlier}


@pytest.mark.criterion("C2")
@given(deis=RUN, seed=st.integers(min_value=0, max_value=1000))
def test_input_order_does_not_matter(kit, deis, seed):
    # oracle: requirement 2 walks period ends, not the input; metamorphic
    filings = kit.run_of(deis)
    shuffled = list(filings)
    random.Random(seed).shuffle(shuffled)
    assert assign_fiscal_labels(shuffled) == assign_fiscal_labels(filings)
    assert len(set(assign_fiscal_labels(filings).values())) == len(filings)


# ---------------------------------------------------------------- C3: jumps and holes


@pytest.mark.criterion("C3")
def test_a_forward_jump_stands(kit):
    # oracle: Salesforce's own tags: its January-2014 10-K says 2013, its January-2015 10-K 2015
    filings = [kit.filing("a", date(2014, 1, 31), 2013), kit.filing("b", date(2015, 1, 31), 2015)]
    assert assign_fiscal_labels(filings) == {"a": 2013, "b": 2015}


@pytest.mark.criterion("C3")
def test_a_collision_across_a_two_year_hole(kit):
    # oracle: requirement 2 — 731 days / 365.25 rounds to 2, so 2023 + 2
    filings = [kit.filing("a", date(2023, 1, 31), 2023), kit.filing("b", date(2025, 1, 31), 2023)]
    assert assign_fiscal_labels(filings) == {"a": 2023, "b": 2025}


@pytest.mark.criterion("C3")
def test_a_short_year_still_moves_by_one(kit):
    # oracle: requirement 2 — 184 days rounds to 1 (k is at least 1)
    filings = [kit.filing("a", date(2024, 6, 30), 2024), kit.filing("b", date(2024, 12, 31), 2024)]
    assert assign_fiscal_labels(filings) == {"a": 2024, "b": 2025}


@pytest.mark.criterion("C3")
@given(dei=DEI, years=st.integers(min_value=1, max_value=5))
def test_a_collision_moves_by_the_years_elapsed(kit, dei, years):
    # oracle: requirement 2 — k years apart, same DEI: the later is dei + k
    filings = [kit.filing("a", date(2020, 1, 31), dei),
               kit.filing("b", date(2020 + years, 1, 31), dei)]
    assert assign_fiscal_labels(filings) == {"a": dei, "b": dei + years}


# ------------------------------------------------------------------ C4: one period end


@pytest.mark.criterion("C4")
def test_an_amendment_shares_its_originals_label(kit):
    # oracle: requirement 4 — same period end, not a collision; the next year is untouched
    filings = [kit.filing("o", date(2024, 12, 31), 2024, filed=date(2025, 1, 30)),
               kit.filing("a", date(2024, 12, 31), 2024, form="10-K/A", filed=date(2025, 4, 30)),
               kit.filing("n", date(2025, 12, 31), 2025, filed=date(2026, 1, 29))]
    assert assign_fiscal_labels(filings) == {"o": 2024, "a": 2024, "n": 2025}


@pytest.mark.criterion("C4")
def test_the_earliest_filed_dei_decides(kit):
    # oracle: requirement 4 — an amendment tagging 2031 does not move its period
    filings = [kit.filing("o", date(2024, 12, 31), 2024, filed=date(2025, 1, 30)),
               kit.filing("a", date(2024, 12, 31), 2031, form="10-K/A", filed=date(2025, 4, 30))]
    assert assign_fiscal_labels(filings) == {"o": 2024, "a": 2024}


@pytest.mark.criterion("C4")
def test_salesforce_january_2021_accessions(kit):
    # oracle: companyfacts carries two accessions for the year ending 2021-01-31, filed the same
    # day, fy 2020 and 2021; Salesforce's label is fiscal 2021, after fiscal 2020
    filed = date(2021, 3, 17)
    filings = [kit.filing("0001108524-20-000014", date(2020, 1, 31), 2020),
               kit.filing("0001108524-21-000014", date(2021, 1, 31), 2020, filed=filed),
               kit.filing("0001108524-22-000008", date(2021, 1, 31), 2021, filed=filed)]
    labels = assign_fiscal_labels(filings)
    assert labels == {"0001108524-20-000014": 2020, "0001108524-21-000014": 2021,
                      "0001108524-22-000008": 2021}


@pytest.mark.criterion("C4")
@given(deis=RUN, which=st.integers(min_value=0, max_value=6), tagged=DEI)
def test_an_amendment_changes_no_other_label(kit, deis, which, tagged):
    # oracle: requirement 4 — metamorphic: amending any year, with any tag, moves nothing
    filings = kit.run_of(deis)
    base = filings[which % len(filings)]
    amendment = kit.filing("amend", base["period_end"], tagged, form="10-K/A",
                           filed=base["filed_date"] + timedelta(days=30))
    before = assign_fiscal_labels(filings)
    after = assign_fiscal_labels([*filings, amendment])
    assert after == {**before, "amend": before[base["accession_no"]]}


# ------------------------------------------------------------------- C5: missing DEI


@pytest.mark.criterion("C5")
def test_missing_dei_takes_the_prior_label_plus_one(kit):
    # oracle: requirement 5 — 365 days after a 2024 label: 2025, whatever the calendar says
    filings = kit.run_of([2024, None, 2026], first_end_year=2030)
    assert kit.labels_in_order(assign_fiscal_labels(filings), filings) == [2024, 2025, 2026]


@pytest.mark.criterion("C5")
def test_missing_dei_on_the_first_filing_takes_the_next_minus_one(kit):
    # oracle: requirement 5 — no prior; the next year's 2025 gives 2024
    filings = kit.run_of([None, 2025], first_end_year=2030)
    assert kit.labels_in_order(assign_fiscal_labels(filings), filings) == [2024, 2025]


@pytest.mark.criterion("C5")
def test_missing_dei_with_no_neighbour_takes_the_calendar_year(kit):
    # oracle: requirement 5 — a lone filing, and one with only non-adjacent neighbours
    lone = kit.filing("a", date(2026, 2, 1), None)
    assert assign_fiscal_labels([lone]) == {"a": 2026}
    apart = [kit.filing("b", date(2023, 1, 31), 2023), kit.filing("c", date(2026, 1, 31), None)]
    assert assign_fiscal_labels(apart) == {"b": 2023, "c": 2026}


@pytest.mark.criterion("C5")
def test_an_amendments_dei_serves_an_untagged_original(kit):
    # oracle: requirement 4 — the earliest filed filing that has a DEI
    filings = [kit.filing("o", date(2024, 12, 31), None, filed=date(2025, 1, 30)),
               kit.filing("a", date(2024, 12, 31), 2024, form="10-K/A", filed=date(2025, 4, 30))]
    assert assign_fiscal_labels(filings) == {"o": 2024, "a": 2024}


@pytest.mark.criterion("C5")
@given(start=DEI, size=st.integers(min_value=3, max_value=7),
       hole=st.integers(min_value=0, max_value=6))
def test_removing_one_tag_from_a_clean_run_changes_nothing(kit, start, size, hole):
    # oracle: requirement 5 — metamorphic: in a run stepping by one, the neighbours rebuild a
    # missing tag exactly
    deis: list[int | None] = list(range(start, start + size))
    deis[hole % size] = None
    filings = kit.run_of(deis)
    expected = list(range(start, start + size))
    assert kit.labels_in_order(assign_fiscal_labels(filings), filings) == expected


# ---------------------------------------------------------------- C6: every other form


@pytest.mark.criterion("C6")
def test_a_10q_takes_its_dei_over_its_calendar_year(kit):
    # oracle: requirement 6 — a January filer's quarter ending 2026-04-26 is its fiscal 2027
    q = kit.filing("q", date(2026, 4, 26), 2027, form="10-Q")
    assert assign_fiscal_labels([q]) == {"q": 2027}


@pytest.mark.criterion("C6")
def test_a_10q_without_dei_takes_its_calendar_year(kit):
    # oracle: requirement 6 — the manifest's value today
    q = kit.filing("q", date(2026, 7, 26), None, form="10-Q")
    assert assign_fiscal_labels([q]) == {"q": 2026}


@pytest.mark.criterion("C6")
def test_a_transition_report_is_no_collision(kit):
    # oracle: requirement 6 — a 10-KT sharing the year's DEI moves neither filing
    filings = [kit.filing("a", date(2024, 6, 30), 2024),
               kit.filing("t", date(2024, 12, 31), 2024, form="10-KT"),
               kit.filing("b", date(2025, 12, 31), 2025)]
    assert assign_fiscal_labels(filings) == {"a": 2024, "t": 2024, "b": 2025}


@pytest.mark.criterion("C6")
@given(deis=RUN, tags=st.lists(DEI, min_size=1, max_size=4))
def test_quarterly_filings_and_annual_filings_are_independent(kit, deis, tags):
    # oracle: requirement 6 — metamorphic: adding 10-Qs moves no annual label, and each 10-Q
    # gets a label. Changed by tenq-fiscal-labels (docs/specs/tenq-fiscal-labels.md,
    # requirement 2): a 10-Q near an annual period now takes the label of the fiscal year that
    # contains it, not its own tag, so the old "keeps its own tag" half no longer holds; its
    # tests (test/spec/tenq-fiscal-labels) state the new label
    filings = kit.run_of(deis)
    quarters = [kit.filing(f"q{i}", date(2016, 4 + i, 30), d, form="10-Q")
                for i, d in enumerate(tags)]
    before = assign_fiscal_labels(filings)
    after = assign_fiscal_labels([*filings, *quarters])
    assert {k: after[k] for k in before} == before
    assert set(after) == set(before) | {f"q{i}" for i in range(len(tags))}
