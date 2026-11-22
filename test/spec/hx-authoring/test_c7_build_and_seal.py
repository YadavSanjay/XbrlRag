"""C7: ``build_hx_suite``, sealed with set B's ``canonical_bytes`` and ``sha256_hex``.

Oracle: requirements 14 and 15 of docs/specs/hx-authoring.md. Every expected case dict is
written out by hand from the conftest builders; the parsed amounts follow set B's
``parse_amount`` rule ("300" in millions = 300e6, "10%" = 0.1).
"""

from __future__ import annotations

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.set_b.core import canonical_bytes, sha256_hex
from src.eval.set_b.hx import build_hx_suite

M = 1e6
UNIVERSE = ["DEVA", "DEVB", "DEVC", "DEVD"]
EXCL = [{"ticker": "DEVD", "reason": "not_tagged"}]


def _res(cid, kind, classes, status="verified"):
    names = [f"rows[{i}]" for i in range(len(classes))]
    return {"id": cid, "kind": kind, "status": status,
            "items": [{"name": n, "status": status, "classes": [c]}
                      for n, c in zip(names, classes, strict=True)],
            "classes": classes}


def _common(cid, kind, subtype=None):
    tags = ["split:external", kind] + ([subtype] if subtype else [])
    return {"id": f"hx-{cid}", "question": f"Synthetic {kind} question {cid}?",
            "suite": "heldout_x_v1", "origin": "handwritten:h_x", "tags": tags}


def _ranking_fields():
    return {"kind": "screen", "fy": 2025, "unit": "USD", "universe": UNIVERSE,
            "expected_exclusions": EXCL,
            "expected_rows": [{"entity": "DEVA", "value": 300 * M},
                              {"entity": "DEVB", "value": 200 * M}],
            "order_sensitive": True, "top_n": 2, "expected_fact_ids": [1, 2],
            "fact_id_classes": [[1, 11], [2]], "rel_tol": 1e-4}


