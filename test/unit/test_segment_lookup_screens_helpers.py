"""Unit tests for the segment-lookup-screens helpers (docs/specs/segment-lookup-screens.md).

The locked spec tests drive `lookup` and `screen` end to end; these pin the pure helpers'
edge cases directly: a nil next to a value, a None unit, a partition with a missing
member's own fact, and `to_dict` without `sum_of`.
"""

from __future__ import annotations

from src.retriever.xbrl.xbrl_factstore_lookup import (
    FactLookupResult,
    LookupStatus,
    _partition_sum,
    compact_label,
)
from src.retriever.xbrl.xbrl_factstore_screen import choose_member_row


def _row(fact_id, label, value, unit="USD"):
    return {"fact_id": fact_id, "segment_label": label, "value": value, "unit": unit}


def _exact(key):
    return lambda r: compact_label(r["segment_label"]) == key


def test_compact_label_keeps_the_trailing_word():
    assert compact_label("Europe Segment [Member]") == "europesegment"
    assert compact_label("Research & Development") == "researchanddevelopment"
    assert compact_label(None) == ""


def test_exact_match_wins_over_containment():
    rows = [_row(2, "Cloud [Member]", 50.0), _row(3, "Google Cloud [Member]", 90.0)]
    outcome, detail = choose_member_row(rows, _exact("cloud"))
    assert outcome == "ranked"
    assert detail["row"]["fact_id"] == 2
    assert detail["member_match"] == "exact"


def test_several_containment_members_are_ambiguous():
    rows = [_row(1, "Sales Cloud", 1.0), _row(2, "Service Cloud", 2.0)]
    assert choose_member_row(rows, _exact("cloud")) == (
        "ambiguous_member", {"labels": ["Sales Cloud", "Service Cloud"]}
    )


def test_a_nil_next_to_a_value_is_not_a_second_value():
    rows = [_row(1, "Cloud", None), _row(2, "Cloud", 7.0)]
    outcome, detail = choose_member_row(rows, _exact("cloud"))
    assert outcome == "ranked"
    assert detail["row"]["value"] == 7.0


def test_a_foreign_unit_is_a_currency_reason_and_none_is_ignored():
    rows = [_row(1, "Cloud", 7.0, unit=None), _row(2, "Cloud", 7.0, unit="EUR")]
    assert choose_member_row(rows, _exact("cloud")) == ("currency", {"currency": "EUR"})


_P = "2025-01-01"
_C = "us-gaap:Revenues"
_GEO = "http://fasb.org/srt/2024:StatementGeographicalAxis"
_PROD = "http://fasb.org/srt/2024:ProductOrServiceAxis"


def _cell(fact_id, geo, value):
    row = {"fact_id": fact_id, "value": value, "concept": _C, "unit": "USD",
           "period_end": _P,
           "dimension_labels": {_PROD: "Widget", _GEO: geo},
           "dimensions": {_PROD: "x:WidgetMember", _GEO: f"x:{geo}Member"}}
    return (row, _PROD, _GEO)


def _flat(fact_id, member, value):
    return {"fact_id": fact_id, "value": value, "concept": _C, "unit": "USD",
            "period_end": _P, "segment_member": member and f"srt:{member}Member",
            "segment_label": member, "has_multiple_dimensions": False}


def test_partition_sum_needs_every_member_fact_and_the_total():
    cells = [_cell(50, "East", 3.0), _cell(51, "West", 4.0)]
    totals = [_flat(1, None, 30.0)]
    on_axis = [_flat(10, "East", 12.0), _flat(11, "West", 18.0)]
    summed, total = _partition_sum(cells, totals, on_axis, _C, "USD", _P)
    assert total == 30.0
    assert [c[0]["fact_id"] for c in summed] == [50, 51]
    # West has no single-dimension fact: no proof.
    assert _partition_sum(cells, totals, on_axis[:1], _C, "USD", _P) is None
    # The members do not add up to the total: no proof.
    assert _partition_sum(cells, [_flat(1, None, 31.0)], on_axis, _C, "USD", _P) is None


def test_to_dict_has_sum_of_only_when_set():
    plain = FactLookupResult("l1", LookupStatus.OK, value=1.0)
    assert "sum_of" not in plain.to_dict()
    summed = FactLookupResult("l1", LookupStatus.OK, value=1.0, sum_of=[3, 4])
    assert summed.to_dict()["sum_of"] == [3, 4]
