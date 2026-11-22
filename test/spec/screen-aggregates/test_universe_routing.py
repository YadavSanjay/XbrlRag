"""Spec tests for screen-aggregates C1-C5 and C7: group mentions resolved by rule and routed
to a screen (docs/specs/screen-aggregates.md, Requirements 1-4 and 6). Pure functions.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.universe_routing import (
    aggregate_cue,
    find_universe_mentions,
    group_plan_errors,
    split_company_mentions,
    universe_aliases,
    universe_intent,
)

# The memberships of configs/eval/v2/groups.yaml, written out (C1, C3 are pure).
GROUPS = {
    "semiconductors": ["AMD", "AVGO", "INTC", "MU", "NVDA", "QCOM", "TSM", "TXN"],
    "retailers": ["AMZN", "COST", "HD", "WMT"],
    "big_tech": ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"],
}
# A synthetic alias table: the mechanism, not the committed content.
ALIASES = {
    "covered": ["covered companies"],
    "semiconductors": ["semiconductor companies", "chip designers"],
    "retailers": ["retail chains"],
    "big_tech": ["mega cap tech"],
}

GROUPS_FILE = Path(__file__).resolve().parents[3] / "configs" / "eval" / "v2" / "groups.yaml"
COMMITTED_GROUPS = yaml.safe_load(GROUPS_FILE.read_text(encoding="utf-8"))["groups"]

# Corpus filers' names and tickers (entity_master, public SEC registrant names).
FILER_NAMES = [
    "Apple Inc.", "Taiwan Semiconductor Manufacturing Company Limited",
    "Texas Instruments Incorporated", "Micron Technology, Inc.", "Advanced Micro Devices, Inc.",
    "The Home Depot, Inc.", "Walmart Inc.", "Costco Wholesale Corporation", "Amazon.com, Inc.",
    "Microsoft Corporation", "NVIDIA Corporation", "Broadcom Inc.", "Intel Corporation",
    "QUALCOMM Incorporated", "Alphabet Inc.", "Meta Platforms, Inc.", "Tesla, Inc.",
    "Oracle Corporation", "Adobe Inc.", "Abbott Laboratories", "Constellation Energy Corporation",
    "Vistra Corp.",
    "AAPL", "TSM", "TXN", "MU", "AMD", "HD", "WMT", "COST", "AMZN", "MSFT", "NVDA", "AVGO",
    "INTC", "QCOM", "GOOGL", "META", "TSLA", "ORCL", "ADBE", "ABT", "CEG", "VST",
]

INTENTS = ["NUMERIC_LOOKUP", "NUMERIC_COMPUTE", "NUMERIC_SCREEN", "NARRATIVE", "HYBRID"]
NUMERIC = {"NUMERIC_LOOKUP", "NUMERIC_COMPUTE", "NUMERIC_SCREEN"}


def _words(text):
    """Requirement 1's normalization, written out."""
    return re.sub(r"[^a-z0-9&]+", " ", text.lower()).split()


def _phrases(groups, aliases):
    out = [(tuple(g.replace("_", " ").split()), g) for g in groups]
    out += [(tuple(_words(p)), u) for u, ps in aliases.items() for p in ps]
    return out


def _reference(words, phrases):
    """Requirement 1's scan, written out: longest phrase at each position, left to right."""
    found, i = [], 0
    while i < len(words):
        best = None
        for p, u in phrases:
            if tuple(words[i:i + len(p)]) == p and (best is None or len(p) > len(best[0])):
                best = (p, u)
        if best is None:
            i += 1
            continue
        if best[1] not in found:
            found.append(best[1])
        i += len(best[0])
    return found