def _expected(kit):
    """(authoring case, resolution, expected built case) for every kind and subtype."""
    out = []

    case = kit.cross_filer("a01", "screen_top")
    out.append((case, _res("a01", "cross_filer", [[1, 11], [2]]),
                {**_common("a01", "cross_filer", "screen_top"), **_ranking_fields()}))

    case = kit.cross_filer("a02", "screen_bottom")
    out.append((case, _res("a02", "cross_filer", [[3], [2]]),
                {**_common("a02", "cross_filer", "screen_bottom"), "kind": "screen",
                 "fy": 2025, "unit": "USD", "universe": UNIVERSE, "expected_exclusions": EXCL,
                 "expected_rows": [{"entity": "DEVC", "value": 100 * M},
                                   {"entity": "DEVB", "value": 200 * M}],
                 "order_sensitive": True, "top_n": 2, "expected_fact_ids": [3, 2],
                 "fact_id_classes": [[3], [2]], "rel_tol": 1e-4}))

    case = kit.cross_filer("a03", "count")
    out.append((case, _res("a03", "cross_filer", [[1, 11], [2]]),
                {**_common("a03", "cross_filer", "count"), "kind": "compute", "fy": 2025,
                 "universe": UNIVERSE, "expected_exclusions": EXCL, "expected_value": 2.0,
                 "unit": "pure", "expected_fact_ids": [1, 2],
                 "fact_id_classes": [[1, 11], [2]], "rel_tol": 0.0}))

    case = kit.cross_filer("a04", "sum")
    out.append((case, _res("a04", "cross_filer", [[1, 11], [2]]),
                {**_common("a04", "cross_filer", "sum"), "kind": "compute", "fy": 2025,
                 "universe": UNIVERSE, "expected_exclusions": EXCL, "expected_value": 500 * M,
                 "unit": "USD", "expected_fact_ids": [1, 2],
                 "fact_id_classes": [[1, 11], [2]], "rel_tol": 1e-2}))

    case = kit.cross_filer("a05", "median")
    out.append((case, _res("a05", "cross_filer", [[1], [2], [3]]),
                {**_common("a05", "cross_filer", "median"), "kind": "compute", "fy": 2025,
                 "universe": UNIVERSE, "expected_exclusions": EXCL, "expected_value": 200 * M,
                 "unit": "USD", "expected_fact_ids": [1, 2, 3],
                 "fact_id_classes": [[1], [2], [3]], "rel_tol": 1e-2}))

    case = kit.cross_filer("a06", "peer_relative")
    out.append((case, _res("a06", "cross_filer", [[1], [2], [3]]),
                {**_common("a06", "cross_filer", "peer_relative"), "kind": "compute",
                 "fy": 2025, "universe": UNIVERSE, "expected_exclusions": EXCL,
                 "expected_value": 2.0, "unit": "pure", "ticker": "DEVB",
                 "expected_fact_ids": [1, 2, 3], "fact_id_classes": [[1], [2], [3]],
                 "rel_tol": 0.0}))

    case = kit.cross_period("b01", "trend")
    out.append((case, _res("b01", "cross_period", [[31], [32], [33]]),
                {**_common("b01", "cross_period", "trend"), "kind": "breakdown",
                 "ticker": "DEVE", "fy": 2025,
                 "expected_rows": [{"entity": "FY2023", "value": 100 * M},
                                   {"entity": "FY2024", "value": 110 * M},
                                   {"entity": "FY2025", "value": 121 * M}],
                 "order_sensitive": False, "rel_tol": 1e-4,
                 "expected_fact_ids": [31, 32, 33], "fact_id_classes": [[31], [32], [33]]}))

    case = kit.cross_period("b02", "cagr")
    out.append((case, _res("b02", "cross_period", [[31], [33]]),
                {**_common("b02", "cross_period", "cagr"), "kind": "compute",
                 "ticker": "DEVE", "fy": 2025, "expected_value": 0.1,
                 "alt_values": [0.1 * 100], "unit": "pure", "rel_tol": 1e-2,
                 "expected_fact_ids": [31, 33], "fact_id_classes": [[31], [33]]}))

    case = kit.cross_period("b03", "point_in_time")
    out.append((case, _res("b03", "cross_period", [[41], [42]]),
                {**_common("b03", "cross_period", "point_in_time"), "kind": "breakdown",
                 "ticker": "DEVE", "fy": 2023,
                 "expected_rows": [{"entity": "as_originally_reported", "value": 100 * M},
                                   {"entity": "latest", "value": 104 * M}],
                 "order_sensitive": False, "rel_tol": 1e-4,
                 "accessions": {"as_originally_reported": kit.ACC_ORIG,
                                "latest": kit.ACC_LATEST},
                 "expected_fact_ids": [41, 42], "fact_id_classes": [[41], [42]]}))

    case = kit.compound("c01")
    ranking = _ranking_fields()
    dependent = {"kind": "breakdown", "ticker": "DEVA", "fy": 2025,
                 "expected_rows": [{"entity": "FY2024", "value": 250 * M},
                                   {"entity": "FY2025", "value": 300 * M}],
                 "order_sensitive": False, "rel_tol": 1e-4,
                 "expected_fact_ids": [34, 1], "fact_id_classes": [[34], [1, 11]]}
    out.append((case, _res("c01", "compound", [[1, 11], [2], [34], [1, 11]]),
                {**_common("c01", "compound"), **ranking,
                 "parts": [{"part": "ranking", **ranking},
                           {"part": "dependent", **dependent}]}))

    case = kit.honesty("d01", "ambiguous")
    out.append((case, _res("d01", "honesty", [], status="skipped"),
                {**_common("d01", "honesty", "ambiguous"), "kind": "narrative",
                 "require_citation": False, "tickers": ["DEVA"],
                 "assumptions": ["the synthetic fiscal year, not the calendar year"]}))

    case = kit.honesty("d02", "false_premise")
    out.append((case, _res("d02", "honesty", [], status="skipped"),
                {**_common("d02", "honesty", "false_premise"), "kind": "narrative",
                 "require_citation": False, "tickers": ["DEVB"], "fy": 2024,
                 "expected_text": "The synthetic metric rose, it did not fall."}))

    case = kit.honesty("d03", "decline", tickers=["DEVC", "DEVA"])
    out.append((case, _res("d03", "honesty", [], status="skipped"),
                {**_common("d03", "honesty", "decline"), "kind": "narrative",
                 "require_citation": False, "tickers": ["DEVA", "DEVC"],
                 "decline_reason": "not_tagged"}))

    case = kit.text("e01", note="never copied")
    out.append((case, _res("e01", "text", [], status="skipped"),
                {**_common("e01", "text"), "kind": "narrative", "ticker": "DEVA", "fy": 2025,
                 "expected_text": "Synthetic reference answer about a made-up filer.",
                 "key_points": ["first synthetic point", "second synthetic point"],
                 "passages": [{"accession": kit.ACC_TEXT,
                               "quote": "a synthetic quoted span"}]}))
    return out


