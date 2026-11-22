"""C3: a compound intent's ranking part is keyed exactly as the ``screen.top`` intent
(U, Y, A, n) would be; a screen drop drops it with the same reason, and fewer than n ranked
filers drop it as ``too_few_ranked_for_n``.

Oracle: requirement 4 of docs/specs/compound-shape.md; rankings read off the conftest
fixture table by hand, plus the metamorphic comparison with ``key_intent`` of the screen.
"""

from __future__ import annotations

import pytest

from src.eval.suites.intents import Intent
from src.eval.suites.v2_suite import IntentDroppedError, key_intent


def _row(t, v):
    return {"ticker": t, "value": v * 1_000_000, "fiscal_year": 2024, "period_end": "2024-12-31"}


REV24_ROWS = [_row("DEVA", 500), _row("DEVB", 400), _row("DEVC", 300)]
REV24_DISCLOSURES = {
    "universe": "big", "members": 5, "ranked": ["DEVA", "DEVB", "DEVC", "DEVD"],
    "excluded": [{"ticker": "DEVE", "reason": "currency"}],
    "period_ends": {t: "2024-12-31" for t in ("DEVA", "DEVB", "DEVC", "DEVD")},
    "disclose_period_ends": False,
}


@pytest.mark.criterion("C3")
def test_ranking_part_of_revenue_2024(kit):
    # oracle: fixture — FY2024 revenue DEVA 500, DEVB 400, DEVC 300, DEVD 200 (USD, all
    # ending 2024-12-31); DEVE reports EUR: excluded as currency (S6). Top 3 = A, B, C
    result = kit.build()
    for kw in (kit.rbr(2024, "Revenue", "operating_margin"), kit.lg(2024, "Revenue", "NetIncome")):
        rec = kit.case_of(result, kw)
        assert rec["kind"] == "screen"
        assert rec["expected_rows"] == REV24_ROWS
        assert rec["top_n"] == 3
        assert rec.get("order_sensitive", True) is True
        assert rec["unit"] == "USD"
        assert rec["disclosures"] == REV24_DISCLOSURES
        ranking = rec["parts"][0]
        assert ranking["part"] == "ranking"
        assert ranking["kind"] == "screen"
        assert ranking["expected_rows"] == REV24_ROWS
        assert ranking["top_n"] == 3
        assert ranking["disclosures"] == REV24_DISCLOSURES


@pytest.mark.criterion("C3")
def test_ranking_by_operating_income_names_the_untagged_member(kit):
    # oracle: fixture — FY2024 operating income DEVB 120, DEVA 100, DEVC 75, DEVD 10; DEVE's
    # inventory has no operating income: metric_not_tagged
    result = kit.build()
    rec = kit.case_of(result, kit.rbr(2024, "OperatingIncome", "operating_margin"))
    assert rec["expected_rows"] == [_row("DEVB", 120), _row("DEVA", 100), _row("DEVC", 75)]
    assert rec["disclosures"]["ranked"] == ["DEVB", "DEVA", "DEVC", "DEVD"]
    assert rec["disclosures"]["excluded"] == [{"ticker": "DEVE", "reason": "metric_not_tagged"}]


@pytest.mark.criterion("C3")
def test_fewer_than_n_ranked_drops_as_too_few_ranked_for_n(kit):
    # oracle: requirement 4 — universe small = DEVA, DEVB, ZZZ ranks 2 filers in every
    # (year, metric): the screen keeps them, the compound needs 3. Candidates by hand:
    # 2 years x 3 metrics = 6 rankings; x 4 ratio ids = 24, and 3 metrics x 2 others in
    # 2024 = 6 leader_growth
    result = kit.build(uni={"small": ["DEVA", "DEVB", "ZZZ"]})
    assert kit.records(result) == []
    assert kit.dropped(result, kit.RBR) == {"too_few_ranked_for_n": 24}
    assert kit.dropped(result, kit.LG) == {"too_few_ranked_for_n": 6}


@pytest.mark.criterion("C3")
def test_screen_drop_reason_carries_over(kit):
    # oracle: requirement 4 — universe tiny = DEVA, ZZZ ranks one filer: the screen drops it
    # as too_few_ranked (v2-suite-build), so every compound candidate drops the same way
    uni = {"tiny": ["DEVA", "ZZZ"]}
    screen = Intent("screen", operation="top", n=3, universe="tiny", fiscal_years=[2024],
                    metrics=["Revenue"])
    docs = kit.companyfacts_docs()
    with pytest.raises(IntentDroppedError) as err:
        key_intent(screen, kit.inventory(), uni, kit.sec(docs), kit.store(docs))
    assert err.value.args[0] == "too_few_ranked"
    result = kit.build(uni=uni)
    assert kit.records(result) == []
    assert kit.dropped(result, kit.RBR) == {"too_few_ranked": 24}
    assert kit.dropped(result, kit.LG) == {"too_few_ranked": 6}


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_every_ranking_equals_its_screen_key(kit):
    # oracle: requirement 4 — for every kept compound intent, rows, disclosures and the
    # leading key inputs are those of key_intent on screen.top (U, Y_rank, A, n)
    result = kit.build()
    kept = [o for o in result["intents"] if o["kind"] == "compound"]
    assert len(kept) == 15  # 9 rank_by_ratio + 6 leader_growth (see test_c8)
    docs = kit.companyfacts_docs()
    for obj in kept:
        screen = Intent("screen", operation="top", n=obj["n"], universe=obj["universe"],
                        fiscal_years=[obj["fiscal_years"][-1]], metrics=[obj["metrics"][0]])
        keyed = key_intent(screen, kit.inventory(), kit.universes(), kit.sec(docs),
                           kit.store(docs))
        for rec in kit.cases_of(result, obj):
            assert rec["expected_rows"] == keyed["rows"]
            assert rec["parts"][0]["expected_rows"] == keyed["rows"]
            assert rec["disclosures"] == keyed["disclosures"]
            assert rec["parts"][0]["disclosures"] == keyed["disclosures"]
            n_inputs = len(keyed["inputs"])
            assert rec["key"]["inputs"][:n_inputs] == keyed["inputs"]
            assert set(keyed["policies"]) <= set(rec["key"]["policies"])
