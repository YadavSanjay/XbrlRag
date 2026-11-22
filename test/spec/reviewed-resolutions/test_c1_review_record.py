"""C1: a resolution outside the original list carries a complete review record.

Oracle: requirement 1 of docs/specs/reviewed-resolutions.md, applied by hand to constructed
records (conftest.py) and to the constructed list fixtures/reviewed.yaml.
"""

from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import load_resolutions, validate_review


def _load(path: Path) -> list[dict[str, Any]]:
    """load_resolutions, a refusal reported as a test failure (requirement 1 admits it)."""
    try:
        return load_resolutions(path)
    except ValueError as exc:
        pytest.fail(f"load_resolutions refused a complete review record: {exc}")


@pytest.mark.criterion("C1")
def test_statement_record_is_normalized(kit):
    # oracle: requirement 1 — the same record with the YAML date as the ISO string
    review = kit.statement_review()
    before = copy.deepcopy(review)
    got = validate_review(review)
    assert got == {"by": "Sanjay", "date": "2026-11-10", "bucket": "combined_line",
                   "evidence": {"kind": "statement", "adsh": kit.aaa, "report": 2, "line": 14,
                                "plabel": "Accounts payable and accrued expenses"}}
    assert review == before  # never mutated
    assert got is not review and got["evidence"] is not review["evidence"]


@pytest.mark.criterion("C1")
def test_note_record_with_an_iso_string_date(kit):
    # oracle: requirement 1 — an ISO string date is kept; note evidence has kind, adsh, label
    got = validate_review(kit.note_review())
    assert got == {"by": "Sanjay", "date": "2026-11-10", "bucket": "note_unlisted",
                   "evidence": {"kind": "note", "adsh": kit.ccc, "label": "Interest income"}}


def _set(path: tuple[str, ...], value: Any):
    def f(r: dict[str, Any]) -> dict[str, Any]:
        node = r
        for p in path[:-1]:
            node = node[p]
        node[path[-1]] = value
        return r
    return f


def _del(path: tuple[str, ...]):
    def f(r: dict[str, Any]) -> dict[str, Any]:
        node = r
        for p in path[:-1]:
            node = node[p]
        del node[path[-1]]
        return r
    return f


# Each defect of requirement 1, applied to the complete statement record.
DEFECTS = {
    "missing by": _del(("by",)),
    "unknown field": _set(("approved",), True),
    "empty by": _set(("by",), "   "),
    "by not a string": _set(("by",), 7),
    "not a real date": _set(("date",), "2026-02-30"),
    "not iso": _set(("date",), "10/11/2026"),
    "date-time": _set(("date",), dt.datetime(2026, 11, 10, 9, 30)),
    "unknown bucket": _set(("bucket",), "other"),
    "unknown kind": _set(("evidence", "kind"), "table"),
    "bad accession": _set(("evidence", "adsh"), "0000900201-26-1"),
    "report zero": _set(("evidence", "report"), 0),
    "report bool": _set(("evidence", "report"), True),
    "report string": _set(("evidence", "report"), "3"),
    "line negative": _set(("evidence", "line"), -1),
    "empty plabel": _set(("evidence", "plabel"), ""),
    "evidence unknown field": _set(("evidence", "label"), "Accounts payable"),
    "missing evidence": _del(("evidence",)),
    "note_unlisted with statement evidence": _set(("bucket",), "note_unlisted"),
}


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("defect", sorted(DEFECTS))
def test_each_defect_is_refused(kit, defect):
    # oracle: requirement 1 — the listed ValueError cases, one at a time
    review = DEFECTS[defect](kit.statement_review())
    with pytest.raises(ValueError):
        validate_review(review)


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("bucket", ["rule_gap", "combined_line"])
def test_statement_buckets_refuse_note_evidence(kit, bucket):
    # oracle: requirement 1, bucket table — rule_gap and combined_line need statement evidence
    review = dict(kit.note_review(), bucket=bucket)
    with pytest.raises(ValueError):
        validate_review(review)


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("bucket", ["definition", "by_design"])
def test_either_kind_buckets_take_both_forms(kit, bucket):
    # oracle: requirement 1, bucket table — definition and by_design take either evidence kind
    for review in (kit.statement_review(), kit.note_review()):
        got = validate_review(dict(review, bucket=bucket))
        assert (got["bucket"], got["date"]) == (bucket, "2026-11-10")


FIELDS = [("by",), ("date",), ("bucket",), ("evidence",), ("evidence", "kind"),
          ("evidence", "adsh"), ("evidence", "report"), ("evidence", "line"),
          ("evidence", "plabel")]


@pytest.mark.criterion("C1")
@given(path=st.sampled_from(FIELDS), note=st.booleans())
def test_removing_any_field_is_refused(kit, path, note):
    # oracle: requirement 1 — every field of the record and of its evidence is required
    review = kit.note_review() if note else kit.statement_review()
    if note and path[-1] in ("report", "line", "plabel"):
        path = ("evidence", "label")
    assert validate_review(copy.deepcopy(review))["date"] == "2026-11-10"
    with pytest.raises(ValueError):
        validate_review(_del(path)(review))


@pytest.mark.criterion("C1")
@given(bucket=st.text(min_size=0, max_size=12))
def test_only_the_five_buckets(kit, bucket):
    # oracle: requirement 1 — bucket is one of the five names
    review = dict(kit.statement_review(), bucket=bucket)
    if bucket in ("combined_line", "rule_gap", "definition", "by_design"):
        assert validate_review(review)["bucket"] == bucket
    else:  # note_unlisted needs note evidence; anything else is not a bucket
        with pytest.raises(ValueError):
            validate_review(review)


@pytest.mark.criterion("C1")
def test_load_returns_reviews_normalized(kit):
    # oracle: requirement 1 — load_resolutions admits review and normalizes it; the expected
    # list is reviewed.yaml with each review date written as "2026-11-10" by hand
    assert validate_review(kit.raw_entries()[1]["review"])["date"] == "2026-11-10"
    got = _load(kit.reviewed)
    assert got == kit.loaded_entries()
    assert [type(e["review"]["date"]) for e in got if "review" in e] == [str] * 4


@pytest.mark.criterion("C1")
def test_load_refuses_an_incomplete_review(kit, tmp_path):
    # oracle: requirement 1 — a ValueError from validate_review propagates out of the loader
    incomplete = kit.statement_review()
    del incomplete["bucket"]
    with pytest.raises(ValueError):
        validate_review(incomplete)
    text = kit.reviewed.read_text(encoding="utf-8").replace(
        "      bucket: combined_line\n", "", 1)
    assert text != kit.reviewed.read_text(encoding="utf-8")
    p = tmp_path / "incomplete.yaml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_resolutions(p)
