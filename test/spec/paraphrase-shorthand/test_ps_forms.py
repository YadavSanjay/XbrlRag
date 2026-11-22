"""C1-C4: the check-only shorthand forms and the abbreviated period-end month."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.suites.intents import Intent, load_lexicon
from src.eval.suites.paraphrase_forms import extend_source, load_paraphrase_forms
from src.eval.suites.paraphrases import ParaphraseSource, check_meaning, normalise

LEXICON = load_lexicon(Path(__file__).resolve().parents[3] / "configs" / "eval" / "v2" / "lexicon.yaml")
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")
_ABBREVIATIONS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_EMPTY = {"metrics": {}, "ratios": (), "universes": {}, "companies": {},
          "possessive_exempt": frozenset()}

FORMS = {
    "metrics": {"CurrentAssets": ("ca",)},
    "ratios": (("CurrentAssets", "CurrentLiabilities", ("current ratio",)),
               ("OperatingIncome", "Revenue", ("operating margin", "op margin"))),
    "universes": {"semiconductors": ("semis",)},
    "companies": {"GOOGL": ("Google",)},
    "possessive_exempt": frozenset({"cost"}),
}


def _source(intent: Intent, question: str = "q") -> ParaphraseSource:
    """A source with the lexicon's forms, as the eval-paraphrases adapter builds it."""
    company: tuple[str, ...] = ()
    if intent.tickers:
        entry = LEXICON.companies[intent.tickers[0]]
        company = (intent.tickers[0], entry["short"], entry["legal"])
    metrics = [(LEXICON.metrics[m]["label"], *LEXICON.metrics[m]["synonyms"],
                *LEXICON.metrics[m]["shorthand"]) for m in intent.metrics]
    universe: tuple[str, ...] = ()
    if intent.universe is not None:
        universe = tuple(LEXICON.universes[intent.universe])
    own = {normalise(f) for f in universe}
    forbidden = tuple(p for uid, phrases in sorted(LEXICON.universes.items())
                      if uid != intent.universe for p in phrases if normalise(p) not in own)
    return ParaphraseSource("case-1", question, intent_id="intent-1", intent_axis="dev",
                            template_axis="dev", company_forms=company,
                            years=list(intent.fiscal_years), metric_forms=metrics,
                            universe_forms=universe, forbidden_forms=forbidden)


def _ratio(ticker: str, numerator: str, denominator: str, year: int = 2024) -> Intent:
    return Intent("compute", tickers=[ticker], fiscal_years=[year],
                  metrics=[numerator, denominator], operation="ratio")


def _write(tmp_path: Path, doc: object) -> Path:
    path = tmp_path / "forms.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


# --- C1: loading -------------------------------------------------------------------------


@pytest.mark.criterion("C1")
def test_a_valid_file_loads_to_the_documented_structure(tmp_path):
    # oracle: requirement 1 by hand; exempt forms are stored normalised
    path = _write(tmp_path, {
        "metrics": {"CurrentAssets": ["ca"]},
        "ratios": [{"metrics": ["CurrentAssets", "CurrentLiabilities"], "forms": ["current ratio"]}],
        "universes": {"semiconductors": ["semis"]},
        "companies": {"GOOGL": ["Google"]},
        "possessive_exempt": ["COST"],
    })
    assert load_paraphrase_forms(path, LEXICON) == {
        "metrics": {"CurrentAssets": ("ca",)},
        "ratios": (("CurrentAssets", "CurrentLiabilities", ("current ratio",)),),
        "universes": {"semiconductors": ("semis",)},
        "companies": {"GOOGL": ("Google",)},
        "possessive_exempt": frozenset({"cost"}),
    }


@pytest.mark.criterion("C1")
def test_absent_keys_give_empty_values(tmp_path):
    # oracle: requirement 1: each top-level key is optional
    assert load_paraphrase_forms(_write(tmp_path, {}), LEXICON) == _EMPTY


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("doc", [
    pytest.param(["a list"], id="not-a-mapping"),
    pytest.param({"aliases": {}}, id="unknown-key"),
    pytest.param({"metrics": {"NoSuchMetric": ["x"]}}, id="unknown-metric"),
    pytest.param({"universes": {"no_such": ["x"]}}, id="unknown-universe"),
    pytest.param({"companies": {"ZZZZ": ["x"]}}, id="unknown-ticker"),
    pytest.param({"ratios": [{"metrics": ["CurrentAssets"], "forms": ["x"]}]}, id="ratio-one-metric"),
    pytest.param({"ratios": [{"metrics": ["CurrentAssets", "Nope"], "forms": ["x"]}]},
                 id="ratio-unknown-metric"),
    pytest.param({"metrics": {"CurrentAssets": "ca"}}, id="value-not-a-list"),
    pytest.param({"metrics": {"CurrentAssets": [7]}}, id="form-not-a-str"),
    pytest.param({"metrics": {"CurrentAssets": ["!!"]}}, id="form-normalises-empty"),
    pytest.param({"metrics": {"CurrentAssets": ["latest current assets"]}}, id="drift-phrase"),
    pytest.param({"universes": {"semiconductors": ["The Retailers"]}}, id="other-universe-phrase"),
])
def test_each_rule_raises(tmp_path, doc):
    # oracle: requirement 1's list of rules, one file breaking each
    with pytest.raises(ValueError):
        load_paraphrase_forms(_write(tmp_path, doc), LEXICON)


