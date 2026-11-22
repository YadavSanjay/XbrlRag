"""C2: TSLA's capex line, qualified "excluding finance leases, net of sales", is witnessed.

Oracle: TSLA's FY2025 10-K consolidated statements of cash flows (USD millions, read by hand
from tsla-20251231.htm, as statement-rules-by-element recorded): "Purchases of property and
equipment excluding finance leases, net of sales" (8,527) 2025, (11,342) 2024, (8,899) 2023;
FSDS report 9 line 20, us-gaap PaymentsToAcquirePropertyPlantAndEquipment, a negating line
(values tagged positive).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# tolerance: filed integer amounts in millions; rel 1e-12 absorbs binary floating point only.
REL = 1e-12
PPE = "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"
YEARS = ((2025, 8_527e6), (2024, 11_342e6), (2023, 8_899e6))


def _select(kit, rule, tables=None, year=2025):
    from src.eval.keys.statement_witness import select_lines

    e = kit.loaded()
    return select_lines(tables if tables is not None else e.tables, rule, kit.TSLA,
                        f"{year}-01-01", f"{year}-12-31", "USD")


@pytest.mark.criterion("C2")
def test_live_capex_selects_tsla_line_every_year(kit):
    # oracle: hand check (module docstring) - the line is the only one selected; 8,527 /
    # 11,342 / 8,899
    live = kit.loaded().live["Capex"]
    for year, value in YEARS:
        got = _select(kit, live, year=year)
        assert [(ln["concept"], ln["report"], ln["line"]) for ln in got] == [(PPE, 9, 20)]
        assert got[0]["value"] == pytest.approx(value, rel=REL)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_the_accepted_rule_misses_it_and_the_head_alone_is_taken_by_both(kit):
    # oracle: the defect (witness-rules-r2-1 §1.3) - the accepted r2.1 rule selects nothing
    # ("\bleases?\b" in the qualifier rejects the line); relabelled to its head "Purchases of
    # property and equipment", both rules select it at 8,527
    e = kit.loaded()
    assert _select(kit, e.accepted["Capex"]) == []
    assert [ln["line"] for ln in _select(kit, e.live["Capex"])] == [20]
    t ={k: [dict(r) for r in v] for k, v in e.tables.items()}
    rows = [r for r in t["pre"] if r["adsh"] == kit.TSLA and r["report"] == "9"
            and r["line"] == "20"]
    assert len(rows) == 1
    rows[0]["plabel"] = "Purchases of property and equipment"
    for rule in (e.accepted["Capex"], e.live["Capex"]):
        got = _select(kit, rule, tables=t)
        assert [ln["line"] for ln in got] == [20]
        assert got[0]["value"] == pytest.approx(8_527e6, rel=REL)


@pytest.mark.criterion("C2")
@settings(max_examples=40, deadline=None)
@given(cue=st.sampled_from(["excluding", "net of", "other than", "exclusive of"]),
       tail=st.sampled_from(["finance leases", "leasehold improvements financed",
                             "amounts in accounts payable", "sales"]))
def test_any_qualifier_on_the_tsla_line_keeps_it(kit, cue, tail):
    # oracle: requirement 2 (property) - "Purchases of property and equipment, <cue> <tail>"
    # on line 20 is selected at 8,527 by the live rule whatever the qualifier says
    e = kit.loaded()
    t = {k: [dict(r) for r in v] for k, v in e.tables.items()}
    for r in t["pre"]:
        if r["adsh"] == kit.TSLA and r["report"] == "9" and r["line"] == "20":
            r["plabel"] = f"Purchases of property and equipment, {cue} {tail}"
    got = _select(kit, e.live["Capex"], tables=t)
    assert [ln["line"] for ln in got] == [20]
    assert got[0]["value"] == pytest.approx(8_527e6, rel=REL)
