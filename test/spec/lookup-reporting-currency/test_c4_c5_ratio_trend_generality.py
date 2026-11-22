"""C4-C5: ratios, trends and generality (docs/specs/lookup-reporting-currency.md).

Ratio expectations are the dev keys' input quotients, computed here from the quoted key
inputs; trend values are the gross-profit key's three inputs.
"""

from __future__ import annotations

import pytest

from src.retriever.xbrl.xbrl_calculator_tool import (
    CalculatorError,
    XbrlCalculatorTool,
    expr_unit,
)

HALF_QUANTUM = 50_000   # decimals -5 (the TSM keys); half of 100,000 either way


# fy, R&D key input, revenue key input (TWD), key case
RATIO_CASES = [
    (2023, 182_370_200_000, 2_161_735_800_000, "int-a967bc81f5f3"),
    (2022, 163_262_200_000, 2_263_891_300_000, "int-e4e6c46945f0"),
]


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("case", RATIO_CASES, ids=[f"FY{c[0]}" for c in RATIO_CASES])
def test_rd_intensity_is_a_pure_ratio_of_twd_amounts(kit, case):
    # oracle: the key's quotient of its two TWD inputs (e.g. 182,370,200,000 /
    # 2,161,735,800,000 = 0.084363 for FY2023), computed here; S6 — a same-currency ratio has
    # no unit
    fy, rd_key, rev_key, _key = case
    rd = kit.run_tsm(fy, "ResearchAndDevelopment")
    rev = kit.run_tsm(fy, "Revenue")
    assert rd.status.value == "OK", rd.resolution_notes
    assert rev.status.value == "OK", rev.resolution_notes
    assert expr_unit("rd / rev", {"rd": rd.unit, "rev": rev.unit}) == "pure"
    value = XbrlCalculatorTool().evaluate("rd / rev", {"rd": rd.value, "rev": rev.value})
    want = rd_key / rev_key
    # graded to 3 significant figures (S6 precision for a ratio)
    assert value == pytest.approx(want, abs=0.0005 if want >= 0.01 else 0.00005)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_a_usd_input_against_a_twd_input_refuses(kit):
    # oracle: units-currency-integrity — two currencies in one expression are refused; the
    # unrequested input is in TWD (S6) and the requested one in USD
    rd = kit.run_tsm(2023, "ResearchAndDevelopment", unit="USD")
    rev = kit.run_tsm(2023, "Revenue")
    assert rd.status.value == "OK", rd.resolution_notes
    assert rev.status.value == "OK", rev.resolution_notes
    assert (rd.unit, rev.unit) == ("USD", "TWD")
    with pytest.raises(CalculatorError):
        expr_unit("rd / rev", {"rd": rd.unit, "rev": rev.unit})


@pytest.mark.criterion("C4")
def test_the_gross_profit_trend_is_three_twd_lookups(kit):
    # oracle: key int-5c685209cc7a inputs (SEC companyfacts, 0001193125-25-083423, TWD)
    want = {2022: 1_348_354_800_000, 2023: 1_175_110_600_000, 2024: 1_624_353_600_000}
    for fy, value in want.items():
        r = kit.run_tsm(fy, "GrossProfit")
        assert r.status.value == "OK", (fy, r.resolution_notes)
        assert r.unit == "TWD"
        assert r.value == pytest.approx(value, abs=HALF_QUANTUM)


# ------------------------------------------------------------------------------------- C5
@pytest.mark.criterion("C5")
def test_a_usd_filer_with_a_stray_euro_row_answers_in_usd(kit):
    # oracle: the rule by hand: USD at 4 year-ends, EUR at the latest only, so USD is the
    # reporting currency; the FY2025 USD value of the synthetic filer is 330e9
    rows = kit.usd_filer_rows(eur_value=301e9)
    r = kit.run_lookup(rows, "0000999001", "ZZUS", 2025, "TotalAssets")
    assert r.status.value == "OK", r.resolution_notes
    assert r.unit == "USD"
    assert r.value == pytest.approx(330e9, abs=0.5)   # an exact synthetic value


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@pytest.mark.parametrize("family, fy, value", [
    ("CashAndEquivalents", 2022, 1_342_814_100_000),
    ("GrossProfit", 2024, 1_624_353_600_000),
])
def test_another_filer_in_another_currency_answers_alike(kit, family, fy, value):
    # oracle: Requirement 5 — nothing depends on the CIK, ticker or code: the TSM rows moved
    # to another filer with TWD renamed EUR answer the same key value in EUR
    moved = kit.rename_unit(kit.tsm_rows(), "TWD", "EUR", "0000999002", "ZZEU")
    r = kit.run_lookup(moved, "0000999002", "ZZEU", fy, family)
    assert r.status.value == "OK", r.resolution_notes
    assert r.unit == "EUR"
    assert r.value == pytest.approx(value, abs=HALF_QUANTUM)
