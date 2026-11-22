"""C5: DebtIssued is "debt issued" (any term), in the lexicon, the check-only forms and every
rendered question; DebtRepaid is not renamed (D3).

Oracle: requirement 5 of docs/specs/key-currency-and-definition.md. The family's primaries
include us-gaap:ProceedsFromIssuanceOfDebt (standard label "Proceeds from Issuance of Debt";
its definition covers debt of any term) and ProceedsFromDebtNetOfIssuanceCosts (any term), so
the question may not name long-term debt. The DebtRepaid surfaces are the committed ones,
copied by hand.
"""

from __future__ import annotations

import re

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.intents import (
    Intent,
    build_phrased_cases,
    check_lexicon_phrasings,
    load_lexicon,
)

LONG_TERM = re.compile(r"\blong[- ]?term\b|\bLT\b|\blonger than\b", re.IGNORECASE)
LABEL = "Proceeds from Issuance of Debt"
SYNONYMS = ["proceeds from debt issuance", "debt issued"]
INTENTS = [
    Intent("screen", operation="sum", universe="covered", fiscal_years=[2023],
           metrics=["DebtIssued"]),
    Intent("screen", operation="top", universe="covered", fiscal_years=[2024],
           metrics=["DebtIssued"], n=3),
    Intent("lookup", tickers=["AMD"], fiscal_years=[2023], metrics=["DebtIssued"]),
]


def _doc(kit, name):
    return yaml.safe_load((kit.LIVE / name).read_text(encoding="utf-8"))


@pytest.mark.criterion("C5")
def test_lexicon_names_debt_issued(kit):
    # oracle: requirement 5 — the label is the standard label of ProceedsFromIssuanceOfDebt,
    # two synonyms (the count is kept, so the seeded phrasing choice keeps its range), no
    # shorthand; DebtRepaid is unchanged; the lexicon still passes the phrasing check
    metrics = _doc(kit, "lexicon.yaml")["metrics"]
    assert metrics["DebtIssued"] == {"label": LABEL, "synonyms": SYNONYMS}
    assert metrics["DebtRepaid"] == {
        "label": "Repayments of Long-Term Debt",
        "synonyms": ["long-term debt repayments", "repayments of long-term borrowings"]}
    check_lexicon_phrasings(load_lexicon(kit.LIVE / "lexicon.yaml"))


@pytest.mark.criterion("C5")
def test_check_only_forms_name_no_term(kit):
    # oracle: requirement 5 — the paraphrase check's DebtIssued forms are debt-issuance
    # phrasings of any term (each names debt or borrowings, none a term); DebtRepaid's are
    # the committed long-term forms
    forms = _doc(kit, "paraphrase_forms.yaml")["metrics"]
    issued = forms["DebtIssued"]
    assert issued and len(issued) == len(set(issued))
    assert not [f for f in issued if LONG_TERM.search(f)]
    assert all(re.search(r"\b(debt|borrowings?)\b", f, re.IGNORECASE) for f in issued)
    assert forms["DebtRepaid"] == [
        "LT debt repayments", "long-term debt paydowns", "LT debt paydowns",
        "long-term debt repaid", "repayment of long-term debt", "repayments of long-term debt",
        "long-term borrowings repayments", "LT borrowings repayments"]


@pytest.mark.criterion("C5")
def test_every_candidate_phrasing_says_debt_issued(kit):
    # oracle: requirement 5 — with every candidate phrasing kept, the questions use the label
    # and both synonyms, and none names a term
    lexicon = load_lexicon(kit.LIVE / "lexicon.yaml")
    cases = build_phrased_cases(INTENTS, kit.phrasebook(), lexicon, per_intent=50, share=0.5,
                                salt="kcd")
    text = " | ".join(c.question.lower() for c in cases)
    for surface in (LABEL, *SYNONYMS):
        assert surface.lower() in text
    assert not [c.question for c in cases if LONG_TERM.search(c.question)]


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(salt=st.text(max_size=8), per_intent=st.integers(min_value=1, max_value=4))
def test_no_draw_names_a_term(kit, salt, per_intent):
    # oracle: requirement 5 — whatever the split salt and phrasing count, no DebtIssued
    # question names long-term debt
    lexicon = load_lexicon(kit.LIVE / "lexicon.yaml")
    cases = build_phrased_cases(INTENTS, kit.phrasebook(), lexicon, per_intent=per_intent,
                                share=0.5, salt=salt)
    assert cases
    assert not [c.question for c in cases if LONG_TERM.search(c.question)]
