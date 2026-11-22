"""C3: the concept mapping's stated substitutes reach the statement witness (D4).

Oracle: requirement 3 of docs/specs/key-currency-and-definition.md; decisions D11 and D21 of
docs/design/XbrlRag_Concept_Decisions.md (as committed in
configs/concept_mapping/concept_family_coverage.yaml); the IFRS definition of
ifrs-full:ProceedsFromIssueOfBondsNotesAndDebentures, "The cash inflow from the issuing of
bonds, notes and debentures" (debt securities only; borrowings from banks are outside it, so
a filer's bonds line is one of several gross debt lines, never the family's total).
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import load_mapping_substitutes, merge_mapping_substitutes

M = 1_000_000
REL = 1e-12  # tolerance: whole-million fixture amounts; only binary floating point differs
ONE_OF_SEVERAL = {"code": "one_of_several_lines",
                  "note": "One of several gross debt lines; the filer reports no total."}


@pytest.mark.criterion("C3")
def test_live_mapping_substitutes(kit):
    # oracle: D21 (DebtIssued, DebtRepaid: one_of_several_lines) and D11 (Debt:
    # excludes_current_portion, one entry stating its own note), with the substitutions' notes
    subs = load_mapping_substitutes(kit.LIVE_COVERAGE)
    assert subs["DebtIssued"] == {kit.BONDS: ONE_OF_SEVERAL}
    assert set(subs["DebtRepaid"]) == {"ifrs-full:RepaymentsOfBondsNotesAndDebentures",
                                       "ifrs-full:RepaymentsOfNoncurrentBorrowings",
                                       "us-gaap:RepaymentsOfLinesOfCredit"}
    assert all(v == ONE_OF_SEVERAL for v in subs["DebtRepaid"].values())
    assert subs["Debt"] == {
        "us-gaap:LongTermDebtNoncurrent": {
            "code": "excludes_current_portion",
            "note": "Long-term debt excluding the current portion."},
        "us-gaap:LongTermDebtAndCapitalLeaseObligations": {
            "code": "excludes_current_portion",
            "note": "Long-term debt and lease obligations excluding the current portion."}}


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("body", [
    # oracle: requirement 3 — a code the substitutions do not define
    "substitutions: {}\nsubstitutes:\n  DebtIssued:\n    ifrs-full:A: no_such_code\n",
    # oracle: requirement 3 — a malformed concept
    "substitutions: {c: note}\nsubstitutes:\n  DebtIssued:\n    not a concept: c\n",
    # oracle: requirement 3 — an entry that is neither a code nor {code, note}
    "substitutions: {c: note}\nsubstitutes:\n  DebtIssued:\n    ifrs-full:A: {code: c}\n",
])
def test_malformed_mapping_raises(tmp_path, body):
    path = tmp_path / "coverage.yaml"
    path.write_text("version: 1\n" + body, encoding="utf-8")
    with pytest.raises(ValueError):
        load_mapping_substitutes(path)


@pytest.mark.criterion("C3")
def test_merge_adds_the_mapping_substitutes(kit):
    # oracle: requirement 3 — the mapping's entry joins the family's rule; a rule's own entry
    # wins; a family without a rule is ignored; nothing else changes; inputs stay unchanged
    rules = kit.rules()
    before = copy.deepcopy(rules)
    own = {"code": "own_code", "note": "The rule's own note."}
    subs = {"DebtIssued": {kit.BONDS: ONE_OF_SEVERAL},
            "Capex": {"us-gaap:PaymentsToAcquireProductiveAssets": ONE_OF_SEVERAL}}
    merged = merge_mapping_substitutes(rules, subs)
    assert rules == before
    assert set(merged) == {"DebtIssued", "Debt"}
    assert merged["DebtIssued"]["substitutes"] == {kit.BONDS: ONE_OF_SEVERAL}
    assert {k: v for k, v in merged["DebtIssued"].items() if k != "substitutes"} == {
        k: v for k, v in rules["DebtIssued"].items() if k != "substitutes"}
    assert merged["Debt"] == rules["Debt"]
    with_own = {**rules, "DebtIssued": {**rules["DebtIssued"], "substitutes": {kit.BONDS: own}}}
    assert merge_mapping_substitutes(with_own, subs)["DebtIssued"]["substitutes"] == {
        kit.BONDS: own}
    listed = {**rules, "DebtIssued": {**rules["DebtIssued"], "elements": [kit.BONDS]}}
    with pytest.raises(ValueError):
        merge_mapping_substitutes(listed, subs)


_CONCEPTS = st.sampled_from(["ifrs-full:A", "ifrs-full:B", "us-gaap:C", "us-gaap:D"])
_ENTRY = st.fixed_dictionaries({"code": st.sampled_from(["x", "y"]), "note": st.just("n")})


@pytest.mark.criterion("C3")
@given(mapping=st.dictionaries(st.sampled_from(["DebtIssued", "Debt", "Capex"]),
                               st.dictionaries(_CONCEPTS, _ENTRY, max_size=3), max_size=3),
       own=st.dictionaries(_CONCEPTS, _ENTRY, max_size=2))
def test_merge_rule_written_out(kit, mapping, own):
    # oracle: requirement 3 restated — per family with a rule, the substitutes are the
    # mapping's updated by the rule's own; merging twice is merging once
    rules = kit.rules()
    rules["DebtIssued"] = {**rules["DebtIssued"], "substitutes": dict(own)}
    merged = merge_mapping_substitutes(rules, mapping)
    assert set(merged) == set(rules)
    for family, rule in rules.items():
        assert merged[family]["substitutes"] == {**mapping.get(family, {}), **rule["substitutes"]}
    assert merge_mapping_substitutes(merged, mapping) == merged


@pytest.mark.criterion("C3")
def test_the_build_witness_carries_the_mapping(kit):
    # oracle: requirement 3 — KeyWitness.from_paths merges the committed mapping by default
    w = kit.witness()
    assert w.rules["DebtIssued"]["substitutes"].get(kit.BONDS) == ONE_OF_SEVERAL


@pytest.mark.criterion("C3")
def test_an_ifrs_bonds_line_is_one_of_several_lines(kit):
    # oracle: the IFRS definition (module docstring) and D21 — IFRU reports in USD (C1) and
    # presents only "Proceeds from issuance of bonds" (700): a stated substitute, never ranked,
    # whether the member is reached unkeyed or by a key on the bonds element
    w = kit.witness()
    cf, labels, cik = kit.companyfacts("IFRU"), kit.labels("IFRU"), kit.CIK["IFRU"]
    assert w.unkeyed_member(cf, labels, cik, "DebtIssued", 2023, "USD") == (
        "substitute:one_of_several_lines", None)
    key, adsh, annual = w.fy_key(cf, labels, cik, "DebtIssued", kit.spec(kit.BONDS), 2023)
    status = w.member("DebtIssued", adsh, key, annual)
    assert (status["status"], status["reason"], status["concept"]) == (
        "definition_excluded", "substitute:one_of_several_lines", kit.BONDS)
    assert status["value"] == pytest.approx(700 * M, rel=REL)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_only_the_merge_moves_the_member(kit):
    # oracle: requirement 3 — the same tables and rules without the mapping rank IFRU's bonds
    # line (700, today's bypass); merged with the committed mapping it is the substitute;
    # USDB's ProceedsFromIssuanceOfDebt (a primary, no substitute) is ranked either way (500)
    def member(w, ticker):
        return w.unkeyed_member(kit.companyfacts(ticker), kit.labels(ticker), kit.CIK[ticker],
                                "DebtIssued", 2023, "USD")

    plain = kit.plain_witness()
    reason, key = member(plain, "IFRU")
    assert reason is None and key["value"] == pytest.approx(700 * M, rel=REL)
    merged = kit.plain_witness(merge_mapping_substitutes(
        kit.rules(), load_mapping_substitutes(kit.LIVE_COVERAGE)))
    assert member(merged, "IFRU") == ("substitute:one_of_several_lines", None)
    for w in (plain, merged):
        reason, key = member(w, "USDB")
        assert reason is None and key["value"] == pytest.approx(500 * M, rel=REL)
