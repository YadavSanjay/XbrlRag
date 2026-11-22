"""C2: the slots a v2 case's key lists — its inputs, then its disclosed readings."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.grounding_v2 import v2_grounding_slots

QCOM = 804328
ORCL = 1341439
COST = 909832
FIELDS = ("role", "slot", "cik", "concept", "unit", "period_start", "period_end", "value",
          "decimals", "accession", "bind_accession", "defined")


def _slot(*values):
    return dict(zip(FIELDS, values, strict=True))


@pytest.mark.criterion("C2")
def test_lookup_with_a_cover_page_alternate(gb):
    # oracle: requirement 3 written out field by field for a hand-built QCOM-like case
    key_input = gb.inp(QCOM, "us-gaap:CommonStockSharesOutstanding", "shares", None,
                       "2025-09-28", 1074000000, decimals=None,
                       accession="0000804328-26-000086")
    alternate = gb.inp(QCOM, "dei:EntityCommonStockSharesOutstanding", "shares", None,
                       "2025-11-03", 1071000000, decimals=None,
                       accession="0000804328-25-000085", selection="cover_page")
    c = gb.case("c-shares", [key_input], alternates=[alternate])
    assert v2_grounding_slots(c) == [
        _slot("key", 0, QCOM, "us-gaap:CommonStockSharesOutstanding", "shares", None,
              "2025-09-28", 1074000000, None, "0000804328-26-000086", False, False),
        _slot("reading", 0, QCOM, "dei:EntityCommonStockSharesOutstanding", None, None,
              "2025-11-03", 1071000000, None, "0000804328-25-000085", False, False),
    ]


@pytest.mark.criterion("C2")
def test_point_in_time_binds_its_accession(gb):
    # oracle: requirement 3 — the shape:point_in_time tag sets bind_accession
    i = gb.inp(310158, "us-gaap:MarketableSecuritiesCurrent", "USD", None, "2023-12-31",
               252000000, accession="0001628280-25-007732", selection="as_reported")
    pit = v2_grounding_slots(gb.case("c-pit", [i], shape="point_in_time"))
    plain = v2_grounding_slots(gb.case("c-lookup", [i], shape="lookup"))
    assert [s["bind_accession"] for s in pit] == [True]
    assert [s["bind_accession"] for s in plain] == [False]
    assert pit[0]["period_start"] is None
    assert pit[0]["decimals"] == -6


@pytest.mark.criterion("C2")
def test_ratio_inputs_in_order_and_an_unattached_reading(gb):
    # oracle: requirement 3 — two key slots 0 and 1; a reading whose date matches no input of
    # a multi-input case serves none; the CIK of a reading falls back to case.cik
    num = gb.inp(COST, "us-gaap:ProfitLoss", "USD", "2024-09-02", "2025-08-31", 8099000000)
    den = gb.inp(COST, "us-gaap:Revenues", "USD", "2024-09-02", "2025-08-31", 275235000000)
    reading = {"reading": "note_total", "concept": "us-gaap:LongTermDebt",
               "period_end": "2025-08-31", "value": 5, "accession": "A"}
    other = {"reading": "x", "concept": "us-gaap:LongTermDebt", "period_end": "2024-09-01",
             "value": 6, "accession": "B"}
    c = gb.case("c-ratio", [num, den], shape="compute.ratio", kind="compute", cik=COST,
                alt_readings=[reading, other])
    slots = v2_grounding_slots(c)
    assert [(s["role"], s["slot"], s["concept"]) for s in slots] == [
        ("key", 0, "us-gaap:ProfitLoss"),
        ("key", 1, "us-gaap:Revenues"),
        ("reading", 0, "us-gaap:LongTermDebt"),  # same cik and period_end as input 0
        ("reading", None, "us-gaap:LongTermDebt"),
    ]
    assert all(s["cik"] == COST for s in slots)


@pytest.mark.criterion("C2")
def test_trend_row_reading_serves_its_year_and_defined_inputs(gb):
    # oracle: requirement 3 — s4: inputs are defined; a row reading serves the input with its
    # period_end (ORCL-like deferred revenue, current line presented only)
    years = [("2023-05-31", 9000000000), ("2024-05-31", 10546000000),
             ("2025-05-31", 11000000000)]
    inputs = [gb.inp(ORCL, "s4:deferred_revenue_total", "USD", None, end, v, decimals=None)
              for end, v in years]
    rows = [{"entity": f"FY{end[:4]}", "period_end": end, "value": v} for end, v in years]
    rows[1]["alt_readings"] = [{"reading": "current_presented",
                                "concept": "us-gaap:ContractWithCustomerLiabilityCurrent",
                                "period_end": "2024-05-31", "value": 9313000000,
                                "accession": "0000950170-24-075605"}]
    c = gb.case("c-trend", inputs, shape="trend", kind="breakdown", cik=ORCL, rows=rows)
    slots = v2_grounding_slots(c)
    assert [(s["role"], s["slot"], s["defined"]) for s in slots] == [
        ("key", 0, True), ("key", 1, True), ("key", 2, True), ("reading", 1, False)]
    assert slots[3] == _slot(
        "reading", 1, ORCL, "us-gaap:ContractWithCustomerLiabilityCurrent", None, None,
        "2024-05-31", 9313000000, None, "0000950170-24-075605", False, False)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(st.permutations(range(4)))
def test_permuting_inputs_permutes_key_slots(gb, order):
    # oracle: requirement 3 — key slots follow the inputs in order, one per input
    inputs = [gb.inp(1000 + n, "us-gaap:Revenues", "USD", "2025-01-01", "2025-12-31",
                     10 ** 9 * (n + 1)) for n in range(4)]
    base = v2_grounding_slots(gb.case("c-a", inputs, shape="screen.top", kind="screen"))
    permuted = v2_grounding_slots(gb.case("c-b", [inputs[k] for k in order],
                                          shape="screen.top", kind="screen"))
    assert [s["slot"] for s in permuted] == [0, 1, 2, 3]
    assert [{**s, "slot": None} for s in permuted] == [{**base[k], "slot": None} for k in order]
    assert [s["cik"] for s in permuted] == [1000 + k for k in order]
