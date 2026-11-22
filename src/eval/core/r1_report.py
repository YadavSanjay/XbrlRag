"""The pre-registered R1 comparison table over stored eval runs (docs/specs/r1-report.md).

Read-only and $0: one stored run per arm on the same suite and split, paired on case id,
gives the per-kind value accuracy, confident-wrong rate, grounding precision (XbrlRag
only), mean cost and latency of each arm, the exact McNemar tests of the reference against
every other arm, and the lines of the v2 success bar (pre-registration §3, Amendment 1 R1).
Held-out runs yield aggregates only.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from src.eval.core.graders.base import percentile
from src.eval.core.regrade import HELDOUT_SPLITS
from src.eval.core.stats import mcnemar_exact
from src.observability.service_tiers import requested_to_served

# Report groups, in table order (requirement 5).
GROUPS = ("cross_filer", "cross_period", "compound", "line1", "single_filer_numeric",
          "lookup_compute", "all")
# The three kinds pooled into the bar's line 1 (pre-registration §3).
LINE1_KINDS = ("cross_filer", "cross_period", "compound")
# Groups the reference is paired against every other arm on (requirement 7).
COMPARISON_GROUPS = (*LINE1_KINDS, "line1")
# H-X kind tags (src/eval/set_b/hx.py), read only when a case has no ``shape:`` tag.
HX_KIND_TAGS = ("cross_filer", "cross_period", "compound")
EXCLUDED_PREFIX = "excluded:"
LEGACY_SPLIT = "legacy"
HELDOUT_SUITE_PREFIX = "heldout"
HELDOUT_CASE_TAGS = frozenset({"set-b"})
NOT_APPLICABLE = "not applicable"
CASE_FIELDS = ("case_id", "tags", "value_correct", "confident_wrong", "grounding_precision",
               "cost_usd", "latency_ms", "error")

# Bar thresholds (§3 as amended), decided on integer counts so each boundary is exact.
P_THRESHOLD = 0.05
COST_FACTOR = 10           # reference mean cost x 10 <= the agent's
LINE3_POINTS_DENOM = 20    # within 5 points: 20 * (k_ref - k_oracle) >= -n
CONFIDENT_WRONG_DENOM = 100  # <= 1%: 100 * k <= n
GROUNDING_MIN = 0.95
STABILITY_NUM, STABILITY_DEN = 9, 10  # >= 90%: 10 * stable >= 9 * intents


class R1Refused(ValueError):  # noqa: N818 - parallels RegradeRefused / HeldOutRefused
    """The arms cannot be read as one pre-registered comparison."""


# ---------------------------------------------------------------------------- kinds


def _shape_groups(shape: str) -> set[str]:
    if shape.startswith("screen.") or shape == "peer_relative":
        return {"cross_filer", "line1"}
    if shape == "compute.cagr":
        return {"cross_period", "line1", "lookup_compute"}
    if shape in ("trend", "point_in_time"):
        return {"cross_period", "line1"}
    if shape.startswith("compound."):
        return {"compound", "line1"}
    if shape == "lookup" or shape.startswith("compute."):
        return {"single_filer_numeric", "lookup_compute"}
    if shape in ("dimensional", "breakdown"):
        return {"single_filer_numeric"}
    return set()


def case_groups(tags: Iterable[str]) -> frozenset[str]:
    """The report groups a case belongs to, from its ``shape:`` or H-X kind tag.

    Args:
        tags: The case's tags.

    Returns:
        A subset of ``cross_filer``, ``cross_period``, ``compound``, ``line1``,
        ``single_filer_numeric``, ``lookup_compute`` and ``all`` (always present).
    """
    tags = list(tags)
    groups = {"all"}
    for tag in tags:
        prefix, sep, value = tag.partition(":")
        if sep and prefix == "shape":
            return frozenset(groups | _shape_groups(value))
    for kind in HX_KIND_TAGS:
        if kind in tags:
            groups |= {kind, "line1"}
    return frozenset(groups)


# ---------------------------------------------------------------------------- inputs


def _tag_values(tags: Iterable[str], prefix: str) -> set[str]:
    out = set()
    for tag in tags:
        p, sep, value = tag.partition(":")
        if sep and p == prefix:
            out.add(value)
    return out


def _is_excluded(row: Mapping[str, Any]) -> bool:
    error = row.get("error")
    return isinstance(error, str) and error.startswith(EXCLUDED_PREFIX)


def _flag(value: Any) -> bool:
    return bool(value) if value is not None else False


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def _arm_split(arm: Mapping[str, Any]) -> str:
    """The arm's one split, or refusal when its sources disagree (requirement 3)."""
    seen: set[str] = set()
    if arm.get("split"):
        seen.add(str(arm["split"]))
    config = arm.get("config") or {}
    if config.get("split"):
        seen.add(str(config["split"]))
    for case in arm.get("cases") or ():
        seen |= _tag_values(case.get("tags") or (), "split")
    if len(seen) > 1:
        raise R1Refused(f"arm {arm.get('label')!r} records more than one split: "
                        f"{sorted(seen)}")
    return seen.pop() if seen else LEGACY_SPLIT


