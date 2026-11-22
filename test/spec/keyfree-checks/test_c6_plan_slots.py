"""C6: ``plan_slots`` — the normalised semantic slots of each plan step.

Oracle: requirement 9 of docs/specs/keyfree-checks.md, over the plan JSON shape of
src/retriever/xbrl/plan_validator.py and the plan-generator prompt. Every expected slot
dict is written out by hand.
"""

from __future__ import annotations

import copy
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keyfree.perturbation import plan_slots

SLOT_KEYS = ["op", "cik", "ticker", "fy", "fiscal_period", "ytd", "as_originally_reported",
             "as_of", "concept_family", "dimensions", "axis", "metric_kind", "order", "top_n",
             "min_threshold", "unit", "expr"]


def blank(**over):
    """A slot dict with every slot at its normalised default, then `over` applied."""
    d = {k: None for k in SLOT_KEYS}
    d.update(fiscal_period="FY", ytd=False, as_originally_reported=False, dimensions="NONE")
    d.update(over)
    return d


def yoy_plan():
    return {"steps": [
        {"id": "r24", "op": "lookup", "cik": "0000320193", "ticker": "AAPL", "fy": 2024,
         "concept_family": "Revenue", "dimensions": "NONE", "section_hint": "Income Statement"},
        {"id": "r23", "op": "lookup", "cik": "0000320193", "ticker": "AAPL", "fy": 2023,
         "concept_family": "Revenue", "dimensions": "NONE", "section_hint": "Income Statement"},
        {"id": "g", "op": "compute", "expr": "(r24 - r23) / r23"},
    ]}


@pytest.mark.criterion("C6")
def test_yoy_plan_slots():
    # oracle: requirement 9 — missing keys None, fiscal_period None -> "FY", ytd/aor None ->
    # False, dimensions None/"NONE" -> "NONE", step ids in expr -> $index, whitespace removed;
    # id and section_hint are not slots
    got = plan_slots(yoy_plan())
    lookup = dict(op="lookup", cik="0000320193", ticker="AAPL", concept_family="Revenue")
    assert got == [
        blank(fy=2024, **lookup),
        blank(fy=2023, **lookup),
        blank(op="compute", expr="($0-$1)/$1"),
    ]


@pytest.mark.criterion("C6")
def test_screen_and_breakdown_steps():
    # oracle: requirement 9 — a screen's `dimension` fills the `dimensions` slot; order is
    # lower-cased; fiscal_period upper-cased; other slots as given
    plan = {"steps": [
        {"id": "s", "op": "screen", "fy": 2024, "concept_family": "Revenue",
         "dimension": "NONE", "metric_kind": "value", "order": "DESC", "top_n": 5,
         "min_threshold": None},
        {"id": "b", "op": "breakdown", "cik": "0000320193", "ticker": "AAPL", "fy": 2024,
         "concept_family": "Revenue", "axis": "product", "fiscal_period": "q4", "ytd": True},
        {"id": "s2", "op": "screen", "fy": 2023, "concept_family": "Revenue",
         "dimension": {"geography": "China"}, "metric_kind": "pct_of_total", "order": "asc"},
    ]}
    assert plan_slots(plan) == [
        blank(op="screen", fy=2024, concept_family="Revenue", metric_kind="value",
              order="desc", top_n=5),
        blank(op="breakdown", cik="0000320193", ticker="AAPL", fy=2024,
              concept_family="Revenue", axis="product", fiscal_period="Q4", ytd=True),
        blank(op="screen", fy=2023, concept_family="Revenue", metric_kind="pct_of_total",
              order="asc", dimensions={"geography": "china"}),
    ]


@pytest.mark.criterion("C6")
def test_value_normalisation():
    # oracle: requirement 9 — ticker stripped+upper; int cik and digit string zero-padded to
    # 10; dimensions keys and values stripped+lower; {} and "none" -> "NONE"; a non-digit cik
    # is kept as given
    plan = {"steps": [
        {"id": "a", "op": "lookup", "ticker": " aapl ", "cik": 320193, "fy": 2024,
         "concept_family": "Revenue", "dimensions": {" Geography ": "China "},
         "unit": "USD", "as_of": "2024-09-28", "as_originally_reported": True},
        {"id": "b", "op": "lookup", "ticker": "msft", "cik": " 789019 ", "fy": 2024,
         "concept_family": "Revenue", "dimensions": {}},
        {"id": "c", "op": "lookup", "ticker": None, "cik": "n/a", "fy": None,
         "concept_family": "Revenue", "dimensions": "none"},
    ]}
    got = plan_slots(plan)
    assert got[0] == blank(op="lookup", ticker="AAPL", cik="0000320193", fy=2024,
                           concept_family="Revenue", dimensions={"geography": "china"},
                           unit="USD", as_of="2024-09-28", as_originally_reported=True)
    assert got[1] == blank(op="lookup", ticker="MSFT", cik="0000789019", fy=2024,
                           concept_family="Revenue")
    assert got[2] == blank(op="lookup", cik="n/a", concept_family="Revenue")


@pytest.mark.criterion("C6")
def test_expr_replaces_whole_step_ids_only():
    # oracle: requirement 9 — "rev2" must not be replaced inside "rev24"; numbers and
    # identifiers that are not step ids are kept
    plan = {"steps": [
        {"id": "rev24", "op": "lookup", "ticker": "AAPL", "fy": 2024,
         "concept_family": "Revenue", "dimensions": "NONE"},
        {"id": "rev2", "op": "lookup", "ticker": "AAPL", "fy": 2023,
         "concept_family": "Revenue", "dimensions": "NONE"},
        {"id": "g", "op": "compute", "expr": " (rev24 - rev2) / rev2 * 100 + k "},
    ]}
    assert plan_slots(plan)[2]["expr"] == "($0-$1)/$1*100+k"


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("bad", [{"error": "cannot plan"}, {"steps": "x"}, [], None])
def test_plan_without_steps_list_raises(bad):
    # oracle: requirement 9 — not a dict, or no `steps` list -> ValueError
    with pytest.raises(ValueError):
        plan_slots(bad)


_ident = st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True)


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(new_ids=st.lists(_ident, min_size=3, max_size=3, unique=True),
       hint=st.text(max_size=20))
def test_renaming_ids_hints_and_key_order_do_not_change_slots(new_ids, hint):
    # oracle: requirement 9 — ids and section_hint are not slots and expr refers to steps by
    # position, so a consistent id rename, a new hint and reversed key order change nothing
    plan = yoy_plan()
    base = plan_slots(copy.deepcopy(plan))
    mapping = dict(zip(["r24", "r23", "g"], new_ids, strict=True))
    renamed = {"steps": []}
    for step in plan["steps"]:
        s = dict(step)
        s["id"] = mapping[s["id"]]
        if "expr" in s:
            s["expr"] = re.sub(r"[A-Za-z_][A-Za-z0-9_]*", lambda m: mapping.get(m.group(0),
                               m.group(0)), s["expr"])
        if "section_hint" in s:
            s["section_hint"] = hint
        renamed["steps"].append(dict(reversed(list(s.items()))))
    assert plan_slots(renamed) == base
