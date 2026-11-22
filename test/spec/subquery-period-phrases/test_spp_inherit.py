"""C6-C7: the two rules wired into inheritance and the decomposer
(docs/specs/subquery-period-phrases.md, R3).

The decompositions are hand-built ``SubQuery`` lists and decomposer replies over synthetic
text. Expected texts and events are Requirement 3 applied by hand: a dependent sub-query with a
lone "from B" becomes "from fiscal year B to fiscal year E"; the excused "Current fiscal year
2024" lets a yearless dependent inherit 2024; everything else is dependent-subquery-period R3.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.retriever.xbrl.query_decomposer import SubQuery
from src.retriever.xbrl.subquery_period import inherit_dependent_periods


def _suffix(text, year):
    # dependent-subquery-period Requirement 3's form, by hand.
    t = text.rstrip()
    if t.endswith(("?", ".")):
        return f"{t[:-1].rstrip()} for fiscal year {year}{t[-1]}"
    return f"{t} for fiscal year {year}"


# --------------------------------------------------------------------------- C6
@pytest.mark.criterion("C6")
def test_c6_the_two_defect_decompositions(kit):
    # oracle: Requirement 3.2 by hand — defect A: sq2 is dependent with a lone "from FY24" and
    # the question's one later year is 2025; defect B: the screen states exactly 2024 (its
    # "Current fiscal year 2024" is excused), so the yearless dependent inherits 2024
    given_a = [kit.sq("sq1", kit.SQ1_A), kit.sq("sq2", kit.SQ2_A, ["sq1"])]
    before = kit.dicts(given_a)
    assert kit.dicts(inherit_dependent_periods(kit.Q_A, given_a)) == [
        {"id": "sq1", "query": kit.SQ1_A, "depends_on": []},
        {"id": "sq2", "query": kit.SQ2_A_REWRITTEN, "depends_on": ["sq1"]},
    ]
    assert kit.dicts(given_a) == before
    given_b = [kit.sq("sq1", kit.SQ1_B), kit.sq("sq2", kit.SQ2_B, ["sq1"])]
    assert kit.dicts(inherit_dependent_periods(kit.Q_B, given_b)) == [
        {"id": "sq1", "query": kit.SQ1_B, "depends_on": []},
        {"id": "sq2", "query": kit.SQ2_B_INHERITED, "depends_on": ["sq1"]},
    ]


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("subqueries", [
    # A non-dependent lone "from FY24".
    [("sq1", "Which 3 covered companies had the largest Net Income in FY25?", []),
     ("sq2", "How did Apple's earnings grow from FY24?", [])],
    # Dependent lookups with a lone "from FY24" and no growth or change word.
    [("sq1", "Which 3 covered companies had the largest Net Income in FY25?", []),
     ("sq2", "For <<TOP:sq1:1>>, what was revenue from FY24?", ["sq1"]),
     ("sq3", "For <<TOP:sq1:2>>, what was the total debt from fiscal 2024?", ["sq1"])],
    # Dependent sub-queries stating an end, a between range or a quarter.
    [("sq1", "Which 3 covered companies had the largest Net Income in FY25?", []),
     ("sq2", "For <<TOP:sq1:1>>, how did earnings grow from FY24 to FY25?", ["sq1"]),
     ("sq3", "For <<TOP:sq1:2>>, how did earnings grow between FY24 and FY25?", ["sq1"]),
     ("sq4", "For <<TOP:sq1:3>>, how did earnings grow from Q3 FY24?", ["sq1"])],
])
def test_c6_other_period_texts_are_unchanged(kit, subqueries):
    # oracle: Requirement 3.2 by hand — not dependent, no growth or change word (R2.1 cond. 6),
    # or no lone from-year: returned as given
    # (the question states two years, so nothing inherits). The paired defect-A call keeps the
    # test failing against today's behaviour.
    given_list = [kit.sq(i, q, d) for i, q, d in subqueries]
    before = kit.dicts(given_list)
    assert kit.dicts(inherit_dependent_periods(kit.Q_A, given_list)) == before
    out = inherit_dependent_periods(kit.Q_A, [kit.sq("sq1", kit.SQ1_A),
                                              kit.sq("sq2", kit.SQ2_A, ["sq1"])])
    assert out[1].query == kit.SQ2_A_REWRITTEN


@pytest.mark.criterion("C6")
def test_c6_a_dependent_of_a_rewritten_sub_query_gets_no_year(kit):
    # oracle: Requirement 3.2 by hand — in a one-year question (FY25), sq2's lone "from FY24"
    # is rewritten to 2024-2025; sq3 depends on sq2, whose returned text now states two years,
    # so sq3 does not inherit 2025
    question = ("Which 3 covered companies had the largest Net Income in FY25, how did the "
                "leader's earnings grow, and what is its debt?")
    out = inherit_dependent_periods(question, [
        kit.sq("sq1", "Which 3 covered companies had the largest Net Income in FY25?"),
        kit.sq("sq2", "For <<TOP:sq1:1>>, how did earnings grow from FY24?", ["sq1"]),
        kit.sq("sq3", "For <<TOP:sq2:1>>, what is the total debt?", ["sq2"]),
    ])
    assert kit.dicts(out) == [
        {"id": "sq1", "query": "Which 3 covered companies had the largest Net Income in FY25?",
         "depends_on": []},
        {"id": "sq2", "query": ("For <<TOP:sq1:1>>, how did earnings grow from fiscal year "
                                "2024 to fiscal year 2025?"), "depends_on": ["sq1"]},
        {"id": "sq3", "query": "For <<TOP:sq2:1>>, what is the total debt?",
         "depends_on": ["sq2"]},
    ]


# --------------------------------------------------------------------------- C6 property
# Dependent texts with no period; the first holds a growth word (R2.1 condition 6), the
# others hold none, so a lone "from B" on them is a lookup and is left alone.
_DEP_NONE = ["For {tok}, what is the percentage growth in revenue?",
             "Among {tok}, which has the highest Current Ratio?",
             "Of {tok}, which had the best operating margin"]
_HAS_GROWTH_WORD = {_DEP_NONE[0]}
_INDEPENDENT_NONE = ["What are Amazon's main logistics-related risk factors?"]
_SCREEN_NONE = ["Which 3 covered companies have the largest Total Debt?"]
_OTHER = [" in Q3?", " for the latest fiscal year?", " versus the prior year.",
          " from FY{b} to FY{e}?"]
_FROM_FORMS = [lambda b: f"FY{b % 100:02d}", lambda b: f"fiscal {b}",
               lambda b: f"fiscal year {b}", lambda b: f"{b}"]


@st.composite
def _decomposition(draw):
    # The question states the year y once, or y and a second distinct year z.
    y = draw(st.integers(min_value=2018, max_value=2035))
    z = draw(st.one_of(st.none(), st.integers(min_value=2018, max_value=2035).filter(
        lambda v: v != y)))
    question = f"Of the top 3 covered companies by Total Debt in fiscal {y}, answer each part."
    if z is not None:
        question += f" Compare with fiscal {z}."
    stated = {y} if z is None else {y, z}
    n = draw(st.integers(min_value=2, max_value=5))
    items = []  # (SubQuery, label, refs, base, has a growth word)
    for i in range(1, n + 1):
        sid = f"sq{i}"
        earlier = [f"sq{j}" for j in range(1, i)]
        dependent = bool(earlier) and draw(st.booleans())
        deps, tok, refs = [], "those companies", []
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
        pool = _DEP_NONE if dependent else (_SCREEN_NONE if i == 1 else _INDEPENDENT_NONE)
        template = draw(st.sampled_from(pool))
        growth = template in _HAS_GROWTH_WORD
        base_text = template.replace("{tok}", tok)
        label = draw(st.sampled_from(["none", "other", "from"]))
        b = draw(st.integers(min_value=2018, max_value=2034))
        if label == "other":
            text = base_text.rstrip("?.") + draw(st.sampled_from(_OTHER)).replace(
                "{b}", str(b)).replace("{e}", str(b + 1))
        elif label == "from":
            text = base_text.rstrip("?.") + f" from {draw(st.sampled_from(_FROM_FORMS))(b)}?"
        else:
            text = base_text
        items.append((SubQuery(id=sid, query=text, depends_on=list(deps)), label, refs, b,
                      growth))
    return question, (y if z is None else None), stated, items


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(case=_decomposition())
def test_c6_property_lone_from_dependents_rewritten_others_as_before(kit, case):
    # oracle: metamorphic, Requirement 3.2 over hand-labelled texts — (a) a dependent "from"
    # text with a growth word gets "from fiscal year B to fiscal year E" (E the one stated
    # year above B, else B + 1); (b) a dependent "none" text inherits " for fiscal year Y"
    # when the question has one year and its dependencies are "none"/"Y" after the walk,
    # every other text is unchanged; (c) ids, order, depends_on kept, input not modified;
    # (d) idempotent
    question, year, stated, items = case
    given_list = [s for s, _label, _refs, _b, _g in items]
    before = copy.deepcopy(kit.dicts(given_list))
    out = inherit_dependent_periods(question, given_list)
    assert kit.dicts(given_list) == before
    assert [(s.id, s.depends_on) for s in out] == [(s.id, s.depends_on) for s in given_list]
    final: dict[str, str] = {}
    for (s, label, refs, b, growth), got in zip(items, out, strict=True):
        dependent = bool(refs)
        if label == "from" and dependent and growth:
            later = [v for v in stated if v > b]
            end = later[0] if len(later) == 1 else b + 1
            stem = s.query[: s.query.index(" from ")]
            assert got.query == f"{stem} from fiscal year {b} to fiscal year {end}?"
            final[s.id] = "other"
        elif (label == "none" and dependent and year is not None
              and all(final[r] in ("none", "Y") for r in refs)):
            assert got.query == _suffix(s.query, year)
            final[s.id] = "Y"
        else:
            assert got.query == s.query
            if label == "from":
                # An unchanged "from B" (non-dependent, or a lookup with no growth word)
                # states exactly B: it counts as "Y" when B is Y.
                final[s.id] = "Y" if b == year else "other"
            else:
                final[s.id] = label
    assert kit.dicts(inherit_dependent_periods(question, out)) == kit.dicts(out)
    # A fixed rewriting pair keeps the property meaningful when the draw rewrites nothing.
    pair = inherit_dependent_periods(kit.Q_A, [kit.sq("sq1", kit.SQ1_A),
                                               kit.sq("sq2", kit.SQ2_A, ["sq1"])])
    assert pair[1].query == kit.SQ2_A_REWRITTEN


# --------------------------------------------------------------------------- C7
@pytest.mark.criterion("C7")
def test_c7_decompose_rewrites_and_logs(kit):
    # oracle: Requirements 3.2-3.3 by hand — defect A: one from_end_year event (sq2, 2024,
    # 2025) and no inherit_period; defect B: one inherit_period event (sq2, 2024) and no
    # from_end_year
    out, events = kit.decompose(kit.Q_A, kit.reply([
        kit.sq("sq1", kit.SQ1_A), kit.sq("sq2", kit.SQ2_A, ["sq1"])]))
    assert kit.dicts(out) == [
        {"id": "sq1", "query": kit.SQ1_A, "depends_on": []},
        {"id": "sq2", "query": kit.SQ2_A_REWRITTEN, "depends_on": ["sq1"]},
    ]
    found = kit.decisions(events, "from_end_year")
    assert [(e["subquery"], e["base_year"], e["end_year"]) for e in found] == [
        ("sq2", 2024, 2025)]
    assert kit.decisions(events, "inherit_period") == []

    out_b, events_b = kit.decompose(kit.Q_B, kit.reply([
        kit.sq("sq1", kit.SQ1_B), kit.sq("sq2", kit.SQ2_B, ["sq1"])]))
    assert kit.dicts(out_b) == [
        {"id": "sq1", "query": kit.SQ1_B, "depends_on": []},
        {"id": "sq2", "query": kit.SQ2_B_INHERITED, "depends_on": ["sq1"]},
    ]
    found_b = kit.decisions(events_b, "inherit_period")
    assert [(e["subquery"], e["fiscal_year"]) for e in found_b] == [("sq2", 2024)]
    assert kit.decisions(events_b, "from_end_year") == []


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@settings(max_examples=25)
@given(base=st.integers(min_value=2018, max_value=2034), data=st.data())
def test_c7_property_any_base_and_later_screen_year(kit, base, data):
    # oracle: metamorphic, Requirements 2-3 — a two-year question whose screen states E > B and
    # whose dependent sub-query says "from FY<B>": that sub-query states B to E, the screen is
    # as the reply wrote it, and one from_end_year event (sq2, B, E) is logged
    end = data.draw(st.integers(min_value=base + 1, max_value=2035))
    question = (f"Which 3 covered companies had the largest Net Income in FY{end % 100:02d}, "
                f"and how did the leader's earnings grow from FY{base % 100:02d}?")
    screen = f"Which 3 covered companies had the largest Net Income in FY{end % 100:02d}?"
    dep = f"For <<TOP:sq1:1>>, how did earnings grow from FY{base % 100:02d}?"
    out, events = kit.decompose(question, kit.reply([kit.sq("sq1", screen),
                                                     kit.sq("sq2", dep, ["sq1"])]))
    assert kit.dicts(out) == [
        {"id": "sq1", "query": screen, "depends_on": []},
        {"id": "sq2", "query": (f"For <<TOP:sq1:1>>, how did earnings grow from fiscal year "
                                f"{base} to fiscal year {end}?"), "depends_on": ["sq1"]},
    ]
    found = kit.decisions(events, "from_end_year")
    assert [(e["subquery"], e["base_year"], e["end_year"]) for e in found] == [
        ("sq2", base, end)]
