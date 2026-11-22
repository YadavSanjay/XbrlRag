"""C4: the eval judge gives a claim citing a row's component id that row's passage (R2.4)."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.judge_wiring import answer_claims
from src.eval.core.system import SystemAnswer


def _passage(row):
    return json.dumps(row, sort_keys=True, default=str)


def _answer(details, steps):
    return SystemAnswer(text="recorded", raw={"answer_details": details,
                                              "execution_result": {"steps": steps},
                                              "narrative_chunks": []})


@pytest.mark.criterion("C4")
def test_component_citation_gets_the_rows_passage(crc):
    # oracle: R2.4: the passage is json.dumps(row, sort_keys=True, default=str) of the row as
    # the step holds it, built here; each passage appears once; an invented id has none.
    details = [crc.detail("Apple's debt was $106.6 billion.", [14502, 14506]),
               crc.detail("Oracle's debt was $86.9 billion.", [71847, 71859]),
               crc.detail("iPhone revenue was $201.2 billion.", [99001]),
               crc.detail("Apple's operating margin was 31.5%.", [99103]),
               crc.detail("Someone's debt was large.", [777777])]
    steps = [crc.debt_screen(), crc.breakdown_step(), crc.derived_screen()]
    claims = answer_claims(_answer(details, steps))
    got = [list(c.cited_passages) for c in claims]
    assert got == [
        [_passage(crc.debt_row("AAPL"))],
        [_passage(crc.debt_row("ORCL"))],
        [_passage(crc.breakdown_step()["rows"][0])],
        [_passage(crc.derived_screen()["rows"][0])],
        [],
    ]


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(rows=st.lists(st.tuples(st.integers(min_value=1, max_value=999),
                               st.lists(st.integers(min_value=1000, max_value=4999),
                                        min_size=1, max_size=3, unique=True)),
                     min_size=1, max_size=4, unique_by=lambda t: t[0]).filter(
                         lambda rows: len({c for _, ids in rows for c in ids})
                         == sum(len(ids) for _, ids in rows)),
       op=st.sampled_from(["screen", "breakdown"]),
       alien=st.integers(min_value=5000, max_value=5999))
def test_every_component_id_gives_its_rows_passage(crc, rows, op, alien):
    # oracle: R2.4. Every component id of a row (no id shared between rows) yields that row's
    # passage among its claim's passages; citing the row's fact_id too
    # adds no second copy; an id outside every step and row yields none.
    built = [{"fact_id": fid, "ticker": f"T{fid}", "value": float(fid),
              "component_fact_ids": {f"part_{i}": c for i, c in enumerate(ids, start=1)}}
             for fid, ids in rows]
    steps = [{"step_id": "s1", "op": op, "status": "OK", "rows": built}]
    details = []
    expected = []
    for row, (fid, ids) in zip(built, rows, strict=True):
        for cid in ids:
            details.append(crc.detail("x", [cid]))
            expected.append(_passage(row))
            details.append(crc.detail("x", [fid, cid]))
            expected.append(_passage(row))
    details.append(crc.detail("x", [alien]))
    claims = answer_claims(_answer(details, steps))
    for claim, want, d in zip(claims[:-1], expected, details[:-1], strict=True):
        assert want in claim.cited_passages
        assert len(claim.cited_passages) == len(set(claim.cited_passages))
        if len(d["fact_ids"]) == 2:
            assert claim.cited_passages[0] == want
    assert list(claims[-1].cited_passages) == []
