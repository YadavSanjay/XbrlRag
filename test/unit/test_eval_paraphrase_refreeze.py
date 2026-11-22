"""Unit tests for the paraphrase re-freeze helpers: carry-over and merge (synthetic sets only).

The content hashes expected here are computed independently in this file (SHA-256 of the
sorted-key, indent-2 JSON plus a newline, the spec's canonical form), not by the library.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from src.eval.suites.paraphrases import (
    ParaphraseSource,
    carry_over_set,
    import_replies,
    load_paraphrase_set,
    merge_paraphrase_sets,
    write_paraphrase_set,
)

SHA_A, SHA_B, SHA_C = "a" * 64, "b" * 64, "c" * 64


def _hash(pset: dict) -> str:
    body = {k: v for k, v in pset.items() if k != "content_hash"}
    text = json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sealed(pset: dict) -> dict:
    return {**pset, "content_hash": _hash(pset)}


def _kept(case_id: str, persona: str, text: str, sha: str, intent: str = "i1") -> dict:
    return {"paraphrase_id": f"{case_id}:{persona}", "case_id": case_id, "intent_id": intent,
            "persona": persona, "paraphrase": text, "source_sha256": sha,
            "intent_axis": "dev", "template_axis": "dev", "persona_axis": "dev",
            "split": "dev"}


def _review(case_id: str, persona: str, sha: str) -> dict:
    return {"paraphrase_id": f"{case_id}:{persona}", "case_id": case_id, "persona": persona,
            "paraphrase": "some drifted text", "reasons": ["semantic_drift"],
            "source_sha256": sha}


def _dup(case_id: str, persona: str, of: str, sha: str) -> dict:
    return {"paraphrase_id": f"{case_id}:{persona}", "case_id": case_id, "persona": persona,
            "duplicate_of": of, "kind": "exact", "source_sha256": sha}


def _prov(sha: str, name: str) -> dict:
    return {"file": name, "sha256": sha, "model": "gemini-2.5-pro", "date": "2026-11-01",
            "prompt_version": "v1"}


def _set(paraphrases=(), review=(), duplicates=(), sources=(), **over) -> dict:
    pset = {"schema": "paraphrase-set/1", "prompt_version": "v1", "sealed": False,
            "split": {"share": 0.15, "salt": "s"}, "sources": list(sources),
            "paraphrases": list(paraphrases), "review": list(review),
            "duplicates": list(duplicates)}
    pset.update(over)
    return _sealed(pset)


def _suite(questions: dict[str, str]) -> list[dict]:
    return [{"id": cid, "question": q, "key": {"x": 1}} for cid, q in questions.items()]


# --------------------------------------------------------------------------- carry-over
OLD_SUITE = _suite({"c1": "q one", "c2": "q two", "c3": "q three", "c4": "q four"})
# c2 reworded in place, c3 removed, c4 unchanged but with no kept paraphrase, c5 added.
NEW_SUITE = _suite({"c1": "q one", "c2": "q two, reworded", "c4": "q four", "c5": "q five"})
OLD_SET = _set(
    paraphrases=[_kept("c1", "terse_pm", "c1 terse", SHA_A),
                 _kept("c1", "typo_prone", "c1 typo", SHA_A),
                 _kept("c2", "terse_pm", "c2 terse", SHA_B),
                 _kept("c3", "terse_pm", "c3 terse", SHA_B)],
    review=[_review("c1", "buried_ask", SHA_A), _review("c4", "terse_pm", SHA_C)],
    duplicates=[_dup("c3", "typo_prone", "c3", SHA_B)],
    sources=[_prov(SHA_A, "a.txt"), _prov(SHA_B, "b.txt"), _prov(SHA_C, "c.txt")])


def test_carry_over_ids_are_added_reworded_and_unkept():
    ids, _ = carry_over_set(OLD_SUITE, NEW_SUITE, OLD_SET)
    assert ids == ["c2", "c4", "c5"]


def test_carry_over_keeps_only_unchanged_cases_byte_for_byte():
    original = copy.deepcopy(OLD_SET)
    _, carried = carry_over_set(OLD_SUITE, NEW_SUITE, OLD_SET)
    assert OLD_SET == original  # the input is not mutated
    dumps = [json.dumps(r, sort_keys=True) for r in OLD_SET["paraphrases"][:2]]
    assert [json.dumps(r, sort_keys=True) for r in carried["paraphrases"]] == dumps
    assert carried["review"] == [OLD_SET["review"][0]]  # c4's review goes with c4
    assert carried["duplicates"] == []  # c3 removed
    assert carried["sources"] == [_prov(SHA_A, "a.txt")]  # only the cited reply file
    assert {k: carried[k] for k in ("schema", "prompt_version", "sealed", "split")} == \
        {k: OLD_SET[k] for k in ("schema", "prompt_version", "sealed", "split")}
    assert carried["content_hash"] == _hash(carried)


def test_carried_set_round_trips_through_write_and_load(tmp_path: Path):
    _, carried = carry_over_set(OLD_SUITE, NEW_SUITE, OLD_SET)
    path = tmp_path / "carried.json"
    assert write_paraphrase_set(carried, path) == _hash(carried)
    assert load_paraphrase_set(path) == carried


def test_carry_over_drops_a_kept_case_the_old_suite_lacks():
    # A case with kept records but no old question cannot be shown unchanged: re-paraphrase.
    ids, carried = carry_over_set(_suite({"c2": "q two"}), _suite({"c1": "q one"}), OLD_SET)
    assert ids == ["c1"] and carried["paraphrases"] == []


def test_carry_over_refuses_a_bad_hash_or_suite():
    with pytest.raises(ValueError):
        carry_over_set(OLD_SUITE, NEW_SUITE, {**OLD_SET, "content_hash": "0" * 64})
    with pytest.raises(ValueError):
        carry_over_set(OLD_SUITE, [*NEW_SUITE, NEW_SUITE[0]], OLD_SET)
    with pytest.raises(ValueError):
        carry_over_set(OLD_SUITE, [{"id": "c9"}], OLD_SET)


# --------------------------------------------------------------------------- merge
def _source(case_id: str, question: str, intent: str = "i1") -> ParaphraseSource:
    return ParaphraseSource(case_id, question, intent_id=intent, intent_axis="dev",
                            template_axis="dev", company_forms=("Apple",), years=(2024,),
                            metric_forms=[("revenue",)])


SOURCES = [_source("c1", "Apple revenue FY2024"), _source("c2", "Apple FY2024 revenue total"),
           _source("c3", "Revenue of Apple in FY 2024", intent="i2")]


def _reply(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    lines = ["# model: gemini-2.5-pro", "# date: 2026-11-01", "# prompt: v1"]
    lines += [json.dumps({"case_id": c, "persona": p, "paraphrase": t}) for c, p, t in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _import(paths: list[Path]) -> dict:
    return import_replies(SOURCES, paths, prompt_version="v1", share=0.15, salt="s")


ROWS_1 = [("c1", "terse_pm", "Apple FY 2024 revenue please"),
          ("c1", "typo_prone", "aple revenue fy2024 Apple"),
          ("c3", "terse_pm", "Apple revenue in fiscal 2024")]
ROWS_2 = [("c2", "terse_pm", "apple FY 2024 revenue please"),  # near-dup of c1:terse_pm
          ("c2", "buried_ask", "ok so Apple, revenue, FY2024?"),
          ("c3", "typo_prone", "Apple revenue, fiscal year 2024 calendar")]  # drifts: review


def test_merge_equals_one_import_of_all_replies(tmp_path: Path):
    r1, r2 = _reply(tmp_path / "r1.txt", ROWS_1), _reply(tmp_path / "r2.txt", ROWS_2)
    merged = merge_paraphrase_sets(_import([r1]), _import([r2]), SOURCES)
    joint = _import([r1, r2])
    assert merged == joint
    assert merged["content_hash"] == _hash(merged)
    dup = next(d for d in merged["duplicates"] if d["paraphrase_id"] == "c2:terse_pm")
    assert (dup["duplicate_of"], dup["kind"]) == ("c1:terse_pm", "near")
    assert [r["paraphrase_id"] for r in merged["review"]] == ["c3:typo_prone"]


def test_merged_set_loads_with_its_hash(tmp_path: Path):
    r1, r2 = _reply(tmp_path / "r1.txt", ROWS_1), _reply(tmp_path / "r2.txt", ROWS_2)
    merged = merge_paraphrase_sets(_import([r1]), _import([r2]), SOURCES)
    path = tmp_path / "merged.json"
    write_paraphrase_set(merged, path)
    assert load_paraphrase_set(path)["content_hash"] == _hash(merged)


def test_merge_keeps_a_carried_set_built_by_hand():
    base = _set(paraphrases=[_kept("c1", "terse_pm", "Apple FY 2024 revenue please", SHA_A)],
                sources=[_prov(SHA_A, "a.txt")])
    addition = _set(paraphrases=[_kept("c2", "terse_pm", "Apple sales line FY24", SHA_B)],
                    review=[_review("c3", "terse_pm", SHA_B)], sources=[_prov(SHA_B, "b.txt")])
    merged = merge_paraphrase_sets(base, addition, SOURCES)
    assert [r["paraphrase_id"] for r in merged["paraphrases"]] == ["c1:terse_pm", "c2:terse_pm"]
    assert merged["sources"] == [_prov(SHA_A, "a.txt"), _prov(SHA_B, "b.txt")]
    assert merged["content_hash"] == _hash(merged)


@pytest.mark.parametrize("where", ["paraphrases", "review", "duplicates"])
def test_merge_refuses_a_shared_paraphrase_id(where):
    base = _set(paraphrases=[_kept("c1", "terse_pm", "Apple FY 2024 revenue please", SHA_A)],
                sources=[_prov(SHA_A, "a.txt")])
    clash = {"paraphrases": _kept("c1", "terse_pm", "Apple revenue for FY 2024", SHA_B),
             "review": _review("c1", "terse_pm", SHA_B),
             "duplicates": _dup("c1", "terse_pm", "c1", SHA_B)}[where]
    addition = _set(**{where: [clash]}, sources=[_prov(SHA_B, "b.txt")])
    with pytest.raises(ValueError, match="c1:terse_pm"):
        merge_paraphrase_sets(base, addition, SOURCES)


def test_merge_refuses_mismatched_or_foreign_sets():
    base = _set(paraphrases=[_kept("c1", "terse_pm", "Apple FY 2024 revenue please", SHA_A)],
                sources=[_prov(SHA_A, "a.txt")])
    ok = _set(paraphrases=[_kept("c2", "terse_pm", "Apple sales FY24", SHA_B)],
              sources=[_prov(SHA_B, "b.txt")])
    bad = [
        _set(prompt_version="v2", sources=[_prov(SHA_B, "b.txt")]),
        _set(sealed=True, sources=[_prov(SHA_B, "b.txt")]),
        _set(split={"share": 0.3, "salt": "s"}, sources=[_prov(SHA_B, "b.txt")]),
        _set(sources=[_prov(SHA_A, "a.txt")]),  # the same reply file twice
        _set(paraphrases=[_kept("c9", "terse_pm", "Apple FY24 revenue", SHA_B)],
             sources=[_prov(SHA_B, "b.txt")]),  # a case not in the sources
        {**ok, "content_hash": "0" * 64},
    ]
    for addition in bad:
        with pytest.raises(ValueError):
            merge_paraphrase_sets(base, addition, SOURCES)
    assert merge_paraphrase_sets(base, ok, SOURCES)["content_hash"]
