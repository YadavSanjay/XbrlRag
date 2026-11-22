"""C10: suite files are canonical, hashed and diffable; the manifest counts them; the intent
list rebuilds the phrased cases.

Oracle: requirements 14-16 of docs/specs/v2-suite-build.md; hashes and JSON restated with
``hashlib`` and ``json`` in conftest; candidate counts worked out by hand.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalSuite
from src.eval.core.run_columns import slice_fields_from_tags
from src.eval.suites.intents import Intent, build_phrased_cases
from src.eval.suites.v2_suite import (
    build_v2_suites,
    canonical_json,
    content_hash,
    write_v2_suites,
)

SALT = "v2-test"
INTENT_FIELDS = {"kind", "tickers", "fiscal_years", "metrics", "dimension", "operation",
                 "universe", "n", "filing_year"}


def _build(kit, *, inv=None, uni=None):
    docs = kit.companyfacts_docs()
    return build_v2_suites(kit.inventory() if inv is None else inv,
                           kit.universes() if uni is None else uni, kit.sec(docs),
                           kit.store(docs), kit.phrasebook, kit.lexicon,
                           quotas={s: 100 for s in kit.SHAPES}, per_intent=1, share=0.5,
                           salt=SALT)


def _dump(doc):
    return (json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
            + "\n").encode("utf-8")


@pytest.mark.criterion("C10")
def test_canonical_json_and_hash():
    # oracle: requirement 15 — sorted keys, no whitespace, non-ASCII kept, NaN refused;
    # sha256 of the UTF-8 text
    import hashlib

    obj = {"b": 1, "a": [1.5, "é"]}
    assert canonical_json(obj) == '{"a":[1.5,"é"],"b":1}'
    digest = hashlib.sha256('{"a":[1.5,"é"],"b":1}'.encode()).hexdigest()
    assert content_hash(obj) == "sha256:" + digest
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


@pytest.mark.criterion("C10")
def test_suites_are_sorted_and_hashed(kit):
    # oracle: requirement 15 — the suite document's fields and the hash of its cases
    build = _build(kit)
    for split in ("dev", "validation"):
        doc = build[split]
        assert set(doc) == {"name", "version", "split", "n_cases", "content_hash", "cases"}
        assert doc["name"] == f"xbrl_v2_{split}" and doc["version"] == "1"
        assert doc["split"] == split
        assert doc["n_cases"] == len(doc["cases"])
        ids = [c["id"] for c in doc["cases"]]
        assert ids == sorted(ids)
        assert doc["content_hash"] == kit.sha256_hash(doc["cases"])
    assert build["dev"]["n_cases"] + build["validation"]["n_cases"] > 0


@pytest.mark.criterion("C10")
def test_manifest_counts(kit):
    # oracle: counts taken from the suites' tags; candidate counts by hand from the
    # inventory (lookup 16; ratio: net margin for AAA x3, BBB x2, CCC x1 = 6; yoy: AAA 3
    # metrics x 2 + BBB 2 = 8; difference: AAA 3 x 3 pairs + BBB 2 = 11; cagr: AAA 3;
    # trend: AAA 3; point_in_time: AAA 3 x 3 + BBB 2 = 11; each screen op 18;
    # peer_relative: covered 9 + 4 + 2 + 1, peers 9 + 4 = 29)
    build = _build(kit)
    m = build["manifest"]
    assert m["content_hashes"] == {s: build[s]["content_hash"] for s in ("dev", "validation")}
    for split in ("dev", "validation"):
        tags = [slice_fields_from_tags(c["tags"]) for c in build[split]["cases"]]
        shapes = Counter(c["tags"][6].partition(":")[2] for c in build[split]["cases"])
        assert m["cases"][split]["total"] == len(tags)
        assert m["cases"][split]["by_shape"] == dict(shapes)
        assert m["cases"][split]["by_key_status"] == dict(Counter(t["key_status"] for t in tags))
    assert m["intents"]["candidates"] == {
        "lookup": 16, "compute.ratio": 6, "compute.yoy_growth": 8, "compute.difference": 11,
        "compute.cagr": 3, "trend": 3, "point_in_time": 11, "screen.top": 18,
        "screen.bottom": 18, "screen.sum": 18, "screen.median": 18, "peer_relative": 29}
    assert m["intents"]["quota"] == {s: 100 for s in kit.SHAPES}
    n_cases = build["dev"]["n_cases"] + build["validation"]["n_cases"]
    assert sum(m["intents"]["selected"].values()) == n_cases  # per_intent = 1
    assert m["disagreements"] == []
    assert m["phrasing"] == {"per_intent": 1, "share": 0.5, "salt": SALT}
    assert m["intents_hash"] == kit.sha256_hash(build["intents"])


@pytest.mark.criterion("C10")
def test_intent_list_rebuilds_the_phrased_cases(kit):
    # oracle: requirement 15 — canonical intent objects sorted by id, one per intent tag;
    # eval-intents' build_phrased_cases over them reproduces every case id
    build = _build(kit)
    objs = build["intents"]
    assert objs and all(set(o) == INTENT_FIELDS for o in objs)
    intents = [Intent(o["kind"], tickers=o["tickers"], fiscal_years=o["fiscal_years"],
                      metrics=o["metrics"], dimension=o["dimension"], operation=o["operation"],
                      universe=o["universe"], n=o["n"], filing_year=o["filing_year"])
               for o in objs]
    ids = [kit.intent_id(i) for i in intents]
    assert ids == sorted(ids)
    case_ids = {c["id"] for s in ("dev", "validation") for c in build[s]["cases"]}
    tagged = {slice_fields_from_tags(c["tags"])["intent_id"]
              for s in ("dev", "validation") for c in build[s]["cases"]}
    assert set(ids) == tagged
    p = build["manifest"]["phrasing"]
    rebuilt = build_phrased_cases(intents, kit.phrasebook, kit.lexicon,
                                  per_intent=p["per_intent"], share=p["share"], salt=p["salt"])
    assert {c.case_id for c in rebuilt} == case_ids


@pytest.mark.criterion("C10")
def test_written_files(kit, tmp_path):
    # oracle: requirement 16 — sorted, indented JSON plus a newline, UTF-8, LF line ends;
    # EvalSuite.load reads the cases back
    build = _build(kit)
    paths = write_v2_suites(build, tmp_path)
    assert set(paths) == {"dev", "validation", "manifest", "intents"}
    names = {"dev": "xbrl_v2_dev.json", "validation": "xbrl_v2_validation.json",
             "manifest": "xbrl_v2_manifest.json", "intents": "intents.json"}
    for key, name in names.items():
        assert paths[key] == tmp_path / name
        raw = paths[key].read_bytes()
        assert b"\r\n" not in raw
        assert raw == _dump(build[key])
    for split in ("dev", "validation"):
        suite = EvalSuite.load(str(paths[split]))
        assert [c.id for c in suite.cases] == [c["id"] for c in build[split]["cases"]]


@pytest.mark.criterion("C10")
@pytest.mark.metamorphic
@given(seed=st.integers(min_value=0, max_value=3))
def test_builds_are_byte_identical(kit, tmp_path_factory, seed):
    # oracle: requirement 15 — nothing depends on input order, the clock or the process
    base_dir = tmp_path_factory.mktemp("base")
    other_dir = tmp_path_factory.mktemp("other")
    base = write_v2_suites(_build(kit), base_dir)
    inv = kit.inventory()
    uni = kit.universes()
    if seed % 2:
        inv = {t: {"cik": e["cik"], "years": dict(reversed(list(e["years"].items())))}
               for t, e in reversed(list(inv.items()))}
    if seed >= 2:
        uni = {u: list(reversed(m)) for u, m in reversed(list(uni.items()))}
    other = write_v2_suites(_build(kit, inv=inv, uni=uni), other_dir)
    for key in ("dev", "validation", "manifest", "intents"):
        assert base[key].read_bytes() == other[key].read_bytes()
    assert base["dev"].read_bytes()
