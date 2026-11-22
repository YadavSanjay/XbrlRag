"""C6: an answer's unit names its currency, so no compute mixes TWD with USD (R5)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from ifm_support import YEARS, candidate_rows, lookup

from src.retriever.xbrl.xbrl_calculator_tool import CalculatorError, expr_unit

# family -> the unit a USD filer's answer to it carries (e.g. AAPL; display_unit form)
USD_COUNTERPART = {"EarningsPerShareDiluted": "USD/shares",
                   "EarningsPerShareBasic": "USD/shares",
                   "Revenue": "USD", "NetIncome": "USD"}


def _ok(fy, family, unit=None):
    r = lookup(fy, family, candidate_rows(fy, family), unit=unit)
    assert r.status.value == "OK", (fy, family, unit, r.resolution_notes)
    assert r.unit is not None
    return r


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("fy", YEARS)
def test_compute_with_a_usd_filer(fy):
    # oracle: units-currency-integrity Requirement 4 (two currencies never meet in one
    # expression); R5 (the asked currency is the answer's unit)
    eps_usd = _ok(fy, "EarningsPerShareDiluted", "USD")
    assert expr_unit("t - a", {"t": eps_usd.unit, "a": "USD/shares"}) == "USD/shares"
    eps_twd = _ok(fy, "EarningsPerShareDiluted")
    with pytest.raises(CalculatorError):
        expr_unit("t - a", {"t": eps_twd.unit, "a": "USD/shares"})
    rev_usd, rev_twd = _ok(fy, "Revenue", "USD"), _ok(fy, "Revenue")
    assert expr_unit("t / a", {"t": rev_usd.unit, "a": "USD"}) == "pure"
    with pytest.raises(CalculatorError):
        expr_unit("t / a", {"t": rev_twd.unit, "a": "USD"})
    ni = _ok(fy, "NetIncome")
    assert expr_unit("ni / rev", {"ni": ni.unit, "rev": rev_twd.unit}) == "pure"


@pytest.mark.criterion("C6")
@given(currency=st.sampled_from(["USD", "TWD"]), family=st.sampled_from(sorted(USD_COUNTERPART)),
       fy=st.sampled_from(YEARS))
def test_compute_is_accepted_iff_tsmc_answers_in_usd(currency, family, fy):
    # oracle: R5 — the answer's unit names the asked currency; units-currency-integrity
    # Requirement 4 — a compute with a USD counterpart is valid iff that currency is USD
    r = _ok(fy, family, currency)
    assert r.unit.split("/", 1)[0] == currency
    units = {"t": r.unit, "a": USD_COUNTERPART[family]}
    if currency == "USD":
        assert expr_unit("t - a", units) == USD_COUNTERPART[family]
    else:
        with pytest.raises(CalculatorError):
            expr_unit("t - a", units)
