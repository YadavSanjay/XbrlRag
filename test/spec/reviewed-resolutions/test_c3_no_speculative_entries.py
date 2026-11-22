"""C3: every resolution must cover a blocked key of the build, and its evidence must exist.

Oracle: requirement 3 of docs/specs/reviewed-resolutions.md; the constructed witnesses and FSDS
pre rows of conftest.py (spec Notes table), counted by hand. Witnesses are resolved with
key-statement-witness's ``resolve``, as the build does.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import check_resolution_use, resolve

# Hand count of reviewed.yaml: ORCL is original; AAA combined_line, BBB rule_gap,
# CCC note_unlisted, EEE definition; no by_design entry.
EXPECTED = {"n": 5, "original": 1, "reviewed": 4,
            "by_bucket": {"rule_gap": 1, "combined_line": 1, "definition": 1,
                          "note_unlisted": 1, "by_design": 0}}


def _resolved(witnesses: list[dict[str, Any]], entries: list[dict[str, Any]]) -> list[Any]:
    return [resolve(w, entries) for w in witnesses]  # type: ignore[arg-type]  # constructed


def _use(kit, entries=None, witnesses=None, tables=None) -> dict[str, Any]:
    entries = kit.loaded_entries() if entries is None else entries
    witnesses = kit.blocked() + kit.accepted() if witnesses is None else witnesses
    return check_resolution_use(entries, _resolved(witnesses, entries),
                                kit.tables() if tables is None else tables)


@pytest.mark.criterion("C3")
def test_hand_counts(kit):
    # oracle: the Notes table — five entries, one original, one per bucket but by_design
    assert _use(kit) == EXPECTED


@pytest.mark.criterion("C3")
def test_an_empty_list_counts_nothing(kit):
    # oracle: requirement 3 — all five buckets, zero included
    got = check_resolution_use([], _resolved(kit.accepted(), []), kit.tables())
    assert got == {"n": 0, "original": 0, "reviewed": 0,
                   "by_bucket": dict.fromkeys(kit.buckets, 0)}


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("change", ["removed", "accepted", "another_value", "another_year"])
def test_an_entry_covering_no_witness_is_refused(kit, change):
    # oracle: requirement 3.1 — EEE's entry must cover its blocked witness exactly
    assert _use(kit) == EXPECTED
    witnesses = kit.blocked() + kit.accepted()
    eee = witnesses[4]
    if change == "removed":
        witnesses.remove(eee)
    elif change == "accepted":
        eee["status"] = "agree"
    elif change == "another_value":
        eee["key_value"] = 2001.0
    else:
        eee["fiscal_year"] = 2024
    with pytest.raises(ValueError):
        _use(kit, witnesses=witnesses)


@pytest.mark.criterion("C3")
def test_an_uncovered_original_entry_is_refused(kit):
    # oracle: requirement 3.1 — original entries included (ORCL after key-definitions)
    assert _use(kit) == EXPECTED
    with pytest.raises(ValueError):
        _use(kit, witnesses=kit.blocked()[1:] + kit.accepted())
    without_orcl = _use(kit, entries=kit.loaded_entries()[1:],
                        witnesses=kit.blocked()[1:] + kit.accepted())
    assert (without_orcl["n"], without_orcl["original"], without_orcl["reviewed"]) == (4, 0, 4)


@pytest.mark.criterion("C3")
def test_evidence_naming_another_filing_is_refused(kit):
    # oracle: requirement 3.2 — AAA's evidence pointed at BBB's (existing) CF line 22
    entries = kit.loaded_entries()
    assert _use(kit, entries=entries) == EXPECTED
    entries[1]["review"]["evidence"].update(
        adsh=kit.bbb, report=6, line=22,
        plabel="Repayments of senior notes and term loan credit agreements")
    with pytest.raises(ValueError):
        _use(kit, entries=entries)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(
    ("report", "line", "plabel"),
    [
        (2, 99, "Accounts payable and accrued expenses"),   # no such line
        (3, 14, "Accounts payable and accrued expenses"),   # no such report
        (2, 14, "Accounts payable"),                         # another label
        (2, 14, "accounts payable and accrued expenses"),    # labels compare exactly
        (1, 3, "Entity Registrant Name"),                    # a cover-page (CP) line
    ],
)
def test_a_statement_line_the_filing_does_not_hold_is_refused(kit, report, line, plabel):
    # oracle: requirement 3.3 against the AAA pre rows of conftest.py
    entries = kit.loaded_entries()
    assert _use(kit, entries=entries) == EXPECTED
    entries[1]["review"]["evidence"].update(report=report, line=line, plabel=plabel)
    with pytest.raises(ValueError):
        _use(kit, entries=entries)


@pytest.mark.criterion("C3")
def test_note_evidence_is_not_checked_against_fsds(kit):
    # oracle: requirement 3.3 — note lines are not in the data sets; CCC has no pre rows
    entries = kit.loaded_entries()
    entries[3]["review"]["evidence"]["label"] = "Other income, net: interest"
    assert _use(kit, entries=entries) == EXPECTED


@pytest.mark.criterion("C3")
@given(data=st.data())
def test_order_does_not_change_the_result(kit, data):
    # oracle: requirement 3 — independent of the order of witnesses and resolutions
    witnesses = data.draw(st.permutations(kit.blocked() + kit.accepted()))
    entries = data.draw(st.permutations(kit.loaded_entries()))
    assert _use(kit, entries=list(entries), witnesses=list(witnesses)) == EXPECTED


@pytest.mark.criterion("C3")
@given(extra=st.lists(st.sampled_from(["Revenue", "NetIncome", "TotalAssets", "Capex"]),
                      max_size=6),
       values=st.lists(st.integers(min_value=1, max_value=10**9), min_size=6, max_size=6))
def test_added_accepted_witnesses_change_nothing(kit, extra, values):
    # oracle: requirement 3 — an accepted witness is never covered, so it adds no use
    added = [kit.witness(f, "0000900205", 2025, "us-gaap:LongTermDebt", v, kit.eee, "agree")
             for f, v in zip(extra, values, strict=False)]
    added += [kit.witness("Debt", "0000900205", 2025, "us-gaap:LongTermDebt", 2000, kit.eee,
                          "agree")]
    assert _use(kit, witnesses=kit.blocked() + kit.accepted() + added) == EXPECTED


@pytest.mark.criterion("C3")
@given(i=st.integers(min_value=0, max_value=4))
def test_removing_a_covering_witness_refuses(kit, i):
    # oracle: requirement 3.1 — each entry is covered by exactly its own blocked witness
    assert _use(kit) == EXPECTED
    blocked = kit.blocked()
    del blocked[i]
    with pytest.raises(ValueError):
        _use(kit, witnesses=blocked + kit.accepted())
