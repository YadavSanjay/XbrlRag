"""Spec tests for breakdown-op: a deterministic single-filer breakdown and one rule for
"the breakdown" (docs/specs/breakdown-op.md).

`choose_partition` is pure. `breakdown()` and the suite builder use fake fact stores
(the DB is an I/O boundary): `psycopg2.connect` is replaced for the former, and the
builder takes a connection object directly.
"""

from __future__ import annotations

import itertools

import psycopg2
import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.numeric import all_fact_ids, all_numeric_values
from src.eval.suites.sql_derived import SqlDerivedBuilder
from src.retriever.xbrl.plan_validator import PlanValidator
from src.retriever.xbrl.xbrl_factstore_breakdown import (
    XbrlFactstoreBreakdown,
    choose_partition,
)

# Real FY2025 values from the 2026-10-14 corpus (USD).
AAPL_PRODUCT = [  # ProductOrServiceAxis: Product/Service AND the five product lines
    ("Product [Member]", 307_003_000_000.0),
    ("iPhone [Member]", 209_586_000_000.0),
    ("Service [Member]", 109_158_000_000.0),
    ("Wearables, Home and Accessories [Member]", 35_686_000_000.0),
    ("Mac [Member]", 33_708_000_000.0),
    ("iPad [Member]", 28_023_000_000.0),
]
AAPL_TOTAL = 416_161_000_000.0
AMZN_SEGMENTS = [
    ("North America Segment [Member]", 426_305_000_000.0),
    ("International Segment [Member]", 161_894_000_000.0),
    ("Amazon Web Services Segment [Member]", 128_725_000_000.0),
]
AMZN_TOTAL = 716_924_000_000.0
AMD_GEO_MISSING_ONE = [  # US + CN + TW + SG; the fifth member (6.055B) left out
    ("US", 11_363_000_000.0),
    ("CN", 7_751_000_000.0),
    ("TW", 5_186_000_000.0),
    ("SG", 4_284_000_000.0),
]
AMD_TOTAL = 34_639_000_000.0


def _brute(members, total):
    """The C1 rule written out by brute force (exact sums: integer test values)."""
    seen, distinct = set(), []
    for i, (label, value) in enumerate(members):
        if (label, value) not in seen:
            seen.add((label, value))
            distinct.append(i)
    if total is None or total <= 0 or len(distinct) > 20:
        return None
    best = None
    for k in range(len(distinct), 1, -1):
        for combo in itertools.combinations(distinct, k):
            if sum(members[i][1] for i in combo) == total:
                key = (sorted(members[i][0] for i in combo), list(combo))
                if best is None or key[0] < best[0]:
                    best = key
        if best is not None:
            return best[1]
    return None


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_finest_partition_point_cases():
    # oracle: the definition, by hand. Apple: Product + Service = total and the five lines
    # = total; the five win. Amazon: the three segments = total. AMD with a member
    # missing reaches 28.58B of 34.64B: no partition.
    assert choose_partition(AAPL_PRODUCT, AAPL_TOTAL) == [1, 2, 3, 4, 5]
    assert choose_partition(AMZN_SEGMENTS, AMZN_TOTAL) == [0, 1, 2]
    assert choose_partition(AMD_GEO_MISSING_ONE, AMD_TOTAL) is None


@pytest.mark.criterion("C1")
def test_partition_edge_rules():
    # oracle: the definition — size >= 2, duplicates once, tie-breaks, guards
    assert choose_partition([("A", 10.0)], 10.0) is None  # a lone member is not a split
    assert choose_partition(AMZN_SEGMENTS, None) is None
    assert choose_partition(AMZN_SEGMENTS, 0.0) is None
    # Apple FY2024 tags Service twice; the copy counts once (first index).
    dup = [("Product", 294_866_000_000.0), ("Service", 96_169_000_000.0),
           ("Service", 96_169_000_000.0)]
    assert choose_partition(dup, 391_035_000_000.0) == [0, 1]
    # Equal size and both exact: the smaller sorted labels win.
    tie = [("D", 4.0), ("A", 5.0), ("E", 4.0), ("B", 3.0), ("F", 2.0), ("C", 2.0)]
    assert choose_partition(tie, 10.0) == [1, 3, 5]
    # Equal size: the smaller deviation wins (5e-5 relative is inside rel_tol).
    dev = [("R", 7.0), ("S", 3.0005), ("P", 6.0), ("Q", 4.0)]
    assert choose_partition(dev, 10.0) == [2, 3]
    # More than 20 distinct members: not attempted.
    many = [(f"m{i}", 1.0 + i) for i in range(21)]
    assert choose_partition(many, 1.0 + 2.0) is None