@pytest.mark.criterion("C1")
@settings(max_examples=30, deadline=None)
@given(name=st.text(alphabet="ABCDEFGHXYZ", min_size=3, max_size=12))
def test_any_metric_outside_the_lexicon_raises(tmp_path_factory, name):
    # oracle: requirement 1: a metric id must be in the lexicon
    if name in LEXICON.metrics:
        return
    path = _write(tmp_path_factory.mktemp("f"), {"metrics": {name: ["x"]}})
    with pytest.raises(ValueError):
        load_paraphrase_forms(path, LEXICON)


# --- C2: extending a source --------------------------------------------------------------


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("intent, paraphrase", [
    pytest.param(_ratio("AAPL", "CurrentAssets", "CurrentLiabilities"), "AAPL current ratio FY24",
                 id="current-ratio"),
    pytest.param(_ratio("GOOGL", "OperatingIncome", "Revenue", 2023), "GOOGL operating margin FY23",
                 id="operating-margin"),
    pytest.param(Intent("screen", fiscal_years=[2025], metrics=["NetIncome"], operation="top",
                        universe="semiconductors", n=3),
                 "top 3 semis by net income FY25", id="semis"),
    pytest.param(_ratio("GOOGL", "OperatingIncome", "Revenue", 2023), "Google op margin FY23",
                 id="company-alias"),
])
def test_shorthand_passes_after_and_not_before(intent, paraphrase):
    # oracle: requirement 2 by hand against the eval-paraphrases meaning check
    source = _source(intent)
    assert check_meaning(source, paraphrase) != []
    assert check_meaning(extend_source(source, intent, FORMS), paraphrase) == []


@pytest.mark.criterion("C2")
def test_a_ratio_form_needs_its_own_pair():
    # oracle: requirement 2: "current ratio" belongs to (CurrentAssets, CurrentLiabilities) only
    other = _ratio("AAPL", "NetIncome", "Revenue")
    extended = extend_source(_source(other), other, FORMS)
    assert "metric_missing" in check_meaning(extended, "AAPL current ratio FY24")
    lookup = Intent("lookup", tickers=["AAPL"], fiscal_years=[2024], metrics=["CurrentAssets"])
    extended = extend_source(_source(lookup), lookup, FORMS)
    assert check_meaning(extended, "AAPL current ratio FY24") == ["metric_missing"]
    assert check_meaning(extended, "AAPL ca FY24") == []


@pytest.mark.criterion("C2")
def test_another_universes_shorthand_drifts():
    # oracle: requirement 2: other universes' extras join forbidden_forms
    intent = Intent("screen", fiscal_years=[2023], metrics=["NetIncome"], operation="median",
                    universe="covered")
    paraphrase = "median net income among all covered companies and the semis FY23"
    assert "semantic_drift" not in check_meaning(_source(intent), paraphrase)
    assert check_meaning(extend_source(_source(intent), intent, FORMS), paraphrase) == [
        "semantic_drift"]


@pytest.mark.criterion("C2")
def test_forms_are_appended_in_order_without_duplicates():
    # oracle: requirement 2: own forms first, then metric extras, then the ratio's, deduplicated
    intent = _ratio("AAPL", "CurrentAssets", "CurrentLiabilities")
    forms = {**FORMS, "metrics": {"CurrentAssets": ("current assets", "ca")}}
    extended = extend_source(_source(intent), intent, forms)
    assert list(extended.metric_forms[0]) == [
        "Assets, Current", "total current assets", "current assets", "ca", "current ratio"]
    assert list(extended.metric_forms[1]) == [
        "Liabilities, Current", "total current liabilities", "current liabilities", "current ratio"]
    assert extended.case_id == "case-1" and list(extended.years) == [2024]


_TOKENS = st.sampled_from(["alpha", "beta", "gamma", "delta", "margin", "ratio"])
_PHRASE = st.lists(_TOKENS, min_size=1, max_size=3).map(" ".join)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@settings(max_examples=60, deadline=None)
@given(metric_extra=st.lists(_PHRASE, max_size=3), company_extra=st.lists(_PHRASE, max_size=2),
       words=st.lists(st.sampled_from(["AAPL", "Apple", "apples", "alpha", "beta", "margin",
                                       "current", "assets", "liabilities", "FY24", "ratio"]),
                      max_size=8))
