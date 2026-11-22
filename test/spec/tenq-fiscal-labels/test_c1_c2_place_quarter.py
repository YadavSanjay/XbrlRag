"""C1, C2: a 10-Q's fiscal year and quarter from the filer's annual calendar (requirements 1-2).

Oracle: ORCL 0001193125-26-389274's own text ("first quarter of fiscal 2027"); companyfacts
``fy``/``fp`` for every other ORCL filing; synthetic calendars built by hand in conftest.py.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.fiscal_labels import label_filings, place_quarter

# ------------------------------------------------------------------ C1: ORCL, real data


@pytest.mark.criterion("C1")
def test_orcl_sixteen_quarters_are_placed(kit):
    # oracle: companyfacts fy/fp of the 15 earlier ORCL 10-Qs; for 0001193125-26-389274 the
    # filing's own "first quarter of fiscal 2027" (its DEI and SEC's fy say 2026)
    annual = kit.annual_periods("ORCL")
    got = {r["accession_no"]: place_quarter(r["period_end"], annual) for r in kit.ORCL_SIXTEEN}
    expected = {r["accession_no"]: (kit.OWN_FY[r["accession_no"]],
                                    kit.OWN_QUARTER[r["accession_no"]])
                for r in kit.ORCL_SIXTEEN}
    assert got[kit.ORCL_Q1_FY2027] == (2027, 1)
    assert got == expected


@pytest.mark.criterion("C1")
def test_label_filings_files_orcl_q1_fy2027_under_2027(kit):
    # oracle: the filing's own text names fiscal 2027; every other ORCL 10-K and 10-Q keeps
    # SEC's fy (companyfacts), the 15 earlier quarters among them
    labels = label_filings(kit.ORCL_ROWS)[0]
    assert labels[kit.ORCL_Q1_FY2027] == 2027
    assert labels == {r["accession_no"]: kit.OWN_FY[r["accession_no"]] for r in kit.FILINGS["ORCL"]}


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(data=st.data())
def test_orcl_label_needs_only_the_annual_filings(kit, data):
    # oracle: the filing's own text and companyfacts fy — metamorphic: dropping any other
    # 10-Qs and shuffling the input changes no label
    others = [r for r in kit.ORCL_ROWS
              if r["form_type"] == "10-Q" and r["accession_no"] != kit.ORCL_Q1_FY2027]
    annual = [r for r in kit.ORCL_ROWS if r["form_type"] == "10-K"]
    q1 = next(r for r in kit.ORCL_ROWS if r["accession_no"] == kit.ORCL_Q1_FY2027)
    kept = data.draw(st.lists(st.sampled_from(others), unique_by=lambda r: r["accession_no"]))
    rows = data.draw(st.permutations([*annual, *kept, q1]))
    labels = label_filings(rows)[0]
    assert labels == {r["accession_no"]: kit.OWN_FY[r["accession_no"]] for r in rows}


# ------------------------------------------------------------------ C2: the rule, synthetic


@pytest.mark.criterion("C2")
def test_a_december_filer_by_hand():
    # oracle: by hand — a calendar-year filer: quarters 1-3 of 2025, its year end is no quarter,
    # the quarter after the last 10-K is the next year's, and a date 18 months on is unplaceable
    annual = {date(2024, 12, 31): 2024, date(2025, 12, 31): 2025}
    assert place_quarter(date(2025, 3, 31), annual) == (2025, 1)
    assert place_quarter(date(2025, 6, 30), annual) == (2025, 2)
    assert place_quarter(date(2025, 9, 30), annual) == (2025, 3)
    assert place_quarter(date(2026, 3, 31), annual) == (2026, 1)
    assert place_quarter(date(2025, 12, 31), annual) is None
    assert place_quarter(date(2027, 6, 30), annual) is None


@pytest.mark.criterion("C2")
def test_start_year_labels_and_a_14_week_quarter_by_hand():
    # oracle: by hand — Home Depot names the year ending 2026-02-01 fiscal 2025, so its quarter
    # ending 2025-05-04 is (2025, 1); Apple's fiscal 2023 first quarter ran 14 weeks, from
    # 2022-09-25 to 2022-12-31 (98 days after the 2022-09-24 year end), and is quarter 1
    hd = {date(2025, 2, 2): 2024, date(2026, 2, 1): 2025}
    assert place_quarter(date(2025, 5, 4), hd) == (2025, 1)
    aapl = {date(2022, 9, 24): 2022, date(2023, 9, 30): 2023}
    assert place_quarter(date(2022, 12, 31), aapl) == (2023, 1)
    assert place_quarter(date(2023, 7, 1), aapl) == (2023, 3)


@pytest.mark.criterion("C2")
def test_label_filings_takes_the_calendar_over_dei_and_dei_when_unplaceable():
    # oracle: requirement 2, by hand — a December filer's 2025 Q2 tagged DEI 2024 is 2025; a
    # 10-Q two years past the last 10-K cannot be placed and keeps its DEI; the 10-Ks keep theirs
    rows = [
        {"accession_no": "k24", "form_type": "10-K", "period_end": date(2024, 12, 31),
         "filed_date": date(2025, 2, 20), "dei_fiscal_year": 2024},
        {"accession_no": "k25", "form_type": "10-K", "period_end": date(2025, 12, 31),
         "filed_date": date(2026, 2, 20), "dei_fiscal_year": 2025},
        {"accession_no": "q", "form_type": "10-Q", "period_end": date(2025, 6, 30),
         "filed_date": date(2025, 8, 1), "dei_fiscal_year": 2024},
        {"accession_no": "qa", "form_type": "10-Q/A", "period_end": date(2025, 9, 30),
         "filed_date": date(2025, 12, 1), "dei_fiscal_year": 2026},
        {"accession_no": "far", "form_type": "10-Q", "period_end": date(2028, 3, 31),
         "filed_date": date(2028, 5, 1), "dei_fiscal_year": 2031},
    ]
    assert label_filings(rows)[0] == {"k24": 2024, "k25": 2025, "q": 2025, "qa": 2025,
                                      "far": 2031}


calendars = st.builds(
    lambda kind, month, first, offset, longq: (kind, month, first, offset, longq),
    st.sampled_from(["month", "week"]), st.integers(1, 12), st.integers(1995, 2030),
    st.sampled_from([0, -1]), st.integers(1, 4),
)


@pytest.mark.criterion("C2")
@given(cal=calendars)
def test_every_built_quarter_is_placed_as_built(kit, cal):
    # oracle: the construction — each quarter end was built n quarters after its year's start
    # (13-week quarters, a 14-week one in a 53-week year, or 3-month steps), with the label of
    # the year it ends in
    kind, month, first, offset, longq = cal
    labels, quarters = kit.build_calendar(kind, month, first, 4, offset, longq)
    for q, label, n in quarters:
        assert place_quarter(q, labels) == (label, n), (q, label, n)
    for end in labels:
        assert place_quarter(end, labels) is None, end


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(cal=calendars, shift=st.integers(-20, 20))
def test_shifting_by_whole_years_shifts_the_label(kit, cal, shift):
    # oracle: metamorphic — the same calendar built `shift` years later places every quarter
    # with the same number and a label `shift` higher
    kind, month, first, offset, longq = cal
    labels, quarters = kit.build_calendar(kind, month, first, 3, offset, longq)
    later, later_quarters = kit.build_calendar(kind, month, first + shift, 3, offset, longq)
    for (q, _label, _n), (q2, _label2, _n2) in zip(quarters, later_quarters, strict=True):
        placed, placed_later = place_quarter(q, labels), place_quarter(q2, later)
        assert placed is not None and placed_later is not None
        assert placed_later == (placed[0] + shift, placed[1])


@pytest.mark.criterion("C2")
@given(cal=calendars, drop=st.integers(1, 2))
def test_past_the_last_annual_end_is_the_next_label(kit, cal, drop):
    # oracle: the construction — remove the last `drop` year ends; the quarters right after the
    # new last end still get its label + 1 and their own number
    kind, month, first, offset, longq = cal
    labels, quarters = kit.build_calendar(kind, month, first, 4, offset, longq)
    ends = sorted(labels)
    kept = {e: labels[e] for e in ends[:-drop]}
    last = max(kept)
    for q, _label, n in quarters:
        if last < q <= last + timedelta(days=300):
            assert place_quarter(q, kept) == (kept[last] + 1, n), q


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(cal=calendars, deis=st.lists(st.one_of(st.none(), st.integers(1990, 2040)),
                                    min_size=9, max_size=9))
def test_a_placeable_10q_label_never_depends_on_its_dei(kit, cal, deis):
    # oracle: requirement 2 — metamorphic: whatever each 10-Q's DEI says, label_filings gives
    # it the label of the year that contains it (the construction); 10-K labels are their DEI
    kind, month, first, offset, longq = cal
    labels, quarters = kit.build_calendar(kind, month, first, 3, offset, longq)
    annual = [{"accession_no": f"k{i}", "form_type": "10-K", "period_end": end,
               "filed_date": end + timedelta(days=50), "dei_fiscal_year": labels[end]}
              for i, end in enumerate(sorted(labels))]
    tenq = [{"accession_no": f"q{i}", "form_type": "10-Q", "period_end": q,
             "filed_date": q + timedelta(days=35), "dei_fiscal_year": dei}
            for i, ((q, _label, _n), dei) in enumerate(zip(quarters, deis, strict=True))]
    got = label_filings([*annual, *tenq])[0]
    assert {f"q{i}": got[f"q{i}"] for i in range(len(tenq))} == {
        f"q{i}": label for i, (_q, label, _n) in enumerate(quarters)}
    assert {r["accession_no"]: got[r["accession_no"]] for r in annual} == {
        r["accession_no"]: r["dei_fiscal_year"] for r in annual}
