"""Spec tests C9-C10 for eval-paraphrases: the three-axis split, and the frozen, hashed set
(docs/specs/eval-paraphrases.md)."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.paraphrases import (
    ParaphraseSource,
    canonical_bytes,
    import_replies,
    load_paraphrase_set,
    write_paraphrase_set,
)


def _import(kit, share, salt, paths=None):
    sources = [ParaphraseSource(**kw) for kw in kit.source_kwargs()]
    return import_replies(sources, paths or [kit.reply_a, kit.reply_b], prompt_version="v1",
                          share=share, salt=salt)


def _check_axes(kit, pset, share, salt):
    axes = {kw["case_id"]: (kw["intent_axis"], kw["template_axis"])
            for kw in kit.source_kwargs()}
    for p in pset["paraphrases"]:
        ia, ta = axes[p["case_id"]]
        pa = kit.axis(p["persona"], share, salt)
        assert (p["intent_axis"], p["template_axis"], p["persona_axis"]) == (ia, ta, pa)
        assert p["split"] == ("validation" if "validation" in (ia, ta, pa) else "dev")
        if p["split"] == "dev":
            assert "validation" not in (p["intent_axis"], p["template_axis"], p["persona_axis"])


# --------------------------------------------------------------------------- C9
@pytest.mark.criterion("C9")
def test_c9_split_point(kit):
    # oracle: by hand, at share=0 no persona is validation, so the split is the sources' own
    # axes: S1 and S4 dev, S2 (validation template) and S3 (validation intent) validation;
    # at share=1 every persona is validation. Mid-share persona axes via hashlib.
    zero = _import(kit, 0.0, "salt-1")
    split = {p["paraphrase_id"]: p["split"] for p in zero["paraphrases"]}
    assert split == {
        f"{kit.s1}:buried_ask": "dev", f"{kit.s1}:sell_side_shorthand": "dev",
        f"{kit.s1}:terse_pm": "dev", f"{kit.s1}:typo_prone": "dev",
        f"{kit.s2}:buried_ask": "validation",
        f"{kit.s3}:conversational_retail": "validation",
        f"{kit.s3}:sell_side_shorthand": "validation",
        f"{kit.s4}:sell_side_shorthand": "dev",
    }
    assert {p["persona_axis"] for p in zero["paraphrases"]} == {"dev"}
    one = _import(kit, 1.0, "salt-1")
    assert {p["persona_axis"] for p in one["paraphrases"]} == {"validation"}
    assert {p["split"] for p in one["paraphrases"]} == {"validation"}
    mid = _import(kit, 0.5, "salt-1")
    _check_axes(kit, mid, 0.5, "salt-1")


@pytest.mark.criterion("C9")
@given(st.floats(0.0, 1.0), st.text(max_size=12))
def test_c9_split_property(kit, share, salt):
    # oracle: persona_axis is the eval-intents axis rule keyed by persona (hashlib), split is
    # validation iff any axis is, and a dev paraphrase never has a validation axis.
    pset = _import(kit, share, salt)
    assert len(pset["paraphrases"]) == len(kit.accepted)
    _check_axes(kit, pset, share, salt)
    assert pset["split"] == {"share": share, "salt": salt}


# --------------------------------------------------------------------------- C10
@pytest.mark.criterion("C10")
def test_c10_hash_and_round_trip_point(kit):
    # oracle: content_hash = sha256 of json.dumps(set minus content_hash, sort_keys, indent=2,
    # ensure_ascii=False) + "\n", computed in the test; the file holds the same canonical
    # bytes of the full set.
    assert canonical_bytes({"b": 1, "a": "’"}) == b'{\n  "a": "\xe2\x80\x99",\n  "b": 1\n}\n'
    pset = _import(kit, 0.5, "s")
    assert pset["content_hash"] == kit.oracle_hash(pset)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "set.json"
        assert write_paraphrase_set(pset, out) == pset["content_hash"]
        raw = out.read_bytes()
        assert raw == (json.dumps(pset, sort_keys=True, indent=2, ensure_ascii=False)
                       + "\n").encode("utf-8")
        assert load_paraphrase_set(out) == pset
        tampered = json.loads(raw)
        tampered["paraphrases"][0]["paraphrase"] = "AAPL FY25 revenue"
        bad = Path(tmp) / "tampered.json"
        bad.write_text(json.dumps(tampered), encoding="utf-8")
        with pytest.raises(ValueError):
            load_paraphrase_set(bad)
        no_hash = {k: v for k, v in pset.items() if k != "content_hash"}
        (Path(tmp) / "nohash.json").write_text(json.dumps(no_hash), encoding="utf-8")
        with pytest.raises(ValueError):
            load_paraphrase_set(Path(tmp) / "nohash.json")
        with pytest.raises(ValueError):
            write_paraphrase_set(no_hash, Path(tmp) / "x.json")
        with pytest.raises(ValueError):
            write_paraphrase_set({**pset, "content_hash": "0" * 64}, Path(tmp) / "y.json")
        with pytest.raises(ValueError):
            write_paraphrase_set({**pset, "sealed": True}, Path(tmp) / "z.json")


@pytest.mark.criterion("C10")
@pytest.mark.metamorphic
@given(st.sampled_from(["\n", "\n\n", "```\n", "   \n"]))
def test_c10_reimport_metamorphic(kit, extra):
    # oracle: the hash depends only on content: a re-import gives the same hash; extra
    # skippable lines in a reply change its file hash (so sources and content_hash) but no
    # paraphrase id or text.
    base = _import(kit, 0.5, "s")
    assert _import(kit, 0.5, "s")["content_hash"] == base["content_hash"]
    with tempfile.TemporaryDirectory() as tmp:
        padded = Path(tmp) / "reply_a.txt"
        padded.write_bytes(kit.reply_a.read_bytes() + extra.encode("utf-8"))
        got = _import(kit, 0.5, "s", paths=[padded, kit.reply_b])
    new_sha = hashlib.sha256(kit.reply_a.read_bytes() + extra.encode("utf-8")).hexdigest()
    assert new_sha in {s["sha256"] for s in got["sources"]}
    assert got["content_hash"] != base["content_hash"]
    assert got["content_hash"] == kit.oracle_hash(got)
    assert [(p["paraphrase_id"], p["paraphrase"]) for p in got["paraphrases"]] == \
        [(p["paraphrase_id"], p["paraphrase"]) for p in base["paraphrases"]]
