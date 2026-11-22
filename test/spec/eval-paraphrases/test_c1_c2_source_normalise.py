"""Spec tests C1-C2 for eval-paraphrases: the ParaphraseSource input record and normalise
(docs/specs/eval-paraphrases.md)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.paraphrases import ParaphraseSource, normalise

BASE = dict(case_id="c-1", question="What was Apple's revenue in fiscal 2024?",
            intent_id="int-1", intent_axis="dev", template_axis="dev",
            company_forms=["AAPL", "Apple"], years=[2024],
            metric_forms=[["Revenues", "revenue"]])


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_c1_fields_stored_point():
    # oracle: the spec's Inputs rules applied by hand (tuples, years sorted and unique,
    # period_ends a dict, {} for None).
    s = ParaphraseSource("c-9", "Q text", intent_id="int-9", intent_axis="validation",
                         template_axis="dev", company_forms=["MSFT", "Microsoft"],
                         years=[2024, 2022, 2024], period_ends={2024: "2024-06-30"},
                         metric_forms=[["Revenues", "revenue"], ["Net income"]],
                         universe_forms=["big tech"], forbidden_forms=["S&P 100"])
    assert s.case_id == "c-9" and s.question == "Q text" and s.intent_id == "int-9"
    assert s.intent_axis == "validation" and s.template_axis == "dev"
    assert s.company_forms == ("MSFT", "Microsoft")
    assert s.years == (2022, 2024)
    assert s.period_ends == {2024: "2024-06-30"}
    assert s.metric_forms == (("Revenues", "revenue"), ("Net income",))
    assert s.universe_forms == ("big tech",)
    assert s.forbidden_forms == ("S&P 100",)
    bare = ParaphraseSource(**BASE)
    assert bare.period_ends == {}
    assert bare.universe_forms == () and bare.forbidden_forms == ()


BAD = [
    dict(case_id=""), dict(case_id="   "), dict(question=""), dict(intent_id=" "),
    dict(intent_axis="train"), dict(template_axis="Dev"), dict(years=[True]),
    dict(years=[2024.0]), dict(period_ends={2024: "28/09/2024"}),
    dict(period_ends={2024: "2024-13-01"}), dict(metric_forms=[]),
    dict(metric_forms=[[]]), dict(metric_forms=[["Revenues", "  "]]),
    dict(company_forms=["AAPL", "?!"]), dict(universe_forms=[""]),
    dict(forbidden_forms=["--"]),
]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("bad", BAD, ids=[str(i) for i in range(len(BAD))])
def test_c1_invalid_inputs_raise(bad):
    # oracle: each dict breaks exactly one Inputs rule (empty id/question, unknown axis,
    # non-int year, non-ISO period end, missing metric form, form normalising to "").
    with pytest.raises(ValueError):
        ParaphraseSource(**{**BASE, **bad})


@pytest.mark.criterion("C1")
@given(st.lists(st.integers(1990, 2040), max_size=8))
def test_c1_years_sorted_set_property(years):
    # oracle: years are stored sorted ascending with duplicates removed.
    s = ParaphraseSource(**{**BASE, "years": years})
    assert s.years == tuple(sorted(set(years)))
    assert isinstance(s.years, tuple)


# --------------------------------------------------------------------------- C2
TABLE = [
    ("Apple's net sales", "apple net sales"),
    ("Apple’s net sales", "apple net sales"),          # U+2019
    ("Microsoft‘s", "microsoft"),                       # U+2018
    ("Appleʼs top line", "apple top line"),             # U+02BC
    ("FY'24", "fy24"),
    ("FY`24", "fy24"),
    ("FY-24", "fy 24"),
    ("the companies' revenue", "the companies revenue"),
    ("Apple Inc.'s revenue, FY2024!", "apple inc revenue fy2024"),
    ("ＦＹ２０２４", "fy2024"),                          # full-width, NFKC
    ("S&P 100", "s p 100"),
    ("  R&D   expense\t(opex) ", "r d expense opex"),
    ("snake_case_word", "snake case word"),
    ("it's", "it"),
    ("Apple's's", "apple"),
    ("STRASSE Straße", "strasse strasse"),             # casefold
    ("", ""),
    ("?!.,", ""),
]


@pytest.mark.criterion("C2")
def test_c2_normalise_table_point():
    # oracle: the four normalisation steps worked by hand on each input.
    for raw, expected in TABLE:
        assert normalise(raw) == expected, raw


_ASCII_WORD = st.text(alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
                      min_size=1, max_size=8)


@pytest.mark.criterion("C2")
@given(st.lists(_ASCII_WORD, min_size=1, max_size=6),
       st.lists(st.sampled_from([" ", ", ", ". ", " - ", "; ", "! ", "/", " (", ") "]),
                min_size=6, max_size=6))
def test_c2_normalise_property(words, seps):
    # oracle: the output is the lower-cased alphanumeric tokens joined by one space, so it is
    # idempotent, blind to ASCII case and to the punctuation between tokens.
    plain = " ".join(words)
    punct = "".join(w + seps[i] for i, w in enumerate(words))
    expected = " ".join(w.lower() for w in words)
    assert normalise(plain) == expected
    assert normalise(punct) == expected
    assert normalise(plain.upper()) == expected
    assert normalise(normalise(punct)) == normalise(punct)
