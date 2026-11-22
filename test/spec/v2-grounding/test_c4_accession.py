"""C4: S1's accession rule — any filing with the same value, except a point-in-time case."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.grounding_v2 import grade_v2_grounding

CIK = 1318605
LATEST = "0001628280-26-003952"  # the key's accession: the latest filing reporting FY2023
ORIGINAL = "0001628280-24-000001"  # the FY2023 10-K itself


def _case(gb, value=14997000000, *, shape="lookup", accession=LATEST):
    i = gb.inp(CIK, "us-gaap:NetIncomeLoss", "USD", "2023-01-01", "2023-12-31", value,
               accession=accession)
    return gb.case("c-ni", [i], shape=shape)


def _fact(gb, fid, value, accession):
    return gb.rec(fid, CIK, "NetIncomeLoss", "USD", "2023-01-01", "2023-12-31", value,
                  accession)


def _pr(g):
    return (g["precision"], g["recall"])


@pytest.mark.criterion("C4")
def test_same_value_in_another_filing_is_right(gb):
    # oracle: requirement 7 — outside point-in-time the accession is not compared
    g = grade_v2_grounding(_case(gb), [5], {5: _fact(gb, 5, 14997000000, ORIGINAL)})
    assert _pr(g) == (1, 1)


@pytest.mark.criterion("C4")
def test_restated_original_value_is_wrong(gb):
    # oracle: S1 — the key is the restated 15,000 million; the FY2023 10-K's original
    # 14,997 million differs by 3 million, more than the -6 half-unit, so it is wrong
    c = _case(gb, value=15000000000)
    facts = {5: _fact(gb, 5, 14997000000, ORIGINAL), 6: _fact(gb, 6, 15000000000, LATEST)}
    assert _pr(grade_v2_grounding(c, [5], facts)) == (0, 0)
    g = grade_v2_grounding(c, [5, 6], facts)
    assert (*_pr(g), g["right"], g["wrong"]) == (0.5, 1, (6,), (5,))


@pytest.mark.criterion("C4")
def test_point_in_time_binds_the_named_filing(gb):
    # oracle: requirement 7 — "in the FY2023 annual report": only the key's accession counts
    c = _case(gb, shape="point_in_time", accession=ORIGINAL)
    facts = {5: _fact(gb, 5, 14997000000, LATEST), 6: _fact(gb, 6, 14997000000, ORIGINAL)}
    assert _pr(grade_v2_grounding(c, [5], facts)) == (0, 0)
    assert _pr(grade_v2_grounding(c, [6], facts)) == (1, 1)
    g = grade_v2_grounding(c, [5, 6], facts)
    assert (*_pr(g), g["right"]) == (0.5, 1, (6,))


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(
    accessions=st.lists(st.text("0123456789-", min_size=5, max_size=20), min_size=3,
                        max_size=3),
)
def test_relabelling_accessions_changes_nothing_outside_point_in_time(gb, accessions):
    # oracle: requirement 7 — the accession is not part of the identity here; a hand count
    # of the fixed citation set (two right facts, one off by 1 billion) gives 2/3 and 1
    c = _case(gb)
    base = [(5, 14997000000), (6, 14997000000), (7, 13997000000)]
    facts = {fid: _fact(gb, fid, v, acc) for (fid, v), acc in zip(base, accessions,
                                                                   strict=True)}
    g = grade_v2_grounding(c, [5, 6, 7], facts)
    assert g["precision"] == pytest.approx(2 / 3, rel=1e-12)  # 2/3 is not exact in binary
    assert g["recall"] == 1
    assert (g["right"], g["wrong"]) == ((5, 6), (7,))