def _is_heldout(arms: Sequence[Mapping[str, Any]]) -> bool:
    """The checks of ``scripts/eval/run_report.py`` (requirement 9)."""
    for arm in arms:
        config = arm.get("config") or {}
        if arm.get("split") in HELDOUT_SPLITS or config.get("split") in HELDOUT_SPLITS:
            return True
        if str(arm.get("suite") or "").lower().startswith(HELDOUT_SUITE_PREFIX):
            return True
        for case in arm.get("cases") or ():
            tags = list(case.get("tags") or ())
            if _tag_values(tags, "split") & HELDOUT_SPLITS or HELDOUT_CASE_TAGS & set(tags):
                return True
    return False


def _validate(arms: Sequence[Mapping[str, Any]], reference: str, bar_agent: str,
              oracle: str | None) -> tuple[str, str, dict[str, frozenset[str]]]:
    """Requirement 3. Returns the suite, the split and every case's groups.

    No message names a case id: it may be shown for a held-out run.
    """
    labels = [a["label"] for a in arms]
    if len(set(labels)) != len(labels):
        raise R1Refused("arm labels repeat")
    if reference not in labels:
        raise R1Refused(f"the reference {reference!r} is not an arm label")
    if bar_agent not in labels:
        raise R1Refused(f"the bar agent {bar_agent!r} is not an arm label")
    if bar_agent == reference:
        raise R1Refused("the bar agent cannot be the reference")
    if oracle is not None and (oracle not in labels or oracle == reference):
        raise R1Refused(f"the oracle {oracle!r} is not an arm label other than the reference")
    suites = {a["suite"] for a in arms}
    if len(suites) != 1:
        raise R1Refused(f"the arms were run on different suites: {sorted(map(str, suites))}")
    splits = {a["label"]: _arm_split(a) for a in arms}
    if len(set(splits.values())) != 1:
        raise R1Refused(f"the arms were run on different splits: {splits}")
    groups: dict[str, frozenset[str]] = {}
    for arm in arms:
        seen: set[str] = set()
        for case in arm["cases"]:
            cid = case["case_id"]
            if cid in seen:
                raise R1Refused(f"arm {arm['label']!r} holds a case id more than once")
            seen.add(cid)
            g = case_groups(case.get("tags") or ())
            if groups.setdefault(cid, g) != g:
                raise R1Refused(f"arm {arm['label']!r} classifies a case into other kinds "
                                "than another arm does; the arms were not run on one suite")
    return next(iter(suites)), next(iter(splits.values())), groups


# ---------------------------------------------------------------------------- cells


def _cell(rows: Sequence[Mapping[str, Any]], n_missing: int, is_reference: bool
          ) -> dict[str, Any]:
    """Requirement 5 over an arm's rows of one group."""
    evaluable = [r for r in rows if not _is_excluded(r)]
    n = len(evaluable)
    k = sum(1 for r in evaluable if _flag(r.get("value_correct")))
    cw = sum(1 for r in evaluable if _flag(r.get("confident_wrong")))
    cost = sum(_float(r.get("cost_usd")) or 0.0 for r in evaluable)
    latencies = [v for v in (_float(r.get("latency_ms")) for r in evaluable) if v is not None]
    grounding: float | str | None = NOT_APPLICABLE
    if is_reference:
        values = [v for v in (_float(r.get("grounding_precision")) for r in evaluable)
                  if v is not None]
        grounding = sum(values) / len(values) if values else None
    return {
        "n_cases": len(rows),
        "n_missing": n_missing,
        "n_excluded": len(rows) - n,
        "n": n,
        "n_value_correct": k,
        "value_accuracy": k / n if n else None,
        "n_confident_wrong": cw,
        "confident_wrong_rate": cw / n if n else None,
        "grounding_precision": grounding,
        "mean_cost_usd": cost / n if n else None,
        "latency_p50_ms": percentile(latencies, 0.50),
        "latency_p95_ms": percentile(latencies, 0.95),
    }


