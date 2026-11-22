"""C3: the overlay generator: drawn filers, the overlay document, and its YAML round trip.

Oracle: requirement 3 of docs/specs/hf-suite-build.md, applied by hand to the synthetic
draw, entity and filing rows below (made-up CIKs 0000900001..0000900009 and tickers QQA..QQD,
none a real held-out filer).
"""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.suites.heldout_build import (
    drawn_filers,
    lexicon_overlay,
    merge_lexicon_overlay,
    write_lexicon_overlay,
)


def _entry(cik: str, symbol: str, name: str) -> dict:
    return {"cik": cik, "symbol": symbol, "name": name, "key": "ab" * 32, "rank": 1,
            "sector": "Industrials"}


DRAW = {
    "scheme": "xbrlrag-heldout-v2",
    "output_value": "A" * 128,
    "strata": {
        "S": {"drawn": [_entry("0000900002", "QQB", "Quill Bravo"),
                        _entry("0000900001", "QQA", " Quill Alpha ")],
              "alternates": [_entry("0000900009", "QQZ", "Quill Zulu")],
              "checked": [], "shortfall": 0},
        "D": {"drawn": [_entry("0000900004", "QQD", "Quill Delta"),
                        _entry("0000900003", "QQC", "Quill Charlie")],
              "alternates": [], "checked": [], "shortfall": 0},
    },
}
DRAWN = [{"cik": "0000900001", "symbol": "QQA", "name": " Quill Alpha "},
         {"cik": "0000900002", "symbol": "QQB", "name": "Quill Bravo"},
         {"cik": "0000900003", "symbol": "QQC", "name": "Quill Charlie"},
         {"cik": "0000900004", "symbol": "QQD", "name": "Quill Delta"}]
ENTITIES = [
    ("900001", "  Quill Alpha Holdings, Inc. ", " qqa "),   # unpadded CIK, padded strings
    ("0000900002", None, "QQB"),                            # no legal name: falls back
    ("0000900003", "Quill Charlie Corp.", None),            # no symbol: left out
    ("0000900009", "Quill Zulu Inc.", "QQZ"),               # an alternate: not drawn
]                                                           # 0000900004: no entity row
FILINGS = [
    # cik, accession, form, fiscal year, filed, period end
    ("0000900001", "0000900001-24-000010", "10-K", 2023, "2024-02-01", "2023-12-31"),
    ("0000900001", "0000900001-24-000020", "10-K/A", 2023, "2024-05-01", "2023-12-30"),
    ("0000900001", "0000900001-24-000030", "10-Q", 2024, "2024-05-02", "2024-03-31"),
    ("900001", "0000900001-25-000040", "10-K", 2024, "2025-03-01", "2024-12-29"),
    ("0000900001", "0000900001-25-000010", "10-K", 2024, "2025-02-01", "2024-12-31"),
    ("0000900002", "0000900002-25-000002", "20-F", 2024, "2025-04-01", "2024-12-31"),
    ("0000900002", "0000900002-25-000001", "20-F", 2024, "2025-04-01", "2024-12-30"),
    ("0000900002", "0000900002-24-000001", "40-F/A", 2023, "2024-04-01", "2023-12-31"),
    ("0000900009", "0000900009-25-000001", "10-K", 2024, "2025-02-01", "2024-09-30"),
]
# By hand: QQA's FY2023 is its 10-K (the 10-K/A ignored); its FY2024 is the earlier-filed of
# two 10-Ks; the 10-Q is ignored. QQB's two FY2024 20-Fs share a filing date, so the smaller
# accession (...-000001) wins; its 40-F/A is an amendment, ignored. QQC has no symbol, QQD
# no entity row, QQZ is not drawn.
EXPECTED = {
    "companies": {"QQA": {"short": "Quill Alpha", "legal": "Quill Alpha Holdings, Inc."},
                  "QQB": {"short": "Quill Bravo", "legal": "Quill Bravo"}},
    "period_ends": {"QQA": {2023: "2023-12-31", 2024: "2024-12-31"},
                    "QQB": {2024: "2024-12-30"}},
}


@pytest.mark.criterion("C3")
def test_drawn_filers_of_both_strata_sorted_by_cik():
    # oracle: requirement 3: the drawn entries of S and D, no alternate, sorted by CIK.
    assert drawn_filers(DRAW) == DRAWN
    with pytest.raises(ValueError):
        drawn_filers({**DRAW, "scheme": "xbrlrag-heldout-v1"})
    with pytest.raises(ValueError):
        drawn_filers({**DRAW, "strata": {"S": DRAW["strata"]["S"]}})


@pytest.mark.criterion("C3")
def test_overlay_from_rows_by_hand():
    # oracle: requirement 3, applied by hand above.
    assert lexicon_overlay(DRAWN, ENTITIES, FILINGS) == EXPECTED
    clash = [*ENTITIES[:1], ("0000900002", "Quill Bravo plc", "QQA")]
    with pytest.raises(ValueError):
        lexicon_overlay(DRAWN, clash, FILINGS)


@pytest.mark.criterion("C3")
def test_written_overlay_round_trips(kit, tmp_path):
    # oracle: requirement 3: the written YAML merges back to exactly EXPECTED.
    path = tmp_path / "overlay.yaml"
    assert write_lexicon_overlay(EXPECTED, path) == path
    merged = merge_lexicon_overlay(kit.lexicon_base(), path)
    assert {t: merged.companies[t] for t in ("QQA", "QQB")} == EXPECTED["companies"]
    assert merged.period_ends["QQA"] == {2023: dt.date(2023, 12, 31),
                                         2024: dt.date(2024, 12, 31)}
    assert merged.period_ends["QQB"] == {2024: dt.date(2024, 12, 30)}
    assert set(merged.companies) == {"DEVA", "DEVC", "DEVE", "QQA", "QQB"}


@pytest.mark.criterion("C3")
@settings(max_examples=30)
@given(drawn=st.permutations(DRAWN), entities=st.permutations(ENTITIES),
       filings=st.permutations(FILINGS))
def test_row_order_changes_nothing(drawn, entities, filings):
    # oracle: requirement 3: the overlay depends on the rows, not their order.
    assert lexicon_overlay(list(drawn), list(entities), list(filings)) == EXPECTED
