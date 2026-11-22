"""The lexicon phrasing check (src/eval/suites/intents.py check_lexicon_phrasings) and its
wiring into scripts/eval/build_v2_suite.py. Every lexicon here is made up, except the test
that the committed one passes.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from src.eval.suites.intents import check_lexicon_phrasings, load_lexicon

REPO = Path(__file__).resolve().parents[2]

HEAD = """\
companies:
  DEVA: {short: Deva, legal: Deva Holdings Inc.}
universes:
  covered: [the covered companies]
period_ends:
  DEVA: {2024: "2024-12-31"}
"""


def _lexicon(tmp_path: Path, body: str):
    path = tmp_path / "lexicon.yaml"
    path.write_text(HEAD + body, encoding="utf-8")
    return load_lexicon(path)


def test_the_committed_lexicon_passes():
    check_lexicon_phrasings(load_lexicon(REPO / "configs" / "eval" / "v2" / "lexicon.yaml"))


def test_an_unquoted_comma_in_sga_fails_on_the_generic_word(tmp_path):
    lex = _lexicon(tmp_path, """\
metrics:
  SellingGeneralAndAdministrative:
    label: Selling, General and Administrative Expense
    synonyms: [SG&A expense, selling, general and administrative expenses]
""")
    assert lex.metrics["SellingGeneralAndAdministrative"]["synonyms"] == (
        "SG&A expense", "selling", "general and administrative expenses")
    with pytest.raises(ValueError, match=r"'selling' of 'SellingGeneralAndAdministrative'"):
        check_lexicon_phrasings(lex)


def test_the_quoted_sga_list_passes(tmp_path):
    lex = _lexicon(tmp_path, """\
metrics:
  SellingGeneralAndAdministrative:
    label: Selling, General and Administrative Expense
    synonyms: [SG&A expense, "selling, general and administrative expenses"]
    shorthand: [SG&A]
""")
    assert lex.metrics["SellingGeneralAndAdministrative"]["synonyms"] == (
        "SG&A expense", "selling, general and administrative expenses")
    check_lexicon_phrasings(lex)


def test_an_unquoted_ebitda_list_collides_with_d_and_a(tmp_path):
    lex = _lexicon(tmp_path, """\
metrics:
  DepreciationAndAmortization:
    label: Depreciation, Depletion and Amortization
    synonyms: [depreciation and amortization]
  EBITDA:
    label: EBITDA
    synonyms: [earnings before interest, taxes, depreciation and amortization]
""")
    with pytest.raises(ValueError) as info:
        check_lexicon_phrasings(lex)
    message = str(info.value)
    assert "'taxes' of 'EBITDA'" in message
    assert ("phrasing 'depreciation and amortization' names 2 entries: "
            "['DepreciationAndAmortization', 'EBITDA']") in message


def test_a_shared_phrasing_is_compared_case_and_space_insensitively(tmp_path):
    lex = _lexicon(tmp_path, """\
metrics:
  GrossProfit: {label: Gross Profit, synonyms: [gross margin  dollars]}
  GrossMarginPct: {label: Gross Margin, synonyms: [Gross Margin Dollars]}
""")
    with pytest.raises(ValueError, match=r"'gross margin dollars' names 2 entries"):
        check_lexicon_phrasings(lex)


def test_a_label_may_not_be_another_metrics_synonym(tmp_path):
    lex = _lexicon(tmp_path, """\
metrics:
  GeneralAndAdministrative: {label: General and Administrative Expense}
  SellingGeneralAndAdministrative:
    label: SG&A
    synonyms: [general and administrative expense]
""")
    with pytest.raises(ValueError, match="GeneralAndAdministrative"):
        check_lexicon_phrasings(lex)


def test_same_measure_ids_may_share_phrasings(tmp_path):
    body = """\
metrics:
  OperatingMarginPct: {label: Operating Margin, shorthand: [op margin]}
  operating_margin: {label: Operating Margin, shorthand: [op margin]}
