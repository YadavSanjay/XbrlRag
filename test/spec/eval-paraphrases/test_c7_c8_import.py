"""Spec tests C7-C8 for eval-paraphrases: importing reply files, and duplicate removal
(docs/specs/eval-paraphrases.md)."""

from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.paraphrases import ParaphraseSource, import_replies

ENTRY_KEYS = {"paraphrase_id", "case_id", "intent_id", "persona", "paraphrase", "source_sha256",
              "intent_axis", "template_axis", "persona_axis", "split"}
REVIEW_TEXT = {
    "int-aaaaaaaaaaaa-11111111:conversational_retail":
        "Hey, how much did Apple sell in calendar 2024?",
    "int-bbbbbbbbbbbb-33333333:terse_pm": "Microsoft revenue trend, last year vs fiscal 2022",
    "int-cccccccccccc-44444444:conversational_retail":
        "Which three of the S&P 100 sold the most in fiscal 2024?",
    "int-cccccccccccc-44444444:typo_prone": "Top 3 big tech companes by revenu in 2024",
}


def _sources(kit):
    return [ParaphraseSource(**kw) for kw in kit.source_kwargs()]


def _import(kit, paths=None, sources=None, **kw):
    args = dict(prompt_version="v1", share=0.5, salt="s")
    args.update(kw)
    return import_replies(sources if sources is not None else _sources(kit),
                          paths if paths is not None else [kit.reply_a, kit.reply_b], **args)


# --------------------------------------------------------------------------- C7
@pytest.mark.criterion("C7")
def test_c7_fixture_import_point(kit):
    # oracle: the import rules applied by hand to the 16 fixture lines (conftest ACCEPTED,
    # REVIEW, DUPLICATES, FROM_B); file hashes computed with hashlib from the fixture bytes.
    pset = _import(kit)
    sha_a, sha_b = kit.sha256_file(kit.reply_a), kit.sha256_file(kit.reply_b)
    assert pset["schema"] == "paraphrase-set/1"
    assert pset["prompt_version"] == "v1"
    assert pset["sealed"] is False
    assert pset["split"] == {"share": 0.5, "salt": "s"}
    expected_sources = sorted([
        {"file": "reply_a.txt", "sha256": sha_a, "model": "gemini-2.5-pro", "date": "2026-10-20",
         "prompt_version": "v1"},
        {"file": "reply_b.txt", "sha256": sha_b, "model": "mistral-large-2",
         "date": "2026-10-21", "prompt_version": "v1"},
    ], key=lambda s: s["sha256"])
    assert pset["sources"] == expected_sources

    para = pset["paraphrases"]
    assert [p["paraphrase_id"] for p in para] == sorted(kit.accepted)
    for p in para:
        pid = p["paraphrase_id"]
        assert set(p) == ENTRY_KEYS
        assert p["paraphrase"] == kit.accepted[pid]
        assert p["case_id"] + ":" + p["persona"] == pid
        assert p["source_sha256"] == (sha_b if pid in kit.from_b else sha_a)
    msft = next(p for p in para if p["paraphrase_id"] == f"{kit.s3}:sell_side_shorthand")
    assert msft == {
        "paraphrase_id": f"{kit.s3}:sell_side_shorthand", "case_id": kit.s3,
        "intent_id": "int-bbbbbbbbbbbb", "persona": "sell_side_shorthand",
        "paraphrase": "MSFT top line FY22 vs FY24", "source_sha256": sha_a,
        "intent_axis": "validation", "template_axis": "dev",
        "persona_axis": kit.axis("sell_side_shorthand", 0.5, "s"), "split": "validation",
    }

    review = pset["review"]
    assert [r["paraphrase_id"] for r in review] == sorted(kit.review)
    for r in review:
        pid = r["paraphrase_id"]
        assert r == {"paraphrase_id": pid, "case_id": pid.split(":")[0],
                     "persona": pid.split(":")[1], "paraphrase": REVIEW_TEXT[pid],
                     "reasons": kit.review[pid], "source_sha256": sha_a}

    dups = pset["duplicates"]
    assert [d["paraphrase_id"] for d in dups] == sorted(kit.duplicates)
    for d in dups:
        pid = d["paraphrase_id"]
        assert d == {"paraphrase_id": pid, "case_id": pid.split(":")[0],
                     "persona": pid.split(":")[1], "duplicate_of": kit.duplicates[pid][0],
                     "kind": kit.duplicates[pid][1],
                     "source_sha256": sha_b if pid in kit.from_b else sha_a}
    assert isinstance(pset["content_hash"], str) and len(pset["content_hash"]) == 64


