"""Check-only surface forms for the paraphrase meaning check (analyst shorthand).

The lexicon renders the suite, so it holds no shorthand the suite would not use. This module
reads a second, check-only forms file (``configs/eval/v2/paraphrase_forms.yaml``) and extends a
``ParaphraseSource`` with it; the suite build never reads it, so the frozen suites do not change.
Spec: docs/specs/paraphrase-shorthand.md.

No error message here quotes a form; messages name keys, ids and positions only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from src.eval.suites.intents import Intent, Lexicon
from src.eval.suites.paraphrases import DRIFT_PHRASES, ParaphraseSource, normalise

_KEYS = frozenset({"metrics", "ratios", "universes", "companies", "possessive_exempt"})


def _contains(text: str, form: str) -> bool:
    """The eval-paraphrases *contains* rule: whole normalised tokens."""
    n = normalise(form)
    return bool(n) and f" {n} " in f" {normalise(text)} "


def _form_list(where: str, value: object) -> tuple[str, ...]:
    """Validates one list of forms; the message names ``where``, never a form."""
    if not isinstance(value, list) or not all(isinstance(f, str) for f in value):
        raise ValueError(f"{where}: must be a list of strings")
    for i, form in enumerate(value):
        if not normalise(form):
            raise ValueError(f"{where}[{i}]: normalises to empty")
        if any(_contains(form, phrase) for phrase in DRIFT_PHRASES):
            raise ValueError(f"{where}[{i}]: contains a drift phrase")
    return tuple(value)


def _mapping(where: str, value: object) -> Mapping[str, Any]:
    """An optional mapping key: ``None`` is empty, anything but a dict raises."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{where}: must be a mapping")
    return value


def load_paraphrase_forms(path: str | Path, lexicon: Lexicon) -> dict[str, Any]:
    """Loads and validates a check-only forms file against the lexicon.

    Args:
        path: The forms YAML: optional keys ``metrics``, ``ratios``, ``universes``,
            ``companies`` and ``possessive_exempt``.
        lexicon: The lexicon whose ids the file must name.

    Returns:
        ``{"metrics": {id: forms}, "ratios": ((numerator, denominator, forms), ...),
        "universes": {id: phrases}, "companies": {ticker: forms}, "possessive_exempt":
        frozenset of normalised forms}``; an absent key gives an empty value.

    Raises:
        ValueError: On any rule of the spec's requirement 1.
    """
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if doc is None:
        doc = {}
    if not isinstance(doc, dict):
        raise ValueError("the forms file must be a mapping")
    unknown = set(doc) - _KEYS
    if unknown:
        raise ValueError(f"unknown keys: {sorted(unknown)}")

    metrics: dict[str, tuple[str, ...]] = {}
    for metric, value in _mapping("metrics", doc.get("metrics")).items():
        if metric not in lexicon.metrics:
            raise ValueError(f"metrics: {metric} is not in the lexicon")
        metrics[metric] = _form_list(f"metrics.{metric}", value)

    ratios = []
    raw_ratios = doc.get("ratios") or []
    if not isinstance(raw_ratios, list):
        raise ValueError("ratios: must be a list")
    for i, entry in enumerate(raw_ratios):
        pair = entry.get("metrics") if isinstance(entry, dict) else None
        if (not isinstance(pair, list) or len(pair) != 2
                or any(m not in lexicon.metrics for m in pair)):
            raise ValueError(f"ratios[{i}]: must name exactly two lexicon metrics")
        ratios.append((pair[0], pair[1], _form_list(f"ratios[{i}].forms", entry.get("forms"))))

    universes: dict[str, tuple[str, ...]] = {}
    for universe, value in _mapping("universes", doc.get("universes")).items():
        if universe not in lexicon.universes:
            raise ValueError(f"universes: {universe} is not in the lexicon")
        phrases = _form_list(f"universes.{universe}", value)
        others = {normalise(p) for uid, ps in lexicon.universes.items() if uid != universe
                  for p in ps}
        if any(normalise(p) in others for p in phrases):
            raise ValueError(f"universes.{universe}: a phrase is another universe's")
        universes[universe] = phrases

    companies: dict[str, tuple[str, ...]] = {}
    for ticker, value in _mapping("companies", doc.get("companies")).items():
        if ticker not in lexicon.companies:
            raise ValueError(f"companies: {ticker} is not in the lexicon")
        companies[ticker] = _form_list(f"companies.{ticker}", value)

    exempt = _form_list("possessive_exempt", doc.get("possessive_exempt") or [])
    return {"metrics": metrics, "ratios": tuple(ratios), "universes": universes,
            "companies": companies,
            "possessive_exempt": frozenset(normalise(f) for f in exempt)}


def _append(base: Sequence[str], extra: Iterable[str]) -> tuple[str, ...]:
    """``base`` then each of ``extra`` whose normalised form is new, in order."""
    seen = {normalise(f) for f in base}
    out = list(base)
    for form in extra:
        n = normalise(form)
        if n not in seen:
            seen.add(n)
            out.append(form)
    return tuple(out)


def extend_source(
    source: ParaphraseSource, intent: Intent, forms: Mapping[str, Any]
) -> ParaphraseSource:
    """Returns ``source`` with the forms file's extra forms for its intent.

    Args:
        source: The source built from the lexicon.
        intent: The source's intent.
        forms: The result of ``load_paraphrase_forms``.

    Returns:
        A new source: metric extras, then the matching ratio's forms on both metrics; the
        universe's extra phrases, with the other universes' extras forbidden; company aliases,
        then dropped-apostrophe variants of every company form that is not exempt.
    """
    ratio_forms: tuple[str, ...] = ()
    if intent.operation == "ratio":
        for numerator, denominator, extra in forms.get("ratios", ()):
            if (numerator, denominator) == tuple(intent.metrics):
                ratio_forms = _append(ratio_forms, extra)
    extra_metrics = forms.get("metrics", {})
    metric_forms = [
        _append(_append(own, extra_metrics.get(metric, ())), ratio_forms)
        for metric, own in zip(intent.metrics, source.metric_forms, strict=True)
    ]

    universe_forms = tuple(source.universe_forms)
    forbidden_forms = tuple(source.forbidden_forms)
    if intent.universe is not None:
        extra_universes = forms.get("universes", {})
        universe_forms = _append(universe_forms, extra_universes.get(intent.universe, ()))
        own = {normalise(f) for f in universe_forms}
        forbidden_forms = _append(forbidden_forms, (
            phrase for uid, phrases in sorted(extra_universes.items())
            if uid != intent.universe for phrase in phrases if normalise(phrase) not in own))

    company_forms = tuple(source.company_forms)
    if company_forms and intent.tickers:
        company_forms = _append(company_forms,
                                forms.get("companies", {}).get(intent.tickers[0], ()))
        exempt = forms.get("possessive_exempt", frozenset())
        company_forms = _append(company_forms, [normalise(f) + "s" for f in company_forms
                                                if normalise(f) not in exempt])

    return ParaphraseSource(
        source.case_id, source.question, intent_id=source.intent_id,
        intent_axis=source.intent_axis, template_axis=source.template_axis,
        company_forms=company_forms, years=source.years, period_ends=source.period_ends,
        metric_forms=metric_forms, universe_forms=universe_forms,
        forbidden_forms=forbidden_forms)
