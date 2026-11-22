"""Adapter from eval-intents phrased cases to the eval-paraphrases input records.

``paraphrases.py`` depends only on its own ``ParaphraseSource``; this module builds those
records from a ``PhrasedCase`` and the ``Lexicon`` / ``Phrasebook`` it was rendered with,
and loads the intents file the export and import CLIs share. Pure apart from file reads.
Spec: docs/specs/eval-paraphrases.md (Notes, the adapter).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from src import PROJECT_ROOT
from src.eval.suites.intents import (
    Intent,
    Lexicon,
    Phrasebook,
    PhrasedCase,
    build_phrased_cases,
    intent_id,
    load_lexicon,
    load_phrasebook,
    template_placeholders,
)
from src.eval.suites.paraphrase_forms import extend_source, load_paraphrase_forms
from src.eval.suites.paraphrases import ParaphraseSource, normalise

SPLITS = ("dev", "validation", "all")
_INTENT_KEYS = frozenset({"kind", "tickers", "fiscal_years", "metrics", "dimension", "operation",
                          "universe", "n", "filing_year"})


def rendered_years(case: PhrasedCase, phrasebook: Phrasebook) -> list[int]:
    """Returns the fiscal years the case's question names, from its template's placeholders.

    ``{period}`` and ``{first_period}`` render the first fiscal year, ``{last_period}`` the
    last and ``{filing_period}`` the filing year, so a trend names only its endpoints and a
    year-on-year template may name only its last year.

    Args:
        case: The phrased case.
        phrasebook: The phrasebook the case was rendered with.

    Returns:
        The years, sorted and unique.
    """
    intent = case.intent
    used = template_placeholders(phrasebook.get(case.template_id).text)
    years: set[int] = set()
    if used & {"period", "first_period"}:
        years.add(intent.fiscal_years[0])
    if "last_period" in used:
        years.add(intent.fiscal_years[-1])
    if "filing_period" in used and intent.filing_year is not None:
        years.add(intent.filing_year)
    return sorted(years)


def paraphrase_source(case: PhrasedCase, lexicon: Lexicon,
                      phrasebook: Phrasebook) -> ParaphraseSource:
    """Builds the paraphrase input record of one phrased case.

    ``company_forms`` are the ticker, short and legal name; ``metric_forms`` the label,
    synonyms and shorthand of each metric; ``universe_forms`` the universe's phrases;
    ``forbidden_forms`` every other universe's phrases (minus any that equal one of the
    case's own); ``period_ends`` the filer's period ends for the rendered years.

    Args:
        case: The phrased case.
        lexicon: The lexicon the case was rendered with.
        phrasebook: The phrasebook the case was rendered with.

    Returns:
        The record.

    Raises:
        ValueError: When the lexicon lacks the case's company, metric or universe.
    """
    intent = case.intent
    company_forms: tuple[str, ...] = ()
    period_ends: dict[int, str] = {}
    years = rendered_years(case, phrasebook)
    if intent.tickers:
        ticker = intent.tickers[0]
        if ticker not in lexicon.companies:
            raise ValueError(f"{case.case_id}: company {ticker} is not in the lexicon")
        entry = lexicon.companies[ticker]
        company_forms = (ticker, entry["short"], entry["legal"])
        ends = lexicon.period_ends.get(ticker, {})
        period_ends = {y: ends[y].isoformat() for y in years if y in ends}

    metric_forms = []
    for metric in intent.metrics:
        if metric not in lexicon.metrics:
            raise ValueError(f"{case.case_id}: metric {metric} is not in the lexicon")
        m = lexicon.metrics[metric]
        metric_forms.append((m["label"], *m["synonyms"], *m["shorthand"]))

    universe_forms: tuple[str, ...] = ()
    if intent.universe is not None:
        if intent.universe not in lexicon.universes:
            raise ValueError(f"{case.case_id}: universe {intent.universe} is not in the lexicon")
        universe_forms = lexicon.universes[intent.universe]
    own = {normalise(f) for f in universe_forms}
    forbidden = tuple(phrase for uid, phrases in sorted(lexicon.universes.items())
                      if uid != intent.universe for phrase in phrases
                      if normalise(phrase) not in own)

    return ParaphraseSource(
        case.case_id, case.question, intent_id=intent_id(intent),
        intent_axis=case.intent_axis, template_axis=case.template_axis,
        company_forms=company_forms, years=years, period_ends=period_ends,
        metric_forms=metric_forms, universe_forms=universe_forms, forbidden_forms=forbidden)


def load_intents(path: str | Path) -> list[Intent]:
    """Loads a JSON list of intents, each a mapping of ``Intent``'s canonical fields.

    Args:
        path: The JSON file: ``[{"kind": ..., "tickers": [...], ...}, ...]``; absent keys
            take ``Intent``'s defaults.

    Returns:
        The intents, in file order.

    Raises:
        ValueError: When the file is not a list of mappings with known keys, or an intent is
            invalid.
    """
    p = Path(path)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        raise ValueError(f"{p.name}: not valid JSON") from None
    if not isinstance(doc, list):
        raise ValueError(f"{p.name}: expected a JSON list of intents")
    intents = []
    for i, raw in enumerate(doc):
        if not isinstance(raw, dict) or "kind" not in raw or set(raw) - _INTENT_KEYS:
            raise ValueError(f"{p.name}: intent {i} must be a mapping with 'kind' and only "
                             f"{sorted(_INTENT_KEYS)}")
        fields = dict(raw)
        kind = fields.pop("kind")
        intents.append(Intent(kind, **fields))
    return intents


def build_sources(
    intents: Sequence[Intent],
    phrasebook: Phrasebook,
    lexicon: Lexicon,
    *,
    per_intent: int,
    share: float,
    salt: str,
    split: str,
    forms: Mapping[str, Any] | None = None,
) -> list[ParaphraseSource]:
    """Builds the phrased cases of a split and adapts each to a ``ParaphraseSource``.

    Args:
        intents: The intents.
        phrasebook: Templates.
        lexicon: Surface forms.
        per_intent: Phrasings per intent (``build_phrased_cases``).
        share: The validation share of each split axis.
        salt: The split salt.
        split: ``dev``, ``validation`` or ``all``.
        forms: Check-only forms (``load_paraphrase_forms``) applied to every source with
            ``extend_source``; None leaves the lexicon's forms alone.

    Returns:
        The records, sorted by ``case_id``.

    Raises:
        ValueError: On an unknown split, or anything ``build_phrased_cases`` rejects.
    """
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}, got {split!r}")
    cases = build_phrased_cases(intents, phrasebook, lexicon, per_intent=per_intent,
                                share=share, salt=salt)
    sources = []
    for c in cases:
        if split != "all" and c.split != split:
            continue
        source = paraphrase_source(c, lexicon, phrasebook)
        sources.append(source if forms is None else extend_source(source, c.intent, forms))
    return sources


def add_source_arguments(parser: argparse.ArgumentParser) -> None:
    """Adds the CLI arguments that rebuild a split's sources (export and import share them).

    Args:
        parser: The parser.
    """
    v2 = Path(PROJECT_ROOT) / "configs" / "eval" / "v2"
    parser.add_argument("--intents", type=Path, required=True,
                        help="JSON list of intents (Intent's canonical fields)")
    parser.add_argument("--phrasebook", type=Path, default=v2 / "phrasebook.yaml")
    parser.add_argument("--lexicon", type=Path, default=v2 / "lexicon.yaml")
    parser.add_argument("--per-intent", type=int, required=True, help="phrasings per intent")
    parser.add_argument("--share", type=float, required=True,
                        help="validation share of each split axis (the suite's own)")
    parser.add_argument("--salt", required=True, help="the split salt (the suite's own)")
    parser.add_argument("--split", choices=SPLITS, required=True)
    parser.add_argument("--forms", type=Path, default=v2 / "paraphrase_forms.yaml",
                        help="check-only shorthand forms for the meaning check")


def load_sources(
    intents_path: str | Path,
    phrasebook_path: str | Path,
    lexicon_path: str | Path,
    *,
    per_intent: int,
    share: float,
    salt: str,
    split: str,
    forms_path: str | Path | None = None,
) -> list[ParaphraseSource]:
    """Loads the input files and returns ``build_sources`` over them.

    Args:
        intents_path: The intents JSON (``load_intents``).
        phrasebook_path: The phrasebook YAML.
        lexicon_path: The lexicon YAML.
        per_intent: Phrasings per intent.
        share: The validation share of each split axis.
        salt: The split salt.
        split: ``dev``, ``validation`` or ``all``.
        forms_path: The check-only forms YAML (``load_paraphrase_forms``), or None for none.

    Returns:
        The records, sorted by ``case_id``.
    """
    lexicon = load_lexicon(lexicon_path)
    forms = None if forms_path is None else load_paraphrase_forms(forms_path, lexicon)
    return build_sources(load_intents(intents_path), load_phrasebook(phrasebook_path),
                         lexicon, per_intent=per_intent, share=share, salt=salt, split=split,
                         forms=forms)