@pytest.mark.criterion("C7")
def test_c7_import_errors_point(kit):
    # oracle: the import's ValueError list, one hand-made violation each.
    rec = {"case_id": kit.s1, "persona": "terse_pm", "paraphrase": "Apple net sales FY24"}
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        with pytest.raises(ValueError):
            _import(kit, paths=[])
        with pytest.raises(ValueError):
            _import(kit, sources=_sources(kit) + [ParaphraseSource(**kit.source_kwargs()[0])])
        for share in (-0.1, 1.1, math.nan):
            with pytest.raises(ValueError):
                _import(kit, share=share)
        copy_a = d / "copy_of_a.txt"
        shutil.copyfile(kit.reply_a, copy_a)
        with pytest.raises(ValueError):
            _import(kit, paths=[kit.reply_a, copy_a])
        latin = d / "latin.txt"
        latin.write_bytes(b"# model: m\n# date: 2026-10-20\n# prompt: v1\n"
                          b'{"case_id": "x", "persona": "terse_pm", "paraphrase": "caf\xe9"}\n')
        with pytest.raises(ValueError):
            _import(kit, paths=[latin])
        with pytest.raises(ValueError):
            _import(kit, paths=[kit.write_reply(d, "v2.txt", [rec], prompt="v2")])
        with pytest.raises(ValueError):
            _import(kit, paths=[kit.write_reply(d, "unknown.txt", [{**rec, "case_id": "int-zz"}])])
        with pytest.raises(ValueError):
            _import(kit, paths=[kit.write_reply(d, "twice.txt", [rec, {**rec, "paraphrase":
                                                                     "Apple net sales FY2024"}])])
        with pytest.raises(ValueError):          # S1:terse_pm is already in reply_a
            _import(kit, paths=[kit.reply_a, kit.write_reply(d, "again.txt", [rec])])
        with pytest.raises(ValueError):          # parse_reply rejects a GPT model header
            _import(kit, paths=[kit.write_reply(d, "gpt.txt", [rec], model="gpt-4.1")])
    with pytest.raises(ValueError):
        _import(kit, prompt_version="v2")


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(st.permutations([0, 1]), st.permutations([0, 1, 2, 3]))
def test_c7_order_invariance_property(kit, file_order, source_order):
    # oracle: the set is sorted throughout and keyed by content, so the order of reply files
    # and of sources never changes it.
    files = [kit.reply_a, kit.reply_b]
    srcs = _sources(kit)
    base = _import(kit)
    got = _import(kit, paths=[files[i] for i in file_order],
                  sources=[srcs[i] for i in source_order])
    assert got == base
    assert len(got["paraphrases"]) == len(kit.accepted)


# --------------------------------------------------------------------------- C8
@pytest.mark.criterion("C8")
def test_c8_fixture_duplicates_point(kit):
    # oracle: the dedup rule by hand: S2 sell_side repeats S1 sell_side exactly; S2 typo is
    # "aapl revenue fy24", its own question in other case; S3 buried_ask is S3's question
    # verbatim; S4 terse_pm normalises like S4 sell_side, which has the smaller id.
    pset = _import(kit)
    got = {d["paraphrase_id"]: (d["duplicate_of"], d["kind"]) for d in pset["duplicates"]}
    assert got == kit.duplicates
    kept = {p["paraphrase_id"] for p in pset["paraphrases"]}
    assert f"{kit.s1}:sell_side_shorthand" in kept and f"{kit.s4}:sell_side_shorthand" in kept


