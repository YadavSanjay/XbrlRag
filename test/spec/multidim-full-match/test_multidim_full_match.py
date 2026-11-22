"""Spec tests for multidim-full-match: a multi-dimension lookup whose hints identify the
fact's dimensions one-to-one earns the dimension credit
(docs/specs/multidim-full-match.md).

Pure: `dimensions_fully_matched`, and `_build_result` driven with candidate rows as the
multi-dimension query returns them (no DB).
"""

from __future__ import annotations

import itertools
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.xbrl_factstore_lookup import (
    XbrlFactstoreLookup,
    dimensions_fully_matched,
)

# Apple FY2024 CashAndCashEquivalentsAtCarryingValue, fact 13665 (2026-10-14 corpus).
AAPL_LABELS = {
    "http://fasb.org/us-gaap/2024:FinancialInstrumentAxis": "Corporate Debt Securities [Member]",
    "http://fasb.org/us-gaap/2024:FairValueByFairValueHierarchyLevelAxis":
        "Fair Value, Inputs, Level 2 [Member]",
}
# Microsoft FY2026, fact 104489: three dimensions.
MSFT_LABELS = {
    "http://fasb.org/us-gaap/2025:FinancialInstrumentAxis":
        "US Government Agencies Debt Securities [Member]",
    "http://fasb.org/us-gaap/2025:FairValueByAssetClassAxis": "Debt Securities [Member]",
    "http://fasb.org/us-gaap/2025:FairValueByFairValueHierarchyLevelAxis":
        "Fair Value, Inputs, Level 2 [Member]",
}