def _compare(ref: Mapping[str, Mapping[str, Any]], other: Mapping[str, Mapping[str, Any]],
             ids: Iterable[str], with_cost: bool) -> tuple[dict[str, Any], list[str], list[str]]:
    """Requirement 7 over one group's case ids. Also returns the discordant ids."""
    pairs: list[str] = []
    n_excluded = n_missing = 0
    for cid in ids:
        if cid not in ref or cid not in other:
            n_missing += 1
        elif _is_excluded(ref[cid]) or _is_excluded(other[cid]):
            n_excluded += 1
        else:
            pairs.append(cid)
    a = {c: _flag(ref[c].get("value_correct")) for c in pairs}
    b = {c: _flag(other[c].get("value_correct")) for c in pairs}
    if pairs:
        test = mcnemar_exact(a, b)
        counts = (test["n"], test["both"], test["a_only"], test["b_only"], test["neither"])
        p_value = float(test["p_value"])
    else:
        counts, p_value = (0, 0, 0, 0, 0), 1.0
    n, both, ref_only, other_only, neither = counts
    out: dict[str, Any] = {
        "n": n, "both": both, "reference_only": ref_only, "other_only": other_only,
        "neither": neither, "n_excluded": n_excluded, "n_missing": n_missing,
        "p_value": p_value, "beats": p_value < P_THRESHOLD and ref_only > other_only,
    }
    if with_cost:
        ref_mean = other_mean = ratio = None
        if pairs:
            ref_mean = sum(_float(ref[c].get("cost_usd")) or 0.0 for c in pairs) / len(pairs)
            other_mean = sum(_float(other[c].get("cost_usd")) or 0.0
                             for c in pairs) / len(pairs)
            ratio = ref_mean / other_mean if other_mean else None
        out.update(reference_mean_cost_usd=ref_mean, other_mean_cost_usd=other_mean,
                   cost_ratio=ratio)
    ref_ids = sorted(c for c in pairs if a[c] and not b[c])
    other_ids = sorted(c for c in pairs if b[c] and not a[c])
    return out, ref_ids, other_ids


# ---------------------------------------------------------------------------- bar


def _line1(cmp: Mapping[str, Any]) -> dict[str, Any]:
    ref_mean, other_mean = cmp["reference_mean_cost_usd"], cmp["other_mean_cost_usd"]
    cost_ok = (cmp["n"] > 0 and other_mean is not None and other_mean > 0
               and COST_FACTOR * ref_mean <= other_mean)
    return {"p_value": cmp["p_value"], "reference_only": cmp["reference_only"],
            "other_only": cmp["other_only"], "cost_ratio": cmp["cost_ratio"],
            "beats": cmp["beats"], "cost_ok": cost_ok, "holds": cmp["beats"] and cost_ok}


def _line3(ref: Mapping[str, Mapping[str, Any]], orc: Mapping[str, Mapping[str, Any]],
           ids: Iterable[str]) -> dict[str, Any]:
    pairs = [c for c in ids if c in ref and c in orc
             and not _is_excluded(ref[c]) and not _is_excluded(orc[c])]
    n = len(pairs)
    k_ref = sum(1 for c in pairs if _flag(ref[c].get("value_correct")))
    k_orc = sum(1 for c in pairs if _flag(orc[c].get("value_correct")))
    return {
        "n": n,
        "reference_accuracy": k_ref / n if n else None,
        "oracle_accuracy": k_orc / n if n else None,
        "difference_points": 100 * (k_ref - k_orc) / n if n else None,
        "holds": n > 0 and LINE3_POINTS_DENOM * (k_ref - k_orc) >= -n,
    }


