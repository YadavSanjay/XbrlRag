"""C3: a cited fact is the key's fact when its normalised identity and value agree."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.grounding_v2 import grade_v2_grounding

INTC = 50863
ACC = "0000050863-26-000011"


def _case(gb, decimals=-6):
    i = gb.inp(INTC, "us-gaap:OperatingIncomeLoss", "USD", "2023-01-01", "2023-12-30",
               93000000, decimals=decimals, accession=ACC)
    return gb.case("c-oi", [i])


def _fact(gb, **change):
    fields = {"cik": INTC, "local": "OperatingIncomeLoss", "unit": "USD",
              "start": "2023-01-01", "end": "2023-12-30", "value": 93000000,
              "dimensional": False}
    fields.update(change)
    return gb.rec(7, fields["cik"], fields["local"], fields["unit"], fields["start"],
                  fields["end"], fields["value"], ACC, dimensional=fields["dimensional"])


def _pr(g):
    return (g["precision"], g["recall"])


@pytest.mark.criterion("C3")
def test_the_key_fact_in_store_form_is_right(gb):
    # oracle: requirements 4-6 — the URI concept normalises to us-gaap:OperatingIncomeLoss
    g = grade_v2_grounding(_case(gb), [7], {7: _fact(gb)})
    assert _pr(g) == (1, 1)
    assert (g["right"], g["wrong"], g["unresolved"], g["evidenced"]) == ((7,), (), (), (0,))


@pytest.mark.criterion("C3")
def test_per_share_unit_normalises(gb):
    # oracle: requirement 4 — the store's shares*USD is companyfacts' USD/shares
    i = gb.inp(909832, "us-gaap:EarningsPerShareBasic", "USD/shares", "2022-08-29",
               "2023-09-03", 14.18, decimals=2)
    c = gb.case("c-eps", [i])
    f = gb.rec(3, 909832, "EarningsPerShareBasic", "shares*USD", "2022-08-29", "2023-09-03",
               14.18)
    assert _pr(grade_v2_grounding(c, [3], {3: f})) == (1, 1)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("change", [
    {"cik": 2488},
    {"local": "OperatingExpenses"},
    {"unit": "EUR"},
    {"start": "2022-12-31"},
    {"end": "2023-12-31"},
    {"dimensional": True},
    {"value": 93600000},
])
def test_one_wrong_field_makes_it_wrong(gb, change):
    # oracle: requirement 6, by hand — each single difference breaks the identity
    g = grade_v2_grounding(_case(gb), [7], {7: _fact(gb, **change)})
    assert _pr(g) == (0, 0)
    assert (g["right"], g["wrong"]) == ((), (7,))


@pytest.mark.criterion("C3")
def test_value_agreement_at_the_keys_decimals(gb):
    # oracle: requirement 5 — at decimals -6 the half-unit is 500,000; with no decimals the
    # values must agree to 1e-9 relative (93,000,001 is off by about 1.1e-8)
    def precision(c, value):
        return grade_v2_grounding(c, [7], {7: _fact(gb, value=value)})["precision"]

    assert precision(_case(gb), 93400000) == 1
    assert precision(_case(gb), 93600000) == 0
    assert precision(_case(gb, decimals=None), 93000000) == 1
    assert precision(_case(gb, decimals=None), 93000001) == 0


_PERTURB = st.sampled_from([
    ("cik", st.integers(1, 10**7).filter(lambda v: v != INTC)),
    ("local", st.sampled_from(["Revenues", "NetIncomeLoss", "OperatingIncomeLossX"])),
    ("unit", st.sampled_from(["EUR", "shares", "USD/shares", "pure"])),
    ("start", st.sampled_from([None, "2022-01-01", "2023-01-02"])),
    ("end", st.sampled_from(["2023-12-29", "2023-12-31", "2024-12-28"])),
    ("value", st.floats(-1e12, 1e12).filter(lambda v: abs(v - 93000000) > 500000)),
    ("dimensional", st.just(True)),
])


@pytest.mark.criterion("C3")
@given(data=st.data())
def test_any_single_perturbation_is_wrong(gb, data):
    # oracle: requirement 6 — the identity is a conjunction, so breaking one field fails it
    field, strategy = data.draw(_PERTURB)
    value = data.draw(strategy)
    g = grade_v2_grounding(_case(gb), [7], {7: _fact(gb, **{field: value})})
    assert _pr(g) == (0, 0)
    assert g["wrong"] == (7,)