# ------------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_find_universe_mentions_points():
    # oracle: Requirement 1 applied by hand to each text
    cases = [
        ("What was the combined capex of the retailers in fiscal 2024?", ["retailers"]),
        ("Median R&D of the semiconductor companies, FY2023", ["semiconductors"]),
        ("the retailers' total debt", ["retailers"]),
        ("big-tech capex in 2024", ["big_tech"]),
        ("Rank the retailers against big tech and the chip designers",
         ["retailers", "big_tech", "semiconductors"]),
        ("big tech, the retailers, and big tech again", ["big_tech", "retailers"]),
        ("combined net income of all covered companies", ["covered"]),
        ("Taiwan Semiconductor's revenue in 2024", []),
        ("the period covered by the report", []),
        ("mega cap tech median", ["big_tech"]),
    ]
    for text, expected in cases:
        assert find_universe_mentions(text, GROUPS, ALIASES) == expected, text


@pytest.mark.criterion("C1")
def test_longest_phrase_wins_and_ignore_masks():
    # oracle: Requirement 1 — longest phrase at a position, scan resumes after it; an
    # ignored string's words match nothing
    groups = {**GROUPS, "tech": ["AAPL"]}
    assert find_universe_mentions("big tech companies", groups, {}) == ["big_tech"]
    assert find_universe_mentions("tech then big tech", groups, {}) == ["tech", "big_tech"]
    aliases = {"retailers": ["retail"], "semiconductors": ["retail chips"]}
    assert find_universe_mentions("retail chips revenue", GROUPS, aliases) == ["semiconductors"]
    assert find_universe_mentions("retail revenue", GROUPS, aliases) == ["retailers"]
    text = "What was Big Tech Holdings' revenue?"
    assert find_universe_mentions(text, GROUPS, ALIASES) == ["big_tech"]
    assert find_universe_mentions(text, GROUPS, ALIASES, ignore=["Big Tech Holdings"]) == []
    assert find_universe_mentions(text + " versus big tech", GROUPS, ALIASES,
                                  ignore=["Big Tech Holdings"]) == ["big_tech"]


@pytest.mark.criterion("C1")
def test_bad_alias_tables_raise():
    # oracle: Requirement 1 — unknown key, empty phrase, a phrase of two universes
    assert find_universe_mentions("the retailers", GROUPS, ALIASES) == ["retailers"]
    with pytest.raises(ValueError, match="megacaps"):
        find_universe_mentions("the retailers", GROUPS, {"megacaps": ["mega caps"]})
    with pytest.raises(ValueError, match="--"):
        find_universe_mentions("the retailers", GROUPS, {"retailers": ["--"]})
    with pytest.raises(ValueError, match="chip designers"):
        find_universe_mentions("the retailers", GROUPS,
                               {"retailers": ["chip designers"],
                                "semiconductors": ["Chip Designers"]})
    with pytest.raises(ValueError, match="big tech"):
        find_universe_mentions("the retailers", GROUPS, {"retailers": ["big tech"]})


_PHRASE_WORDS = sorted({w for p, _u in _phrases(GROUPS, ALIASES) for w in p})
_FILLER = ["what", "was", "the", "capex", "of", "in", "fy2024", "revenue", "median", "rank"]
_SEPARATORS = [" ", ", ", " - ", "-", ". ", "  ", " / ", "'", "' "]


@st.composite
def _texts(draw):
    words = draw(st.lists(st.sampled_from(_PHRASE_WORDS + _FILLER), max_size=8))
    phrase, _u = draw(st.sampled_from(_phrases(GROUPS, ALIASES)))
    at = draw(st.integers(0, len(words)))
    words = words[:at] + list(phrase) + words[at:]
    cased = [draw(st.sampled_from([w, w.upper(), w.title()])) for w in words]
    seps = [draw(st.sampled_from(_SEPARATORS)) for _ in cased]
    text = "".join(w + s for w, s in zip(cased, seps, strict=True)).strip()
    return words, text


@pytest.mark.criterion("C1")
@given(_texts(), st.lists(st.sampled_from(_FILLER), max_size=3),
       st.lists(st.sampled_from(_FILLER), max_size=3))
