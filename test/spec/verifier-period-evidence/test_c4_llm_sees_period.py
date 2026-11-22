"""C4: the verifier LLM sees a lookup's, compute's and aggregate's period (Requirement 4).

The verifier is the real ``XbrlFaithfulnessVerifier``; its LLM (a scripted adapter behind the
real gateway, the only fake) answers SUPPORTED for everything. The evidence is read back from
the prompt the adapter received. The steps already carry ``period`` (the executor wiring is
not locked here), with the values C1-C3 state by hand.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

MEDIAN = ("The median total current assets of the semiconductor companies in fiscal 2023 was "
          "21,244,000,000 USD.")
RATIO = "Apple's current ratio for fiscal 2025 was 0.87."
CA = "Apple's total current assets for fiscal 2025 were 152,987,000,000 USD."
CL = "Apple's total current liabilities for fiscal 2025 were 176,392,000,000 USD."
ROW = ("Micron's total current assets for fiscal 2023 were 21,244,000,000 USD (period ended "
       "2023-08-31).")


def _steps(vpe, cl_period="default"):
    cl = vpe.cl_step(cl_period)
    return [vpe.ca_step(), cl, vpe.cur_ratio_step(), vpe.ca_screen(), vpe.ca_median_step()]


def _run(vpe, details, steps):
    verifier, adapter = vpe.make_verifier()
    out = verifier.verify(vpe.response(details, steps))
    return out, vpe.sent_entries(adapter)


def _entry(sent, statement, step_id):
    matches = [e for e in sent[statement]["fact_evidence"] if e.get("step_id") == step_id]
    assert len(matches) == 1, (statement, step_id, sent[statement]["fact_evidence"])
    return matches[0]


@pytest.mark.criterion("C4")
def test_each_value_step_shows_its_period_and_verdicts_apply(vpe):
    # oracle: Requirement 4 with C1 (ca), C2 (cur_ratio) and C3 (ca_median) written by hand;
    # the fake says SUPPORTED to all, so every statement is kept and fully_answered stays
    details = [vpe.detail(MEDIAN, ["ca_median"]), vpe.detail(RATIO, ["cur_ratio"]),
               vpe.detail(CA, [14482]), vpe.detail(ROW, [55674])]
    out, sent = _run(vpe, details, _steps(vpe))
    assert _entry(sent, MEDIAN, "ca_median").get("period") == vpe.CA_MEDIAN_PERIOD
    assert _entry(sent, RATIO, "cur_ratio").get("period") == vpe.CUR_RATIO_PERIOD
    assert _entry(sent, CA, "ca").get("period") == vpe.CA_PERIOD
    row = _entry(sent, ROW, "ca_screen:55674")
    assert "period" not in row
    assert row.get("fiscal_year") == 2023 and row.get("period_end") == "2023-08-31"
    assert all("period" not in e for e in sent[ROW]["fact_evidence"])
    assert [d["statement"] for d in out.answer_details] == [MEDIAN, RATIO, CA, ROW]
    assert all(v["verdict"] == "SUPPORTED" for v in out.metadata["faithfulness"]["verdicts"])
    assert out.metadata.get("fully_answered") is not False


@pytest.mark.criterion("C4")
def test_other_keys_are_unchanged(vpe):
    # oracle: Requirement 4: period is added; the entry's other keys are the ones the verifier
    # already sent (step_id, op, status, value, unit, citation; aggregate for an aggregate)
    details = [vpe.detail(MEDIAN, ["ca_median"]), vpe.detail(CA, [14482])]
    _, sent = _run(vpe, details, _steps(vpe))
    ca = _entry(sent, CA, "ca")
    assert set(ca) == {"step_id", "op", "status", "value", "unit", "citation", "period"}
    # exact: the stored value is passed through unchanged, never computed
    assert ca["value"] == pytest.approx(152_987_000_000.0, abs=0)
    median = _entry(sent, MEDIAN, "ca_median")
    assert set(median) == {"step_id", "op", "status", "value", "unit", "citation", "aggregate",
                           "period"}
    assert "period_ends" not in median["aggregate"]


@pytest.mark.criterion("C4")
def test_a_step_without_a_mapping_period_shows_none(vpe):
    # oracle: Requirement 4 by hand: cl without period (no key, or None) gets no period key,
    # while ca, cited in the same statement, still shows C1's
    for cl_period in (vpe.OMIT, None, "FY2025"):
        details = [vpe.detail("Apple's current assets and liabilities for fiscal 2025 are as "
                              "stated.", [14482, 14504]), vpe.detail(CL, [14504])]
        out, sent = _run(vpe, details, _steps(vpe, cl_period=cl_period))
        both = details[0]["statement"]
        assert _entry(sent, both, "ca").get("period") == vpe.CA_PERIOD
        assert "period" not in _entry(sent, both, "cl")
        assert "period" not in _entry(sent, CL, "cl")
        assert [d["statement"] for d in out.answer_details] == [both, CL]


_EXPECTED = {
    "ca_median": ("ca_median", "CA_MEDIAN_PERIOD"),
    "cur_ratio": ("cur_ratio", "CUR_RATIO_PERIOD"),
    "14482": ("ca", "CA_PERIOD"),
    "ca": ("ca", "CA_PERIOD"),
    "14504": ("cl", "CL_PERIOD"),
    "cl": ("cl", "CL_PERIOD"),
}
_ids = st.sampled_from(["ca_median", "cur_ratio", "14482", "ca", "14504", "cl", "55674",
                        "154795", "ca_screen"])


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@settings(max_examples=30)
@given(ids=st.lists(_ids, min_size=1, max_size=6), data=st.data())
def test_pairs_follow_the_cited_steps_only(vpe, ids, data):
    # oracle: Requirement 4 with C1-C3 by hand: each cited lookup/compute/aggregate shows its
    # own period once; permuting or repeating fact_ids never changes the (step_id, period) set
    expected = {(_EXPECTED[i][0], json.dumps(getattr(vpe, _EXPECTED[i][1]), sort_keys=True))
                for i in ids if i in _EXPECTED}
    statement = "The figures are as stated."
    shuffled = data.draw(st.permutations(ids)) + [ids[0]]
    for variant in (ids, shuffled):
        _, sent = _run(vpe, [vpe.detail(statement, variant)], _steps(vpe))
        pairs = [(e.get("step_id"), json.dumps(e["period"], sort_keys=True))
                 for e in sent[statement]["fact_evidence"] if "period" in e]
        assert len(pairs) == len(set(pairs))
        assert set(pairs) == expected
