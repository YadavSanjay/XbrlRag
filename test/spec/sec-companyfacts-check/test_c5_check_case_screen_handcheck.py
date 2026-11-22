"""C5: ``check_case`` for screens and the hand-check kinds.

Oracle: requirement 7 of docs/specs/sec-companyfacts-check.md. Case dicts copy the shape of
test/eval/suites/sql_derived_v1.json (``expected_rows`` of ``{"entity", "value"}``,
``expected_fact_ids``, ``fy``, ``top_n``) with synthetic values; documents and records are
built by hand so every expected item is read off them.
"""

from __future__ import annotations

from collections import Counter

import pytest

from src.eval.sec_check.core import check_case

# One synthetic filing per filer and fiscal year.
ACC = {
    ("AAPL", 2024): "0000320193-24-000123",
    ("MSFT", 2024): "0000950170-24-087843",
    ("MSFT", 2023): "0000950170-23-035122",
    ("NVDA", 2024): "0001045810-24-000029",
    ("META", 2024): "0001326801-25-000017",
}
# Fiscal-year periods per filer, (start, end).
PERIOD = {
    ("AAPL", 2024): ("2023-10-01", "2024-09-28"),
    ("MSFT", 2024): ("2023-07-01", "2024-06-30"),
    ("MSFT", 2023): ("2022-07-01", "2023-06-30"),
    ("NVDA", 2024): ("2023-01-30", "2024-01-28"),
    ("META", 2024): ("2024-01-01", "2024-12-31"),
}

# (fact_id, ticker, fy, us-gaap local name, value); every one has a matching SEC entry.
_FACTS = [
    (201, "AAPL", 2024, "Revenues", 391035e6),
    (202, "MSFT", 2024, "Revenues", 245122e6),
    (203, "MSFT", 2023, "Revenues", 211915e6),
    (204, "NVDA", 2024, "Revenues", 60922e6),
    (211, "AAPL", 2024, "OperatingIncomeLoss", 123216e6),
    (212, "MSFT", 2024, "OperatingIncomeLoss", 109433e6),
    (213, "META", 2024, "Revenues", 164501e6),
    (214, "META", 2024, "OperatingIncomeLoss", 69380e6),
]


def _world(kit, dimensional_ids=()):
    """Our records and one companyfacts document per filer, all facts matching."""
    records, by_cik = [], {}
    for fact_id, ticker, fy, local, value in _FACTS:
        start, end = PERIOD[(ticker, fy)]
        accn = ACC[(ticker, fy)]
        records.append(kit.fact(fact_id, concept=f"{kit.US_GAAP}:{local}", value=value,
                                accession_no=accn, period_start=start, period_end=end,
                                ticker=ticker, dimensional=fact_id in dimensional_ids))
        cik = kit.CIKS[ticker]
        units = by_cik.setdefault(cik, {}).setdefault(local, {}).setdefault("USD", [])
        units.append(kit.entry(value, accn, end, start, fy=fy))
    docs = {cik: kit.companyfacts(cik, f"Synthetic {cik}", {"us-gaap": concepts})
            for cik, concepts in by_cik.items()}
    return records, docs


def _screen(case_id, rows, fact_ids, concept="Revenue"):
    return {
        "id": case_id,
        "question": "List the top covered companies in fiscal 2024, ranked highest first.",
        "kind": "screen",
        "suite": "sql_derived_v1",
        "expected_rows": [{"entity": t, "value": v} for t, v in rows],
        "top_n": len(rows),
        "fy": 2024,
        "concept": concept,
        "expected_fact_ids": list(fact_ids),
        "fact_id_classes": [[i] for i in fact_ids],
        "origin": "analyst_queries.sql#1b",
        "rel_tol": 0.0001,
    }


# A non-metric screen: AAPL direct (201), MSFT yoy (202 FY2024, 203 FY2023), NVDA direct
# (204). Hand calculation for MSFT: (245122e6 - 211915e6) / |211915e6|.
_MSFT_YOY = (245122e6 - 211915e6) / abs(211915e6)
_MIXED_ROWS = [("AAPL", 391035e6), ("MSFT", _MSFT_YOY), ("NVDA", 60922e6)]
_MIXED_IDS = [201, 202, 203, 204]