def test_find_universe_mentions_matches_the_rule(case, before, after):
    # oracle: the Requirement 1 scan written out (_reference) over the plain words; case,
    # punctuation and non-phrase words added before or after change nothing
    words, text = case
    expected = _reference(words, _phrases(GROUPS, ALIASES))
    assert expected  # every generated text holds a phrase
    assert find_universe_mentions(" ".join(words), GROUPS, ALIASES) == expected
    assert find_universe_mentions(text, GROUPS, ALIASES) == expected
    padded = " ".join(before + [text] + after)
    got = find_universe_mentions(padded, GROUPS, ALIASES)
    assert got == expected
    assert len(set(got)) == len(got) and set(got) <= {*GROUPS, "covered"}


# ------------------------------------------------------------------------------- C2
def _contains(haystack, needle):
    return any(haystack[i:i + len(needle)] == needle for i in range(len(haystack)))


@pytest.mark.criterion("C2")
def test_committed_alias_table_shape():
    # oracle: Requirement 1 — keys are `covered` and committed group ids; `covered` has
    # "covered companies"; no phrase of two universes; no phrase inside a filer's name
    aliases = universe_aliases()
    assert isinstance(aliases, dict) and aliases
    assert set(aliases) <= {"covered", *COMMITTED_GROUPS}
    assert "covered companies" in [" ".join(_words(p)) for p in aliases["covered"]]
    owner = {}
    for u, phrases in aliases.items():
        for p in phrases:
            key = tuple(_words(p))
            assert key, (u, p)
            assert owner.setdefault(key, u) == u, (p, owner[key], u)
            assert key not in {tuple(g.replace("_", " ").split()) for g in COMMITTED_GROUPS
                               if g != u}, p
            for name in FILER_NAMES:
                assert not _contains(_words(name), list(key)), (p, name)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_every_committed_phrase_resolves_to_its_own_universe():
    # oracle: Requirement 1 — each group's own words and each alias name that universe, also
    # inside a question
    aliases = universe_aliases()
    phrases = [(g.replace("_", " "), g) for g in COMMITTED_GROUPS]
    phrases += [(p, u) for u, ps in aliases.items() for p in ps]
    assert len(phrases) > len(COMMITTED_GROUPS)
    for phrase, u in phrases:
        assert find_universe_mentions(phrase, COMMITTED_GROUPS, aliases) == [u], phrase
        question = f"What was the combined revenue of the {phrase} in fiscal 2024?"
        assert find_universe_mentions(question, COMMITTED_GROUPS, aliases) == [u], question


# ------------------------------------------------------------------------------- C3
# Each pool mention with the universes Requirement 1 finds in it, by hand.
MENTION_POOL = {
    "Apple": [],
    "the big tech companies": ["big_tech"],
    "Walmart": [],
    "the retailers": ["retailers"],
    "Taiwan Semiconductor": [],
    "chip designers": ["semiconductors"],
    "Home Depot": [],
    "retailers and big tech": ["retailers", "big_tech"],
}


@pytest.mark.criterion("C3")
def test_split_company_mentions_points():
    # oracle: Requirement 2 applied by hand
    assert split_company_mentions(["the big tech companies", "Apple"], GROUPS, ALIASES) == (
        ["Apple"], ["big_tech"])
    assert split_company_mentions(["Apple", "Taiwan Semiconductor"], GROUPS, ALIASES) == (
        ["Apple", "Taiwan Semiconductor"], [])
    assert split_company_mentions(
        ["the retailers", "Walmart", "retailers and big tech", "chip designers"], GROUPS,
        ALIASES) == (["Walmart"], ["retailers", "big_tech", "semiconductors"])


@pytest.mark.criterion("C3")
@given(st.lists(st.sampled_from(sorted(MENTION_POOL)), min_size=1, max_size=6))
def test_split_company_mentions_partitions(mentions):
    # oracle: Requirement 2 — kept is the mentions naming no universe, in order; universes
    # the others' universes in order of first occurrence (MENTION_POOL labels by hand)
    mentions = mentions + ["the retailers", "Apple"]
    kept, universes = split_company_mentions(mentions, GROUPS, ALIASES)
    assert kept == [m for m in mentions if not MENTION_POOL[m]]
    expected = []
    for m in mentions:
        for u in MENTION_POOL[m]:
            if u not in expected:
                expected.append(u)
    assert universes == expected


