"""C4: set B's and H-X's Postgres stores read S1 candidates (requirements 1-2).

The database is the I/O boundary: ``FakeConn`` (conftest) returns every stored row of the filer
named by the ``ticker`` parameter and ignores every other predicate (spec Notes). AMD's
receivables at 2023-12-30 are reported by 0000002488-24-000012 ($5,376M, the FY2023 10-K) and
0000002488-25-000012 ($4,323M, the FY2024 10-K, filed 2025-02-05, reclassified): S1 by hand
names the latter (FSDS num of both agrees, conftest).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.core import resolve_case
from src.eval.set_b.hx_pg_store import PostgresHxKeyFactStore
from src.eval.set_b.pg_store import PostgresKeyFactStore

TAG = "AccountsReceivableNetCurrent"
S1_ACC = "0000002488-25-000012"
ORIG_ACC = "0000002488-24-000012"
END = "2023-12-30"
STORES = [PostgresKeyFactStore, PostgresHxKeyFactStore]


def _rows(kit):
    return kit.store_rows("AMD") + kit.store_rows("NVDA") + kit.nvda_dim_rows()


def _case(expected):
    return {"id": "amd-ar", "question": "-", "kind": "lookup", "ticker": "AMD", "fy": 2023,
            "expected": expected, "scale": "millions", "unit": "USD",
            "concept": "AccountsReceivable"}


# ---------------------------------------------------------------------------------- points
@pytest.mark.criterion("C4")
@pytest.mark.parametrize("store_cls", STORES, ids=["set_b", "hx"])
def test_the_store_returns_the_restating_filings_fact(kit, store_cls):
    # oracle: S1 by hand over the companyfacts entries in the module docstring; FSDS num
    # 0000002488-25-000012 AccountsReceivableNetCurrent ddate 20231231 = 4,323,000,000
    rows = _rows(kit)
    want = sorted(r["fact_id"] for r in kit.rows_of(rows, TAG, S1_ACC, END))
    assert want
    store = store_cls(conn=kit.FakeConn(rows))
    cands = store.facts_for("AMD", 2023)
    got = kit.flat(cands, TAG)
    assert sorted(c["fact_id"] for c in got) == want
    # amounts tagged in millions: half a million either way
    assert [c["value"] for c in got] == [pytest.approx(4_323e6, abs=5e5)] * len(got)
    nvda_ids = {r["fact_id"] for r in rows if r["ticker"] == "NVDA"}
    assert nvda_ids.isdisjoint(c["fact_id"] for c in cands)


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("store_cls", STORES, ids=["set_b", "hx"])
def test_a_key_from_the_latest_report_resolves_and_the_original_does_not(kit, store_cls):
    # oracle: S1 by hand — the key written from the latest report ($4,323M) resolves to the
    # restating filing's fact; one written from the original 10-K ($5,376M) finds nothing
    rows = _rows(kit)
    want = sorted(r["fact_id"] for r in kit.rows_of(rows, TAG, S1_ACC, END))
    store = store_cls(conn=kit.FakeConn(rows))
    latest = resolve_case(_case("4,323"), store)
    assert latest["status"] == "verified"
    assert latest["classes"] == [want]
    original = resolve_case(_case("5,376"), store)
    assert original["status"] == "not_found"
    assert sorted(r["fact_id"] for r in kit.rows_of(rows, TAG, ORIG_ACC, END)), \
        "the original fact is in the store"


# ------------------------------------------------------------------------------ property
@pytest.mark.criterion("C4")
@given(store_idx=st.sampled_from([0, 1]), order=st.randoms(use_true_random=False),
       extra=st.sampled_from(["AVGO", "TSLA", "COST"]))
def test_row_order_and_other_filers_never_change_facts_for(kit, store_idx, order, extra):
    # oracle: metamorphic — the store reads one filer's facts: shuffling the stored rows and
    # adding another filer's rows leaves the hand-checked AMD FY2023 receivables unchanged
    rows = _rows(kit) + kit.store_rows(extra)
    order.shuffle(rows)
    store = STORES[store_idx](conn=kit.FakeConn(rows))
    got = kit.flat(store.facts_for("AMD", 2023), TAG)
    want = sorted(r["fact_id"] for r in kit.rows_of(rows, TAG, S1_ACC, END))
    assert want
    assert sorted(c["fact_id"] for c in got) == want