"""
    check_lexicon_phrasings(_lexicon(tmp_path, body))
    with pytest.raises(ValueError, match="'op margin' names 2 entries"):
        check_lexicon_phrasings(_lexicon(tmp_path, body), same_measure=())


@pytest.mark.parametrize("phrase", ["Revenue", "revenues", "REVENUES"])
def test_a_one_word_phrasing_that_is_the_id_or_label_passes(tmp_path, phrase):
    check_lexicon_phrasings(_lexicon(tmp_path, f"""\
metrics:
  Revenue: {{label: Revenues, synonyms: [{phrase}]}}
"""))


def test_acronyms_and_multi_word_phrasings_are_not_one_word(tmp_path):
    check_lexicon_phrasings(_lexicon(tmp_path, """\
metrics:
  Revenue: {label: Revenues, synonyms: [net sales, R&D/sales], shorthand: [REV, A/R, GP]}
"""))


def test_a_one_word_phrasing_needs_the_allow_list(tmp_path):
    body = """\
metrics:
  Revenue: {label: Revenues, synonyms: [Sales]}
"""
    with pytest.raises(ValueError, match="'Sales' of 'Revenue'"):
        check_lexicon_phrasings(_lexicon(tmp_path, body), one_word=frozenset())
    check_lexicon_phrasings(_lexicon(tmp_path, body),
                            one_word=frozenset({("metrics", "Revenue", "sales")}))
    with pytest.raises(ValueError, match="'Sales' of 'Revenue'"):  # allowed for another entry
        check_lexicon_phrasings(_lexicon(tmp_path, body),
                                one_word=frozenset({("metrics", "Sales", "sales")}))


def test_dimension_and_universe_phrasings_are_checked_per_section(tmp_path):
    split = _lexicon(tmp_path, """\
dimensions:
  emea: [EMEA, Europe, the Middle East and Africa]
""")
    with pytest.raises(ValueError, match="dimensions: one-word phrasing 'Europe' of 'emea'"):
        check_lexicon_phrasings(split)
    clash = _lexicon(tmp_path, """\
metrics:
  Revenue: {label: Revenues}
dimensions:
  china: [China]
  greater_china: [Greater China, china]
""")
    with pytest.raises(ValueError, match=r"dimensions: phrasing 'china' names 2 entries"):
        check_lexicon_phrasings(clash)
    # The same phrase in two sections is no clash: each section is phrased in its own slot.
    check_lexicon_phrasings(_lexicon(tmp_path, """\
metrics:
  Revenue: {label: Revenues}
dimensions:
  revenue: [revenue]
"""))


def test_every_problem_is_reported_at_once(tmp_path):
    lex = _lexicon(tmp_path, """\
metrics:
  A1: {label: alpha one, synonyms: [taxes, shared phrase]}
  B1: {label: beta one, synonyms: [selling, shared phrase]}
""")
    with pytest.raises(ValueError) as info:
        check_lexicon_phrasings(lex)
    message = str(info.value)
    assert "'taxes' of 'A1'" in message and "'selling' of 'B1'" in message
    assert "'shared phrase' names 2 entries: ['A1', 'B1']" in message


def _build_cli():
    spec = importlib.util.spec_from_file_location(
        "build_v2_suite_cli", REPO / "scripts" / "eval" / "build_v2_suite.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_v2_build_fails_on_a_bad_lexicon_before_any_database_read(tmp_path, monkeypatch):
    cli = _build_cli()

    def no_database():
        raise AssertionError("the build reached the database")

    monkeypatch.setattr(cli, "connect_readonly", no_database)
    bad = tmp_path / "bad.yaml"
    bad.write_text(HEAD + """\
metrics:
  EBITDA: {label: EBITDA, synonyms: [earnings before interest, taxes]}
""", encoding="utf-8")
    with pytest.raises(ValueError, match="'taxes' of 'EBITDA'"):
        cli.main(["--total", "10", "--per-intent", "1", "--share", "0.15", "--salt", "s",
                  "--lexicon", str(bad), "--out-dir", str(tmp_path / "out"),
                  "--log-dir", str(tmp_path / "logs")])