# ------------------------------------------------------------------------------- C4
CUE_CASES = [
    ("What was the median R&D of the chipmakers in FY2023?", "median"),
    ("Medians of capex, retailers, FY2024", "median"),
    ("What was the combined capex of the retailers in fiscal 2024?", "sum"),
    ("aggregate dividends paid, big tech, 2024", "sum"),
    ("aggregated operating income of the covered companies", "sum"),
    ("How much did the retailers spend on capex collectively?", "sum"),
    ("How much debt did big tech carry altogether?", "sum"),
    ("Net income summed across the retailers for 2023", "sum"),
    ("What is the sum of the retailers' capex?", "sum"),
    ("How much did big tech spend on R&D in total in 2024?", "sum"),
    ("The retailers' capex, taken together, for FY2024", "sum"),
    ("What were the total assets of the retailers in 2024?", None),
    ("What was the average capex of the retailers?", None),
    ("What was the mean R&D of big tech?", None),
    ("How many shares did Apple have outstanding?", None),
    ("Median of the combined capex of the retailers", None),
    ("What was Apple's revenue in fiscal 2024?", None),
]


@pytest.mark.criterion("C4")
def test_aggregate_cue_points():
    # oracle: Requirement 3's word lists applied by hand
    for query, expected in CUE_CASES:
        assert aggregate_cue(query) == expected, query


_CUE_FILLER = ["what", "was", "capex", "the", "retailers", "fy2024", "revenue"]


@pytest.mark.criterion("C4")
@given(st.sampled_from([c for c in CUE_CASES if c[1] is not None]),
       st.lists(st.sampled_from(_CUE_FILLER), max_size=3),
       st.lists(st.sampled_from(_CUE_FILLER), max_size=3),
       st.sampled_from([str.upper, str.lower, str.title]),
       st.sampled_from([" ", ", ", " - ", "; "]))
def test_aggregate_cue_ignores_case_punctuation_and_filler(case, before, after, recase, sep):
    # oracle: Requirement 3 — the cue depends on the words only
    query, expected = case
    text = sep.join(before + [recase(query)] + after)
    assert aggregate_cue(text) == expected


# ------------------------------------------------------------------------------- C5
def _expected_intent(intent, universes, companies, cue):
    """Requirement 4 written out."""
    if len(universes) != 1 or companies > 0:
        return intent
    if intent in NUMERIC:
        return "NUMERIC_SCREEN"
    return "NUMERIC_SCREEN" if cue is not None else intent


@pytest.mark.criterion("C5")
def test_universe_intent_table():
    # oracle: Requirement 4, every combination checked against the rule and a few by hand
    assert universe_intent("NUMERIC_COMPUTE", ["retailers"], 0, "sum") == "NUMERIC_SCREEN"
    assert universe_intent("NUMERIC_LOOKUP", ["big_tech"], 0, None) == "NUMERIC_SCREEN"
    assert universe_intent("NARRATIVE", ["retailers"], 0, "median") == "NUMERIC_SCREEN"
    assert universe_intent("NARRATIVE", ["retailers"], 0, None) == "NARRATIVE"
    assert universe_intent("NUMERIC_COMPUTE", ["retailers"], 1, "sum") == "NUMERIC_COMPUTE"
    assert universe_intent("NUMERIC_COMPUTE", ["retailers", "big_tech"], 0, "sum") == (
        "NUMERIC_COMPUTE")
    assert universe_intent("NUMERIC_COMPUTE", [], 0, "sum") == "NUMERIC_COMPUTE"
    for intent in INTENTS:
        for universes in ([], ["retailers"], ["retailers", "big_tech"]):
            for companies in (0, 1):
                for cue in (None, "sum", "median"):
                    assert universe_intent(intent, universes, companies, cue) == (
                        _expected_intent(intent, universes, companies, cue))