def _stability(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    by_intent: dict[str, list[bool]] = {}
    for row in rows:
        if _is_excluded(row):
            continue
        intents = sorted(_tag_values(row.get("tags") or (), "intent"))
        if intents:
            by_intent.setdefault(intents[0], []).append(_flag(row.get("value_correct")))
    kept = [v for v in by_intent.values() if len(v) >= 2]
    n_stable = sum(1 for v in kept if len(set(v)) == 1)
    n_intents = len(kept)
    return {"n_intents": n_intents, "n_stable": n_stable,
            "value": n_stable / n_intents if n_intents else None,
            "holds": n_intents > 0 and STABILITY_DEN * n_stable >= STABILITY_NUM * n_intents}


# ---------------------------------------------------------------------------- report


def build_r1(arms: Sequence[Mapping[str, Any]], reference: str, bar_agent: str,
             oracle: str | None = None) -> dict[str, Any]:
    """The R1 table and the bar lines over one stored run per arm.

    Args:
        arms: One mapping per arm (``label``, ``run_id``, ``system``, ``suite``, ``split``,
            ``config``, ``cases``).
        reference: The label of XbrlRag's arm.
        bar_agent: The label of the tools agent the bar is read against.
        oracle: The label of ``long_context_oracle``'s arm, if it ran.

    Returns:
        A JSON-serialisable mapping (see the spec, requirements 5 to 9).

    Raises:
        R1Refused: When the arms are not one comparison (requirement 3).
    """
    suite, split, case_kinds = _validate(arms, reference, bar_agent, oracle)
    heldout = _is_heldout(arms)
    labels = [a["label"] for a in arms]
    rows = {a["label"]: {c["case_id"]: c for c in a["cases"]} for a in arms}
    ids_by_group = {g: sorted(c for c, kinds in case_kinds.items() if g in kinds)
                    for g in GROUPS}

    groups: dict[str, dict[str, Any]] = {}
    for g in GROUPS:
        groups[g] = {}
        for label in labels:
            present = [rows[label][c] for c in ids_by_group[g] if c in rows[label]]
            groups[g][label] = _cell(present, len(ids_by_group[g]) - len(present),
                                     label == reference)

    arm_facts: dict[str, dict[str, Any]] = {}
    for arm in arms:
        config = arm.get("config") or {}
        cell = groups["all"][arm["label"]]
        arm_facts[arm["label"]] = {
            "run_id": arm.get("run_id"), "system": arm.get("system"),
            "service_tier": config.get("service_tier"),
            "max_workers": config.get("max_workers"),
            "kind_concurrency": config.get("kind_concurrency"),
            "service_tier_calls": config.get("service_tier_calls"),
            "cache_groups": config.get("cache_groups"),
            "scheduled_with": config.get("scheduled_with"),
            "segments": _segment_settings(config.get("segments")),
            "n_cases": cell["n_cases"], "n_missing": cell["n_missing"],
            "n_excluded": cell["n_excluded"],
        }

    comparisons: dict[str, dict[str, Any]] = {}
    discordant: dict[str, dict[str, list[str]]] = {}
    for label in labels:
        if label == reference:
            continue
        comparisons[label] = {
            g: _compare(rows[reference], rows[label], ids_by_group[g], g == "line1")[0]
            for g in COMPARISON_GROUPS
        }
        _, ref_only, other_only = _compare(rows[reference], rows[label], ids_by_group["all"],
                                           False)
        discordant[label] = {"reference_only": ref_only, "other_only": other_only}

    ref_all = groups["all"][reference]
    n_ref = ref_all["n"]
    k_cw = ref_all["n_confident_wrong"]
    grounding = ref_all["grounding_precision"]
    bar: dict[str, Any] = {
        "line1": _line1(comparisons[bar_agent]["line1"]),
        "line2": {"status": "withdrawn", "holds": True},
        "line3": (_line3(rows[reference], rows[oracle], ids_by_group["lookup_compute"])
                  if oracle is not None else {"status": "not computed", "holds": False}),
        "confident_wrong": {"n": n_ref, "n_confident_wrong": k_cw,
                            "rate": k_cw / n_ref if n_ref else None,
                            "holds": n_ref > 0 and CONFIDENT_WRONG_DENOM * k_cw <= n_ref},
        "grounding": {"value": grounding,
                      "holds": grounding is not None and grounding >= GROUNDING_MIN},
        "stability": _stability(rows[reference].values()),
    }
    bar["met"] = all(entry["holds"] for entry in bar.values())

    complete = all(groups["all"][label]["n_missing"] == 0 for label in labels)
    case_level = None
    if not heldout:
        case_level = {
            "missing": {label: sorted(c for c in case_kinds if c not in rows[label])
                        for label in labels},
            "excluded": {label: sorted(c for c, r in rows[label].items() if _is_excluded(r))
                         for label in labels},
            "discordant": discordant,
        }
    return {
        "suite": suite, "split": split, "heldout": heldout, "complete": complete,
        "reference": reference, "bar_agent": bar_agent, "oracle": oracle,
        "arms": arm_facts, "groups": groups, "comparisons": comparisons, "bar": bar,
        "case_level": case_level,
    }


# ---------------------------------------------------------------------------- markdown


def _pct_cell(cell: Mapping[str, Any], count_key: str) -> str:
    n = cell["n"]
    if not n:
        return "-"
    k = cell[count_key]
    return f"{100 * k / n:.1f}% ({k}/{n})"


def _num(value: Any, fmt: str) -> str:
    return "-" if value is None else format(value, fmt)


def _concurrency(facts: Mapping[str, Any]) -> str:
    workers = facts.get("max_workers")
    if workers is None:
        return "not recorded"
    text = f"max_workers {workers}"
    kinds = facts.get("kind_concurrency")
    if kinds:
        text += ", per kind " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()))
    if facts.get("cache_groups"):
        text += f", cache groups {facts['cache_groups']}"
    if facts.get("scheduled_with"):
        text += ", scheduled with " + ", ".join(str(s) for s in facts["scheduled_with"])
    segments = facts.get("segments") or []
    if len(segments) > 1:
        text += "; resumed in segments: " + ", ".join(
            f"max_workers {s['max_workers']} tier {s['service_tier'] or 'unset'}"
            for s in segments)
    return text


