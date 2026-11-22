"""The dev sample draw (src/eval/suites/dev_sample.py).

Expected quotas are worked by hand from the largest-remainder rule, not from the code.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from src.eval.suites.dev_sample import draw_sample, sample_suite, shape_quotas
from src.eval.suites.v2_suite import content_hash


def _cases(spec: dict[str, tuple[int, int]]) -> list[dict]:
    """Synthetic cases: ``{shape: (intents, phrasings per intent)}``."""
    cases = []
    for shape, (n_intents, per) in spec.items():
        for i in range(n_intents):
            group = f"int-{shape}-{i:03d}"
            for j in range(per):
                cases.append({"id": f"{group}-{j}", "paraphrase_group": group,
                              "tags": ["split:dev", f"shape:{shape}"]})
    return cases


def test_quotas_follow_largest_remainder_by_hand() -> None:
    # 10 cases over 7/2/1 intents: exact shares 7.0, 2.0, 1.0.
    assert shape_quotas({"a": 7, "b": 2, "c": 1}, 10) == {"a": 7, "b": 2, "c": 1}
    # 3 cases over 5/3/2: exact 1.5, 0.9, 0.6 -> floors 1/0/0, leftover 2 to b (.9), c (.6).
    assert shape_quotas({"a": 5, "b": 3, "c": 2}, 3) == {"a": 1, "b": 1, "c": 1}
    # 2 cases over 1/1/1: shares 2/3 each, tie broken by name -> a, b.
    assert shape_quotas({"c": 1, "b": 1, "a": 1}, 2) == {"a": 1, "b": 1, "c": 0}


@pytest.mark.parametrize("n", [0, 11])
def test_quota_size_out_of_range_raises(n: int) -> None:
    with pytest.raises(ValueError):
        shape_quotas({"a": 7, "b": 3}, n)


def test_one_phrasing_per_intent_and_mix_matches_quotas() -> None:
    cases = _cases({"lookup": (40, 3), "compute.ratio": (30, 2), "screen.top": (10, 1)})
    sample = draw_sample(cases, 16, "seed-1")
    groups = Counter(c["paraphrase_group"] for c in sample)
    assert len(sample) == 16 and max(groups.values()) == 1
    shapes = Counter(c["tags"][1].removeprefix("shape:") for c in sample)
    # 16 over 40/30/10 intents: exact 8.0, 6.0, 2.0.
    assert shapes == {"lookup": 8, "compute.ratio": 6, "screen.top": 2}


def test_same_seed_any_input_order_gives_same_sample() -> None:
    cases = _cases({"lookup": (20, 3), "trend": (5, 2)})
    shuffled = list(cases)
    random.Random(7).shuffle(shuffled)
    assert draw_sample(cases, 10, "s") == draw_sample(shuffled, 10, "s")
    assert draw_sample(cases, 10, "s") != draw_sample(cases, 10, "t")


def test_an_intent_mixing_shapes_raises() -> None:
    cases = _cases({"lookup": (2, 1)})
    cases.append({"id": "int-lookup-000-x", "paraphrase_group": "int-lookup-000",
                  "tags": ["shape:trend"]})
    with pytest.raises(ValueError, match="mixes shapes"):
        draw_sample(cases, 1, "s")


def test_sample_suite_records_provenance_and_hash() -> None:
    cases = _cases({"lookup": (6, 2), "peer_relative": (4, 1)})
    suite = {"name": "xbrl_v2_dev", "version": "1", "split": "dev",
             "content_hash": "sha256:src", "cases": cases}
    out = sample_suite(suite, 5, "s")
    assert out["name"] == "xbrl_v2_dev_sample" and out["split"] == "dev"
    assert out["n_cases"] == 5 and out["content_hash"] == content_hash(out["cases"])
    meta = out["sample"]
    # 5 over 6/4 intents: exact 3.0, 2.0.
    assert meta["by_shape"] == {"lookup": 3, "peer_relative": 2}
    assert (meta["single_filer"], meta["cross_filer"]) == (3, 2)
    assert meta["source_content_hash"] == "sha256:src" and meta["seed"] == "s"
