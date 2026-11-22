"""What a cross-filer screen ranked, and the sentence that says so (S2, S3).

Spec: docs/specs/screen-universe.md. A screen ranks the members of a universe (``covered``,
every corpus filer, or a named group of ``configs/eval/v2/groups.yaml``). Its result carries
a structured disclosure: the universe, the ranked filers, each member it could not rank with
the reason, and the ranked filers' period ends. ``screen_disclosure_text`` turns that into a
fixed sentence, and ``append_screen_disclosures`` appends it to the answer after the
faithfulness verifier. The sentence is built from the screen's data, never written by the
LLM, so no exclusion or date can be dropped or misquoted.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any

COVERED = "covered"
# S2: the answer gives each filer's period end when the ranked year-ends are more than
# three months apart.
_DISCLOSE_DAYS = 92


def stated_period_end(stored: dt.date | str | None) -> str | None:
    """The period's last day for a stored ``mv_financial_facts.period_end``.

    The view stores Arelle's next-midnight date, one day after the period's last day
    (MSFT fiscal 2022 is stored as 2022-07-01; SEC states 2022-06-30).

    Args:
        stored: The stored end, as a date or an ISO ``YYYY-MM-DD`` string, or None.

    Returns:
        The ISO date one day before ``stored``, or None for None.
    """
    if stored is None:
        return None
    if isinstance(stored, dt.datetime):
        day = stored.date()
    elif isinstance(stored, dt.date):
        day = stored
    else:
        day = dt.date.fromisoformat(str(stored).strip())
    return (day - dt.timedelta(days=1)).isoformat()


def resolve_universe_members(
    universe: str, roster: Sequence[str], groups: Mapping[str, Sequence[str]]
) -> list[str]:
    """The tickers a screen's universe holds.

    Args:
        universe: ``"covered"`` or a group id.
        roster: The corpus filers' tickers (``entity_master``).
        groups: Group id to member tickers (``load_universe_groups()["groups"]``).

    Returns:
        The sorted, de-duplicated roster tickers for ``covered``; the sorted group
        tickers for a group id, including members the roster does not hold.

    Raises:
        ValueError: ``universe`` is neither ``covered`` nor a group id; the message names
            the value.
    """
    if universe == COVERED:
        return sorted(set(roster))
    if universe in groups:
        return sorted(set(groups[universe]))
    valid = ", ".join([COVERED, *sorted(groups)])
    raise ValueError(f"unknown screen universe {universe!r}; valid: {valid}")


def build_screen_disclosure(
    universe: str,
    fiscal_year: int,
    members: Sequence[str],
    roster: Sequence[str],
    ranked_rows: Sequence[Mapping[str, Any]],
    exclusions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The structured disclosure of a screen's universe.

    Args:
        universe: ``"covered"`` or the group id.
        fiscal_year: The screened fiscal year.
        members: The universe's member tickers.
        roster: The corpus filers' tickers.
        ranked_rows: Every ranked member's row in rank order, before ``top_n``; each has
            ``ticker`` and the stored ``period_end`` (or None).
        exclusions: Exclusions found by the screen's filters, each
            ``{"ticker", "reason", ...}``; those of non-members are dropped.

    Returns:
        ``{"universe", "fiscal_year", "members", "ranked", "exclusions", "period_ends",
        "period_ends_differ"}`` as in the spec's Requirement 5.
    """
    member_set = set(members)
    in_corpus = set(roster)
    ranked = [str(r.get("ticker")) for r in ranked_rows]
    ranked_set = set(ranked)

    # The first given exclusion per member wins; a ranked member is never excluded.
    given: dict[str, dict[str, Any]] = {}
    for e in exclusions:
        ticker = e.get("ticker")
        if ticker in member_set and ticker not in ranked_set and ticker not in given:
            given[str(ticker)] = dict(e)

    out_exclusions: list[dict[str, Any]] = []
    for ticker in sorted(member_set - ranked_set):
        if ticker in given:
            out_exclusions.append(given[ticker])
        elif ticker not in in_corpus:
            out_exclusions.append({"ticker": ticker, "reason": "not_in_corpus"})
        else:
            out_exclusions.append({"ticker": ticker, "reason": "metric_not_tagged"})

    period_ends: dict[str, str] = {}
    for r in ranked_rows:
        stated = stated_period_end(r.get("period_end"))
        if stated is not None:
            period_ends[str(r.get("ticker"))] = stated
    ends = [dt.date.fromisoformat(s) for s in period_ends.values()]
    differ = len(ends) >= 2 and (max(ends) - min(ends)).days > _DISCLOSE_DAYS

    return {
        "universe": universe,
        "fiscal_year": fiscal_year,
        "members": len(member_set),
        "ranked": ranked,
        "exclusions": out_exclusions,
        "period_ends": period_ends,
        "period_ends_differ": differ,
    }


