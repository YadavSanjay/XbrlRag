"""C1: ``load_authoring`` and ``parse_amount``.

Oracle: requirements 1-3 of docs/specs/set-b-tooling.md. Every expected ``(value,
half_unit)`` is worked out by hand in the test from the spec's rules: ``value = number x
scale`` and ``half_unit = 0.5 x scale x 10**-d`` (``d`` = digits after the point), or, with
``%``, ``value = number / 100`` and ``half_unit = 0.5 x 10**-d / 100``.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.core import load_authoring, parse_amount

_SCALES = {None: 1.0, "units": 1.0, "thousands": 1e3, "millions": 1e6, "billions": 1e9}


def _assert_amount(got, value, half_unit):
    assert isinstance(got, tuple)
    assert len(got) == 2
    # tolerance: the expected values are exact decimal products; rel=1e-12 absorbs float rounding
    assert got[0] == pytest.approx(value, rel=1e-12, abs=1e-15)
    # tolerance: same as above, half_unit is a power-of-ten product
    assert got[1] == pytest.approx(half_unit, rel=1e-12)


@pytest.mark.criterion("C1")
def test_spec_example_thousands_separators_in_millions():
    # oracle: requirement 2 example — "391,035" in millions: 391035 x 1e6, d=0 so
    # half_unit = 0.5 x 1e6 x 10**0 = 0.5e6
    _assert_amount(parse_amount("391,035", "millions"), 391035e6, 0.5e6)


@pytest.mark.criterion("C1")
def test_spec_example_parentheses_mean_negative():
    # oracle: requirement 2 example — "(1.25)" in billions: -1.25 x 1e9, d=2 so
    # half_unit = 0.5 x 1e9 x 10**-2 = 0.005e9
    _assert_amount(parse_amount("(1.25)", "billions"), -1.25e9, 0.005e9)


@pytest.mark.criterion("C1")
def test_spec_example_percent():
    # oracle: requirement 2 example — "21.5%": 21.5 / 100 = 0.215, d=1 so
    # half_unit = 0.5 x 10**-1 / 100 = 0.0005
    _assert_amount(parse_amount("21.5%"), 0.215, 0.0005)


@pytest.mark.criterion("C1")
def test_yaml_int_and_float_amounts():
    # oracle: requirements 1 and 2 — yaml.safe_load gives an int for 245122 and a float for
    # 12.75; str(245122) has d=0 -> (245122e6, 0.5e6) in millions; str(12.75) has d=2 ->
    # (12.75 x 1e3, 0.5 x 1e3 x 0.01) = (12750, 5) in thousands
    cases = load_authoring(
        "cases:\n"
        "  - id: a1\n"
        "    expected: 245122\n"
        "    scale: millions\n"
        "  - id: a2\n"
        "    expected: 12.75\n"
        "    scale: thousands\n"
    )
    assert [c["id"] for c in cases] == ["a1", "a2"]
    assert isinstance(cases[0]["expected"], int)
    assert isinstance(cases[1]["expected"], float)
    _assert_amount(parse_amount(cases[0]["expected"], cases[0]["scale"]), 245122e6, 0.5e6)
    _assert_amount(parse_amount(cases[1]["expected"], cases[1]["scale"]), 12750.0, 5.0)


@pytest.mark.criterion("C1")
def test_thousands_and_units_scales():
    # oracle: requirement 2 — "$1,000" in thousands: 1000 x 1e3, half 0.5 x 1e3 = 500;
    # "1,234.5" in units: 1234.5, half 0.5 x 10**-1 = 0.05; None behaves as units;
    # " -7.25 " ignores whitespace: -7.25, half 0.5 x 10**-2 = 0.005
    _assert_amount(parse_amount("$1,000", "thousands"), 1e6, 500.0)
    _assert_amount(parse_amount("1,234.5", "units"), 1234.5, 0.05)
    _assert_amount(parse_amount("1,234.5", None), 1234.5, 0.05)
    _assert_amount(parse_amount(" -7.25 "), -7.25, 0.005)


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("raw,scale", [
    ("5%", "millions"),       # a % with a scale
    ("5", "lakhs"),           # an unknown scale
    ("abc", None),            # not a number
    ("abc", "millions"),
])
def test_rejected_amounts_raise_value_error(raw, scale):
    # oracle: requirement 2 — "with %, scale must be None"; scale is one of
    # None/units/thousands/millions/billions; anything else raises ValueError
    with pytest.raises(ValueError):
        parse_amount(raw, scale)


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("text", [
    "version: 1\n",                      # no cases key
    "cases: 3\n",                        # cases is not a list
    "cases:\n  id: a1\n  kind: lookup\n",  # cases is a mapping
])
def test_missing_or_non_list_cases_raise(text):
    # oracle: requirement 1 — a missing or non-list `cases` raises ValueError
    with pytest.raises(ValueError):
        load_authoring(text)


@pytest.mark.criterion("C1")
def test_load_authoring_returns_the_case_dicts():
    # oracle: requirement 1 — the parsed `cases` list, each item the case dict as written
    cases = load_authoring(
        "cases:\n"
        "  - id: q1\n"
        "    kind: lookup\n"
        "    ticker: NVDA\n"
        "    fy: 2025\n"
        "    expected: \"60,922\"\n"
        "    scale: millions\n"
        "    unit: USD\n"
    )
    assert cases == [{"id": "q1", "kind": "lookup", "ticker": "NVDA", "fy": 2025,
                      "expected": "60,922", "scale": "millions", "unit": "USD"}]


def _with_commas(n: int) -> str:
    s = str(n)
    groups = []
    while len(s) > 3:
        groups.insert(0, s[-3:])
        s = s[:-3]
    groups.insert(0, s)
    return ",".join(groups)


@pytest.mark.criterion("C1")
@given(
    whole=st.integers(min_value=0, max_value=10**12),
    d=st.integers(min_value=0, max_value=3),
    frac_seed=st.integers(min_value=0, max_value=999),
    scale=st.sampled_from(sorted(_SCALES, key=str)),
    negative=st.booleans(),
    style=st.sampled_from(["plain", "commas", "parens", "dollar"]),
)
def test_formatted_digit_strings_round_trip(whole, d, frac_seed, scale, negative, style):
    # oracle: requirement 2 closed form computed here — the written number is
    # whole + frac / 10**d (sign applied), value = number x scale, half_unit = 0.5 x scale x 10**-d
    frac = frac_seed % (10 ** d) if d else 0
    digits = _with_commas(whole) if style in ("commas", "parens", "dollar") else str(whole)
    if d:
        digits += "." + str(frac).zfill(d)
    if style == "dollar":
        digits = "$" + digits
    if negative:
        text = f"({digits})" if style == "parens" else f"-{digits}"
    else:
        text = digits
    mult = _SCALES[scale]
    number = (whole + frac / 10 ** d) * (-1 if negative else 1)
    got = parse_amount(text, scale)
    assert isinstance(got, tuple)
    # tolerance: float precision of the decimal-to-binary conversion
    assert got[0] == pytest.approx(number * mult, rel=1e-12, abs=1e-12)
    # tolerance: float precision of 10**-d
    assert got[1] == pytest.approx(0.5 * mult * 10.0 ** -d, rel=1e-12)


@pytest.mark.criterion("C1")
@given(
    whole=st.integers(min_value=0, max_value=999),
    d=st.integers(min_value=0, max_value=3),
    frac_seed=st.integers(min_value=0, max_value=999),
)
def test_percent_strings_round_trip(whole, d, frac_seed):
    # oracle: requirement 2 closed form computed here — with %, value = number / 100 and
    # half_unit = 0.5 x 10**-d / 100
    frac = frac_seed % (10 ** d) if d else 0
    text = str(whole) + ("." + str(frac).zfill(d) if d else "") + "%"
    got = parse_amount(text)
    assert isinstance(got, tuple)
    # tolerance: float precision of the decimal-to-binary conversion
    assert got[0] == pytest.approx((whole + frac / 10 ** d) / 100, rel=1e-12, abs=1e-15)
    # tolerance: float precision of 10**-d
    assert got[1] == pytest.approx(0.5 * 10.0 ** -d / 100, rel=1e-12)
