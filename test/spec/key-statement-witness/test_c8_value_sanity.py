"""C8: a key whose value no filing presents for its period is never keyed silently.

Oracle: the companyfacts entries as SEC serves them (excerpt) and the rendered annual
statements, checked by hand: LMT's 2025-12-31 PP&E is 8,875 in its FY2025 10-K and 11,292 in
every FY2026 10-Q; AAPL's 2025-09-27 LongTermDebt is 90,678 in its FY2025 10-K ("Total term
debt") and 90,700 in every FY2026 10-Q.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import select_key, statement_witness

# tolerance: filed integer amounts; rel 1e-12 absorbs binary floating point only.
REL = 1e-12
LMT_Q = "0001628280-26-066928"
AAPL_Q = "0000320193-26-000020"
PPE = "us-gaap:PropertyPlantAndEquipmentNet"


def _w(kit, family, k, annual, annual_value=None):
    e = kit.env()
    return statement_witness(e.tables, e.rules, e.notes, family, k, annual, annual_value)


@pytest.mark.criterion("C8")
def test_lmt_ppe_recast_in_a_10q_is_held(kit):
    # oracle: LMT FY2025 10-K balance sheet "Property, plant and equipment, net" 8,875; the
    # 10-Q entry 11,292 (companyfacts, form 10-Q, filed 2026-10-22)
    k = kit.key("LMT", PPE, LMT_Q, "2025-12-31", None, 2025)
    assert k["form"] == "10-Q"
    annual = kit.cf_value("LMT", PPE, kit.LMT, "2025-12-31", None)
    assert annual == pytest.approx(8_875e6, rel=REL)
    w = _w(kit, "PropertyPlantAndEquipment", k, kit.LMT, annual)
    assert (w["status"], w["adsh"], w["key_accession"]) == ("value_recast", kit.LMT, LMT_Q)
    assert w["value"] == pytest.approx(8_875e6, rel=REL)
    assert w["key_value"] == pytest.approx(11_292e6, rel=REL)
    assert [(ln["report"], ln["line"]) for ln in w["lines"]] == [(6, 9)]


@pytest.mark.criterion("C8")
def test_aapl_debt_total_recast_in_a_10q_is_held(kit):
    # oracle: AAPL FY2025 10-K debt note "Total term debt" 90,678 (= "Term debt" 12,350 +
    # 78,328 on its balance sheet); the FY2026 10-Q entries 90,700
    k = kit.key("AAPL", "us-gaap:LongTermDebt", AAPL_Q, "2025-09-27", None, 2025)
    annual = kit.cf_value("AAPL", "us-gaap:LongTermDebt", kit.AAPL, "2025-09-27", None)
    w = _w(kit, "Debt", k, kit.AAPL, annual)
    assert w["status"] == "value_recast"
    assert w["value"] == pytest.approx(90_678e6, rel=REL)
    e = kit.env()
    noncurrent = "us-gaap:LongTermDebtNoncurrent"
    cands = [k, kit.key("AAPL", noncurrent, AAPL_Q, "2025-09-27", None, 2025)]
    vals = {"us-gaap:LongTermDebt": annual, noncurrent: 78_328e6}
    held = select_key(e.tables, e.rules, e.notes, e.resolutions, "Debt", cands, kit.AAPL, vals)
    assert (held["status"], held["key_concept"]) == ("value_recast", "us-gaap:LongTermDebt")


@pytest.mark.criterion("C8")
def test_a_witnessed_zero_is_keyed(kit):
    # oracle: AAPL FY2025 10-K cash flows "Proceeds from issuance of term debt, net" 2024: "—"
    # (FSDS num 0.0000; companyfacts 0)
    tag = "ProceedsFromIssuanceOfLongTermDebt"
    k = kit.key("AAPL", "us-gaap:" + tag, kit.AAPL, "2024-09-28", "2023-10-01", 2024)
    assert kit.raw_value(kit.AAPL, tag, "us-gaap/2025", "20240930", "4", "USD") == 0
    w = _w(kit, "DebtIssued", k, kit.AAPL)
    assert w["status"] == "agree"
    # tolerance: an exact zero; half a dollar cannot hide a real amount
    assert w["value"] == pytest.approx(0.0, abs=0.5)
    assert [ln["concept"] for ln in w["lines"]] == ["us-gaap:" + tag]


@pytest.mark.criterion("C8")
def test_a_zero_against_a_non_zero_line_blocks(kit):
    # oracle: LMT FY2025 10-K balance sheet 2024-12-31 "Property, plant and equipment, net"
    # 8,726; the key's value 0 is constructed (the ORCL FY2022 LongTermDebt = 0 pattern, a
    # validation key, is not a fixture)
    k = kit.key("LMT", PPE, kit.LMT, "2024-12-31", None, 2024, value=0.0)
    w = _w(kit, "PropertyPlantAndEquipment", k, kit.LMT)
    assert w["status"] == "disagree"
    assert w["value"] == pytest.approx(8_726e6, rel=REL)


@pytest.mark.criterion("C8")
@given(value=st.integers(min_value=0, max_value=20_000).map(lambda m: m * 1e6))
def test_an_interim_value_is_held_unless_it_equals_the_annual_report(kit, value):
    # oracle: requirement 1 step 3 over LMT's 10-Q key with any value; the annual report's
    # balance sheet shows 8,875 (2025-12-31)
    k = kit.key("LMT", PPE, LMT_Q, "2025-12-31", None, 2025, value=value)
    w = _w(kit, "PropertyPlantAndEquipment", k, kit.LMT, 8_875e6)
    expected = "agree" if value == pytest.approx(8_875e6, rel=REL) else "value_recast"
    assert w["status"] == expected
    assert w["value"] == pytest.approx(8_875e6, rel=REL)
