"""C1: every get_concept row names its fact: cik, taxonomy and tag, with the SEC's fields intact."""

from __future__ import annotations

import json

import pytest
from ftv2_payloads import (
    AAA_2022_ORIGINAL,
    AAA_2022_RESTATED,
    AAA_2023,
    AAA_2023_SAME_DAY,
    AAA_CIK,
    AAA_SHARES,
    DDD_2023,
    DDD_CIK,
)
from hypothesis import given
from hypothesis import strategies as st

REVENUES = {"taxonomy": "us-gaap", "tag": "Revenues"}
ADDED = ("cik", "taxonomy", "tag")
# AAA's Revenues facts known by 2024-06-30 (the late 150 is filed after it), by hand.
AAA_KNOWN = [AAA_2022_ORIGINAL, AAA_2022_RESTATED, AAA_2023, AAA_2023_SAME_DAY]


def _canon(rows: list[dict]) -> list[str]:
    return sorted(json.dumps(r, sort_keys=True) for r in rows)


@pytest.mark.criterion("C1")
def test_each_row_carries_cik_taxonomy_and_tag(make_toolbox):
    # oracle: requirement 1 — the fixture's four known AAA Revenues facts, each with its unit and
    # the three added fields: the ten-digit CIK and the taxonomy and tag as given
    toolbox, _ = make_toolbox()
    outcome = toolbox.execute("get_concept", {"cik": "1", **REVENUES})
    assert outcome.error_code is None
    rows = outcome.payload["rows"]
    assert len(rows) == 4
    for row in rows:
        assert row.get("cik") == AAA_CIK
        assert row.get("taxonomy") == "us-gaap"
        assert row.get("tag") == "Revenues"
        assert row.get("unit") == "USD"
    expected = [{**f, "unit": "USD", "cik": AAA_CIK, "taxonomy": "us-gaap", "tag": "Revenues"}
                for f in AAA_KNOWN]
    assert _canon(rows) == _canon(expected)


@pytest.mark.criterion("C1")
def test_another_taxonomy_is_named_as_given(make_toolbox):
    # oracle: requirement 1 — the fixture's one dei shares fact
    toolbox, _ = make_toolbox()
    outcome = toolbox.execute(
        "get_concept", {"cik": 1, "taxonomy": "dei", "tag": "EntityCommonStockSharesOutstanding"})
    assert outcome.error_code is None
    assert outcome.payload["rows"] == [{**AAA_SHARES, "unit": "shares", "cik": AAA_CIK,
                                        "taxonomy": "dei",
                                        "tag": "EntityCommonStockSharesOutstanding"}]


@pytest.mark.criterion("C1")
def test_the_added_value_wins_over_an_sec_field_of_the_same_name(make_toolbox):
    # oracle: requirement 1 — DDD's entry carries cik 12345, taxonomy and tag of its own; the
    # added values replace them, and every other SEC field is unchanged
    toolbox, _ = make_toolbox()
    outcome = toolbox.execute("get_concept", {"cik": "4", **REVENUES})
    assert outcome.error_code is None
    assert outcome.payload["rows"] == [{**DDD_2023, "unit": "USD", "cik": DDD_CIK,
                                        "taxonomy": "us-gaap", "tag": "Revenues"}]


@pytest.mark.criterion("C1")
@given(width=st.integers(1, 10), as_int=st.booleans())
def test_any_spelling_of_the_cik_gives_the_ten_digit_cik_and_the_sec_rows(make_toolbox, width,
                                                                         as_int):
    # oracle: requirement 1 — "1" padded to any width (or the integer 1) names one filer; less
    # the added fields, the rows are exactly the SEC's entries with their unit
    toolbox, _ = make_toolbox()
    cik: int | str = 1 if as_int else "1".zfill(width)
    outcome = toolbox.execute("get_concept", {"cik": cik, **REVENUES})
    assert outcome.error_code is None
    rows = outcome.payload["rows"]
    assert {r.get("cik") for r in rows} == {AAA_CIK}
    stripped = [{k: v for k, v in r.items() if k not in ADDED} for r in rows]
    assert _canon(stripped) == _canon([{**f, "unit": "USD"} for f in AAA_KNOWN])
