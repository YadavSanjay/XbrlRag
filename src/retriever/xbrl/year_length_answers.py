"""The year-length note in answers that compare fiscal periods.

Spec: docs/specs/period-year-length-answers.md. Pure functions: which compared periods of a
compute, cagr or growth-screen answer differ in length, by period-disclosure's one rule
(``year_length_disclosure``), and the fixed sentence a growth screen's answer carries.
Periods are the cited facts' stored ``period_start`` / ``period_end`` (the end one day after
the period's last day), never a fiscal-year label. Nothing here changes a number.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.retriever.xbrl.period_disclosure import year_length_disclosure

# The keys an entry copies from ``year_length_disclosure`` (``differs`` is implied).
_DISCLOSURE_KEYS = ("days_current", "days_prior", "current_period", "prior_period", "note")
_FIFTY_THREE_WEEK_DAYS = (370, 371, 372)
_SCREEN_HEAD = "Growth compares fiscal years of different lengths: "
_SCREEN_TAIL = ". Growth is on the reported basis, not adjusted for the different number of days."


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _eligible(item: Mapping[str, Any]) -> bool:
    return (
        item.get("op") == "lookup"
        and item.get("status") == "OK"
        and item.get("period_start") is not None
        and item.get("period_end") is not None
        and _is_int(item.get("fy"))
    )


def _group_key(item: Mapping[str, Any]) -> tuple[Any, Any, str, bool]:
    fiscal_period = item.get("fiscal_period")
    period = "FY" if fiscal_period is None else str(fiscal_period).upper()
    return (item.get("cik"), item.get("concept_family"), period, bool(item.get("ytd")))


def _period(item: Mapping[str, Any]) -> dict[str, Any]:
    return {"period_start": item["period_start"], "period_end": item["period_end"]}


def compared_year_lengths(inputs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The compared periods of one compute or cagr step that differ in length.

    Args:
        inputs: The step's input steps, in the order the step references them; each has
            ``step_id``, ``op``, ``status``, ``cik``, ``concept_family``, ``fiscal_period``,
            ``ytd``, ``fy``, ``fact_id`` and the cited fact's stored ``period_start`` and
            ``period_end``.

    Returns:
        One entry per compared pair whose lengths differ, as in the spec's Requirement 2;
        empty when there is none.
    """
    # group key -> fiscal year -> its first input (dicts keep first-seen group order).
    groups: dict[tuple[Any, Any, str, bool], dict[int, Mapping[str, Any]]] = {}
    for item in inputs:
        if not _eligible(item):
            continue
        years = groups.setdefault(_group_key(item), {})
        years.setdefault(int(item["fy"]), item)
    entries: list[dict[str, Any]] = []
    for years in groups.values():
        ordered = [years[fy] for fy in sorted(years)]
        for prior, current in zip(ordered, ordered[1:], strict=False):
            disclosure = year_length_disclosure(_period(current), _period(prior))
            if not disclosure["differs"]:
                continue
            entry: dict[str, Any] = {
                "current_step": current["step_id"],
                "prior_step": prior["step_id"],
                "fact_ids": [current["fact_id"], prior["fact_id"]],
            }
            entry.update({k: disclosure[k] for k in _DISCLOSURE_KEYS})
            entries.append(entry)
    return entries


def _fact_id(value: Any) -> int | None:
    if _is_int(value):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _component_pair(components: Any) -> tuple[int, int] | None:
    """``(current, prior)`` fact ids of a derived row, or None when there is no single pair."""
    if not isinstance(components, Mapping):
        return None
    current = [v for k, v in components.items() if str(k).endswith("_current")]
    prior = [v for k, v in components.items() if str(k).endswith("_prior")]
    if len(current) != 1 or len(prior) != 1:
        return None
    current_id, prior_id = _fact_id(current[0]), _fact_id(prior[0])
    if current_id is None or prior_id is None:
        return None
    return current_id, prior_id


def screen_year_lengths(
    rows: Sequence[Mapping[str, Any]], periods: Mapping[int, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The ranked growth-screen rows whose two compared years differ in length.

    Args:
        rows: The screen's ranked rows, each with ``ticker`` and ``component_fact_ids``.
        periods: Fact id to the stored ``period_start`` and ``period_end`` of that fact.

    Returns:
        One entry per such row, in row order, as in the spec's Requirement 3.
    """
    entries: list[dict[str, Any]] = []
    for row in rows:
        pair = _component_pair(row.get("component_fact_ids"))
        if pair is None:
            continue
        current, prior = (periods.get(fid) for fid in pair)
        if current is None or prior is None:
            continue
        if any(p.get(k) is None for p in (current, prior) for k in ("period_start", "period_end")):
            continue
        disclosure = year_length_disclosure(current, prior)
        if not disclosure["differs"]:
            continue
        entry: dict[str, Any] = {"ticker": str(row.get("ticker")), "fact_ids": list(pair)}
        entry.update({k: disclosure[k] for k in _DISCLOSURE_KEYS})
        entries.append(entry)
    return entries


def _screen_part(entry: Mapping[str, Any]) -> str:
    cs, ce = entry["current_period"]
    ps, pe = entry["prior_period"]
    dc, dp = entry["days_current"], entry["days_prior"]
    part = f"{entry['ticker']} {cs} to {ce} ({dc} days) against {ps} to {pe} ({dp} days)"
    if dc in _FIFTY_THREE_WEEK_DAYS or dp in _FIFTY_THREE_WEEK_DAYS:
        part += ", one is a 53-week year"
    return part


def screen_year_length_text(entries: Sequence[Mapping[str, Any]]) -> str:
    """The sentence naming a growth screen's members whose compared years differ in length.

    Args:
        entries: ``screen_year_lengths`` entries.

    Returns:
        The sentence of the spec's Requirement 4, or ``""`` when there is no entry.
    """
    if not entries:
        return ""
    return _SCREEN_HEAD + "; ".join(_screen_part(e) for e in entries) + _SCREEN_TAIL


def append_year_length_disclosures(summary: str, steps: Sequence[Mapping[str, Any]]) -> str:
    """Append each growth screen's year-length sentence to an answer summary.

    Args:
        summary: The answer summary.
        steps: The plan's step results as ``StepResult.to_dict()`` dicts.

    Returns:
        The summary with the sentence of each ``screen`` step that has ``year_lengths``
        appended in step order, as in the spec's Requirement 5.
    """
    sentences: list[str] = []
    for step in steps:
        entries = step.get("year_lengths")
        if step.get("op") != "screen" or not isinstance(entries, list) or not entries:
            continue
        text = screen_year_length_text(entries)
        if text not in summary and text not in sentences:
            sentences.append(text)
    if not sentences:
        return summary
    base = summary.rstrip()
    return (f"{base} " if base else "") + " ".join(sentences)