@pytest.mark.criterion("C8")
def test_c8_other_intent_not_duplicate_point(kit):
    # oracle: candidates of different intents are never duplicates of each other.
    common = dict(intent_axis="dev", template_axis="dev", company_forms=["AAPL", "Apple"],
                  years=[2024], metric_forms=[["revenue"]])
    srcs = [ParaphraseSource("p1-0001", "What was Apple's revenue in fiscal 2024?",
                             intent_id="int-p1", **common),
            ParaphraseSource("p2-0001", "Apple revenue, fiscal 2024?", intent_id="int-p2",
                             **common)]
    with tempfile.TemporaryDirectory() as tmp:
        path = kit.write_reply(Path(tmp), "r.txt", [
            {"case_id": "p1-0001", "persona": "terse_pm", "paraphrase": "AAPL FY24 revenue"},
            {"case_id": "p2-0001", "persona": "terse_pm", "paraphrase": "AAPL FY24 revenue"},
            {"case_id": "p2-0001", "persona": "typo_prone",
             "paraphrase": "what was apple's REVENUE in fiscal 2024"},
        ])
        pset = import_replies(srcs, [path], prompt_version="v1", share=0.0, salt="s")
    assert [p["paraphrase_id"] for p in pset["paraphrases"]] == [
        "p1-0001:terse_pm", "p2-0001:terse_pm", "p2-0001:typo_prone"]
    assert pset["duplicates"] == []


_SEPS = st.sampled_from([" ", ", ", " - ", "; ", "! ", " ... "])
_PAIRS = st.sets(st.tuples(st.sampled_from(["q-1", "q-2", "q-3"]),
                           st.sampled_from(["sell_side_shorthand", "terse_pm",
                                            "conversational_retail", "typo_prone",
                                            "buried_ask"])), min_size=2, max_size=10)


@pytest.mark.criterion("C8")
@given(_PAIRS, st.data())
def test_c8_variants_collapse_property(kit, pairs, data):
    # oracle: every case/punctuation variant of one paraphrase normalises to the same key, so
    # within one intent only the smallest paraphrase_id is kept and every other one points
    # at it, "exact" iff its text equals the kept text.
    common = dict(intent_id="int-q", intent_axis="dev", template_axis="dev",
                  company_forms=["AAPL", "Apple"], years=[2024], metric_forms=[["revenue"]])
    srcs = [ParaphraseSource(f"q-{i}", f"Question {i}: Apple revenue in fiscal 2024?", **common)
            for i in (1, 2, 3)]
    words = ["aapl", "revenue", "fy24", "please"]
    records = []
    for cid, persona in sorted(pairs):
        flips = data.draw(st.lists(st.booleans(), min_size=4, max_size=4))
        seps = data.draw(st.lists(_SEPS, min_size=3, max_size=3))
        ws = [w.upper() if f else w for w, f in zip(words, flips, strict=True)]
        text = ws[0] + seps[0] + ws[1] + seps[1] + ws[2] + seps[2] + ws[3]
        records.append({"case_id": cid, "persona": persona, "paraphrase": text})
    with tempfile.TemporaryDirectory() as tmp:
        path = kit.write_reply(Path(tmp), "r.txt", records)
        pset = import_replies(srcs, [path], prompt_version="v1", share=0.0, salt="s")
    ids = sorted(f"{r['case_id']}:{r['persona']}" for r in records)
    text_of = {f"{r['case_id']}:{r['persona']}": r["paraphrase"] for r in records}
    assert [p["paraphrase_id"] for p in pset["paraphrases"]] == [ids[0]]
    assert pset["review"] == []
    assert [d["paraphrase_id"] for d in pset["duplicates"]] == ids[1:]
    for d in pset["duplicates"]:
        assert d["duplicate_of"] == ids[0]
        exact = text_of[d["paraphrase_id"]] == text_of[ids[0]]
        assert d["kind"] == ("exact" if exact else "near")