members_st = st.lists(
    st.tuples(st.sampled_from("ABCDEFGHIJ"), st.integers(min_value=1, max_value=60)),
    min_size=1, max_size=9, unique_by=lambda m: m[0],
)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(members_st, st.data(), st.randoms(use_true_random=False))
def test_partition_rule_for_any_members(members, data, rnd):
    # oracle: brute force over every subset (exact integer sums); relabelling order of the
    # input changes the indices, not the chosen members
    members = [(lbl, float(v)) for lbl, v in members]
    pick = data.draw(st.lists(st.sampled_from(range(len(members))), unique=True))
    total = float(sum(members[i][1] for i in pick)) or 1.0
    got = choose_partition(members, total)
    assert got == _brute(members, total)
    shuffled = list(members)
    rnd.shuffle(shuffled)
    again = choose_partition(shuffled, total)
    def as_set(idx, ms):
        return None if idx is None else sorted(ms[i] for i in idx)

    assert as_set(again, shuffled) == as_set(got, members)


# --------------------------------------------------------------------------- C2 fake DB
class _Cursor:
    def __init__(self, store, as_dict):
        self._store = store
        self._as_dict = as_dict
        self._rows: list = []

    def execute(self, sql, params=None):
        if "to_regclass" in sql:
            rows = [{"present": False}]
        elif "mv_segment_matrix" in sql or "mv_geographic_facts" in sql:
            rows = [{"member_label": lbl, "value": v, "fact_id": 5000 + i, "unit": "USD"}
                    for i, (lbl, v) in enumerate(self._store["members"])]
        elif "mv_financial_facts" in sql:
            t = self._store["total"]
            rows = [] if t is None else [{"value": t, "fact_id": 4999, "unit": "USD"}]
        else:
            rows = []
        self._rows = rows if self._as_dict else [tuple(r.values()) for r in rows]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def mogrify(self, sql, params=None):
        return sql.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, store):
        self._store = store

    def cursor(self, cursor_factory=None):
        return _Cursor(self._store, as_dict=cursor_factory is not None)

    def get_dsn_parameters(self):
        return {"dbname": "fake_breakdown_db"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def _run(monkeypatch, members, total, axis="product", **step_extra):
    monkeypatch.setattr(psycopg2, "connect", lambda **kw: _Conn({"members": members,
                                                                 "total": total}))
    step = {"id": "b1", "op": "breakdown", "cik": "0000320193", "fy": 2025,
            "concept_family": "Revenue", "axis": axis, **step_extra}
    return XbrlFactstoreBreakdown(db_conn_params={}).breakdown(step)


@pytest.mark.criterion("C2")
def test_breakdown_returns_the_finest_partition(monkeypatch):
    # oracle: C1 point case — Apple's five product lines, value-descending, ranked, each
    # with its own fact id; the [Member] marker removed from the label
    result = _run(monkeypatch, AAPL_PRODUCT, AAPL_TOTAL, axis="product")
    assert result.status.value == "OK"
    assert [r["label"] for r in result.rows] == [
        "iPhone", "Service", "Wearables, Home and Accessories", "Mac", "iPad"]
    assert [r["value"] for r in result.rows] == [
        209_586_000_000.0, 109_158_000_000.0, 35_686_000_000.0, 33_708_000_000.0,
        28_023_000_000.0]
    assert [r["fact_id"] for r in result.rows] == ["5001", "5002", "5003", "5004", "5005"]
    assert [r["rank"] for r in result.rows] == [1, 2, 3, 4, 5]
    assert {r["unit"] for r in result.rows} == {"USD"}
    assert {r["fiscal_year"] for r in result.rows} == {2025}
    seg = _run(monkeypatch, AMZN_SEGMENTS, AMZN_TOTAL, axis="segment")
    assert seg.status.value == "OK" and len(seg.rows) == 3


@pytest.mark.criterion("C2")
def test_breakdown_not_found_and_errors(monkeypatch):
    # oracle: requirement — no complete partition / no total / no members: NOT_FOUND with
    # a reason; malformed steps: ERROR
    for members, total in ((AMD_GEO_MISSING_ONE, AMD_TOTAL), (AMZN_SEGMENTS, None),
                           ([], AMZN_TOTAL)):
        r = _run(monkeypatch, members, total, axis="geography")
        assert r.status.value == "NOT_FOUND" and r.rows == [] and r.resolution_notes
    assert _run(monkeypatch, AMZN_SEGMENTS, AMZN_TOTAL, axis="customer").status.value == "ERROR"
    assert _run(monkeypatch, AMZN_SEGMENTS, AMZN_TOTAL, op="lookup").status.value == "ERROR"


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(members_st, st.data(), st.sampled_from(["segment", "product", "geography"]))
def test_breakdown_rows_match_the_rule(members, data, axis):
    # oracle: the brute-force rule over the same members; rows sum to the total
    members = [(lbl, float(v)) for lbl, v in members]
    pick = data.draw(st.lists(st.sampled_from(range(len(members))), unique=True))
    total = float(sum(members[i][1] for i in pick)) or 1.0
    with pytest.MonkeyPatch.context() as mp:
        result = _run(mp, members, total, axis=axis)
    chosen = _brute(members, total)
    if chosen is None:
        assert result.status.value == "NOT_FOUND" and result.rows == []
    else:
        assert result.status.value == "OK"
        assert sorted(r["label"] for r in result.rows) == sorted(members[i][0] for i in chosen)
        assert sum(r["value"] for r in result.rows) == total
        assert [r["value"] for r in result.rows] == sorted(
            (r["value"] for r in result.rows), reverse=True)


# --------------------------------------------------------------------------- C3
def _plan(**step):
    base = {"id": "bd", "op": "breakdown", "cik": "0001018724", "fy": 2025,
            "concept_family": "Revenue", "axis": "segment"}
    base.update(step)
    return {"steps": [{k: v for k, v in base.items() if v is not None}]}


@pytest.mark.criterion("C3")
def test_validator_accepts_a_breakdown_step():
    # oracle: requirement — a well-formed breakdown validates; missing fields and an axis
    # outside segment/product/geography do not
    assert PlanValidator().validate(_plan()) == []
    for axis in ("segment", "product", "geography"):
        assert PlanValidator().validate(_plan(axis=axis)) == []
    for field in ("cik", "fy", "concept_family", "axis"):
        errors = PlanValidator().validate(_plan(**{field: None}))
        assert errors and any(field in e for e in errors), field
    assert PlanValidator().validate(_plan(axis="customer"))


@pytest.mark.criterion("C3")
@given(st.one_of(st.sampled_from(["segment", "product", "geography"]),
                 st.text(min_size=1, max_size=12)))
def test_validator_axis_rule_for_any_value(axis):
    # oracle: the three axis kinds are the only valid ones
    errors = PlanValidator().validate(_plan(axis=axis))
    assert (errors == []) is (axis in ("segment", "product", "geography"))


# --------------------------------------------------------------------------- C4
def _resp(*steps):
    return {"execution_result": {"steps": list(steps)}}


def _bd_step(status, rows):
    return {"step_id": "bd", "op": "breakdown", "status": status, "value": None,
            "citation": None, "rows": rows}


@pytest.mark.criterion("C4")
def test_breakdown_rows_are_produced_values_and_facts():
    # oracle: requirement — an OK breakdown's row values and fact ids count; a non-OK
    # breakdown's do not; a lookup still counts as before
    rows = [{"label": lbl, "value": v, "fact_id": str(5000 + i)}
            for i, (lbl, v) in enumerate(AMZN_SEGMENTS)]
    lookup = {"step_id": "l1", "op": "lookup", "status": "OK", "value": 1.5,
              "citation": {"fact_id": 77}}
    resp = _resp(lookup, _bd_step("OK", rows))
    assert sorted(all_numeric_values(resp)) == sorted([1.5] + [v for _, v in AMZN_SEGMENTS])
    assert set(all_fact_ids(resp)) == {77, 5000, 5001, 5002}
    bad = _resp(lookup, _bd_step("NOT_FOUND", rows))
    assert all_numeric_values(bad) == [1.5]
    assert all_fact_ids(bad) == [77]


@pytest.mark.criterion("C4")
@given(st.lists(st.floats(min_value=1, max_value=1e12, allow_nan=False), max_size=6),
       st.sampled_from(["OK", "NOT_FOUND", "ERROR"]))
def test_breakdown_values_for_any_rows(values, status):
    # oracle: the C4 rule written out
    rows = [{"label": f"m{i}", "value": v, "fact_id": str(9000 + i)}
            for i, v in enumerate(values)]
    resp = _resp(_bd_step(status, rows))
    ok = status == "OK"
    assert sorted(all_numeric_values(resp)) == (sorted(values) if ok else [])
    assert sorted(all_fact_ids(resp)) == ([9000 + i for i in range(len(values))] if ok else [])


# --------------------------------------------------------------------------- C5
class _BuildCursor:
    def __init__(self, store):
        self._store = store
        self.description = None
        self._rows: list = []

    def execute(self, sql, params=None):
        params = params or {}
        if "publish_manifest" in sql:
            cols, rows = ["accession_no", "is_published"], [("0000000000-26-000001", True)]
        elif "array_agg" in sql:
            cols, rows = ["anchor", "members"], []
        else:
            members, total = self._store[(params.get("ticker"), params.get("fy"))]
            cols = ["member_label", "value", "fact_id", "total"]
            rows = [(lbl, v, 5000 + i, total) for i, (lbl, v) in enumerate(members)]
        self.description = [(c,) for c in cols]
        self._rows = rows

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _BuildConn:
    def __init__(self, store):
        self._store = store

    def cursor(self):
        return _BuildCursor(self._store)

    def rollback(self):
        pass

    def close(self):
        pass


def _build(store):
    tickers = sorted({t for t, _ in store})
    spec = {
        "version": "v1", "suite_name": "sql_derived",
        "corpus": {"tickers": tickers, "fiscal_years": [2025]},
        "tiers": {"standard": {"paraphrases": 1}},
        "templates": [{
            "id": "t-bd", "source": "analyst_queries.sql#3a", "kind": "breakdown",
            "nl": ["Break down {company}'s fiscal {fy} revenue by product."],
            "params": {"ticker": "corpus.tickers", "fy": "corpus.fiscal_years"},
            "sql": "SELECT member_label, value, fact_id, total FROM mv "
                   "WHERE t=%(ticker)s AND y=%(fy)s AND fiscal_period = 'FY'",
            "expect": {"rows": {"entity": "member_label", "value": "value"},
                       "fact_ids": "fact_id", "total": "total"},
            "top_n": 2, "min_rows": 2,
        }],
    }
    builder = SqlDerivedBuilder(spec, _BuildConn(store), company_names={t: t for t in tickers})
    return builder.build("standard")


@pytest.mark.criterion("C5")
def test_builder_expects_the_whole_partition():
    # oracle: C1 point cases by hand — Apple's five lines (top_n=2 does not cut them), their
    # five fact ids; AMD with a member missing has no partition and yields no case
    suite = _build({("AAPL", 2025): (AAPL_PRODUCT, AAPL_TOTAL),
                    ("AMD", 2025): (AMD_GEO_MISSING_ONE, AMD_TOTAL)})
    assert [c.ticker for c in suite.cases] == ["AAPL"]
    case = suite.cases[0]
    assert sorted((r["entity"], r["value"]) for r in case.expected_rows) == sorted([
        ("iPhone", 209_586_000_000.0), ("Service", 109_158_000_000.0),
        ("Wearables, Home and Accessories", 35_686_000_000.0), ("Mac", 33_708_000_000.0),
        ("iPad", 28_023_000_000.0)])
    assert sorted(case.expected_fact_ids) == [5001, 5002, 5003, 5004, 5005]


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(members_st, st.data())
def test_builder_partition_for_any_members(members, data):
    # oracle: the brute-force rule; no partition, no case
    members = [(lbl, float(v)) for lbl, v in members]
    pick = data.draw(st.lists(st.sampled_from(range(len(members))), unique=True))
    total = float(sum(members[i][1] for i in pick)) or 1.0
    suite = _build({("AAPL", 2025): (members, total)})
    chosen = _brute(members, total)
    if chosen is None:
        assert suite.cases == []
    else:
        assert len(suite.cases) == 1
        got = sorted((r["entity"], r["value"]) for r in suite.cases[0].expected_rows)
        assert got == sorted(members[i] for i in chosen)
        assert sorted(suite.cases[0].expected_fact_ids) == sorted(5000 + i for i in chosen)