def _reason_phrase(exclusion: Mapping[str, Any], fiscal_year: Any) -> str:
    """Requirement 7's words for one exclusion's reason."""
    reason = str(exclusion.get("reason"))
    if reason == "period_not_filed":
        latest = exclusion.get("latest_fiscal_year")
        phrase = f"no FY{fiscal_year} figure"
        return phrase if latest is None else f"{phrase}; latest FY{latest}"
    if reason == "currency":
        if "currency" not in exclusion:
            return "does not report in USD"
        return f"reports in {exclusion['currency']}, not USD"
    # The member screens' reasons (docs/specs/segment-lookup-screens.md, Requirement 4).
    if reason in ("ambiguous_member", "different_member") and exclusion.get("labels"):
        labels = ", ".join(str(x) for x in exclusion["labels"])
        if reason == "ambiguous_member":
            return f"several members match: {labels}"
        return f"reports a different member: {labels}"
    fixed = {
        "metric_not_tagged": "metric not reported",
        "component_only": "reports only components of the metric",
        "not_in_corpus": "not in the corpus",
        "nil": "filed nil, nothing reported",  # docs/specs/sign-and-nil.md R2
        "ambiguous_member": "several members match",
        "different_member": "reports a different member",
        "multi_dimension_only": "the member is reported only in a table with another dimension",
        "member_not_found": "no member by that name",
    }
    return fixed.get(reason, reason.replace("_", " "))


def screen_disclosure_text(disclosure: Mapping[str, Any]) -> str:
    """The disclosure sentence(s) for one screen.

    Args:
        disclosure: A disclosure as ``build_screen_disclosure`` returns it.

    Returns:
        ``Universe: ... ranked for FY<fy>).`` followed, when they apply, by the
        "Not ranked" and "Fiscal years end on different dates" sentences.
    """
    universe = str(disclosure.get("universe"))
    fiscal_year = disclosure.get("fiscal_year")
    members = disclosure.get("members")
    ranked = list(disclosure.get("ranked") or [])
    label = (
        "the covered companies" if universe == COVERED
        else f"the {universe.replace('_', ' ')} group"
    )
    noun = "company" if members == 1 else "companies"
    sentences = [f"Universe: {label} ({members} {noun}; {len(ranked)} ranked for FY{fiscal_year})."]

    exclusions = sorted(disclosure.get("exclusions") or [], key=lambda e: str(e.get("ticker")))
    if exclusions:
        sentences.append(
            "Not ranked: "
            + "; ".join(f"{e.get('ticker')} ({_reason_phrase(e, fiscal_year)})" for e in exclusions)
            + "."
        )

    if disclosure.get("period_ends_differ"):
        ends = disclosure.get("period_ends") or {}
        listed = [f"{t} {ends[t]}" for t in ranked if t in ends]
        sentences.append("Fiscal years end on different dates: " + ", ".join(listed) + ".")
    return " ".join(sentences)


def append_screen_disclosures(summary: str, steps: Sequence[Mapping[str, Any]]) -> str:
    """Append each screen step's disclosure sentence to an answer summary.

    Args:
        summary: The answer summary.
        steps: The plan's step results as ``StepResult.to_dict()`` dicts.

    Returns:
        The summary with the sentence of each ``screen`` step that has a dict
        ``disclosure`` appended in step order, one space apart, skipping sentences the
        text already contains; the summary unchanged when there is none to add.
    """
    sentences: list[str] = []
    for step in steps:
        disclosure = step.get("disclosure")
        if step.get("op") != "screen" or not isinstance(disclosure, Mapping):
            continue
        text = screen_disclosure_text(disclosure)
        if text not in summary and text not in sentences:
            sentences.append(text)
    if not sentences:
        return summary
    base = summary.rstrip()
    return (f"{base} " if base else "") + " ".join(sentences)