def _assert_items(items, expected):
    """``expected``: ``[(label, expected, sec_value | None, agree)]`` in order."""
    assert [it["label"] for it in items] == [e[0] for e in expected]
    # tolerance: hand-computed closed forms vs the implementation's; rel=1e-12 float idiom
    assert [it["expected"] for it in items] == pytest.approx([e[1] for e in expected],
                                                             rel=1e-12)
    for it, (_, _, sec, agree) in zip(items, expected, strict=True):
        if sec is None:
            assert it["sec_value"] is None
        else:
            assert it["sec_value"] == pytest.approx(sec, rel=1e-12)
        assert it["agree"] is agree


@pytest.mark.criterion("C5")
def test_mixed_screen_has_one_item_per_row_in_order(kit):
    # oracle: requirement 7 — one item per expected_rows row, in order, labelled by entity;
    # a row with 1 fact takes its sec_value, a row with 2 facts is yoy (current = later
    # period_end); every fact matches, so every item agrees and the case is a match
    records, docs = _world(kit)
    source = kit.FakeSource(docs)
    case = _screen("aq-1b-revenue-screen:fy=2024", _MIXED_ROWS, _MIXED_IDS)
    got = check_case(case, kit.FakeStore(records), source)
    assert got["kind"] == "screen"
    _assert_items(got["items"], [("AAPL", 391035e6, 391035e6, True),
                                 ("MSFT", _MSFT_YOY, _MSFT_YOY, True),
                                 ("NVDA", 60922e6, 60922e6, True)])
    assert got["status"] == "match"
    assert {fc["fact_id"] for fc in got["facts"]} == set(_MIXED_IDS)
    assert len(got["facts"]) == 4
    assert all(n == 1 for n in Counter(source.calls).values())
    assert set(source.calls) == {kit.CIKS["AAPL"], kit.CIKS["MSFT"], kit.CIKS["NVDA"]}


@pytest.mark.criterion("C5")
def test_metric_screen_rows_use_metric_components(kit):
    # oracle: requirement 7 — a metric parameter means each row's roles come from
    # metric_components(entity, fy, metric); hand calculation, OperatingMarginPct =
    # operating_income / revenue: MSFT 109433/245122, META 69380/164501, AAPL 123216/391035
    msft, meta, aapl = 109433e6 / 245122e6, 69380e6 / 164501e6, 123216e6 / 391035e6
    records, docs = _world(kit)
    store = kit.FakeStore(records, {
        ("MSFT", 2024, "OperatingMarginPct"): {"revenue": 202, "operating_income": 212},
        ("META", 2024, "OperatingMarginPct"): {"revenue": 213, "operating_income": 214},
        ("AAPL", 2024, "OperatingMarginPct"): {"revenue": 201, "operating_income": 211},
    })
    case = _screen("aq-7b-margin-screen:fy=2024-metric=OperatingMarginPct",
                   [("MSFT", msft), ("META", meta), ("AAPL", aapl)],
                   [202, 212, 213, 214, 201, 211], concept="OperatingMarginPct")
    got = check_case(case, store, kit.FakeSource(docs))
    _assert_items(got["items"], [("MSFT", msft, msft, True), ("META", meta, meta, True),
                                 ("AAPL", aapl, aapl, True)])
    assert got["status"] == "match"
    assert sorted(store.component_calls) == sorted([("MSFT", 2024, "OperatingMarginPct"),
                                                    ("META", 2024, "OperatingMarginPct"),
                                                    ("AAPL", 2024, "OperatingMarginPct")])


@pytest.mark.criterion("C5")
def test_screen_row_value_disagreeing_is_a_mismatch(kit):
    # oracle: requirement 7 — the NVDA row expects 61000e6 but the SEC value is 60922e6
    # (|diff| = 78e6 >> 1e-9 rel): that item does not agree, so the case is a mismatch
    records, docs = _world(kit)
    rows = [("AAPL", 391035e6), ("MSFT", _MSFT_YOY), ("NVDA", 61000e6)]
    got = check_case(_screen("aq-1b-revenue-screen:fy=2024", rows, _MIXED_IDS),
                     kit.FakeStore(records), kit.FakeSource(docs))
    _assert_items(got["items"], [("AAPL", 391035e6, 391035e6, True),
                                 ("MSFT", _MSFT_YOY, _MSFT_YOY, True),
                                 ("NVDA", 61000e6, 60922e6, False)])
    assert got["status"] == "mismatch"