def _norm(text: str) -> str:
    s = text.lower().replace("[member]", " ")
    s = re.sub(r"[\[\]]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _oracle(labels, hints) -> bool:
    """The C1 rule written out: >= 2 hints, equal counts, a one-to-one containment."""
    labels = list((labels or {}).values())
    if len(hints) < 2 or len(labels) != len(hints):
        return False
    return any(
        all(_norm(h) in _norm(lbl) for h, lbl in zip(hints, perm, strict=True))
        for perm in itertools.permutations(labels)
    )


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_point_cases():
    # oracle: the definition, by hand on the 2026-10-14 facts
    assert dimensions_fully_matched(AAPL_LABELS, ["corporate debt securities", "level 2"]) is True
    assert dimensions_fully_matched(AAPL_LABELS, ["Level 2", "Corporate Debt Securities"]) is True
    # Three dimensions, two hints: under-specified.
    assert dimensions_fully_matched(MSFT_LABELS, ["us government agencies", "level 2"]) is False
    assert dimensions_fully_matched(
        MSFT_LABELS, ["us government agencies", "debt securities", "level 2"]) is True
    # Both hints only inside the same label: no one-to-one assignment.
    assert dimensions_fully_matched(AAPL_LABELS, ["corporate debt", "debt securities"]) is False
    # A single hint is never credited here; nothing to match is never credited.
    one = {"ax": "Corporate Debt Securities [Member]"}
    assert dimensions_fully_matched(one, ["corporate debt securities"]) is False
    assert dimensions_fully_matched(None, ["a", "b"]) is False
    assert dimensions_fully_matched({}, []) is False


words = st.sampled_from(["corporate", "debt", "securities", "level", "1", "2", "fair",
                         "value", "us", "treasury", "agencies", "cash"])
label_st = st.lists(words, min_size=1, max_size=4).map(lambda w: " ".join(w) + " [Member]")
labels_st = st.lists(label_st, min_size=1, max_size=3).map(
    lambda ls: {f"ax{i}": lbl for i, lbl in enumerate(ls)})
hints_st = st.lists(st.lists(words, min_size=1, max_size=2).map(" ".join),
                    min_size=1, max_size=3)


@pytest.mark.criterion("C1")
@given(labels_st, hints_st)
def test_rule_for_any_labels_and_hints(labels, hints):
    # oracle: the C1 rule written out by brute force over assignments
    assert dimensions_fully_matched(labels, hints) is _oracle(labels, hints)


@pytest.mark.criterion("C1")
@given(labels_st)
def test_full_labels_as_hints_always_match(labels):
    # oracle: the definition — every label contains itself; >= 2 dims needed
    hints = [lbl.replace(" [Member]", "") for lbl in labels.values()]
    assert dimensions_fully_matched(labels, hints) is (len(labels) >= 2)


# --------------------------------------------------------------------------- C2
def _row(fact_id, value, labels, concept, pref_rank=1, period_end="2024-09-29"):
    return {
        "fact_id": fact_id, "value": value, "unit": "USD", "period_type": "instant",
        "period_start": None, "period_end": period_end, "concept": concept,
        "concept_family": None, "concept_pref_rank": pref_rank, "segment_axis": None,
        "segment_member": None, "segment_label": None, "html_anchor": None,
        "page_num": None, "section_path": None, "accession_no": None,
        "linked_chunk_ids": [], "dimension_labels": labels,
    }


CASH = "http://fasb.org/us-gaap/2024:CashAndCashEquivalentsAtCarryingValue"


def _result(dims, rows):
    lk = XbrlFactstoreLookup()
    notes: list[str] = []
    specs, status = lk._parse_dimensions(dims, notes)
    assert status is None
    step = {"id": "s1", "op": "lookup", "dimensions": dims, "section_hint": None}
    return lk._build_result("s1", step, "0000320193", 2024, "CashAndEquivalents",
                            "instant", specs, rows, notes)


@pytest.mark.criterion("C2")
def test_one_to_one_match_resolves_and_partial_does_not():
    # oracle: _score_confidence by hand — 0.4 (one value) + 0.2 (preferred concept)
    # + 0.2 (dimensions matched) = 0.8 >= 0.70 -> OK; without the dimension credit 0.6
    dims = {"instrument": "corporate debt securities", "fair_value_level": "level 2"}
    ok = _result(dims, [_row(13665, 26_000_000.0, AAPL_LABELS, CASH)])
    assert ok.status.value == "OK"
    assert ok.value == pytest.approx(26_000_000.0, rel=1e-12)  # the stored fact, unrounded
    assert ok.confidence == pytest.approx(0.8, abs=1e-9)  # sum of fixed credits
    three = _result({"instrument": "us government agencies", "level": "level 2"},
                    [_row(104489, 1_787_000_000.0, MSFT_LABELS, CASH)])
    assert three.status.value == "AMBIGUOUS"
    assert three.confidence == pytest.approx(0.6, abs=1e-9)  # no dimension credit
    same = _result({"instrument": "corporate debt", "security": "debt securities"},
                   [_row(13665, 26_000_000.0, AAPL_LABELS, CASH)])
    assert same.status.value == "AMBIGUOUS"


@pytest.mark.criterion("C2")
@given(st.sampled_from(["corporate debt securities", "Corporate Debt Securities",
                        "corporate debt"]),
       st.sampled_from(["level 2", "Level 2", "fair value, inputs, level 2", "inputs, level 2"]),
       st.booleans())
def test_hint_wording_does_not_matter_but_extra_dimensions_do(instrument, level, extra):
    # oracle: the rule — any contained wording of the two qualifiers credits a
    # two-dimension fact; a third, unmentioned dimension removes the credit
    labels = dict(AAPL_LABELS)
    if extra:
        labels["http://fasb.org/us-gaap/2024:FairValueByAssetClassAxis"] = (
            "Debt Securities [Member]")
    r = _result({"instrument": instrument, "fair_value_level": level},
                [_row(13665, 26_000_000.0, labels, CASH)])
    assert r.status.value == ("AMBIGUOUS" if extra else "OK")


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(labels_st, hints_st, st.randoms(use_true_random=False))
def test_order_and_extra_dimension(labels, hints, rnd):
    # oracle: metamorphic — order is irrelevant; an unmentioned dimension removes a match;
    # replacing a hint by its full label keeps a match
    base = dimensions_fully_matched(labels, hints)
    shuffled_hints = list(hints)
    rnd.shuffle(shuffled_hints)
    items = list(labels.items())
    rnd.shuffle(items)
    assert dimensions_fully_matched(dict(items), shuffled_hints) is base
    if base:
        grown = {**labels, "extra_axis": "Something Else Entirely [Member]"}
        assert dimensions_fully_matched(grown, hints) is False
        full = [lbl.replace(" [Member]", "") for lbl in labels.values()]
        assert dimensions_fully_matched(labels, full) is True


@pytest.mark.criterion("C3")
def test_metamorphic_point_case():
    # oracle: the definition on Apple 13665 — reordering keeps the match, a third
    # dimension removes it, full labels keep it
    hints = ["corporate debt securities", "level 2"]
    assert dimensions_fully_matched(AAPL_LABELS, list(reversed(hints))) is True
    grown = {**AAPL_LABELS, "x:FairValueByAssetClassAxis": "Debt Securities [Member]"}
    assert dimensions_fully_matched(grown, hints) is False
    assert dimensions_fully_matched(
        AAPL_LABELS, ["Corporate Debt Securities", "Fair Value, Inputs, Level 2"]) is True
