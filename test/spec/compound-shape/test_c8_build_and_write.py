"""C8: ``build_compound_suites`` and ``write_compound_suites`` — names, files, manifest,
quotas met in candidate order with drops counted by reason, the two-axis split, and
determinism.

Oracle: requirements 3 and 7 of docs/specs/compound-shape.md applied by hand to the conftest
fixture; the candidate order (one universe: sha1 of the intent id) and the eval-intents
split formula restated with ``hashlib``.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.suites.v2_suite import write_compound_suites

RATIOS = ("operating_margin", "net_margin", "rd_intensity", "current_ratio")
METRICS = ("Revenue", "OperatingIncome", "NetIncome")
MANIFEST_KEYS = {"content_hashes", "cases", "intents", "disagreements", "phrasing",
                 "intents_hash"}
SUITE_KEYS = {"name", "version", "split", "n_cases", "content_hash", "cases"}


def _candidates(kit):
    """Requirement 3 by hand: (kwargs, drop reason or None) per compound shape."""
    rbr = []
    for y in (2023, 2024):
        for a in METRICS:
            for r in RATIOS:
                if r in ("rd_intensity", "current_ratio"):
                    reason = "dependent:metric_not_tagged"  # no filer tags their inputs
                elif r == "net_margin" and y == 2024:
                    reason = "dependent_tie"  # DEVA 0.12 = DEVB 0.12 in every top 3
                else:
                    reason = None
                rbr.append((kit.rbr(y, a, r), reason))
    lg = [(kit.lg(2024, a, b), None) for a in METRICS for b in METRICS if b != a]
    return {kit.RBR: rbr, kit.LG: lg}


def _ordered(kit, items):
    # one universe, so the round-robin is the order of sha1(intent id)
    return sorted(items, key=lambda it: hashlib.sha1(
        kit.oracle_id(it[0]).encode("utf-8")).hexdigest())


@pytest.mark.criterion("C8")
def test_suites_and_manifest(kit):
    # oracle: requirement 7 and the hand counts of _candidates: 24 rank_by_ratio candidates
    # (9 kept, 12 + 3 dropped), 6 leader_growth (all kept); per_intent 1, share 0: all dev
    result = kit.build()
    for split in ("dev", "validation"):
        doc = result[split]
        assert set(doc) == SUITE_KEYS
        assert doc["name"] == f"xbrl_v2_{split}_compound"
        assert doc["version"] == "1" and doc["split"] == split
        assert doc["n_cases"] == len(doc["cases"])
        ids = [c["id"] for c in doc["cases"]]
        assert ids == sorted(ids)
        assert doc["content_hash"] == kit.sha256_hash(doc["cases"])
    assert result["dev"]["n_cases"] == 15 and result["validation"]["n_cases"] == 0
    m = result["manifest"]
    assert set(m) == MANIFEST_KEYS
    assert m["intents"] == {
        "quota": {kit.RBR: 100, kit.LG: 100},
        "selected": {kit.RBR: 9, kit.LG: 6},
        "candidates": {kit.RBR: 24, kit.LG: 6},
        "dropped": {"dependent:metric_not_tagged": 12, "dependent_tie": 3},
        "dropped_by_shape": {kit.RBR: {"dependent:metric_not_tagged": 12,
                                       "dependent_tie": 3}},
    }
    assert m["cases"]["dev"] == {"total": 15, "by_shape": {kit.LG: 6, kit.RBR: 9},
                                 "by_key_status": {"verified": 15}}
    assert m["content_hashes"] == {s: result[s]["content_hash"] for s in ("dev", "validation")}
    assert m["disagreements"] == []
    assert m["phrasing"] == {"per_intent": 1, "share": 0.0, "salt": kit.SALT}
    assert m["intents_hash"] == kit.sha256_hash(result["intents"])
    objs = result["intents"]
    assert all(set(o) == set(kit.KEYS_INTENT) for o in objs)
    assert [kit.oracle_id(o) for o in objs] == sorted(kit.oracle_id(o) for o in objs)


@pytest.mark.criterion("C8")
@settings(max_examples=10)
@given(q_rbr=st.integers(min_value=0, max_value=11), q_lg=st.integers(min_value=0, max_value=7))
def test_quotas_are_met_in_candidate_order(kit, q_rbr, q_lg):
    # oracle: requirement 3 — each shape's candidates are keyed in order until its quota of
    # kept intents is met; the drops met on the way are counted by reason
    result = kit.build(quotas={kit.RBR: q_rbr, kit.LG: q_lg})
    selected_ids = {kit.oracle_id(o) for o in result["intents"]}
    for shape, quota in ((kit.RBR, q_rbr), (kit.LG, q_lg)):
        kept, drops = [], {}
        for kw, reason in _ordered(kit, _candidates(kit)[shape]):
            if len(kept) >= quota:
                break
            if reason is None:
                kept.append(kit.oracle_id(kw))
            else:
                drops[reason] = drops.get(reason, 0) + 1
        assert result["manifest"]["intents"]["selected"][shape] == len(kept)
        assert kit.dropped(result, shape) == drops
        assert set(kept) <= selected_ids
    assert len(selected_ids) == min(q_rbr, 9) + min(q_lg, 6)


@pytest.mark.criterion("C8")
@settings(max_examples=8)
@given(share=st.floats(min_value=0.0, max_value=1.0), salt=st.text(min_size=1, max_size=8))
def test_split_follows_both_axes(kit, share, salt):
    # oracle: eval-intents split rule — validation iff the intent's or the template's axis
    # hash falls under the share; per_intent 2 keeps two phrasings of every intent
    result = kit.build(share=share, salt=salt, per_intent=2)
    pairs = kit.records(result)
    assert len(pairs) == 30
    for split, rec in pairs:
        iid = rec["tags"][1].partition(":")[2]
        tid = rec["tags"][3].partition(":")[2]
        want = kit.oracle_axis(iid, share, salt) or kit.oracle_axis(tid, share, salt)
        assert split == ("validation" if want else "dev")
        assert rec["tags"][0] == f"split:{split}"


def _dump(doc):
    return (json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
            + "\n").encode("utf-8")


@pytest.mark.criterion("C8")
def test_written_files(kit, tmp_path):
    # oracle: requirement 7 — four files with the compound names, in the main writer's
    # format (sorted, indented JSON plus a newline, UTF-8, LF)
    result = kit.build(share=0.5)
    paths = write_compound_suites(result, tmp_path)
    names = {"dev": "xbrl_v2_dev_compound.json",
             "validation": "xbrl_v2_validation_compound.json",
             "manifest": "xbrl_v2_compound_manifest.json", "intents": "intents_compound.json"}
    assert set(paths) == set(names)
    for key, name in names.items():
        assert paths[key] == tmp_path / name
        raw = paths[key].read_bytes()
        assert b"\r\n" not in raw
        assert raw == _dump(result[key])
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(names.values())


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
@given(flip_inventory=st.booleans(), flip_members=st.booleans())
@settings(max_examples=4)
def test_builds_are_deterministic(kit, tmp_path_factory, flip_inventory, flip_members):
    # oracle: requirement 7 — nothing depends on input order, the clock or the process
    base = write_compound_suites(kit.build(share=0.5), tmp_path_factory.mktemp("a"))
    inv = kit.inventory()
    uni = kit.universes()
    if flip_inventory:
        inv = {t: {"cik": e["cik"], "years": dict(reversed(list(e["years"].items())))}
               for t, e in reversed(list(inv.items()))}
    if flip_members:
        uni = {u: list(reversed(m)) for u, m in uni.items()}
    other = write_compound_suites(kit.build(inv=inv, uni=uni, share=0.5),
                                  tmp_path_factory.mktemp("b"))
    for key in ("dev", "validation", "manifest", "intents"):
        assert base[key].read_bytes() == other[key].read_bytes()


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
def test_adding_a_filer_never_changes_a_kept_intents_cases(kit):
    # oracle: requirement 7 — DEVF joins the inventory in a new universe "extra" (DEVA,
    # DEVB, DEVC, DEVF): new candidates appear, but every intent kept in both builds has
    # the same case records
    base = kit.build(share=0.5)
    inv = kit.inventory()
    inv["DEVF"] = kit.devf_entry()
    uni = {**kit.universes(), "extra": ["DEVA", "DEVB", "DEVC", "DEVF"]}
    grown = kit.build(inv=inv, uni=uni, share=0.5)
    assert len(grown["intents"]) > len(base["intents"])
    common = {kit.oracle_id(o) for o in base["intents"]} & {
        kit.oracle_id(o) for o in grown["intents"]}
    assert len(common) == 15
    for iid in common:
        tag = f"intent:{iid}"
        before = sorted((r["id"], r) for _s, r in kit.records(base) if tag in r["tags"])
        after = sorted((r["id"], r) for _s, r in kit.records(grown) if tag in r["tags"])
        assert [r for _i, r in before] == [r for _i, r in after]
