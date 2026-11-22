"""C2: the values a re-grade reads (docs/specs/regrade-raw-values.md, requirement 2).

Branches: a stored raw list; else a rebuild from the stored text with the system's own parser
(``extract_stated_numbers`` for xbrlrag arms, ``parse_structured_answer`` for the baseline
arms); else the stored ``numeric_values``.
"""

from __future__ import annotations

import copy
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import extract_stated_numbers, parse_structured_answer
from src.eval.core.regrade import regrade_values

PROSE = "Revenue was 3,500,000,000 USD and net income was 400,000,000 USD."
PROSE_VALUES = [3_500_000_000.0, 400_000_000.0]
REPLY = ('{"value": 42.5, "rows": [{"entity": "AAA", "value": 7}], '
         '"note": "about 9 units", "refused": false}')
# parse_structured_answer reads "value" and each row's "value"; the note is not read.
REPLY_STRUCTURED = [42.5, 7.0]
# extract_stated_numbers reads every amount the reply's text states, the note's 9 too.
REPLY_STATED = [42.5, 7.0, 9.0]
STORED = [-1.0]
MU = 21_244_000_000.0


def _payload(text, *, raw=None, stored=STORED):
    produced = {"text": text, "numeric_values": list(stored), "rows": [], "fact_ids": [],
                "chunk_ids": [], "refused": False, "hedged": False, "error": None}
    if raw is not None:
        produced["raw_numeric_values"] = list(raw)
    return produced


@pytest.mark.criterion("C2")
def test_a_stored_raw_list_wins_over_the_text_for_every_system(kit):
    # oracle: requirement 2 step 1 by hand — raw_numeric_values is a list, so it is returned
    # whatever the system and the text
    for system in (*kit.XBRLRAG_ARMS, *kit.BASELINE_ARMS, "html_rag", "unknown_arm", None):
        got = regrade_values(_payload(PROSE, raw=[1.0, 2.0]), system)
        assert got == [1.0, 2.0], system


@pytest.mark.criterion("C2")
def test_xbrlrag_arms_rebuild_with_extract_stated_numbers(kit):
    # oracle: requirement 2 step 2 by hand — the prose states 3,500,000,000 and 400,000,000;
    # the JSON reply's text states 42.5, 7 and 9 (extract_stated_numbers reads all three)
    for system in kit.XBRLRAG_ARMS:
        assert regrade_values(_payload(PROSE), system) == PROSE_VALUES, system
        assert regrade_values(_payload(REPLY), system) == REPLY_STATED, system


@pytest.mark.criterion("C2")
def test_baseline_arms_rebuild_with_parse_structured_answer(kit):
    # oracle: requirement 2 step 2 by hand — a JSON reply gives its value and row values
    # (42.5, 7); a prose reply falls back to the stated amounts (3,500,000,000, 400,000,000)
    for system in kit.BASELINE_ARMS:
        assert regrade_values(_payload(REPLY), system) == REPLY_STRUCTURED, system
        assert regrade_values(_payload(PROSE), system) == PROSE_VALUES, system


@pytest.mark.criterion("C2")
def test_other_systems_and_empty_text_return_the_stored_values(kit):
    # oracle: requirement 2 step 3 by hand — html_rag, an unknown system and None read the
    # stored values; so does a known arm whose stored text is empty or missing
    stored = [3.0, 4.0]
    for system in ("html_rag", "unknown_arm", None):
        assert regrade_values(_payload(PROSE, stored=stored), system) == stored, system
    for system in ("xbrlrag_full", "naive_rag_filtered"):
        assert regrade_values(_payload("", stored=stored), system) == stored, system
        missing = _payload("", stored=stored)
        del missing["text"]
        assert regrade_values(missing, system) == stored, system
    assert regrade_values({"text": PROSE}, "html_rag") == []


@pytest.mark.criterion("C2")
def test_the_real_filtered_payload_rebuilds_the_median(kit):
    # oracle: requirement 2 step 2 by hand — the stored values [7, 8, 7, 7, 7, 7, 8, 7] are
    # what grader filters left; the stored text states MU's 21,244,000,000 (fixture)
    produced = kit.real_payload()
    assert produced["numeric_values"] == [7.0, 8.0, 7.0, 7.0, 7.0, 7.0, 8.0, 7.0]
    got = regrade_values(produced, "xbrlrag_full")
    # rel 1e-12: a parsed amount compared with the literal, no arithmetic
    assert any(v == pytest.approx(MU, rel=1e-12) for v in got)


_AMOUNTS = st.lists(st.integers(1, 10**6).map(lambda v: v * 1000), min_size=1, max_size=5)


def _prose(amounts):
    return " ".join(f"Item {chr(65 + i)} was {a:,} USD." for i, a in enumerate(amounts))


def _reply(amounts):
    return json.dumps({"value": amounts[0],
                       "rows": [{"entity": f"E{i}", "value": a}
                                for i, a in enumerate(amounts[1:])]})


_TEXTS = st.one_of(_AMOUNTS.map(_prose), _AMOUNTS.map(_reply))
_SYSTEMS = st.sampled_from(["xbrlrag_full", "xbrlrag_custom_arm", "closed_book",
                            "naive_rag_filtered", "naive_rag_plain", "long_context_oracle",
                            "long_context_resolved", "long_context_multi",
                            "frontier_sec_tools"])


def _system_parser(system, text):
    """The system's own parser, as requirement 2 names it."""
    if system.startswith("xbrlrag_"):
        return [n.value for n in extract_stated_numbers(text)]
    return parse_structured_answer(text).numeric_values


@pytest.mark.criterion("C2")
@given(text=_TEXTS, system=_SYSTEMS)
def test_the_rebuild_is_the_systems_own_parser(text, system):
    # oracle: requirement 2 step 2 — the system's own parser (extract_stated_numbers or
    # parse_structured_answer, named by the spec) applied to the stored text
    got = regrade_values(_payload(text), system)
    assert got == _system_parser(system, text)
    assert got != STORED


@pytest.mark.criterion("C2")
@given(text=_TEXTS,
       system=st.one_of(_SYSTEMS, st.sampled_from(["html_rag", "unknown_arm", None])),
       raw=st.one_of(st.none(), st.lists(st.integers(1, 99).map(float), min_size=1,
                                          max_size=4)))
def test_produced_is_never_mutated(text, system, raw):
    # oracle: requirement 2 — the branch decides the values (raw, else the system's own
    # parser, else stored); the payload compares equal before and after and the result is
    # a new list
    produced = _payload(text, raw=raw)
    before = copy.deepcopy(produced)
    got = regrade_values(produced, system)
    if raw is not None:
        expected = raw
    elif system is None or system in ("html_rag", "unknown_arm"):
        expected = STORED
    else:
        expected = _system_parser(system, text)
    assert got == expected
    assert produced == before
    assert got is not produced["numeric_values"]
    assert got is not produced.get("raw_numeric_values")
