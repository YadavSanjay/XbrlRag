"""C1: ``parse_exclusion_lists``.

Oracle: requirement 1 of docs/specs/heldout-draw-v2.md. Inputs are synthetic; each expected
result is the input normalized by hand (strip, upper-case, ``.`` and ``/`` to ``-``).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.aprime.v2 import parse_exclusion_lists


def _valid():
    return {"wave1": [" csco ", "brk.b", "BF/B"], "wave2": ["Xom"], "left_out": ["SNDK", "ge"]}


@pytest.mark.criterion("C1")
def test_symbols_normalized_in_input_order():
    # oracle: requirement 1 — each symbol normalized: " csco " -> CSCO, "brk.b" -> BRK-B,
    # "BF/B" -> BF-B, "Xom" -> XOM, "ge" -> GE; input order kept per list
    data = _valid()
    assert parse_exclusion_lists(data) == {
        "wave1": ["CSCO", "BRK-B", "BF-B"],
        "wave2": ["XOM"],
        "left_out": ["SNDK", "GE"],
    }
    assert data == _valid()  # the caller's mapping is left as it was


def _without(key):
    d = _valid()
    del d[key]
    return d


def _with(key, value):
    d = _valid()
    d[key] = value
    return d


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(
    "data",
    [
        pytest.param(["wave1", "wave2", "left_out"], id="not-a-dict"),
        pytest.param(None, id="none"),
        pytest.param(_without("wave2"), id="missing-wave2"),
        pytest.param(_without("left_out"), id="missing-left_out"),
        pytest.param(_with("wave3", ["ABC"]), id="unknown-key"),
        pytest.param(_with("wave1", "CSCO"), id="value-not-list"),
        pytest.param(_with("wave2", []), id="empty-list"),
        pytest.param(_with("wave2", ["XOM", 42]), id="non-string-entry"),
        pytest.param(_with("wave2", ["XOM", "   "]), id="blank-entry"),
        pytest.param(_with("wave2", ["XOM", "xom"]), id="duplicate-within-list"),
        pytest.param(_with("left_out", ["BRK-B"]), id="duplicate-across-lists"),
    ],
)
def test_each_error_raises(data):
    # oracle: requirement 1 — each listed condition raises ValueError ("brk.b" in wave1 and
    # "BRK-B" in left_out normalize to the same symbol)
    with pytest.raises(ValueError):
        parse_exclusion_lists(data)


@pytest.mark.criterion("C1")
def test_errors_name_the_key_and_the_symbol():
    # oracle: requirement 1 — the unknown-key error names the key; the duplicate error names
    # the symbol
    with pytest.raises(ValueError) as err:
        parse_exclusion_lists(_with("wave3", ["ABC"]))
    assert "wave3" in str(err.value)
    with pytest.raises(ValueError) as err:
        parse_exclusion_lists(_with("left_out", ["brk/b"]))
    assert "BRK-B" in str(err.value)


_SYM = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=1, max_size=5)


@st.composite
def _lists(draw_):
    syms = draw_(st.lists(_SYM, min_size=3, max_size=24, unique=True))
    cut1 = draw_(st.integers(1, len(syms) - 2))
    cut2 = draw_(st.integers(cut1 + 1, len(syms) - 1))
    raw = {"wave1": syms[:cut1], "wave2": syms[cut1:cut2], "left_out": syms[cut2:]}
    lower = draw_(st.lists(st.booleans(), min_size=len(syms), max_size=len(syms)))
    pad = draw_(st.booleans())
    styled = {}
    i = 0
    for key, vals in raw.items():
        out = []
        for v in vals:
            s = v.lower() if lower[i] else v
            out.append(f"  {s} " if pad else s)
            i += 1
        styled[key] = out
    return raw, styled


@pytest.mark.criterion("C1")
@given(case=_lists())
def test_result_is_normalized_input_and_parse_is_idempotent(case):
    # oracle: requirement 1 — upper-case letter symbols, lower-cased and padded at random,
    # normalize back to the generated upper-case originals; parsing the result again is a
    # no-op
    raw, styled = case
    got = parse_exclusion_lists(styled)
    assert got == raw
    assert parse_exclusion_lists(got) == got