@pytest.mark.criterion("C7")
def test_exact_case_dict_for_every_kind_and_subtype(kit):
    # oracle: requirement 14 — each expected dict written by hand above.
    triples = _expected(kit)
    suite = build_hx_suite([t[0] for t in triples], [t[1] for t in triples])
    assert set(suite) == {"name", "version", "n_cases", "cases"}
    assert suite["name"] == "heldout_x"
    assert suite["version"] == "v1"
    assert suite["n_cases"] == len(triples)
    want = sorted((t[2] for t in triples), key=lambda c: c["id"])
    assert [c["id"] for c in suite["cases"]] == [c["id"] for c in want]
    for got, exp in zip(suite["cases"], want, strict=True):
        assert got == exp, exp["id"]


@pytest.mark.criterion("C7")
def test_every_built_case_loads_as_an_evalcase(kit):
    # oracle: requirement 14 and the EvalCase kinds in src/eval/core/case.py — every built
    # kind is one EvalCase accepts, and from_dict keeps id, kind and tags.
    triples = _expected(kit)
    suite = build_hx_suite([t[0] for t in triples], [t[1] for t in triples])
    assert suite["cases"]
    for d in suite["cases"]:
        ec = EvalCase.from_dict(d)
        assert ec.id == d["id"]
        assert ec.kind == d["kind"]
        assert ec.tags[0] == "split:external"


@pytest.mark.criterion("C7")
def test_unverified_raises_with_ids_only_or_is_excluded(kit):
    # oracle: requirement 14 — a non-verified, non-skipped case raises naming its id and no
    # case text; with allow_unverified it is left out and listed (authoring id).
    good = kit.cross_filer("g1")
    bad = kit.cross_filer("g2")
    bad["question"] = "Q-zqxjleakprobe"
    text_case = kit.text("g3")
    res = [_res("g1", "cross_filer", [[1, 11], [2]]),
           _res("g2", "cross_filer", [[1, 11], []], status="not_found"),
           _res("g3", "text", [], status="skipped")]
    with pytest.raises(ValueError) as err:
        build_hx_suite([good, bad, text_case], res)
    assert "g2" in str(err.value)
    assert "zqxjleakprobe" not in str(err.value)
    assert "g1" not in str(err.value)
    suite = build_hx_suite([good, bad, text_case], res, allow_unverified=True)
    assert [c["id"] for c in suite["cases"]] == ["hx-g1", "hx-g3"]
    assert suite["n_cases"] == 2
    assert suite["excluded"] == ["g2"]
    # a case with no resolution is unverified too
    with pytest.raises(ValueError):
        build_hx_suite([good, kit.text("g4")], res)


@pytest.mark.criterion("C7")
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_shuffled_input_gives_identical_bytes_and_hash(kit, seed):
    # oracle: requirements 14-15 — cases sorted by id and set B's canonical JSON, so the
    # authoring order never changes the sealed bytes; the content is pinned above.
    triples = _expected(kit)
    base = build_hx_suite([t[0] for t in triples], [t[1] for t in triples])
    assert base["n_cases"] == len(triples)
    shuffled = list(triples)
    random.Random(seed).shuffle(shuffled)
    res = [t[1] for t in shuffled]
    random.Random(seed + 1).shuffle(res)
    again = build_hx_suite([t[0] for t in shuffled], res)
    assert canonical_bytes(again) == canonical_bytes(base)
    assert sha256_hex(canonical_bytes(again)) == sha256_hex(canonical_bytes(base))
