"""The frozen dev sample: one phrasing per intent, stratified by shape.

Some baseline arms are too costly to run on the whole v2 dev suite, so they run on a fixed
sample of it (docs/eval/heldout_v2/measurement_plan.md §3: ``frontier_sec_tools``,
``html_rag`` and the long-context arms). The sample is drawn once, written as a suite file
and pre-registered by its content hash, so every arm answers the same cases.

The draw:

* **One phrasing per intent.** An intent is a ``paraphrase_group``; its phrasings ask the
  same question, so taking two would count one question twice.
* **Stratified by shape.** Each shape (the ``shape:`` tag) gets a share of ``n`` in
  proportion to its number of intents, by the largest-remainder method, so the sample's mix
  matches the dev suite's.
* **Seeded and order-free.** Every random choice comes from ``random.Random`` seeded by a
  string built from the seed and the shape or intent, over id-sorted inputs, so the same
  suite and seed always give the same sample whatever order the cases arrive in.

Cross-filer shapes (screens and ``peer_relative``) stay in the sample; an arm that can only
answer one filer, such as ``long_context_oracle``, reports them as not applicable.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from src.eval.suites.v2_suite import content_hash

# Shapes whose question spans more than one filer.
CROSS_FILER_SHAPES = frozenset(
    {"screen.top", "screen.bottom", "screen.sum", "screen.median", "peer_relative"}
)


def case_shape(case: dict[str, Any]) -> str:
    """The case's shape, from its ``shape:`` tag.

    Args:
        case: A v2 suite case record.

    Returns:
        The shape, e.g. ``"compute.ratio"``.

    Raises:
        ValueError: If the case has no ``shape:`` tag, or more than one.
    """
    shapes = [t.removeprefix("shape:") for t in case.get("tags", []) if t.startswith("shape:")]
    if len(shapes) != 1:
        raise ValueError(f"case {case.get('id')!r} has {len(shapes)} shape tags, expected 1")
    return shapes[0]


def shape_quotas(intents_by_shape: dict[str, int], n: int) -> dict[str, int]:
    """Splits ``n`` across shapes in proportion to their intent counts.

    Largest remainder (Hamilton): each shape gets the floor of its exact share, and the
    cases left over go to the largest remainders, ties broken by shape name.

    Args:
        intents_by_shape: Number of intents of each shape.
        n: The sample size.

    Returns:
        The number of intents to draw from each shape; the values sum to ``n``.

    Raises:
        ValueError: If ``n`` is not between 1 and the total number of intents.
    """
    total = sum(intents_by_shape.values())
    if not 1 <= n <= total:
        raise ValueError(f"sample size {n} must be between 1 and the {total} intents")
    exact = {s: n * k / total for s, k in intents_by_shape.items()}
    quotas = {s: int(v) for s, v in exact.items()}
    left = n - sum(quotas.values())
    by_remainder = sorted(exact, key=lambda s: (-(exact[s] - quotas[s]), s))
    for s in by_remainder[:left]:
        quotas[s] += 1
    return quotas


def draw_sample(cases: Sequence[dict[str, Any]], n: int, seed: str) -> list[dict[str, Any]]:
    """Draws ``n`` cases, one phrasing per intent, stratified by shape.

    Args:
        cases: The dev suite's case records.
        n: The sample size, at most the number of intents.
        seed: The draw's seed string.

    Returns:
        The chosen case records, unchanged, sorted by id.

    Raises:
        ValueError: If an intent mixes shapes, a case lacks a shape or a ``paraphrase_group``,
            or ``n`` is out of range.
    """
    by_intent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        group = case.get("paraphrase_group")
        if not group:
            raise ValueError(f"case {case.get('id')!r} has no paraphrase_group")
        by_intent[group].append(case)

    intents_of_shape: dict[str, list[str]] = defaultdict(list)
    for group in sorted(by_intent):
        shapes = {case_shape(c) for c in by_intent[group]}
        if len(shapes) != 1:
            raise ValueError(f"intent {group!r} mixes shapes {sorted(shapes)}")
        intents_of_shape[shapes.pop()].append(group)

    quotas = shape_quotas({s: len(g) for s, g in intents_of_shape.items()}, n)
    chosen: list[dict[str, Any]] = []
    for shape in sorted(intents_of_shape):
        groups = random.Random(f"{seed}:shape:{shape}").sample(
            intents_of_shape[shape], quotas[shape]
        )
        for group in groups:
            phrasings = sorted(by_intent[group], key=lambda c: c["id"])
            chosen.append(random.Random(f"{seed}:intent:{group}").choice(phrasings))
    return sorted(chosen, key=lambda c: c["id"])


def sample_suite(suite: dict[str, Any], n: int, seed: str) -> dict[str, Any]:
    """The sample as a suite file ``run_suite.py --suite-path`` loads.

    Args:
        suite: The dev suite file's content.
        n: The sample size.
        seed: The draw's seed string.

    Returns:
        The sample suite: the dev suite's ``version`` and ``split``, a ``_sample`` name,
        ``content_hash`` over the chosen cases (as the suite builder computes it), and a
        ``sample`` block recording the source suite's hash, the seed and the mix.
    """
    cases = draw_sample(suite["cases"], n, seed)
    by_shape: dict[str, int] = defaultdict(int)
    for case in cases:
        by_shape[case_shape(case)] += 1
    cross = sum(k for s, k in by_shape.items() if s in CROSS_FILER_SHAPES)
    return {
        "name": f"{suite['name']}_sample",
        "version": suite["version"],
        "split": suite["split"],
        "n_cases": len(cases),
        "content_hash": content_hash(cases),
        "sample": {
            "source_suite": suite["name"],
            "source_content_hash": suite["content_hash"],
            "n": n,
            "seed": seed,
            "rule": "one phrasing per intent; shapes by largest remainder over intent counts",
            "by_shape": dict(sorted(by_shape.items())),
            "single_filer": len(cases) - cross,
            "cross_filer": cross,
        },
        "cases": cases,
    }
