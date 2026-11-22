"""C6: ``presented_value`` shows a value as the statement prints it (negated on negating lines).

Oracle: SEC readme (pre.negating "Flag to indicate whether the plabel is negating") and the
parentheses on the rendered statements, checked by hand in our local EDGAR copies. A
printed "(x)" is -x; the XBRL fact behind it is +x when the line's label is negating.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.sec_reference.fsds import load_tables, presented_value, statement_lines

# tolerance: FSDS values are the filed amounts (at most 4 decimals); rel 1e-12 absorbs only
# binary floating-point representation, so a wrong figure can never pass.
REL = 1e-12


def _line(tables, adsh, stmt, tag):
    found = [ln for ln in statement_lines(tables, adsh, stmt) if ln["tag"] == tag]
    assert len(found) == 1
    return found[0]


@pytest.mark.criterion("C6")
@pytest.mark.parametrize(
    ("acc", "stmt", "tag", "fact", "printed"),
    [
        # AAPL FY2024 cash flows, "Other" (adjustments), 2024: fact +2,266, printed (2,266)
        ("AAPL_24", "CF", "OtherNoncashIncomeExpense", 2_266_000_000.0, -2_266_000_000.0),
        # same line, 2022 column: fact -1,006 (XBRL sign="-"), printed 1,006
        ("AAPL_24", "CF", "OtherNoncashIncomeExpense", -1_006_000_000.0, 1_006_000_000.0),
        # AAPL FY2024 cash flows, "Payments for acquisition of property, plant and
        # equipment": (9,447)
        ("AAPL_24", "CF", "PaymentsToAcquirePropertyPlantAndEquipment",
         9_447_000_000.0, -9_447_000_000.0),
        # AVGO FY2024 statements of operations, "Interest expense", fiscal 2024: (3,953)
        ("AVGO_24", "IS", "InterestExpense", 3_953_000_000.0, -3_953_000_000.0),
        # TSM 2024 20-F, "Finance costs" 2024: NT$(10,495.4) million
        ("TSM_24", "IS", "FinanceCosts", 10_495_400_000.0, -10_495_400_000.0),
        # AAPL FY2024 income statement "Total net sales" 391,035: not negating, printed as is
        ("AAPL_24", "IS", "RevenueFromContractWithCustomerExcludingAssessedTax",
         391_035_000_000.0, 391_035_000_000.0),
    ],
)
def test_printed_sign_matches_the_rendered_statement(kit, acc, stmt, tag, fact, printed):
    # oracle: the rendered line named in the parametrize comment, checked by hand
    tables = load_tables(kit.dirs)
    line = _line(tables, getattr(kit, acc), stmt, tag)
    assert presented_value(line, fact) == printed


@pytest.mark.criterion("C6")
def test_none_stays_none_on_both_kinds_of_line(kit):
    # oracle: requirement 9 — a nil value has no sign to apply
    tables = load_tables(kit.dirs)
    neg = _line(tables, kit.AAPL_24, "CF", "OtherNoncashIncomeExpense")
    pos = _line(tables, kit.AAPL_24, "IS", "GrossProfit")
    assert presented_value(neg, 5.0) == pytest.approx(-5.0, rel=REL)
    assert presented_value(neg, None) is None
    assert presented_value(pos, None) is None
    assert presented_value(pos, 5.0) == pytest.approx(5.0, rel=REL)


@pytest.mark.criterion("C6")
@given(
    value=st.floats(allow_nan=False, allow_infinity=False, min_value=-1e15, max_value=1e15),
    negating=st.booleans(),
)
def test_negation_is_an_involution_and_identity_otherwise(kit, value, negating):
    # oracle: requirement 9 — presented = -value exactly when negating; applying it twice
    # is the identity
    line = {
        "adsh": kit.AAPL_24, "report": 8, "line": 7, "stmt": "CF", "inpth": False,
        "tag": "OtherNoncashIncomeExpense", "version": "us-gaap/2024", "custom": False,
        "plabel": "Other", "negating": negating,
    }
    shown = presented_value(line, value)
    assert shown is not None and not math.isnan(shown)
    assert shown == (-value if negating else value)
    assert presented_value(line, shown) == value