def _segment_settings(segments: Any) -> list[dict[str, Any]]:
    """Each checkpoint segment's ``max_workers`` and ``service_tier``, in order.

    Args:
        segments: ``config["segments"]`` of a checkpointed run, or None.

    Returns:
        One ``{"max_workers", "service_tier"}`` per segment; empty when none were recorded.
    """
    if not isinstance(segments, list):
        return []
    return [{"max_workers": s.get("max_workers"), "service_tier": s.get("service_tier")}
            for s in segments if isinstance(s, Mapping)]


def _served_tiers(facts: Mapping[str, Any]) -> str:
    """The arm's LLM calls as ``requested -> served n`` pairs, or ``not recorded``.

    ``unset`` means no tier was sent (the provider's default applied) and ``unreported``
    that the reply named none (src/observability/service_tiers.py).
    """
    counts = facts.get("service_tier_calls")
    if not isinstance(counts, Mapping):
        return "not recorded"
    pairs = requested_to_served(counts)
    if not pairs:
        return "no LLM calls"
    by_requested: dict[str, list[str]] = {}
    for (requested, served), n in pairs.items():
        by_requested.setdefault(requested, []).append(f"{served} {n}")
    return "; ".join(f"{requested} -> {', '.join(served)}"
                     for requested, served in by_requested.items())


def _verdict(holds: bool) -> str:
    return "met" if holds else "missed"


