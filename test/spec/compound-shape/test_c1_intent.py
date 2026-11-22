"""C1: the ``compound`` intent kind validates per requirement 1's table, has the shapes
``compound.rank_by_ratio`` / ``compound.leader_growth``, and keeps eval-intents' id rule.

Oracle: the requirement 1 table, applied by hand; the eval-intents C3 id formula restated
with ``hashlib`` and ``json`` in conftest (``oracle_id``).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.intents import Intent, intent_id, intent_shape

RBR = {"kind": "compound", "operation": "rank_by_ratio", "universe": "big", "n": 3,
       "fiscal_years": [2024], "metrics": ["Revenue", "operating_margin"]}
LG = {"kind": "compound", "operation": "leader_growth", "universe": "big", "n": 3,
      "fiscal_years": [2023, 2024], "metrics": ["Revenue", "NetIncome"]}

# One field changed per entry; each breaks exactly one row of the requirement 1 table.
RBR_BAD = [
    {"tickers": ["DEVA"]},                                   # tickers must be empty
    {"fiscal_years": [2023, 2024]},                          # exactly 1 year
    {"fiscal_years": []},
    {"metrics": ["Revenue"]},                                # exactly 2 metrics
    {"metrics": ["Revenue", "operating_margin", "net_margin"]},
    {"metrics": ["Revenue", "NetIncome"]},                   # R must be an S4 ratio id
    {"metrics": ["Revenue", "gross_margin"]},
    {"dimension": "segment"},                                # dimension must be None
    {"filing_year": 2025},                                   # filing_year must be None
    {"operation": "top"},                                    # unknown compound operation
    {"operation": None},                                     # operation required
    {"universe": None},                                      # universe required
    {"universe": ""},                                        # ... and non-empty
    {"n": None},                                             # n required
    {"n": 1},                                                # n >= 2
    {"n": 0},
]
LG_BAD = [
    {"tickers": ["DEVA"]},
    {"fiscal_years": [2024]},                                # exactly 2 years
    {"fiscal_years": [2022, 2024]},                          # consecutive
    {"fiscal_years": [2022, 2023, 2024]},
    {"metrics": ["Revenue"]},                                # exactly 2 metrics
    {"metrics": ["Revenue", "NetIncome", "OperatingIncome"]},
    {"dimension": "segment"},
    {"filing_year": 2025},
    {"operation": None},
    {"operation": "yoy_growth"},
    {"universe": None},
    {"universe": ""},
    {"n": None},
    {"n": 1},
]


@pytest.mark.criterion("C1")
def test_valid_compound_intents_and_their_shapes(kit):
    # oracle: requirement 1 — both examples satisfy every row of the table; the shape is
    # kind.operation, as for compute and screen
    for kw, shape in ((RBR, "compound.rank_by_ratio"), (LG, "compound.leader_growth")):
        intent = kit.make_intent(kw)
        assert intent_shape(intent) == shape
        assert intent.kind == "compound"
        assert intent.tickers == ()
        assert intent.metrics == tuple(kw["metrics"])  # order kept: ranking metric first
        assert intent.fiscal_years == tuple(kw["fiscal_years"])
        assert (intent.universe, intent.n) == ("big", 3)
    for ratio in kit.RATIO_IDS:
        kit.make_intent({**RBR, "metrics": ["NetIncome", ratio]})


@pytest.mark.criterion("C1")
def test_single_field_violations_raise(kit):
    # oracle: requirement 1 — "any violation raises ValueError, as for the other kinds";
    # the unbroken example must construct first, so a refusal of the whole kind is no pass
    kit.make_intent(RBR)
    kit.make_intent(LG)
    for base, bad in ((RBR, RBR_BAD), (LG, LG_BAD)):
        for change in bad:
            with pytest.raises(ValueError):
                Intent(**{**base, **change})


@pytest.mark.criterion("C1")
def test_compound_id_follows_the_c3_rule_and_old_ids_do_not_move(kit):
    # oracle: eval-intents C3 — "int-" + sha1(canonical nine-field JSON)[:12], computed in
    # conftest; the non-compound examples are eval-intents' fixture intents
    for kw in (RBR, LG):
        assert intent_id(kit.make_intent(kw)) == kit.oracle_id(kw)
    old = [
        {"kind": "lookup", "tickers": ["AAPL"], "fiscal_years": [2024], "metrics": ["revenue"]},
        {"kind": "compute", "tickers": ["NVDA"], "fiscal_years": [2024],
         "metrics": ["gross_profit", "revenue"], "operation": "ratio"},
        {"kind": "compute", "tickers": ["AAPL"], "fiscal_years": [2023, 2024],
         "metrics": ["revenue"], "operation": "yoy_growth"},
        {"kind": "screen", "fiscal_years": [2024], "metrics": ["revenue"], "operation": "top",
         "universe": "big_tech", "n": 3},
        {"kind": "point_in_time", "tickers": ["AAPL"], "fiscal_years": [2022],
         "metrics": ["revenue"], "filing_year": 2024},
    ]
    ids = {kit.oracle_id(kw) for kw in old}
    for kw in old:
        assert intent_id(Intent(**kw)) == kit.oracle_id(kw)
    # a compound intent's id differs from every non-compound one's (its kind differs)
    screen_twin = {**RBR, "kind": "screen", "operation": "top", "metrics": ["Revenue"]}
    assert intent_id(kit.make_intent(RBR)) not in ids | {kit.oracle_id(screen_twin)}


@pytest.mark.criterion("C1")
@given(year=st.integers(min_value=1990, max_value=2040),
       n=st.integers(min_value=-2, max_value=12),
       ratio=st.sampled_from(["operating_margin", "net_margin", "rd_intensity",
                              "current_ratio"]))
def test_n_and_years_property(kit, year, n, ratio):
    # oracle: requirement 1 — n must be an int >= 2 for both operations; leader_growth's
    # two years are consecutive (the ranking year the later); the id is the C3 rule
    rbr = {**RBR, "fiscal_years": [year], "n": n, "metrics": ["Revenue", ratio]}
    lg = {**LG, "fiscal_years": [year, year - 1], "n": n}
    if n >= 2:
        assert intent_id(kit.make_intent(rbr)) == kit.oracle_id(rbr)
        made = kit.make_intent(lg)
        assert made.fiscal_years == (year - 1, year)
        assert intent_id(made) == kit.oracle_id(lg)
    else:
        kit.make_intent({**rbr, "n": 2})
        for kw in (rbr, lg):
            with pytest.raises(ValueError):
                Intent(**kw)