@pytest.mark.criterion("C5")
def test_screen_with_dimensional_fact_is_hand_check(kit):
    # oracle: requirement 7 — "a screen with any dimensional fact gives hand_check, with
    # its fact checks kept": 204 is dimensional, the others match
    records, docs = _world(kit, dimensional_ids={204})
    got = check_case(_screen("aq-1b-revenue-screen:fy=2024", _MIXED_ROWS, _MIXED_IDS),
                     kit.FakeStore(records), kit.FakeSource(docs))
    assert got["status"] == "hand_check"
    statuses = {fc["fact_id"]: fc["status"] for fc in got["facts"]}
    assert statuses == {201: "match", 202: "match", 203: "match", 204: "dimensional"}


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("kind", ["dimensional", "breakdown"])
def test_hand_check_kinds_touch_neither_store_nor_source(kit, kind):
    # oracle: requirement 7 — dimensional and breakdown give hand_check, reason
    # "dimensional", no facts, no items, and neither the store nor the source is called
    records, docs = _world(kit)
    store, source = kit.FakeStore(records), kit.FakeSource(docs)
    if kind == "dimensional":
        case = {"id": "aq-3d-segment-member-lookup:fy=2024-segment=Europe_Segment-ticker=AAPL",
                "question": "How much revenue did Apple report for Europe Segment in 2024?",
                "kind": "dimensional", "suite": "sql_derived_v1", "expected_value": 101e9,
                "ticker": "AAPL", "fy": 2024, "concept": "Revenue", "unit": "USD",
                "expected_fact_ids": [201], "fact_id_classes": [[201]],
                "origin": "analyst_queries.sql#3d", "rel_tol": 0.0001}
    else:
        case = {"id": "aq-3a-product-segment-breakdown:fy=2024-ticker=AAPL",
                "question": "Break down Apple's fiscal 2024 revenue by product segment.",
                "kind": "breakdown", "suite": "sql_derived_v1",
                "expected_rows": [{"entity": "iPhone", "value": 201e9},
                                  {"entity": "Mac", "value": 30e9}],
                "ticker": "AAPL", "fy": 2024, "concept": "Revenue",
                "expected_fact_ids": [201, 211], "fact_id_classes": [[201], [211]],
                "origin": "analyst_queries.sql#3a", "rel_tol": 0.0001,
                "order_sensitive": False}
    got = check_case(case, store, source)
    assert got["case_id"] == case["id"]
    assert got["kind"] == kind
    assert got["status"] == "hand_check"
    assert got["reason"] == "dimensional"
    assert got["facts"] == []
    assert got["items"] == []
    assert store.called is False
    assert source.calls == []


@pytest.mark.criterion("C5")
def test_row_without_cited_fact_is_unverifiable(kit):
    # oracle: requirement 7 — GOOGL has no cited fact: its row's sec_value is None, it does
    # not agree, and with every fact matching the reason is "no_value"
    records, docs = _world(kit)
    rows = [("AAPL", 391035e6), ("GOOGL", 350018e6)]
    got = check_case(_screen("aq-1b-revenue-screen:fy=2024", rows, [201]),
                     kit.FakeStore(records), kit.FakeSource(docs))
    _assert_items(got["items"], [("AAPL", 391035e6, 391035e6, True),
                                 ("GOOGL", 350018e6, None, False)])
    assert got["status"] == "unverifiable"
    assert got["reason"] == "no_value"


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@pytest.mark.parametrize("dimensional_ids", [(), (204,)])
def test_reversing_cited_fact_order_keeps_the_items(kit, dimensional_ids):
    # oracle: requirement 7 — rows are matched to facts by ticker and yoy picks current by
    # period_end, so the order of expected_fact_ids cannot change the items; the forward
    # run is also pinned to the hand-derived items
    records, docs = _world(kit, dimensional_ids=set(dimensional_ids))
    forward = check_case(_screen("aq-1b-revenue-screen:fy=2024", _MIXED_ROWS, _MIXED_IDS),
                         kit.FakeStore(records), kit.FakeSource(docs))
    backward = check_case(
        _screen("aq-1b-revenue-screen:fy=2024", _MIXED_ROWS, list(reversed(_MIXED_IDS))),
        kit.FakeStore(records), kit.FakeSource(docs))
    if not dimensional_ids:
        _assert_items(forward["items"], [("AAPL", 391035e6, 391035e6, True),
                                         ("MSFT", _MSFT_YOY, _MSFT_YOY, True),
                                         ("NVDA", 60922e6, 60922e6, True)])
    assert len(forward["items"]) in (0, 3)
    assert forward["status"] == ("hand_check" if dimensional_ids else "match")
    assert backward["items"] == forward["items"]
    assert backward["status"] == forward["status"]
