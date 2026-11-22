"""C1: ``load_constituents``, ``normalize_symbol`` and ``cik_map``.

Oracle: requirements 1-3 of docs/specs/aprime-draw.md. CSV texts and ``company_tickers``
documents are built here with synthetic values chosen by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.aprime.core import cik_map, load_constituents, normalize_symbol


@pytest.mark.criterion("C1")
def test_three_rows_with_extra_column_and_padding_parse_stripped_in_file_order():
    # oracle: requirement 1 — one {"symbol","name","sector"} per data row, in file order,
    # values stripped; the extra "weight" column is ignored; symbols are not normalized here
    text = (
        "symbol,name,weight,sector\n"
        "  ZZZQ , Zeta Synthetic Inc ,1.5,  Energy \n"
        "aaqx,Alpha Synthetic Co,2.0,Information Technology\n"
        " mmq.b ,  Mu Synthetic Class B,0.3 , Health Care\n"
    )
    assert load_constituents(text) == [
        {"symbol": "ZZZQ", "name": "Zeta Synthetic Inc", "sector": "Energy"},
        {"symbol": "aaqx", "name": "Alpha Synthetic Co", "sector": "Information Technology"},
        {"symbol": "mmq.b", "name": "Mu Synthetic Class B", "sector": "Health Care"},
    ]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("text", [
    "name,sector\nAlpha Co,Energy\n",               # no symbol column
    "symbol,sector\nAAQX,Energy\n",                  # no name column
    "symbol,name\nAAQX,Alpha Co\n",                  # no sector column
    "symbol,name,sector\n  ,Alpha Co,Energy\n",      # an empty (blank) symbol
    "symbol,name,sector\nbrk.b,Beta A,Energy\nBRK-B,Beta B,Energy\n",  # equal once normalized
    "symbol,name,sector\nBF/B,Gamma A,Energy\nbf.b,Gamma B,Energy\n",  # equal once normalized
    "symbol,name,sector\n",                          # no data rows
])
def test_load_constituents_errors_raise(text):
    # oracle: requirement 1 — missing column, empty symbol, symbols equal after
    # normalize_symbol, and no data rows each raise ValueError
    with pytest.raises(ValueError):
        load_constituents(text)


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("raw,expected", [
    (" brk.b ", "BRK-B"),
    ("BF/B", "BF-B"),
    ("msft", "MSFT"),
])
def test_normalize_symbol_examples(raw, expected):
    # oracle: requirement 2 — strip, upper-case, '.' and '/' become '-' (worked by hand)
    assert normalize_symbol(raw) == expected


@pytest.mark.criterion("C1")
def test_cik_map_pads_and_normalizes():
    # oracle: requirement 3 — cik10 is the CIK zero-padded to 10 digits (320193 ->
    # "0000320193", 4567 -> "0000004567"); keys are normalize_symbol(ticker), "BRK.B" -> "BRK-B"
    doc = {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Synthetic Apple Row"},
        "1": {"cik_str": 4567, "ticker": "BRK.B", "title": "Synthetic Class B Row"},
    }
    assert cik_map(doc) == {"AAPL": "0000320193", "BRK-B": "0000004567"}


@pytest.mark.criterion("C1")
def test_cik_map_accepts_exact_repeat_and_rejects_two_ciks():
    # oracle: requirement 3 — the same ticker with the same CIK is accepted (one entry);
    # the same normalized ticker with two different CIKs raises ValueError
    repeat = {
        "0": {"cik_str": 1234, "ticker": "QQX", "title": "Synthetic Q"},
        "1": {"cik_str": 1234, "ticker": "QQX", "title": "Synthetic Q"},
    }
    assert cik_map(repeat) == {"QQX": "0000001234"}
    clash = {
        "0": {"cik_str": 1234, "ticker": "QQX.A", "title": "Synthetic Q"},
        "1": {"cik_str": 5678, "ticker": "qqx-a", "title": "Synthetic Q other"},
    }
    with pytest.raises(ValueError):
        cik_map(clash)


_SYMBOL_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789./-"


@pytest.mark.criterion("C1")
@given(core=st.text(alphabet=_SYMBOL_CHARS, min_size=1, max_size=8),
       left=st.text(alphabet=" \t", max_size=3), right=st.text(alphabet=" \t", max_size=3))
def test_normalize_symbol_is_idempotent(core, left, right):
    # oracle: requirement 2 — the rule (strip, upper, '.'/'/' -> '-') applied by hand gives
    # the expected value, and applying normalize_symbol twice equals applying it once
    raw = left + core + right
    expected = core.upper().replace(".", "-").replace("/", "-")
    once = normalize_symbol(raw)
    assert once == expected
    assert normalize_symbol(once) == once
