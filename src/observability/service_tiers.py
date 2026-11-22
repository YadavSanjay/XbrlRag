"""Counts of LLM calls by requested and served service tier, per provider and stage.

The gateway counts every call that reached the provider (success, billed empty reply or
refusal) on the active trace; an eval answer copies its trace's counts, and the run sums
its answers' counts into ``config["service_tier_calls"]``, which the R1 report prints
beside latency.

The shape is ``{"<provider>/<stage>": {<requested>: {<served>: n}}}``. ``<requested>`` is
the ``service_tier`` sent on the request, or :data:`UNSET` when none was sent (the
provider's default applies); ``<served>`` is the tier the reply reported, or
:data:`UNREPORTED` when it reported none.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

# No service_tier was sent: the provider's default tier applies.
UNSET = "unset"
# The reply named no tier (DeepSeek, older SDKs, a refusal without usage).
UNREPORTED = "unreported"

TierCounts = dict[str, dict[str, dict[str, int]]]


def _label(value: object, missing: str) -> str:
    return value if isinstance(value, str) and value.strip() else missing


def count_call(
    counts: TierCounts,
    *,
    provider: str,
    stage: str,
    requested: str | None,
    served: str | None,
) -> None:
    """Adds one call to *counts* in place.

    Args:
        counts: The counts to add to.
        provider: The provider called (``ModelRef.provider``).
        stage: The calling stage.
        requested: The ``service_tier`` sent, or None.
        served: The tier the reply reported, or None.
    """
    by_requested = counts.setdefault(f"{provider}/{stage}", {})
    by_served = by_requested.setdefault(_label(requested, UNSET), {})
    served_label = _label(served, UNREPORTED)
    by_served[served_label] = by_served.get(served_label, 0) + 1


def merge_counts(sources: Iterable[Mapping[str, Mapping[str, Mapping[str, int]]]]) -> TierCounts:
    """The sum of several counts, keys sorted at every level.

    Args:
        sources: Counts to add up; a malformed entry (not a mapping, a non-integer
            count) is skipped.

    Returns:
        A new mapping; empty when every source is.
    """
    total: TierCounts = {}
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        for key, by_requested in source.items():
            if not isinstance(by_requested, Mapping):
                continue
            for requested, by_served in by_requested.items():
                if not isinstance(by_served, Mapping):
                    continue
                for served, n in by_served.items():
                    if isinstance(n, bool) or not isinstance(n, int):
                        continue
                    cell = total.setdefault(str(key), {}).setdefault(str(requested), {})
                    cell[str(served)] = cell.get(str(served), 0) + n
    return {
        key: {req: dict(sorted(by_served.items())) for req, by_served in sorted(by_req.items())}
        for key, by_req in sorted(total.items())
    }


def requested_to_served(counts: Mapping[str, Mapping[str, Mapping[str, int]]]) -> dict[
        tuple[str, str], int]:
    """Calls per ``(requested, served)`` pair, summed over providers and stages.

    Args:
        counts: Counts in the module's shape.

    Returns:
        ``(requested, served) -> n``, in sorted key order.
    """
    pairs: dict[tuple[str, str], int] = {}
    for by_requested in merge_counts([counts]).values():
        for requested, by_served in by_requested.items():
            for served, n in by_served.items():
                pairs[(requested, served)] = pairs.get((requested, served), 0) + n
    return dict(sorted(pairs.items()))
