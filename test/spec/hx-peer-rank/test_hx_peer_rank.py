"""H-X peer-relative ("rank") cases use the stated-rank reader (docs/specs/hx-peer-rank.md).

The case is fictional, in hx-authoring's built shape (requirement 14: a ``peer_relative``
case is built as ``count`` plus ``ticker``): universe DEVA, DEVB, DEVC, DEVD; DEVD excluded
(``not_tagged``); ranked filers DEVA 300m > DEVB 200m > DEVC 100m, so the subject DEVB's
rank is 2 and ``n_ranked`` is 3. Expected grades are derived by hand from
hx-peer-rank requirements 1-5 and peer-rank-grading requirement 4.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.graders.base import Grade
from src.eval.core.graders.v2 import grade_v2, peer_names
from src.eval.core.system import SystemAnswer

M = 1e6
VALUES = {"DEVA": 300 * M, "DEVB": 200 * M, "DEVC": 100 * M}
TICKERS = ["DEVA", "DEVB", "DEVC", "DEVD"]
FACTS = [1, 2, 3]
DISCLOSED = " DEVD was excluded."
ORDINALS = {1: "1st", 2: "2nd", 3: "3rd"}


def hx_rank(rename: dict[str, str] | None = None, **overrides: Any) -> dict[str, Any]:
    r = rename or {t: t for t in TICKERS}
    d: dict[str, Any] = {
        "id": "hx-r01", "question": "Synthetic cross_filer question r01?",
        "suite": "heldout_x_v1", "origin": "handwritten:h_x",
        "tags": ["split:external", "cross_filer", "peer_relative"],
        "kind": "compute", "fy": 2025, "unit": "pure", "rel_tol": 0.0,
        "universe": sorted(r[t] for t in TICKERS),
        "expected_exclusions": [{"ticker": r["DEVD"], "reason": "not_tagged"}],
        "expected_value": 2.0, "ticker": r["DEVB"],
        "expected_fact_ids": FACTS, "fact_id_classes": [[1], [2], [3]],
    }
    d.update(overrides)
    return d


def case(rename: dict[str, str] | None = None, **overrides: Any) -> EvalCase:
    return EvalCase.from_dict(hx_rank(rename, **overrides))


def rows(order: list[str], rename: dict[str, str] | None = None) -> list[dict]:
    r = rename or {t: t for t in TICKERS}
    return [{"ticker": r[t], "value": VALUES[t]} for t in order]


def answer(text: str, row_list: list[dict] | None = None, values: list[float] | None = None,
           fact_ids: list[int] | None = None, refused: bool = False) -> SystemAnswer:
    return SystemAnswer(text=text, numeric_values=list(values or []),
                        rows=list(row_list or []),
                        fact_ids=list(FACTS if fact_ids is None else fact_ids),
                        refused=refused)


def ranks_text(k: int, subject: str = "DEVB", excluded: str = "DEVD") -> str:
    return f"{subject} ranks {ORDINALS[k]} of the 3 ranked filers. {excluded} was excluded."


@pytest.mark.criterion("C1")
def test_stated_ordinal_rank_is_read():
    # oracle: requirements 1-3 with 4.1 — "2nd" is the key's rank 2 (the scalar path read no 2,
    # because the number extractor drops ordinals); "of the 3" names the ranked count and
    # DEVD is named, so both disclosure checks hold
    g = grade_v2(case(), answer(ranks_text(2)))
    assert g.grade is Grade.CORRECT and g.passed is True
    assert g.matched_value == pytest.approx(2.0, abs=0.0)  # a rank: exact
    assert g.checks == {"rank": True, "universe": True, "exclusions": True}

    wrong = grade_v2(case(), answer(ranks_text(3)))
    assert wrong.grade is Grade.INCORRECT
    assert wrong.checks["rank"] is False


@pytest.mark.criterion("C1")
def test_values_rule_without_rank_or_rows():
    # oracle: peer-rank-grading 4.3 via requirement 2 — no rank expression, no rows:
    # some value equal to r decides (the retired v2-grading C5 answers, unchanged)
    text = "Of the 4 filers, DEVD was excluded."
    right = grade_v2(case(), answer(text, values=[2.0]))
    assert right.grade is Grade.CORRECT
    # the H-X rank grade carries requirement 4's "rank" check, no longer the scalar "value"
    assert right.checks == {"rank": True, "universe": True, "exclusions": True}
    assert grade_v2(case(), answer(text, values=[3.0])).grade is Grade.INCORRECT


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("order", [["DEVB", "DEVA", "DEVC"], ["DEVC", "DEVB", "DEVA"]])
def test_complete_rows_decide_by_value(order):
    # oracle: peer-rank-grading 4.2 via requirement 2 — no stated rank; 3 valued entities
    # (= n_ranked 3) put DEVB 2nd by value (300m > 200m > 100m), whatever their order
    text = "The ranked filers are listed in the rows. Of the 4 filers, DEVD was excluded."
    g = grade_v2(case(), answer(text, rows(order)))
    assert g.grade is Grade.CORRECT
    assert g.checks["rank"] is True


@pytest.mark.criterion("C1")
def test_partial_rows_imply_no_rank():
    # oracle: peer-rank-grading 2 via requirement 2 — 2 valued entities < n_ranked 3: no
    # rank from rows, no stated rank, no values: "no rank in the answer"
    text = "The ranked filers are listed in the rows. Of the 4 filers, DEVD was excluded."
    g = grade_v2(case(), answer(text, rows(["DEVA", "DEVB"])))
    assert g.grade is Grade.INCORRECT
    assert g.checks.get("rank") is False


@pytest.mark.criterion("C1")
def test_silent_on_the_exclusion_is_partial():
    # oracle: requirements 1-3 — right rank, DEVD never named: downgraded to PARTIAL
    g = grade_v2(case(), answer("DEVB ranks 2nd of the 3 ranked filers."))
    assert g.grade is Grade.PARTIAL and g.passed is False
    assert g.checks == {"rank": True, "universe": True, "exclusions": False}


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("overrides", [{"expected_value": 2.5}, {"ticker": None}])
def test_no_usable_subject_or_rank_is_incorrect(overrides):
    # oracle: requirements 1-3 — a non-whole rank or no ticker leaves no subject/r: INCORRECT
    g = grade_v2(case(**overrides), answer(ranks_text(2)))
    assert g.grade is Grade.INCORRECT
    assert g.checks.get("rank") is False


@pytest.mark.criterion("C2")
def test_grounding_recorded_and_never_decides():
    # oracle: requirement 4 — cited {1, 99} against classes [1], [2], [3]:
    # matched {1}, so precision 1/2 and recall 1/3 (hand count); the grade stays CORRECT
    g = grade_v2(case(), answer(ranks_text(2), fact_ids=[1, 99]))
    assert g.grade is Grade.CORRECT
    assert g.grounding_precision == pytest.approx(0.5, abs=1e-12)  # exact ratio
    assert g.grounding_recall == pytest.approx(1 / 3, abs=1e-12)  # exact ratio


@pytest.mark.criterion("C3")
def test_peer_names_has_the_hx_keys():
    # oracle: requirement 5 — case.ticker, universe and expected_exclusions;
    # fictional tickers are in no lexicon, so each has no names
    names = peer_names(case())
    assert set(names) == {"DEVA", "DEVB", "DEVC", "DEVD"}
    assert all(v == [] for v in names.values())


@pytest.mark.criterion("C1")
@given(order=st.permutations(["DEVA", "DEVB", "DEVC"]))
def test_row_order_never_matters(order):
    # oracle: requirements 1-3 — the stated rank decides; rows in any order change nothing
    g = grade_v2(case(), answer(ranks_text(2), rows(list(order))))
    assert g.grade is Grade.CORRECT
    assert g.checks == {"rank": True, "universe": True, "exclusions": True}


_NEW = ["QXA", "QXB", "QXC", "QXD", "QXE", "QXF"]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(new=st.permutations(_NEW), k=st.sampled_from([1, 2, 3]))
def test_renaming_tickers_never_changes_the_grade(new, k):
    # oracle: requirements 1-3 — names are read from the case, so a consistent renaming of the
    # case and the answer gives the grade of the original
    rename = dict(zip(TICKERS, list(new)[:4], strict=True))
    base = grade_v2(case(), answer(ranks_text(k)))
    moved = grade_v2(case(rename),
                     answer(ranks_text(k, rename["DEVB"], rename["DEVD"])))
    assert moved.grade is base.grade
    assert moved.checks == base.checks
    assert (moved.grade is Grade.CORRECT) == (k == 2)  # the key's rank is 2


@pytest.mark.criterion("C1")
@given(k=st.sampled_from([1, 2, 3]))
def test_correct_exactly_at_the_key_rank(k):
    # oracle: requirements 1-3 — the key's rank is 2
    g = grade_v2(case(), answer(ranks_text(k)))
    assert (g.grade is Grade.CORRECT) == (k == 2)


@pytest.mark.criterion("C2")
@given(cited=st.lists(st.sampled_from([1, 2, 3]), min_size=1, max_size=3, unique=True),
       unknown=st.lists(st.sampled_from([90, 91]), max_size=2, unique=True))
def test_grounding_counts_and_grade_unchanged(cited, unknown):
    # oracle: requirement 4 — grade_grounding's per-class rule, counted here: classes are
    # [1], [2], [3]; precision = matched / cited ids, recall = matched classes / 3
    produced = cited + unknown
    g = grade_v2(case(), answer(ranks_text(2), fact_ids=produced))
    all_cited = grade_v2(case(), answer(ranks_text(2), fact_ids=[1, 2, 3]))
    assert g.grounding_precision == pytest.approx(len(cited) / len(produced), abs=1e-12)  # exact
    assert g.grounding_recall == pytest.approx(len(cited) / 3, abs=1e-12)  # exact ratio
    assert g.grade is all_cited.grade is Grade.CORRECT


_POOL = ["DEVA", "DEVB", "DEVC", "DEVD", "DEVE", "DEVF"]


@pytest.mark.criterion("C3")
@given(universe=st.lists(st.sampled_from(_POOL), min_size=2, max_size=5, unique=True),
       n_excl=st.integers(min_value=0, max_value=1), subject_i=st.integers(min_value=0))
def test_peer_names_keys_are_the_hx_union(universe, n_excl, subject_i):
    # oracle: requirement 5 — the keys are the set union of ticker, universe and the
    # exclusions' tickers, computed here
    excluded = universe[-n_excl:] if n_excl else []
    ranked = [t for t in universe if t not in excluded]
    subject = ranked[subject_i % len(ranked)]
    c = EvalCase.from_dict({**hx_rank(), "universe": universe, "ticker": subject,
                            "expected_exclusions": [{"ticker": t, "reason": "other"}
                                                    for t in excluded]})
    assert set(peer_names(c)) == {subject, *universe, *excluded}
