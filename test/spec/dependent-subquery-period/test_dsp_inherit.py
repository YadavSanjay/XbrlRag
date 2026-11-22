"""C3: inheritance (docs/specs/dependent-subquery-period.md, R3).

The decompositions are hand-built ``SubQuery`` lists (the 094b one copied from its stored
trace). Expected texts are Requirement 3 applied by hand: only a dependent sub-query with no
period of its own, in a question stating one fiscal year Y, whose dependencies state no other
period, gets " for fiscal year Y" before its final "?" or "." (or at the end).
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.query_decomposer import SubQuery
from src.retriever.xbrl.subquery_period import inherit_dependent_periods

PARENT_2024 = ("Of the 3 largest of the covered companies by Total Debt in FY24, which had the "
               "highest operating margin?")


def _suffix(text, year):
    # Requirement 3's form, by hand.
    t = text.rstrip()
    if t.endswith(("?", ".")):
        return f"{t[:-1].rstrip()} for fiscal year {year}{t[-1]}"
    return f"{t} for fiscal year {year}"


@pytest.mark.criterion("C3")
def test_c3_the_094b_decomposition(kit):
    # oracle: Requirement 3 by hand — the question states FY24 once; sq2 depends on sq1, writes
    # no period, and sq1 states exactly 2024, so sq2 inherits 2024; sq1 is not dependent
    given_list = [kit.sq("sq1", kit.SQ1_094B), kit.sq("sq2", kit.SQ2_094B, ["sq1"])]
    before = kit.dicts(given_list)
    out = inherit_dependent_periods(kit.Q094B, given_list)
    assert kit.dicts(out) == [
        {"id": "sq1", "query": kit.SQ1_094B, "depends_on": []},
        {"id": "sq2", "query": kit.SQ2_094B_INHERITED, "depends_on": ["sq1"]},
    ]
    assert kit.dicts(given_list) == before
    assert out is not given_list


@pytest.mark.criterion("C3")
def test_c3_either_dependency_marker_is_enough(kit):
    # oracle: Requirement 3 by hand — depends_on alone, or a <<TOP…>> token alone, makes the
    # sub-query dependent
    by_list = inherit_dependent_periods(PARENT_2024, [
        kit.sq("sq1", "Which 3 covered companies have the largest Total Debt in FY24?"),
        kit.sq("sq2", "Which of those companies had the highest operating margin?", ["sq1"]),
    ])
    by_token = inherit_dependent_periods(PARENT_2024, [
        kit.sq("sq1", "Which 3 covered companies have the largest Total Debt in FY24?"),
        kit.sq("sq2", "Among <<TOP:sq1:3>>, which had the highest operating margin?"),
    ])
    assert by_list[1].query == ("Which of those companies had the highest operating margin "
                                "for fiscal year 2024?")
    assert by_list[1].depends_on == ["sq1"]
    assert by_token[1].query == ("Among <<TOP:sq1:3>>, which had the highest operating margin "
                                 "for fiscal year 2024?")
    assert by_token[1].depends_on == []


@pytest.mark.criterion("C3")
def test_c3_a_chain_and_the_ending_forms(kit):
    # oracle: Requirement 3 by hand — sq2 inherits from the parent; sq3 depends on sq2, whose
    # returned text now states exactly 2024, so sq3 inherits too; no final punctuation means the
    # suffix goes at the end, a final "." stays last
    out = inherit_dependent_periods(PARENT_2024, [
        kit.sq("sq1", "Rank the covered companies by Total Debt"),
        kit.sq("sq2", "For <<TOP:sq1:3>>, list operating margin", ["sq1"]),
        kit.sq("sq3", "For <<TOP:sq2:1>>, state its revenue growth.  ", ["sq2"]),
    ])
    assert kit.dicts(out) == [
        {"id": "sq1", "query": "Rank the covered companies by Total Debt", "depends_on": []},
        {"id": "sq2", "query": "For <<TOP:sq1:3>>, list operating margin for fiscal year 2024",
         "depends_on": ["sq1"]},
        {"id": "sq3", "query": "For <<TOP:sq2:1>>, state its revenue growth for fiscal year 2024.",
         "depends_on": ["sq2"]},
    ]


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("question, subqueries", [
    # A two-year question: no one fiscal year.
    ("Looking at the top 3 of the covered companies by total debt in FY24, by how much did the "
     "leader's capital expenditures change from FY23?",
     [("sq1", "Which are the top 3 covered companies by total debt in FY24?", []),
      ("sq2", "For <<TOP:sq1:1>>, by how much did capital expenditures change?", ["sq1"])]),
    # A question with no year.
    ("Which companies have significant China revenue exposure, and what are the top 3 such "
     "companies' supply-chain risks?",
     [("sq1", "Which companies have significant China revenue exposure?", []),
      ("sq2", "For <<TOP:sq1:3>>, what are the main supply-chain risk factors?", ["sq1"])]),
    # A dependency stating another year.
    (PARENT_2024,
     [("sq1", "Which 3 covered companies have the largest Total Debt in FY23?", []),
      ("sq2", "Among <<TOP:sq1:3>>, which had the highest operating margin?", ["sq1"])]),
    # A dependency stating a quarter.
    (PARENT_2024,
     [("sq1", "Which 3 covered companies have the largest Total Debt in Q3 FY24?", []),
      ("sq2", "Among <<TOP:sq1:3>>, which had the highest operating margin?", ["sq1"])]),
    # Dependent sub-queries with their own year, quarter or relative period.
    (PARENT_2024,
     [("sq1", "Which 3 covered companies have the largest Total Debt in FY24?", []),
      ("sq2", "Among <<TOP:sq1:3>>, which had the highest operating margin in FY23?", ["sq1"]),
      ("sq3", "For <<TOP:sq1:1>>, what was Q3 revenue?", ["sq1"]),
      ("sq4", "For <<TOP:sq1:1>>, what was revenue in the latest fiscal year?", ["sq1"])]),
    # A non-dependent sub-query with no period.
    (PARENT_2024,
     [("sq1", "Which 3 covered companies have the largest Total Debt in FY24?", []),
      ("sq2", "What are Amazon's main logistics-related risk factors?", [])]),
])
def test_c3_nothing_is_inherited(kit, question, subqueries):
    # oracle: Requirement 3 by hand — each case fails one condition (one fiscal year, a
    # dependency stating only Y, dependent, no own period), so every sub-query is returned as
    # given. The paired 094b call keeps the test meaningful against a stub returning its input.
    given_list = [kit.sq(i, q, d) for i, q, d in subqueries]
    before = kit.dicts(given_list)
    assert kit.dicts(inherit_dependent_periods(question, given_list)) == before
    assert kit.dicts(given_list) == before
    sq2 = inherit_dependent_periods(kit.Q094B, [kit.sq("sq1", kit.SQ1_094B),
                                                kit.sq("sq2", kit.SQ2_094B, ["sq1"])])[1]
    assert sq2.query == kit.SQ2_094B_INHERITED


@pytest.mark.criterion("C3")
def test_c3_one_sub_query_is_never_changed(kit):
    # oracle: Requirement 3 by hand — a single sub-query depends on nothing, so a non-compound
    # question (or a passthrough) is returned as given, even with no period
    for text in ("What was Apple's revenue?", kit.Q094B):
        given_list = [kit.sq("sq1", text)]
        assert kit.dicts(inherit_dependent_periods(kit.Q094B, given_list)) == kit.dicts(given_list)
    assert inherit_dependent_periods(kit.Q094B, [kit.sq("sq1", kit.SQ1_094B),
                                                 kit.sq("sq2", kit.SQ2_094B, ["sq1"])]
                                     )[1].query == kit.SQ2_094B_INHERITED


# --------------------------------------------------------------------------- property
# Texts labelled by hand: "none" states no period; "Y" states exactly the parent's year (form
# filled per example); "other" states another period.
_SCREEN_NONE = ["Which 3 covered companies have the largest Total Debt?",
                "Rank the retailers by Assets, Current"]
_DEP_NONE = ["For {tok}, what is the percentage growth in revenue?",
             "Among {tok}, which has the highest Current Ratio?",
             "For {tok}, how much did total current liabilities change.",
             "Of {tok}, which had the best operating margin"]
_INDEPENDENT_NONE = ["What are Amazon's main logistics-related risk factors?",
                     "What are the main supply-chain risks"]
_Y_FORMS = [lambda y: f" in FY{y % 100:02d}?", lambda y: f" for fiscal year {y}."]
_OTHER = [" in Q3?", " for the latest fiscal year?", " versus the prior year."]


@st.composite
def _decomposition(draw):
    year = draw(st.integers(min_value=2018, max_value=2035))
    n = draw(st.integers(min_value=2, max_value=5))
    items = []  # (SubQuery, label, dependent)
    for i in range(1, n + 1):
        sid = f"sq{i}"
        earlier = [f"sq{j}" for j in range(1, i)]
        dependent = bool(earlier) and draw(st.booleans())
        deps, tok = [], "those companies"
        if dependent:
            refs = draw(st.lists(st.sampled_from(earlier), min_size=1, max_size=len(earlier),
                                 unique=True))
            marker = draw(st.sampled_from(["list", "token", "both"]))
            if marker in ("token", "both"):
                tok = f"<<TOP:{refs[0]}:{draw(st.integers(1, 5))}>>"
            if marker in ("list", "both"):
                deps = refs
            if marker == "token":
                refs = refs[:1]
        else:
            refs = []
        pool = _DEP_NONE if dependent else (_SCREEN_NONE if i == 1 else _INDEPENDENT_NONE)
        base = draw(st.sampled_from(pool)).replace("{tok}", tok)
        label = draw(st.sampled_from(["none", "Y", "other"]))
        if label == "Y":
            text = base.rstrip("?.") + draw(st.sampled_from(_Y_FORMS))(year)
        elif label == "other":
            text = base.rstrip("?.") + draw(st.sampled_from(_OTHER))
        else:
            text = base
        items.append((kit_sq(sid, text, deps), label, refs))
    question = f"Of the top 3 covered companies by Total Debt in fiscal {year}, answer each part."
    return year, question, items


def kit_sq(sid, text, deps):
    return SubQuery(id=sid, query=text, depends_on=list(deps))


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(case=_decomposition())
def test_c3_property_only_yearless_dependents_change_and_idempotent(kit, case):
    # oracle: metamorphic, Requirement 3 over hand-labelled texts — (a) own period: unchanged;
    # (b) non-dependent: unchanged; (c) dependent "none" whose dependencies are all "none"/"Y"
    # after the walk: exactly the suffix; (d) idempotent; (e) ids, order, depends_on kept
    year, question, items = case
    given_list = [s for s, _label, _refs in items]
    before = copy.deepcopy(kit.dicts(given_list))
    out = inherit_dependent_periods(question, given_list)
    assert kit.dicts(given_list) == before
    assert [(s.id, s.depends_on) for s in out] == [(s.id, s.depends_on) for s in given_list]
    final_label: dict[str, str] = {}
    for (s, label, refs), got in zip(items, out, strict=True):
        dependent = bool(refs)
        if label == "none" and dependent and all(final_label[r] in ("none", "Y") for r in refs):
            assert got.query == _suffix(s.query, year)
            final_label[s.id] = "Y"
        else:
            assert got.query == s.query
            final_label[s.id] = label
    assert kit.dicts(inherit_dependent_periods(question, out)) == kit.dicts(out)
    # A fixed inheriting pair keeps the property meaningful when the draw inherits nothing.
    dep = _DEP_NONE[0].replace("{tok}", "<<TOP:sq1:1>>")
    pair = inherit_dependent_periods(question, [kit.sq("sq1", _SCREEN_NONE[0]),
                                                kit.sq("sq2", dep, ["sq1"])])
    assert pair[1].query == _suffix(dep, year)
