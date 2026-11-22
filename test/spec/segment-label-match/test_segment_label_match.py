"""Spec tests for segment-label-match: a one-dimension member hint matches its label as the
analyst wrote it (docs/specs/segment-label-match.md).

Pure: `member_label_key`, `label_contains_hint`, and `_build_result` driven with rows as the
queries return them (no DB).
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.xbrl_factstore_lookup import (
    XbrlFactstoreLookup,
    label_contains_hint,
    member_label_key,
)

G24 = "http://fasb.org/us-gaap/2024:"
REV = G24 + "RevenueFromContractWithCustomerExcludingAssessedTax"
GENERIC = {"segment", "segments", "member"}


def _words(text):
    """Requirement 1, steps 1-4."""
    s = text.lower().replace("[member]", " ").replace("&", " and ")
    return re.findall(r"[a-z0-9]+", s)


def _key_oracle(text):
    """Requirement 1 written out."""
    words = _words(text)
    if len(words) > 1 and words[-1] in GENERIC:
        words = words[:-1]
    return "".join(words)


words_st = st.lists(
    st.text("abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=8).filter(
        lambda w: w != "member"
    ),
    min_size=1, max_size=5,
)


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_key_point_cases():
    # oracle: Requirement 1 by hand
    assert member_label_key("Europe Segment [Member]") == "europe"
    assert member_label_key("Datacenter [Member]") == "datacenter"
    assert member_label_key("data center") == "datacenter"
    assert member_label_key("License Support [Member]") == "licensesupport"
    assert member_label_key("Cloud Services And License Support [Member]") == (
        "cloudservicesandlicensesupport")
    assert member_label_key("Search & other") == "searchandother"
    assert member_label_key("Operating Segments [Member]") == "operating"
    assert member_label_key("Segment") == "segment"
    assert member_label_key("") == ""


@pytest.mark.criterion("C1")
@given(words_st)
def test_key_matches_rule(words):
    # oracle: Requirement 1 written out in _key_oracle
    text = " ".join(words)
    assert member_label_key(text) == _key_oracle(text)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(words_st, st.sampled_from([" ", "  ", ", ", " - ", "\t"]))
def test_key_ignores_case_spacing_punctuation_and_member_suffix(words, sep):
    # oracle: metamorphic — only the words matter, not how they are written
    text = " ".join(words)
    key = member_label_key(text)
    assert key == _key_oracle(text)
    assert member_label_key(text.upper()) == key
    assert member_label_key(sep.join(words)) == key
    assert member_label_key(text + " [Member]") == key


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_contains_point_cases():
    # oracle: Requirement 2 by hand — hint key inside the label's compact form
    assert label_contains_hint("Datacenter [Member]", "data center")
    assert label_contains_hint("Europe Segment [Member]", "europe")
    assert label_contains_hint("Europe Segment [Member]", "europe segment")
    assert label_contains_hint("Cloud Services And License Support [Member]", "license support")
    assert not label_contains_hint("Europe Segment [Member]", "japan")


@pytest.mark.criterion("C2")
@given(words_st, words_st)
def test_contains_matches_rule(label_words, hint_words):
    # oracle: Requirement 2 written out — key(hint) in compact(label)
    label, hint = " ".join(label_words), " ".join(hint_words)
    expected = _key_oracle(hint) in "".join(_words(label))
    assert label_contains_hint(label, hint) is expected


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(words_st)
def test_label_contains_its_own_key(words):
    # oracle: metamorphic — a label always contains itself, however it is spaced
    label = " ".join(words) + " [Member]"
    assert label_contains_hint(label, label)
    assert label_contains_hint(label, member_label_key(label))
    assert label_contains_hint(label.upper(), "  ".join(words))


# --------------------------------------------------------------------------- C3
def _row(fact_id, value, label, period_end, axis="srt:ProductOrServiceAxis"):
    return {"fact_id": fact_id, "value": value, "unit": "USD", "period_type": "duration",
            "period_start": None, "period_end": period_end, "concept": REV,
            "concept_family": "Revenue", "concept_pref_rank": 1, "segment_axis": axis,
            "segment_member": None, "segment_label": label, "html_anchor": None,
            "page_num": None, "section_path": None, "accession_no": None,
            "linked_chunk_ids": [], "decimals": -6}


def _result(dims, rows):
    lk = XbrlFactstoreLookup()
    notes: list[str] = []
    specs, status = lk._parse_dimensions(dims, notes)
    assert status is None
    step = {"id": "s1", "op": "lookup", "dimensions": dims, "section_hint": None}
    return lk._build_result("s1", step, "x", 2025, "Revenue", "duration", specs, rows, notes)


ORCL = [
    _row(73633, 19_523e6, "License Support [Member]", "2025-06-01"),
    _row(73636, 44_029e6, "Cloud Services And License Support [Member]", "2025-06-01"),
    _row(73639, 19_383e6, "Applications Cloud Services And License Support [Member]",
         "2025-06-01"),
    _row(73642, 24_646e6, "Infrastructure Cloud Services And License Support [Member]",
         "2025-06-01"),
]
SEG = "us-gaap:StatementBusinessSegmentsAxis"


@pytest.mark.criterion("C3")
def test_oracle_license_support_picks_the_exact_member():
    # oracle: corpus facts (2026-10-16); confidence 0.4 + 0.2 + 0.2 by hand
    for rows in (ORCL, ORCL[::-1]):
        r = _result({"product": "license support"}, rows)
        assert r.status.value == "OK"
        assert r.value == pytest.approx(19_523e6, rel=1e-12)  # stored value, unrounded
        assert r.citation.fact_id == 73633
        assert r.confidence == pytest.approx(0.8, abs=1e-9)  # sum of fixed credits


@pytest.mark.criterion("C3")
def test_apple_europe_and_amd_datacenter_earn_the_exact_credit():
    # oracle: corpus facts 14215 and 19541 (2026-10-16); confidence 0.4 + 0.2 + 0.2 by hand
    aapl = _result({"segment": "europe"},
                   [_row(14215, 101_328e6, "Europe Segment [Member]", "2024-09-29", SEG)])
    assert aapl.status.value == "OK"
    assert aapl.value == pytest.approx(101_328e6, rel=1e-12)  # stored value, unrounded
    assert aapl.confidence == pytest.approx(0.8, abs=1e-9)  # sum of fixed credits
    amd = _result({"segment": "data center"},
                  [_row(19541, 12_579e6, "Datacenter [Member]", "2024-12-29", SEG)])
    assert amd.status.value == "OK"
    assert amd.citation.fact_id == 19541
    assert amd.confidence == pytest.approx(0.8, abs=1e-9)  # sum of fixed credits


@pytest.mark.criterion("C3")
def test_no_exact_member_stays_ambiguous():
    # oracle: by hand — "cloud services" names no member exactly; three values remain
    r = _result({"product": "cloud services"}, ORCL[1:])
    assert r.status.value == "AMBIGUOUS"
    exact = _result({"product": "license support"}, ORCL)
    assert exact.status.value == "OK"


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(st.lists(st.sampled_from(["Cloud", "Applications", "Infrastructure", "Other", "Total"]),
                min_size=1, max_size=4, unique=True),
       st.integers(min_value=1, max_value=900_000), st.randoms(use_true_random=False))
def test_containing_rivals_never_displace_the_exact_member(prefixes, value_m, rnd):
    # oracle: metamorphic — rows whose labels merely contain the hint change nothing
    exact = _row(1, 19_523e6, "License Support [Member]", "2025-06-01")
    rivals = [_row(10 + i, float((value_m + i) * 1_000_000), f"{p} License Support [Member]",
                   "2025-06-01") for i, p in enumerate(prefixes)]
    rows = [exact] + rivals
    rnd.shuffle(rows)
    alone = _result({"product": "license support"}, [exact])
    r = _result({"product": "license support"}, rows)
    assert r.status.value == alone.status.value == "OK"
    assert r.citation.fact_id == 1
    assert r.confidence == pytest.approx(alone.confidence, abs=1e-9)  # same credits
