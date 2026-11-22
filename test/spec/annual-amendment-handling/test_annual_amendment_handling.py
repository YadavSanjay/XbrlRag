"""Spec tests for annual-amendment-handling: a Part III-only amendment must not replace
the filing it amends (docs/specs/annual-amendment-handling.md).

All pure: candidate filings are simple objects (EDGAR is a network boundary); no DB.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.downloader.sec_filing_downloader import choose_filings
from src.ingest.sec_edgar.ingest.layer0_populator import (
    ManifestRecord,
    has_primary_statements,
    resolve_supersession,
)
from src.ingest.sec_edgar.xbrl.promotion import PublishGate

TSLA_CIK = "0001318605"


def _filing(form: str, filed: date | str, acc: str) -> SimpleNamespace:
    return SimpleNamespace(form=form, filing_date=filed, accession_no=acc)


def _record(acc: str, form: str, filed: date, fy: int = 2025) -> ManifestRecord:
    return ManifestRecord(
        ticker="TSLA",
        cik=TSLA_CIK,
        accession_no=acc,
        form_type=form,
        filing_type=form.removesuffix("/A"),
        is_amendment=form.endswith("/A"),
        fiscal_year=fy,
        quarter=None,
        filed_date=filed,
        period_start=date(fy, 1, 1),
        period_end=date(fy, 12, 31),
        fye_month=12,
        fye_day=31,
    )


def _row(**extra) -> dict:
    row = {
        "accession_no": "0001318605-26-000050",
        "cik": TSLA_CIK,
        "fiscal_year": 2025,
        "fiscal_period": "FY",
        "form_type": "10-K/A",
        "anchor_coverage_pct": 1.0,
        "completeness_pct": None,
        "sum_of_parts_failures": 0,
    }
    row.update(extra)
    return row


# Synthetic accession numbers; dates in TSLA's pattern (10-K late January, Part III
# 10-K/A late April).
TSLA_10K = _filing("10-K", date(2026, 1, 29), "0001628280-26-003952")
TSLA_10KA = _filing("10-K/A", date(2026, 4, 30), "0001104659-26-051234")

forms = st.sampled_from(["10-K", "10-K/A", "20-F", "20-F/A"])
days = st.integers(min_value=0, max_value=400).map(lambda d: date(2025, 1, 1) + timedelta(days=d))
filings = st.lists(
    st.builds(_filing, forms, days, st.from_regex(r"000\d{7}-2[56]-\d{6}", fullmatch=True)),
    max_size=8,
    unique_by=lambda f: f.accession_no,
)


def _latest(items, date_of):
    return max(items, key=lambda f: (date_of(f), f.accession_no))


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_original_and_later_amendment_are_both_chosen():
    # oracle: requirement — the 10-K plus the Part III 10-K/A filed after it, by date
    assert choose_filings([TSLA_10KA, TSLA_10K]) == [TSLA_10K, TSLA_10KA]
    assert choose_filings([TSLA_10K]) == [TSLA_10K]
    assert choose_filings([TSLA_10KA]) == [TSLA_10KA]
    assert choose_filings([]) == []


@pytest.mark.criterion("C1")
def test_latest_of_each_kind_and_string_dates():
    # oracle: requirement — latest original, latest amendment after it; ISO strings allowed;
    # an amendment filed before the original is not chosen; date ties go to the larger
    # accession
    old_10k = _filing("10-K", "2026-01-10", "0000000001-26-000001")
    new_10k = _filing("10-K", "2026-01-29", "0000000001-26-000002")
    early_a = _filing("10-K/A", "2026-01-20", "0000000001-26-000003")
    a1 = _filing("10-K/A", "2026-03-01", "0000000001-26-000004")
    a2 = _filing("10-K/A", "2026-04-30", "0000000001-26-000005")
    assert choose_filings([a1, old_10k, a2, new_10k, early_a]) == [new_10k, a2]
    assert choose_filings([old_10k, early_a]) == [old_10k, early_a]
    tie_lo = _filing("20-F", "2026-04-01", "0000000002-26-000001")
    tie_hi = _filing("20-F", "2026-04-01", "0000000002-26-000009")
    assert choose_filings([tie_hi, tie_lo]) == [tie_hi]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(filings, st.randoms(use_true_random=False))
def test_choice_matches_rule_for_any_candidates(cands, rnd):
    # oracle: the C1 rule written out, and permutation invariance (candidates are a set)
    originals = [f for f in cands if not f.form.endswith("/A")]
    amendments = [f for f in cands if f.form.endswith("/A")]
    if originals:
        orig = _latest(originals, lambda f: f.filing_date)
        later = [a for a in amendments if a.filing_date > orig.filing_date]
        expected = [orig] + ([_latest(later, lambda f: f.filing_date)] if later else [])
    else:
        expected = [_latest(amendments, lambda f: f.filing_date)] if amendments else []
    shuffled = list(cands)
    rnd.shuffle(shuffled)
    assert choose_filings(cands) == expected
    assert choose_filings(shuffled) == expected


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_primary_statements_definition():
    # oracle: the definition — TotalAssets plus Revenue or NetIncome
    assert has_primary_statements(["TotalAssets", "Revenue", None]) is True
    assert has_primary_statements(["NetIncome", "TotalAssets"]) is True
    assert has_primary_statements([None, None]) is False  # cover page only
    assert has_primary_statements([]) is False
    assert has_primary_statements(["TotalAssets", "CurrentAssets", "Debt"]) is False
    assert has_primary_statements(["Revenue", "NetIncome", "OperatingIncome"]) is False


family = st.one_of(
    st.none(),
    st.sampled_from(["TotalAssets", "Revenue", "NetIncome", "Debt", "CurrentAssets", "Goodwill"]),
)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(st.lists(family, max_size=12), st.lists(family, max_size=6))
def test_primary_statements_rule_and_monotonicity(fams, more):
    # oracle: the C2 rule written out; adding facts never removes statements, and order
    # and duplicates do not matter
    present = set(fams)
    expected = "TotalAssets" in present and bool({"Revenue", "NetIncome"} & present)
    assert has_primary_statements(fams) is expected
    assert has_primary_statements(list(reversed(fams)) + fams) is expected
    if expected:
        assert has_primary_statements(fams + more) is True


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_part_iii_amendment_does_not_replace_the_10k():
    # oracle: requirement — TSLA FY2025: the 10-K stays canonical
    k = _record("0001628280-26-003952", "10-K", date(2026, 1, 29))
    ka = _record("0001104659-26-051234", "10-K/A", date(2026, 4, 30))
    got = resolve_supersession([k, ka], {ka.accession_no: False})
    assert got == {k.accession_no: None, ka.accession_no: k.accession_no}
    # A missing entry counts as "no statements".
    assert resolve_supersession([ka, k], {}) == got


@pytest.mark.criterion("C3")
def test_full_restatement_replaces_and_lone_amendment_stays():
    # oracle: requirement — a restatement carrying the statements is canonical; with no
    # eligible filing the latest one stays canonical, as today
    k = _record("0000000001-26-000001", "10-K", date(2026, 2, 1))
    ka = _record("0000000001-26-000002", "10-K/A", date(2026, 6, 1))
    assert resolve_supersession([k, ka], {ka.accession_no: True}) == {
        k.accession_no: ka.accession_no,
        ka.accession_no: None,
    }
    assert resolve_supersession([ka], {ka.accession_no: False}) == {ka.accession_no: None}
    ka2 = _record("0000000001-26-000003", "10-K/A", date(2026, 7, 1))
    assert resolve_supersession([ka, ka2], {}) == {
        ka.accession_no: ka2.accession_no,
        ka2.accession_no: None,
    }


records = st.lists(
    st.builds(
        _record,
        st.from_regex(r"000\d{7}-26-\d{6}", fullmatch=True),
        st.sampled_from(["10-K", "10-K/A"]),
        days,
    ),
    min_size=1,
    max_size=7,
    unique_by=lambda r: r.accession_no,
)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(records, st.data(), st.randoms(use_true_random=False))
def test_supersession_rule_for_any_group(group, data, rnd):
    # oracle: the C3 rule written out; permutation invariance; exactly one canonical
    full = {
        r.accession_no: data.draw(st.booleans()) for r in group if r.is_amendment
    }
    eligible = [r for r in group if not r.is_amendment or full.get(r.accession_no, False)]
    canon = _latest(eligible or group, lambda r: r.filed_date)
    expected = {
        r.accession_no: (None if r is canon else canon.accession_no) for r in group
    }
    shuffled = list(group)
    rnd.shuffle(shuffled)
    assert resolve_supersession(group, full) == expected
    assert resolve_supersession(shuffled, full) == expected
    assert sum(v is None for v in expected.values()) == 1


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(records, days, st.from_regex(r"999\d{7}-26-\d{6}", fullmatch=True))
def test_partial_amendment_never_moves_the_canonical(group, filed, acc):
    # oracle: metamorphic — adding a Part III amendment to a period that has an original
    # leaves the canonical filing unchanged
    group = group + [_record(group[0].accession_no.replace("-26-", "-25-"), "10-K", filed)]
    before = resolve_supersession(group, {})
    canon = next(a for a, v in before.items() if v is None)
    partial = _record(acc, "10-K/A", filed + timedelta(days=90))
    after = resolve_supersession(group + [partial], {acc: False})
    assert after[canon] is None
    assert after[acc] == canon


# --------------------------------------------------------------------------- C4
# (is_canonical, has_primary_statements) combinations that must not change a decision.
UNCHANGED_KEYS = [
    (False, False), (False, True), (True, True), (None, False), (True, None), (None, None),
]


@pytest.mark.criterion("C4")
def test_canonical_filing_without_statements_is_held_back():
    # oracle: requirement — anchor coverage 2/2 = 1.0 must not publish an empty canonical
    # annual filing; the same filing as a non-canonical amendment is not held back, since
    # the read-time gate is per period and would hide the original
    gate = PublishGate()
    held = gate._decide(_row(is_canonical=True, has_primary_statements=False))
    assert held.is_published is False and held.gate_a_passed is False
    assert any(r.startswith("GATE-A") and "primary statements" in r for r in held.held_back_reasons)
    amendment = gate._decide(_row(is_canonical=False, has_primary_statements=False))
    assert amendment.is_published is True
    assert amendment.to_dict() == gate._decide(_row()).to_dict()


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(
    st.one_of(st.none(), st.floats(min_value=0.0, max_value=1.0)),
    st.one_of(st.none(), st.floats(min_value=0.0, max_value=1.0)),
    st.one_of(st.none(), st.integers(min_value=0, max_value=20)),
    st.booleans(),
    st.sampled_from(UNCHANGED_KEYS),
)
def test_only_canonical_without_statements_changes_the_decision(anchor, compl, sop, enforce, keys):
    # oracle: requirement — the new rule holds back exactly one combination, whatever the
    # other signals; every other combination decides as a row without the keys
    gate = PublishGate(gate_b_enforce=enforce)
    base = _row(anchor_coverage_pct=anchor, completeness_pct=compl, sum_of_parts_failures=sop)
    held = gate._decide({**base, "is_canonical": True, "has_primary_statements": False})
    assert held.is_published is False and held.gate_a_passed is False
    canonical, statements = keys
    other = gate._decide({**base, "is_canonical": canonical, "has_primary_statements": statements})
    assert other.to_dict() == gate._decide(base).to_dict()
