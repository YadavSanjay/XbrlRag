"""C4: the stores, the seam's default and the build's label recorder wire it
(docs/specs/set-b-dimensional-s1.md, R3-R4).

The database is the I/O boundary: ``FakeConn`` (conftest) returns every stored row of the filer
named by the ``ticker`` parameter, ignores every other predicate, and returns the multidim
``dimensions`` column only when the query names it (spec Notes). Expected candidates are named by
hand from FSDS rows, as in C1 and C2.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.base import KeyFactStore
from src.eval.set_b.core import build_suite, resolve_case
from src.eval.set_b.hx_pg_store import PostgresHxKeyFactStore
from src.eval.set_b.pg_store import PostgresKeyFactStore

REV = "Revenues"
STORES = [PostgresKeyFactStore, PostgresHxKeyFactStore]
NOTEBOOK = ("BusinessSegments=ClientComputingGroup;ConsolidatedEntities=OperatingSegments;"
            "ProductOrService=Notebook;")


def _ids(cands):
    return sorted(int(c["fact_id"]) for c in cands)


class _OnlyFactsFor(KeyFactStore):
    """A store that knows no breakdown rule (as set-b-tooling's and hx-authoring's fakes)."""

    def __init__(self, facts):
        self.facts = facts
        self.calls = []

    def facts_for(self, ticker, fiscal_year):
        self.calls.append((ticker, fiscal_year))
        return copy.deepcopy(self.facts)


# ---------------------------------------------------------------------------------- points
@pytest.mark.criterion("C4")
@pytest.mark.parametrize("store_cls", STORES, ids=["set_b", "hx"])
def test_the_stores_read_both_rules_from_every_filing(kit, store_cls):
    # oracle: FSDS num and the rule by hand — facts_for answers Intel FY2022 notebook revenue
    # (a three-dimension cell) at the FY2023 10-K's 18,781, which needs the cell's full
    # dimension set; breakdown_facts_for gives the FY2026 10-K's FY2024 split; no other
    # filer's row comes back
    rows = kit.store_rows() + kit.other_filer_rows(kit.store_rows("NVDA"), "ZZZZ")
    conn = kit.FakeConn(rows)
    store = store_cls(conn=conn)
    nb = kit.find(kit.INTC_23, "RevenueFromContractWithCustomerExcludingAssessedTax", NOTEBOOK,
                  2022)
    intc = kit.by_id(store.facts_for("INTC", 2022))
    assert nb["fact_id"] in intc
    # amounts tagged in millions: half a million either way
    assert intc[nb["fact_id"]]["value"] == pytest.approx(18_781e6, abs=5e5)
    split = store.breakdown_facts_for("NVDA", 2024)
    compute = kit.find(kit.NVDA_26, REV, "ProductOrService=Compute;", 2024)
    assert compute["fact_id"] in kit.by_id(split)
    assert {r["accession_no"] for r in kit.store_rows() if r["fact_id"] in kit.by_id(split)} \
        == {kit.NVDA_26}
    assert all(c["fact_id"] < 9_000_000 for c in split + store.facts_for("NVDA", 2024))
    assert conn.queries
    for _sql, params in conn.queries:
        assert isinstance(params, Mapping) and params.get("ticker") in ("INTC", "NVDA")


@pytest.mark.criterion("C4")
def test_the_seams_default_is_facts_for(kit):
    # oracle: requirement 3 — a store without a breakdown rule answers breakdown_facts_for
    # with facts_for's candidates, so a breakdown resolves as before against it
    cands = [{"fact_id": 7, "concept": "us-gaap:Revenues", "concept_family": "Revenue",
              "unit": "USD", "value": 10e6, "period_start": "2024-01-01",
              "period_end": "2024-12-31", "dimensional": True, "member_label": "Alpha"},
             {"fact_id": 8, "concept": "us-gaap:Revenues", "concept_family": "Revenue",
              "unit": "USD", "value": 20e6, "period_start": "2024-01-01",
              "period_end": "2024-12-31", "dimensional": True, "member_label": "Beta"}]
    store = _OnlyFactsFor(cands)
    assert store.breakdown_facts_for("MSFT", 2024) == cands
    case = {"id": "b", "question": "-", "kind": "breakdown", "ticker": "MSFT", "fy": 2024,
            "unit": "USD", "rows": [{"member": "Alpha", "expected": "10", "scale": "millions"},
                                    {"member": "Beta", "expected": "20", "scale": "millions"}]}
    got = resolve_case(case, _OnlyFactsFor(cands))
    assert got["status"] == "verified"
    assert got["classes"] == [[7], [8]]


@pytest.mark.criterion("C4")
def test_the_build_records_the_breakdown_members_labels(kit):
    # oracle: FSDS num — building set B through the CLI's label recorder labels a breakdown
    # row with its breakdown member's label (the FY2026 10-K's "Compute", "Networking"), read
    # from breakdown_facts_for, not facts_for (which does not hold them)
    cli = kit.load_set_b_cli()
    inner = PostgresKeyFactStore(conn=kit.FakeConn(kit.store_rows()))
    rec = cli.LabelRecordingStore(inner)
    split = rec.breakdown_facts_for("NVDA", 2024)
    assert _ids(split) == _ids(inner.breakdown_facts_for("NVDA", 2024))
    compute = kit.find(kit.NVDA_26, REV, "ProductOrService=Compute;", 2024)["fact_id"]
    assert rec.labels[compute] == "Compute"
    case = {"id": "b", "question": "q", "kind": "breakdown", "ticker": "NVDA", "fy": 2024,
            "unit": "USD", "concept": "Revenue",
            "rows": [{"member": "Compute", "expected": "38,950", "scale": "millions"},
                     {"member": "Networking", "expected": "8,575", "scale": "millions"}]}
    rec2 = cli.LabelRecordingStore(PostgresKeyFactStore(conn=kit.FakeConn(kit.store_rows())))
    res = resolve_case(case, rec2)
    assert res["status"] == "verified"
    suite = build_suite([case], [res], rec2.labels)
    assert [r["entity"] for r in suite["cases"][0]["expected_rows"]] == ["Compute", "Networking"]


# ------------------------------------------------------------------------------ properties
@pytest.mark.criterion("C4")
@given(store_idx=st.sampled_from([0, 1]), order=st.randoms(use_true_random=False),
       year=st.sampled_from([("NVDA", 2023), ("NVDA", 2024), ("NVDA", 2025), ("INTC", 2022)]))
def test_row_order_and_other_filers_never_change_the_stores(kit, store_idx, order, year):
    # oracle: metamorphic, with the FY2024 split pinned by hand — shuffling the stored rows and
    # adding another filer's copies leaves both store reads unchanged
    ticker, fy = year
    plain = STORES[store_idx](conn=kit.FakeConn(kit.store_rows()))
    rows = kit.store_rows() + kit.other_filer_rows(kit.store_rows(), "ZZZZ")
    order.shuffle(rows)
    mixed = STORES[store_idx](conn=kit.FakeConn(rows))
    assert mixed.facts_for(ticker, fy) == plain.facts_for(ticker, fy)
    assert mixed.breakdown_facts_for(ticker, fy) == plain.breakdown_facts_for(ticker, fy)
    split = {kit.find(kit.NVDA_26, REV, f"ProductOrService={p};", 2024)["fact_id"]
             for p in ("Compute", "Networking")}
    assert split <= set(_ids(mixed.breakdown_facts_for("NVDA", 2024)))