def test_extending_never_adds_a_reason(metric_extra, company_extra, words):
    # oracle: requirement 2 with no universe: forms only grow, so no reason can appear
    intent = _ratio("AAPL", "CurrentAssets", "CurrentLiabilities")
    forms = {**_EMPTY, "metrics": {"CurrentAssets": tuple(metric_extra)},
             "companies": {"AAPL": tuple(company_extra)}}
    base = _source(intent)
    extended = extend_source(base, intent, forms)
    paraphrase = " ".join(words)
    assert set(check_meaning(extended, paraphrase)) <= set(check_meaning(base, paraphrase))
    for own, grown in zip(base.metric_forms, extended.metric_forms, strict=True):
        assert list(grown[: len(own)]) == list(own)
    assert list(extended.company_forms[: len(base.company_forms)]) == list(base.company_forms)


# --- C3: dropped apostrophes -------------------------------------------------------------


@pytest.mark.criterion("C3")
def test_a_dropped_apostrophe_passes_unless_exempt():
    # oracle: requirement 3 by hand: "qcoms" is QCOM's; "costs" is a word, so COST is exempt
    qcom = _ratio("QCOM", "CurrentAssets", "CurrentLiabilities")
    assert "company_missing" in check_meaning(_source(qcom), "wat was qcoms current ratio fy24")
    assert check_meaning(extend_source(_source(qcom), qcom, FORMS),
                         "wat was qcoms current ratio fy24") == []
    cost = _ratio("COST", "CurrentAssets", "CurrentLiabilities")
    assert "company_missing" in check_meaning(extend_source(_source(cost), cost, FORMS),
                                              "wat was costs current ratio fy24")
    not_exempt = {**FORMS, "possessive_exempt": frozenset()}
    assert check_meaning(extend_source(_source(cost), cost, not_exempt),
                         "wat was costs current ratio fy24") == []


@pytest.mark.criterion("C3")
def test_a_multi_word_name_gets_its_variant():
    # oracle: requirement 3: normalise("McDonald's") is "mcdonald", so "mcdonalds" is added
    mcd = _ratio("MCD", "CurrentAssets", "CurrentLiabilities")
    assert check_meaning(extend_source(_source(mcd), mcd, FORMS),
                         "mcdonalds current ratio fy24") == []


@pytest.mark.criterion("C3")
@settings(max_examples=40, deadline=None)
@given(form=st.text(alphabet="bcdfghjklmnpqrtvwxz", min_size=3, max_size=8))
def test_any_non_exempt_form_gets_its_variant(form):
    # oracle: requirement 3: <form>s names the company for any single-token form not exempt
    intent = Intent("lookup", tickers=["AAPL"], fiscal_years=[2024], metrics=["Revenue"])
    forms = {**_EMPTY, "companies": {"AAPL": (form,)}}
    extended = extend_source(_source(intent), intent, forms)
    assert "company_missing" not in check_meaning(extended, f"{form}s revenue fy24")


# --- C4: abbreviated months --------------------------------------------------------------


def _dated(end: dt.date) -> ParaphraseSource:
    return ParaphraseSource("case-1", "q", intent_id="intent-1", intent_axis="dev",
                            template_axis="dev", years=[end.year],
                            period_ends={end.year: end.isoformat()}, metric_forms=[("revenue",)])


@pytest.mark.criterion("C4")
def test_an_abbreviated_month_names_the_period_end():
    # oracle: requirement 4 by hand, period end 2024-09-28, no company so no bare year
    source = _dated(dt.date(2024, 9, 28))
    table = [
        ("revenue for the year ended sep 28 2024", True),
        ("revenue for the year ended sept 28 2024", True),
        ("revenue for the year ended Sep. 28, 2024", True),
        ("revenue for the year ended september 28 2024", True),
        ("revenue for the year ended sep 27 2024", False),
        ("revenue for the year ended oct 28 2024", False),
    ]
    assert [("period_missing" not in check_meaning(source, text)) for text, _ in table] == [
        passes for _, passes in table]


@pytest.mark.criterion("C4")
@settings(max_examples=60, deadline=None)
@given(end=st.dates(min_value=dt.date(2000, 1, 1), max_value=dt.date(2030, 12, 31)),
       other=st.dates(min_value=dt.date(2000, 1, 1), max_value=dt.date(2030, 12, 31)))
def test_abbreviation_passes_iff_the_full_month_does(end, other):
    # oracle: requirement 4: the abbreviation is accepted exactly where the full name is
    source = _dated(end)
    for day in (end, other):
        full = f"ended {_MONTHS[day.month - 1]} {day.day} {day.year}"
        short = f"ended {_ABBREVIATIONS[day.month - 1]} {day.day} {day.year}"
        assert ("period_missing" in check_meaning(source, full)) == (
            "period_missing" in check_meaning(source, short))
