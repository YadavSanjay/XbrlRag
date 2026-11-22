"""C1: a screen/breakdown row's component fact ids resolve to that row (R1 amended)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.citation_integrity import evidence_index, unresolved_citation_ids


def _execution(crc):
    return crc.execution([crc.lookup_step(), crc.debt_screen(), crc.breakdown_step(),
                          crc.derived_screen()])


@pytest.mark.criterion("C1")
def test_component_ids_of_screen_and_breakdown_rows_resolve(crc):
    # oracle: R1 as amended, applied by hand: every id below is a row's fact_id, a row's
    # component (object values, array items, one nested array level), a lookup's citation, or
    # nothing ("None" is the derived row's null component, which lists nothing).
    er = _execution(crc)
    cases = [
        (["14500", "14502", "14506"], []),          # AAPL row: fact_id + its two other parts
        (["71859"], []),                             # ORCL row: second part only
        (["26587", "99999999"], ["99999999"]),       # AVGO part; an invented id
        (["99001"], []),                             # breakdown row, array-form components
        (["99102", "99103", "99101"], []),           # derived row: nested array + object value
        ([crc.DERIVED_ROW_FACT], []),                # the derived row's synthetic fact_id
        (["None"], ["None"]),                        # a null component lists nothing
        (["13253", "14506", "s9"], ["s9"]),          # lookup citation, AAPL part, unknown step
    ]
    got = [unresolved_citation_ids(crc.detail("A statement.", ids), er, [])
           for ids, _ in cases]
    assert got == [want for _, want in cases]


@pytest.mark.criterion("C1")
def test_component_id_maps_to_the_row_record_with_its_op(crc):
    # oracle: R1 as amended: the record is {**row, "op": <step op>}, the record the row's own
    # fact_id resolves to; built here from the fixture rows.
    er = _execution(crc)
    index = evidence_index(er)
    aapl = {**crc.debt_row("AAPL"), "op": "screen"}
    bk_row = {**crc.breakdown_step()["rows"][0], "op": "breakdown"}
    derived = {**crc.derived_screen()["rows"][0], "op": "screen"}
    assert index.get("14502", []) == [aapl]
    assert index.get("14506", []) == [aapl]
    assert index.get("71859", []) == [{**crc.debt_row("ORCL"), "op": "screen"}]
    assert index.get("99001", []) == [bk_row]
    assert index.get("99102", []) == [derived] and index.get("99103", []) == [derived]
    assert index.get("99101", []) == [derived]
    assert aapl in index.get("14500", [])
    assert "None" not in index


# Fact ids 1-999 are row fact ids, 1000-4999 components, 5000-5999 never used anywhere.
_component = st.integers(min_value=1000, max_value=4999)


@st.composite
def _rows(draw):
    n = draw(st.integers(min_value=1, max_value=4))
    fact_ids = draw(st.lists(st.integers(min_value=1, max_value=999), min_size=n, max_size=n,
                             unique=True))
    rows = []
    for fid in fact_ids:
        ids = draw(st.lists(_component, min_size=1, max_size=4, unique=True))
        form = draw(st.sampled_from(["object", "array", "nested"]))
        if form == "object":
            components = {f"part_{i}": c for i, c in enumerate(ids, start=1)}
        elif form == "array":
            components = list(ids)
        else:
            components = {"a": ids[0], "b": list(ids[1:])}
        rows.append(({"fact_id": fid, "ticker": f"T{fid}", "value": float(fid),
                      "component_fact_ids": components}, ids))
    op = draw(st.sampled_from(["screen", "breakdown"]))
    alien = draw(st.integers(min_value=5000, max_value=5999))
    return op, rows, alien


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(case=_rows())
def test_every_row_component_resolves_and_adding_components_never_unresolves(crc, case):
    # oracle: R1 as amended (every listed component resolves to its row) and R3 (adding
    # components never unresolves an id; an id outside every step and row stays unresolved).
    op, rows, alien = case
    bare = [{k: v for k, v in r.items() if k != "component_fact_ids"} for r, _ in rows]
    with_components = crc.execution([{"step_id": "s1", "op": op, "status": "OK",
                                      "rows": [r for r, _ in rows]}])
    without = crc.execution([{"step_id": "s1", "op": op, "status": "OK", "rows": bare}])
    index = evidence_index(with_components)
    for row, ids in rows:
        for cid in ids:
            assert unresolved_citation_ids(crc.detail("x", [cid]), with_components, []) == []
            assert {**row, "op": op} in index.get(str(cid), [])
    for row, _ in rows:
        cited = [row["fact_id"], "s1"]
        assert unresolved_citation_ids(crc.detail("x", cited), without, []) == []
        assert unresolved_citation_ids(crc.detail("x", cited), with_components, []) == []
    assert unresolved_citation_ids(crc.detail("x", [alien]), with_components, []) == [str(alien)]