@pytest.mark.criterion("C5")
@given(st.sampled_from(INTENTS), st.lists(st.sampled_from(["covered", *GROUPS]), max_size=3),
       st.integers(0, 3), st.sampled_from([None, "sum", "median"]))
def test_universe_intent_only_ever_widens_to_screen(intent, universes, companies, cue):
    # oracle: Requirement 4 — the result is the input or NUMERIC_SCREEN, never narrative for
    # a numeric input
    got = universe_intent(intent, universes, companies, cue)
    assert got == _expected_intent(intent, universes, companies, cue)
    assert got in (intent, "NUMERIC_SCREEN")
    if intent in NUMERIC:
        assert got in NUMERIC


# ------------------------------------------------------------------------------- C7
def _screen(sid="s1", universe="<absent>"):
    step = {"id": sid, "op": "screen", "fy": 2024, "dimension": "NONE", "metric_kind": "value",
            "order": "desc", "concept_family": "Capex", "top_n": None}
    if universe != "<absent>":
        step["universe"] = universe
    return step


def _agg(of="s1", fn="sum", sid="a1"):
    return {"id": sid, "op": "aggregate", "of": of, "fn": fn}


LOOKUP = {"id": "l1", "op": "lookup", "cik": "0000104169", "fy": 2024, "concept_family": "Capex",
          "dimensions": "NONE", "section_hint": None}


@pytest.mark.criterion("C7")
def test_group_plan_errors_points():
    # oracle: Requirement 6 applied by hand
    good = {"steps": [_screen(universe="retailers"), _agg()]}
    assert group_plan_errors(good, "retailers", "sum") == []
    assert group_plan_errors({"steps": [_screen(), _agg()]}, "covered", "sum") == []
    assert group_plan_errors({"steps": [_screen(universe=None)]}, "covered", None) == []

    errors = group_plan_errors({"steps": [_screen(), _agg()]}, "retailers", "sum")
    assert len(errors) == 1
    assert all(s in errors[0] for s in ("universe", "covered", "retailers", "s1", "0"))

    for plan in ({"steps": [_screen(universe="retailers")]},
                 {"steps": [_screen(universe="retailers"), _agg(fn="median")]},
                 {"steps": [LOOKUP, _screen(universe="retailers"), _agg(of="l1")]}):
        errors = group_plan_errors(plan, "retailers", "sum")
        assert len(errors) == 1 and "aggregate" in errors[0] and "sum" in errors[0], plan

    both = group_plan_errors({"steps": [_screen(universe="big_tech")]}, "retailers", "median")
    assert len(both) == 2
    assert "big_tech" in both[0] and "retailers" in both[0]
    assert "aggregate" in both[1] and "median" in both[1]
    assert group_plan_errors({"error": "cannot plan"}, "retailers", "sum") == []
    assert group_plan_errors("not a plan", "retailers", "sum") == []


@pytest.mark.criterion("C7")
@given(st.lists(st.sampled_from(["covered", "<absent>", None, *GROUPS]), min_size=1,
                max_size=4),
       st.sampled_from([None, "sum", "median", "mean", "count"]))
def test_group_plan_errors_one_per_mismatched_screen(universes, fn):
    # oracle: Requirement 6 — nothing to check gives []; with a universe, one error per
    # screen step of another universe (absent/null counting as covered)
    steps = [_screen(f"s{i}", u) for i, u in enumerate(universes)]
    if fn is not None:
        steps.append(_agg(of="s0", fn=fn))
    plan = {"steps": steps}
    assert group_plan_errors(plan, None, None) == []
    as_named = ["covered" if u in (None, "<absent>") else u for u in universes]
    for target in ("retailers", "covered"):
        mismatched = [u for u in as_named if u != target]
        errors = group_plan_errors(plan, target, None)
        assert len(errors) == len(mismatched)
        for e, u in zip(errors, mismatched, strict=True):
            assert "universe" in e and u in e and target in e
    cue_errors = group_plan_errors(plan, None, "sum")
    assert len(cue_errors) == (0 if fn == "sum" else 1)
