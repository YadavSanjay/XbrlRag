"""C1: EvalCase carries the extra keys hx-authoring and v2-suite-build emit, and round-trips them.

Oracle: requirement 1 of docs/specs/v2-grading.md; the input dicts are the emitted shapes,
written by hand in conftest.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase

EXTRA = ("universe", "expected_exclusions", "accessions", "passages", "assumptions",
         "decline_reason", "parts", "tickers", "key", "disclosures", "subject")
DEFAULTS = {"universe": [], "expected_exclusions": [], "accessions": None, "passages": [],
            "assumptions": [], "decline_reason": None, "parts": [], "tickers": [],
            "key": None, "disclosures": None, "subject": None}
MISSING = "<missing attribute>"


def _emitted(kit):
    return [kit.hx_screen(), kit.hx_pit(), kit.hx_compound(), kit.hx_ambiguous(),
            kit.hx_false_premise(), kit.hx_decline(), kit.hx_text(), kit.v2_peer()]


@pytest.mark.criterion("C1")
def test_extra_keys_are_attributes_and_written_back(kit):
    # oracle: requirement 1 — each extra key present in an emitted dict is the attribute of
    # that name, with the given value, and to_dict writes it back unchanged.
    seen = set()
    for d in _emitted(kit):
        ec = EvalCase.from_dict(d)
        out = ec.to_dict()
        for name in EXTRA:
            if name in d:
                seen.add(name)
                assert getattr(ec, name, MISSING) == d[name], (d["id"], name)
                assert out.get(name, MISSING) == d[name], (d["id"], name)
    # every extra field is exercised by at least one emitted dict
    assert seen == set(EXTRA)


@pytest.mark.criterion("C1")
def test_specific_values_read_by_hand(kit):
    # oracle: the conftest dicts — the H-X universe of four, DEVD excluded as not_tagged, the
    # point-in-time accessions, the compound's two parts, the v2 peer subject at rank 2.
    screen = EvalCase.from_dict(kit.hx_screen())
    assert getattr(screen, "universe", MISSING) == ["DEVA", "DEVB", "DEVC", "DEVD"]
    assert getattr(screen, "expected_exclusions", MISSING) == [
        {"ticker": "DEVD", "reason": "not_tagged"}]
    pit = EvalCase.from_dict(kit.hx_pit())
    assert getattr(pit, "accessions", MISSING) == {
        "as_originally_reported": "0000900101-24-000001", "latest": "0000900101-25-000002"}
    comp = EvalCase.from_dict(kit.hx_compound())
    assert [p["part"] for p in getattr(comp, "parts", [])] == ["ranking", "dependent"]
    dec = EvalCase.from_dict(kit.hx_decline())
    assert getattr(dec, "decline_reason", MISSING) == "not_tagged"
    assert getattr(dec, "tickers", MISSING) == ["DEVA", "DEVC"]
    peer = EvalCase.from_dict(kit.v2_peer())
    assert getattr(peer, "subject", MISSING)["rank"] == 2
    assert getattr(peer, "disclosures", MISSING)["members"] == 3
    assert getattr(peer, "key", MISSING)["status"] == "verified"


@pytest.mark.criterion("C1")
def test_case_without_extras_has_defaults_and_no_keys(kit):
    # oracle: requirement 1 — absent means the default; to_dict writes none of them; a
    # legacy case's to_dict is unchanged (its keys are exactly today's).
    d = {"id": "legacy-1", "question": "What was DEVA revenue in FY2024?", "kind": "lookup",
         "expected_value": 5.0, "ticker": "DEVA", "fy": 2024, "tags": ["split:dev"]}
    ec = EvalCase.from_dict(d)
    for name, default in DEFAULTS.items():
        assert getattr(ec, name, MISSING) == default, name
    out = ec.to_dict()
    assert not set(EXTRA) & set(out)
    assert out == {"id": "legacy-1", "question": "What was DEVA revenue in FY2024?",
                   "kind": "lookup", "expected_value": 5.0, "ticker": "DEVA", "fy": 2024,
                   "tags": ["split:dev"], "rel_tol": 1e-4}


_word = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabc _|", min_size=1, max_size=12)
_tick = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=1, max_size=5)
_small = st.dictionaries(st.sampled_from(["a", "b", "c"]),
                         st.one_of(_word, st.integers(-5, 5), st.booleans()),
                         min_size=1, max_size=3)


@pytest.mark.criterion("C1")
@given(universe=st.lists(_tick, min_size=1, max_size=5),
       exclusions=st.lists(st.fixed_dictionaries({"ticker": _tick, "reason": _word}),
                           min_size=1, max_size=3),
       accessions=_small, passages=st.lists(_small, min_size=1, max_size=3),
       assumptions=st.lists(_word, min_size=1, max_size=3), reason=_word,
       parts=st.lists(_small, min_size=1, max_size=2),
       tickers=st.lists(_tick, min_size=1, max_size=3), key=_small, disclosures=_small,
       subject=_small)
def test_round_trip_property(universe, exclusions, accessions, passages, assumptions, reason,
                             parts, tickers, key, disclosures, subject):
    # oracle: requirement 1 — from_dict(to_dict()) keeps every non-empty extra value.
    values = {"universe": universe, "expected_exclusions": exclusions,
              "accessions": accessions, "passages": passages, "assumptions": assumptions,
              "decline_reason": reason, "parts": parts, "tickers": tickers, "key": key,
              "disclosures": disclosures, "subject": subject}
    d = {"id": "p-1", "question": "q?", "kind": "narrative", **values}
    once = EvalCase.from_dict(d).to_dict()
    twice = EvalCase.from_dict(once)
    for name, value in values.items():
        assert once.get(name, MISSING) == value, name
        assert getattr(twice, name, MISSING) == value, name
