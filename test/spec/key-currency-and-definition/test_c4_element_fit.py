"""C4: a member's element must fit the family's definition, over its presented label (K-R2).

Oracle: requirement 4 of docs/specs/key-currency-and-definition.md; the us-gaap definition of
ProceedsFromShortTermDebt ("The cash inflow from a borrowing having initial term of repayment
within one year or the normal operating cycle, if longer"); the reviewed-resolutions review
record (docs/specs/reviewed-resolutions.md requirement 1); the conftest's AMDX filing (a
"Proceeds from debt, net of issuance costs" line tagged ProceedsFromShortTermDebt, 250).

The fixture list (fixtures/element_fit.yaml) states the K2b reading under DebtIssued's old
"long-term debt" meaning; under the renamed meaning (D3, "debt issued", any term) short-term
debt proceeds fit, so the live list carries no such entry and AMDX stays ranked.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import element_fit_status, load_element_fit

M = 1_000_000
REL = 1e-12  # tolerance: whole-million fixture amounts; only binary floating point differs
REVIEWERS = ["Reviewer A"]
SHORT_DEF = ("The cash inflow from a borrowing having initial term of repayment within one "
             "year or the normal operating cycle, if longer.")


@pytest.mark.criterion("C4")
def test_fixture_list_loads(kit):
    # oracle: fixtures/element_fit.yaml read by hand; the review date normalized to ISO text
    fit = load_element_fit(kit.ELEMENT_FIT, reviewers=REVIEWERS)
    assert fit == {"DebtIssued": {kit.SHORT_TERM: {
        "definition": SHORT_DEF,
        "review": {"by": "Reviewer A", "date": "2026-11-11", "bucket": "definition",
                   "evidence": {"kind": "statement", "adsh": "0000830006-24-000060",
                                "report": 4, "line": 1,
                                "plabel": "Proceeds from debt, net of issuance costs"}}}}}


_REVIEW = ("review: {by: Reviewer A, date: 2026-11-11, bucket: definition, evidence: {kind: "
           "statement, adsh: 0000830006-24-000060, report: 4, line: 1, plabel: P}}")


def _one(concept: str, *fields: str) -> str:
    """A list file with one DebtIssued entry whose fields are the given YAML lines."""
    body = "".join(f"      {f}\n" for f in fields)
    return f"version: 1\nfamilies:\n  DebtIssued:\n    {concept}:\n{body}"


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("text", [
    # oracle: requirement 4 — version other than 1
    "version: 2\nfamilies: {}\n",
    # oracle: requirement 4 — an unknown top-level field
    "version: 1\nfamilies: {}\nextra: 1\n",
    # oracle: requirement 4 — an extension is never a standard element
    _one("ext:Foo", "definition: d", _REVIEW),
    # oracle: requirement 4 — an empty definition
    _one("us-gaap:Foo", "definition: ''", _REVIEW),
    # oracle: requirement 4 — no review record
    _one("us-gaap:Foo", "definition: d"),
    # oracle: requirement 4 — a reviewer outside the allow-list
    _one("us-gaap:Foo", "definition: d", _REVIEW.replace("Reviewer A", "Somebody Else")),
    # oracle: requirement 4 — an unknown entry field
    _one("us-gaap:Foo", "definition: d", "x: 1", _REVIEW),
])
def test_malformed_lists_raise(tmp_path, text):
    path = tmp_path / "element_fit.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_element_fit(path, reviewers=REVIEWERS)


def _status(status, concept, value=250.0 * M):
    return {"status": status, "reason": None if status == "ranked" else "r",
            "concept": concept, "value": value, "witness": None}


@pytest.mark.criterion("C4")
def test_status_rule(kit):
    # oracle: requirement 4 — a ranked member on a listed element is excluded, keeping its
    # concept, value and witness; anything else is returned unchanged
    fit = {"DebtIssued": {kit.SHORT_TERM: {"definition": SHORT_DEF}}}
    assert element_fit_status(_status("ranked", kit.SHORT_TERM), "DebtIssued", fit) == {
        "status": "definition_excluded", "reason": "element_outside_family",
        "concept": kit.SHORT_TERM, "value": 250.0 * M, "witness": None}
    other = _status("ranked", "us-gaap:ProceedsFromIssuanceOfDebt")
    assert element_fit_status(other, "DebtIssued", fit) == other
    held = _status("held", kit.SHORT_TERM)
    assert element_fit_status(held, "DebtIssued", fit) == held
    elsewhere = _status("ranked", kit.SHORT_TERM)
    assert element_fit_status(elsewhere, "DebtRepaid", fit) == elsewhere
    assert element_fit_status(elsewhere, "DebtIssued", {}) == elsewhere


@pytest.mark.criterion("C4")
@given(status=st.sampled_from(["ranked", "held", "definition_excluded", "metric_not_tagged"]),
       concept=st.sampled_from(["us-gaap:A", "us-gaap:B", None]),
       family=st.sampled_from(["DebtIssued", "Capex"]),
       listed=st.dictionaries(st.sampled_from(["DebtIssued", "Capex"]),
                              st.dictionaries(st.sampled_from(["us-gaap:A", "us-gaap:B"]),
                                              st.just({"definition": "d"}), max_size=2),
                              max_size=2))
def test_status_rule_written_out(status, concept, family, listed):
    # oracle: requirement 4 restated — excluded exactly when ranked and listed for the family
    s = _status(status, concept)
    got = element_fit_status(s, family, listed)
    if status == "ranked" and concept is not None and concept in listed.get(family, {}):
        assert got == {**s, "status": "definition_excluded", "reason": "element_outside_family"}
    else:
        assert got == s


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_the_witness_applies_its_list_on_every_member_path(kit):
    # oracle: requirement 4 — with the fixture list, AMDX's line (250, presented as "debt",
    # tagged short-term) is excluded unkeyed and keyed; with an empty list it is ranked
    w = kit.witness()
    cf, labels, cik = kit.companyfacts("AMDX"), kit.labels("AMDX"), kit.CIK["AMDX"]
    w.element_fit = load_element_fit(kit.ELEMENT_FIT, reviewers=REVIEWERS)
    assert w.unkeyed_member(cf, labels, cik, "DebtIssued", 2023, "USD") == (
        "element_outside_family", None)
    key, adsh, annual = w.fy_key(cf, labels, cik, "DebtIssued", kit.spec(kit.SHORT_TERM), 2023)
    status = w.member("DebtIssued", adsh, key, annual)
    assert (status["status"], status["reason"]) == ("definition_excluded",
                                                    "element_outside_family")
    w.element_fit = {}
    reason, ranked = w.unkeyed_member(cf, labels, cik, "DebtIssued", 2023, "USD")
    assert reason is None and ranked["value"] == pytest.approx(250 * M, rel=REL)
    assert w.member("DebtIssued", adsh, key, annual)["status"] == "ranked"


@pytest.mark.criterion("C4")
def test_live_list_and_the_d3_reading_of_amd(kit):
    # oracle: requirement 4 and D3 — the committed list loads (live reviewers), the build's
    # witness carries it, and it does not list ProceedsFromShortTermDebt under DebtIssued
    # (short-term debt proceeds are debt issued), so AMDX is ranked at 250
    live = load_element_fit(kit.LIVE / "element_fit.yaml")
    assert kit.SHORT_TERM not in live.get("DebtIssued", {})
    w = kit.witness()
    assert w.element_fit == live
    reason, ranked = w.unkeyed_member(kit.companyfacts("AMDX"), kit.labels("AMDX"),
                                      kit.CIK["AMDX"], "DebtIssued", 2023, "USD")
    assert reason is None and ranked["value"] == pytest.approx(250 * M, rel=REL)