def render_markdown(r1: Mapping[str, Any]) -> str:
    """The R1 table and bar lines as Markdown; never names a case.

    Args:
        r1: A result of :func:`build_r1`.

    Returns:
        The Markdown text.
    """
    labels = list(r1["arms"])
    groups = r1["groups"]
    reference = r1["reference"]
    rows: list[tuple[str, list[str]]] = []
    for title, group in (("value accuracy, cross-filer", "cross_filer"),
                         ("value accuracy, cross-period", "cross_period"),
                         ("value accuracy, compound", "compound"),
                         ("value accuracy, pooled", "line1"),
                         ("value accuracy, lookup + compute", "lookup_compute")):
        rows.append((title, [_pct_cell(groups[group][lb], "n_value_correct")
                             for lb in labels]))
    rows.append(("confident-wrong", [_pct_cell(groups["all"][lb], "n_confident_wrong")
                                     for lb in labels]))
    grounding = []
    for lb in labels:
        value = groups["all"][lb]["grounding_precision"]
        if lb != reference:
            grounding.append(NOT_APPLICABLE)
        else:
            grounding.append("-" if value is None else f"{100 * value:.1f}%")
    rows.append(("grounding precision", grounding))
    rows.append(("mean cost per question",
                 ["-" if groups["all"][lb]["mean_cost_usd"] is None
                  else f"${groups['all'][lb]['mean_cost_usd']:.4f}" for lb in labels]))
    rows.append(("latency p50 / p95 (ms)",
                 [f"{_num(groups['all'][lb]['latency_p50_ms'], '.0f')} / "
                  f"{_num(groups['all'][lb]['latency_p95_ms'], '.0f')}" for lb in labels]))
    rows.append(("service tier", [str(r1["arms"][lb]["service_tier"] or "not recorded")
                                  for lb in labels]))
    rows.append(("LLM calls, requested -> served tier",
                 [_served_tiers(r1["arms"][lb]) for lb in labels]))
    rows.append(("concurrency", [_concurrency(r1["arms"][lb]) for lb in labels]))

    lines = [
        f"# R1 comparison: suite `{r1['suite']}`, split `{r1['split']}`",
        "",
        f"Reference `{reference}`, bar agent `{r1['bar_agent']}`, oracle "
        f"`{r1['oracle'] or 'none'}`. Complete: {'yes' if r1['complete'] else 'no'}"
        f"{'' if r1['complete'] else ' (some arm misses cases; see n_missing)'}. "
        f"Held-out: {'yes (aggregates only)' if r1['heldout'] else 'no'}.",
        "",
        "Runs: " + "; ".join(
            f"`{lb}` = {r1['arms'][lb]['run_id']} ({r1['arms'][lb]['system']}, "
            f"{r1['arms'][lb]['n_cases']} cases, {r1['arms'][lb]['n_missing']} missing, "
            f"{r1['arms'][lb]['n_excluded']} excluded)" for lb in labels),
        "",
        "| | " + " | ".join(labels) + " |",
        "|---|" + "---|" * len(labels),
    ]
    lines += [f"| {title} | " + " | ".join(cells) + " |" for title, cells in rows]

    bar = r1["bar"]
    l1, l3 = bar["line1"], bar["line3"]
    ratio = "-" if l1["cost_ratio"] is None else f"{l1['cost_ratio']:.3g}"
    lines += [
        "",
        "## Success bar",
        "",
        f"- Line 1 (beats `{r1['bar_agent']}` on the pooled line-1 kinds at 1/10 the cost): "
        f"p = {l1['p_value']:.3g}, discordant {l1['reference_only']} vs "
        f"{l1['other_only']}, cost ratio {ratio}: {_verdict(l1['holds'])}",
        f"- Line 2 (html_rag): withdrawn (Amendment 1 D4): {_verdict(bar['line2']['holds'])}",
    ]
    if l3.get("status") == "not computed":
        lines.append("- Line 3 (within 5 points of the oracle on lookup + compute): "
                     f"not computed (no oracle arm): {_verdict(l3['holds'])}")
    else:
        lines.append("- Line 3 (within 5 points of the oracle on lookup + compute): "
                     f"{_num(l3['difference_points'], '+.1f')} points over {l3['n']} "
                     f"cases: {_verdict(l3['holds'])}")
    cw, gp, st = bar["confident_wrong"], bar["grounding"], bar["stability"]
    rate = "-" if cw["rate"] is None else f"{100 * cw['rate']:.1f}%"
    gp_text = "-" if gp["value"] is None else f"{100 * gp['value']:.1f}%"
    lines += [
        f"- Confident-wrong <= 1%: {rate} ({cw['n_confident_wrong']}/{cw['n']}): "
        f"{_verdict(cw['holds'])}",
        f"- Grounding precision >= 95%: {gp_text}: {_verdict(gp['holds'])}",
        f"- Paraphrase stability >= 90%: {st['n_stable']}/{st['n_intents']} intents: "
        f"{_verdict(st['holds'])}",
        f"- **Bar: {_verdict(bar['met'])}**"
        f"{'' if r1['complete'] else ' (report incomplete)'}",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- loading


def _decode(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def load_arm(label: str, run_id: str, reader: Any) -> dict[str, Any]:
    """One arm from a stored run, read through a SELECT-only reader.

    Args:
        label: The arm's label in the report.
        run_id: The stored ``eval`` run id.
        reader: An object with ``run_row(run_id)`` and ``case_rows(run_id)``.

    Returns:
        The arm mapping :func:`build_r1` takes.

    Raises:
        R1Refused: An unknown run, or a run without case rows.
    """
    run = reader.run_row(run_id)
    if run is None:
        raise R1Refused(f"no stored run {run_id!r}")
    config = dict(_decode(run.get("config")) or {})
    split = run.get("split") or config.get("split") or LEGACY_SPLIT
    rows = reader.case_rows(run_id)
    if not rows:
        raise R1Refused(f"run {run_id!r} has no case rows")
    cases = []
    for row in rows:
        case = {key: row.get(key) for key in CASE_FIELDS}
        case["tags"] = list(_decode(row.get("tags")) or [])
        cases.append(case)
    return {"label": label, "run_id": run_id, "system": run.get("system"),
            "suite": run.get("suite"), "split": split, "config": config, "cases": cases}
