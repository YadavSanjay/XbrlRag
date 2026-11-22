"""Unit tests for the eval-paraphrases adapter and its two CLIs (synthetic inputs, tmp dirs).

The spec tests (test/spec/eval-paraphrases/) cover src/eval/suites/paraphrases.py; these cover
the implementer's parts: the PhrasedCase -> ParaphraseSource adapter and the export / import
scripts, including that their console output never carries question or paraphrase text.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pytest

from src.eval.suites.intents import (
    Intent,
    build_phrased_cases,
    intent_id,
    load_lexicon,
    load_phrasebook,
)
from src.eval.suites.paraphrase_forms import load_paraphrase_forms
from src.eval.suites.paraphrase_sources import (
    add_source_arguments,
    build_sources,
    load_intents,
    load_sources,
    paraphrase_source,
)
from src.eval.suites.paraphrases import check_meaning, load_paraphrase_set

REPO = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_cli", REPO / "scripts" / "eval"
                                                  / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


export_cli = _load_script("export_paraphrase_batches")
import_cli = _load_script("import_paraphrases")

LEXICON = """
companies:
  AAPL: {short: Apple, legal: Apple Inc.}
  MSFT: {short: Microsoft, legal: Microsoft Corporation}
metrics:
  Revenue:
    label: Revenues
    synonyms: [net sales]
    shorthand: [top line]
universes:
  big_tech: [big tech, megacap tech]
  sp100: [S&P 100 companies]
period_ends:
  AAPL: {2023: 2023-09-30, 2024: 2024-09-28}
"""

PHRASEBOOK = """
templates:
  - {id: lookup-terse-1, register: terse, shapes: [lookup], text: "{company} {metric} {period}"}
  - {id: lookup-conv-1, register: conversational, shapes: [lookup],
     text: "What was {company}'s {metric} in {period}?"}
  - {id: yoy-terse-1, register: terse, shapes: [compute.yoy_growth],
     text: "{company} {metric} growth into {last_period}"}
  - {id: top-terse-1, register: terse, shapes: [screen.top],
     text: "Top {n} {universe} by {metric}, {period}"}
"""

INTENTS = [
    {"kind": "lookup", "tickers": ["AAPL"], "fiscal_years": [2024], "metrics": ["Revenue"]},
    {"kind": "lookup", "tickers": ["MSFT"], "fiscal_years": [2023], "metrics": ["Revenue"]},
    {"kind": "compute", "operation": "yoy_growth", "tickers": ["AAPL"],
     "fiscal_years": [2023, 2024], "metrics": ["Revenue"]},
    {"kind": "screen", "operation": "top", "universe": "big_tech", "n": 3,
     "fiscal_years": [2024], "metrics": ["Revenue"]},
]


FORMS = """
universes:
  big_tech: [mega caps]
"""


@pytest.fixture()
def inputs(tmp_path: Path) -> dict[str, Path]:
    paths = {"lexicon": tmp_path / "lexicon.yaml", "phrasebook": tmp_path / "phrasebook.yaml",
             "intents": tmp_path / "intents.json", "forms": tmp_path / "forms.yaml"}
    paths["lexicon"].write_text(LEXICON, encoding="utf-8")
    paths["phrasebook"].write_text(PHRASEBOOK, encoding="utf-8")
    paths["intents"].write_text(json.dumps(INTENTS), encoding="utf-8")
    paths["forms"].write_text(FORMS, encoding="utf-8")
    return paths


def _cases(inputs, per_intent=4):
    lexicon, phrasebook = load_lexicon(inputs["lexicon"]), load_phrasebook(inputs["phrasebook"])
    intents = load_intents(inputs["intents"])
    cases = build_phrased_cases(intents, phrasebook, lexicon, per_intent=per_intent, share=0.5,
                                salt="t")
    return cases, lexicon, phrasebook


def _source_args(inputs, split="all"):
    return ["--intents", str(inputs["intents"]), "--phrasebook", str(inputs["phrasebook"]),
            "--lexicon", str(inputs["lexicon"]), "--per-intent", "4", "--share", "0.5",
            "--salt", "t", "--split", split, "--forms", str(inputs["forms"])]


# --------------------------------------------------------------------------- adapter
def test_adapter_lookup_fields(inputs):
    cases, lexicon, phrasebook = _cases(inputs)
    case = next(c for c in cases if c.intent.tickers == ("AAPL",) and c.intent.kind == "lookup")
    s = paraphrase_source(case, lexicon, phrasebook)
    assert s.case_id == case.case_id and s.question == case.question
    assert s.intent_id == intent_id(case.intent)
    assert (s.intent_axis, s.template_axis) == (case.intent_axis, case.template_axis)
    assert s.company_forms == ("AAPL", "Apple", "Apple Inc.")
    assert s.years == (2024,)
    assert s.period_ends == {2024: "2024-09-28"}
    assert s.metric_forms == (("Revenues", "net sales", "top line"),)
    assert s.universe_forms == ()
    assert s.forbidden_forms == ("big tech", "megacap tech", "S&P 100 companies")


def test_adapter_years_follow_template_placeholders(inputs):
    # The yoy template names only {last_period}: the first year is not in the question.
    cases, lexicon, phrasebook = _cases(inputs)
    case = next(c for c in cases if c.intent.kind == "compute")
    s = paraphrase_source(case, lexicon, phrasebook)
    assert s.years == (2024,)
    assert s.period_ends == {2024: "2024-09-28"}


def test_adapter_screen_has_universe_and_no_company(inputs):
    cases, lexicon, phrasebook = _cases(inputs)
    case = next(c for c in cases if c.intent.kind == "screen")
    s = paraphrase_source(case, lexicon, phrasebook)
    assert s.company_forms == () and s.period_ends == {}
    assert s.universe_forms == ("big tech", "megacap tech")
    assert s.forbidden_forms == ("S&P 100 companies",)


def test_every_rendered_question_passes_its_own_meaning_check(inputs):
    cases, lexicon, phrasebook = _cases(inputs)
    assert len(cases) > 4
    for case in cases:
        s = paraphrase_source(case, lexicon, phrasebook)
        assert check_meaning(s, s.question) == [], case.case_id


def test_build_sources_split_filter(inputs):
    lexicon, phrasebook = load_lexicon(inputs["lexicon"]), load_phrasebook(inputs["phrasebook"])
    intents = load_intents(inputs["intents"])
    kw = dict(per_intent=4, share=0.5, salt="t")
    every = build_sources(intents, phrasebook, lexicon, split="all", **kw)
    dev = build_sources(intents, phrasebook, lexicon, split="dev", **kw)
    val = build_sources(intents, phrasebook, lexicon, split="validation", **kw)
    assert sorted(s.case_id for s in dev + val) == [s.case_id for s in every]
    assert all("validation" not in (s.intent_axis, s.template_axis) for s in dev)
    with pytest.raises(ValueError):
        build_sources(intents, phrasebook, lexicon, split="test", **kw)


def test_load_sources_applies_the_forms_file(inputs):
    # paraphrase-shorthand requirement 5: the forms reach every source; without them, nothing.
    kw = dict(per_intent=4, share=0.5, salt="t", split="all")
    paths = (inputs["intents"], inputs["phrasebook"], inputs["lexicon"])
    plain = {s.case_id: s for s in load_sources(*paths, **kw)}
    shorthand = {s.case_id: s for s in load_sources(*paths, forms_path=inputs["forms"], **kw)}
    screen = next(cid for cid, s in plain.items() if s.universe_forms)
    assert check_meaning(plain[screen], "Top 3 mega caps by net sales, FY24") == [
        "universe_missing"]
    assert check_meaning(shorthand[screen], "Top 3 mega caps by net sales, FY24") == []
    assert all(shorthand[cid].metric_forms == s.metric_forms for cid, s in plain.items())


def test_the_cli_reads_the_committed_forms_by_default():
    # paraphrase-shorthand requirement 5: --forms defaults to the committed check-only file,
    # which loads against the committed lexicon.
    parser = argparse.ArgumentParser()
    add_source_arguments(parser)
    args = parser.parse_args(["--intents", "i.json", "--per-intent", "3", "--share", "0.15",
                              "--salt", "s", "--split", "dev"])
    assert args.forms == REPO / "configs" / "eval" / "v2" / "paraphrase_forms.yaml"
    forms = load_paraphrase_forms(args.forms, load_lexicon(args.lexicon))
    assert forms["ratios"] and forms["universes"] and "cost" in forms["possessive_exempt"]


def test_load_intents(tmp_path):
    p = tmp_path / "i.json"
    p.write_text(json.dumps(INTENTS[:1]), encoding="utf-8")
    assert load_intents(p) == [Intent("lookup", tickers=["AAPL"], fiscal_years=[2024],
                                      metrics=["Revenue"])]
    for bad in ('{"kind": "lookup"}', '[{"tickers": ["AAPL"]}]',
                '[{"kind": "lookup", "colour": 1}]', "not json"):
        p.write_text(bad, encoding="utf-8")
        with pytest.raises(ValueError):
            load_intents(p)


# --------------------------------------------------------------------------- CLIs
def _all_questions(inputs):
    cases, _, _ = _cases(inputs)
    return [c.question for c in cases]


def test_export_cli_writes_batches_and_prints_no_text(inputs, tmp_path, capsys):
    out = tmp_path / "batches"
    code = export_cli.main(_source_args(inputs) + ["--out-dir", str(out), "--batch-size", "3",
                                                   "--list-ids"])
    printed = capsys.readouterr().out
    assert code == 0
    files = sorted(out.glob("batch_*.txt"))
    n_questions = len(_all_questions(inputs))
    assert len(files) == -(-n_questions // 3)
    assert files[0].name == f"batch_001_of_{len(files):03d}.txt"
    first = files[0].read_text(encoding="utf-8")
    assert first.startswith("# paraphrase-instructions v1\n")
    assert f"### BATCH 1 OF {len(files)} | PROMPT v1 | 3 QUESTIONS\n" in first
    assert f"questions: {n_questions}" in printed
    for q in _all_questions(inputs):
        assert q not in printed
    # A second export into the same directory refuses rather than mixing batches.
    assert export_cli.main(_source_args(inputs) + ["--out-dir", str(out)]) == 1


def _reply_from_batches(batch_dir: Path, path: Path) -> list[str]:
    """Writes a reply that paraphrases each question by echo (dup) or a fixed form; returns
    the paraphrase texts written."""
    lines = ["# model: gemini-2.5-pro", "# date: 2026-10-20", "# prompt: v1", "```json"]
    texts = []
    for batch in sorted(batch_dir.glob("batch_*.txt")):
        body = batch.read_text(encoding="utf-8").split("\n### BATCH ", 1)[1]
        for row in body.splitlines()[1:]:
            if not row:
                continue
            q = json.loads(row)
            for i, persona in enumerate(q["personas"]):
                text = q["question"] if i == 0 else f"ZQX calendar musing {q['case_id'][-4:]}"
                texts.append(text)
                lines.append(json.dumps({"case_id": q["case_id"], "persona": persona,
                                         "paraphrase": text}))
    lines.append("```")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return texts


@pytest.mark.parametrize("sealed", [True, False])
def test_import_cli_round_trip(inputs, tmp_path, capsys, sealed):
    batches, replies = tmp_path / "batches", tmp_path / "replies"
    replies.mkdir()
    assert export_cli.main(_source_args(inputs) + ["--out-dir", str(batches)]) == 0
    texts = _reply_from_batches(batches, replies / "reply_1.txt")
    capsys.readouterr()
    out = tmp_path / "set.json"
    args = _source_args(inputs) + ["--replies-dir", str(replies), "--out", str(out)]
    code = import_cli.main(args + (["--sealed"] if sealed else []))
    printed = capsys.readouterr().out
    assert code == 0
    pset = load_paraphrase_set(out)
    assert pset["sealed"] is sealed
    n = len(_all_questions(inputs))
    # Each case: the echo is an exact duplicate of its question; the other drifts to review.
    assert len(pset["duplicates"]) == n and len(pset["review"]) == n
    assert pset["paraphrases"] == []
    assert all(d["kind"] == "exact" for d in pset["duplicates"])
    assert f"content_hash: {pset['content_hash']}" in printed
    assert ("ZQX" in printed) is (not sealed)
    if sealed:
        for t in texts + _all_questions(inputs):
            assert t not in printed
    # The frozen set is not overwritten without --force.
    assert import_cli.main(args) == 1
    assert import_cli.main(args + ["--force"]) == 0


def test_import_cli_errors_carry_no_text(inputs, tmp_path, capsys):
    replies = tmp_path / "replies"
    replies.mkdir()
    (replies / "r.txt").write_text(
        "# model: gemini-2.5-pro\n# date: 2026-10-20\n# prompt: v1\n"
        '{"case_id": "int-nope-00000000", "persona": "terse_pm", "paraphrase": "ZQX words"}\n',
        encoding="utf-8")
    code = import_cli.main(_source_args(inputs) + ["--replies-dir", str(replies),
                                                   "--out", str(tmp_path / "s.json")])
    printed = capsys.readouterr().out
    assert code == 1
    assert printed.startswith("error: ") and "ZQX" not in printed
    assert not (tmp_path / "s.json").exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    assert import_cli.main(_source_args(inputs) + ["--replies-dir", str(empty),
                                                   "--out", str(tmp_path / "s.json")]) == 1


# --------------------------------------------------------------------------- re-freeze CLIs
carryover_cli = _load_script("paraphrase_carryover")


def _all_ids(inputs):
    cases, _, _ = _cases(inputs)
    return sorted(c.case_id for c in cases)


def _batch_ids(batch_dir: Path) -> list[str]:
    ids = []
    for batch in sorted(batch_dir.glob("batch_*.txt")):
        body = batch.read_text(encoding="utf-8").split("\n### BATCH ", 1)[1]
        ids += [json.loads(row)["case_id"] for row in body.splitlines()[1:] if row]
    return ids


def test_export_cli_case_ids_exports_only_the_listed_cases(inputs, tmp_path, capsys):
    every = _all_ids(inputs)
    chosen = [every[-1], every[0]]
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text(f"# re-freeze\n{chosen[0]}\n\n{chosen[1]}\n", encoding="utf-8")
    full, subset = tmp_path / "full", tmp_path / "subset"
    assert export_cli.main(_source_args(inputs) + ["--out-dir", str(full)]) == 0
    assert export_cli.main(_source_args(inputs) + ["--out-dir", str(subset),
                                                   "--case-ids", str(ids_file)]) == 0
    printed = capsys.readouterr().out
    assert "questions: 2" in printed
    assert _batch_ids(subset) == sorted(chosen)
    # Same instruction block and the same per-case row as the full export.
    text = (subset / "batch_001_of_001.txt").read_text(encoding="utf-8")
    full_text = "".join(p.read_text(encoding="utf-8") for p in sorted(full.glob("batch_*")))
    head = text.split("### BATCH ", 1)[0]
    assert full_text.startswith(head) and head.startswith("# paraphrase-instructions v1\n")
    assert "### BATCH 1 OF 1 | PROMPT v1 | 2 QUESTIONS\n" in text
    for row in text.split("\n### BATCH ", 1)[1].splitlines()[1:]:
        assert row and row + "\n" in full_text
    for q in _all_questions(inputs):
        assert q not in printed


def test_export_cli_case_ids_refuses_unknown_or_repeated_ids(inputs, tmp_path, capsys):
    every = _all_ids(inputs)
    for i, body in enumerate((f"{every[0]}\nint-nope-00000000\n", f"{every[0]}\n{every[0]}\n",
                              "# nothing\n")):
        ids_file = tmp_path / f"ids{i}.txt"
        ids_file.write_text(body, encoding="utf-8")
        out = tmp_path / f"out{i}"
        assert export_cli.main(_source_args(inputs) + ["--out-dir", str(out),
                                                       "--case-ids", str(ids_file)]) == 1
        assert not out.exists()
    assert "error: " in capsys.readouterr().out


def _suite_file(path: Path, cases: list[dict], split: str = "dev") -> Path:
    from src.eval.suites.v2_suite import content_hash
    path.write_text(json.dumps({"name": "s", "split": split, "version": 1, "cases": cases,
                                "n_cases": len(cases), "content_hash": content_hash(cases)}),
                    encoding="utf-8")
    return path


def test_carryover_then_subset_export_then_merge_import(inputs, tmp_path, capsys):
    # A first freeze: every case paraphrased with one kept paraphrase each.
    every = _all_ids(inputs)
    by_id = {c.case_id: c for c in _cases(inputs)[0]}
    batches, replies = tmp_path / "b0", tmp_path / "r0"
    replies.mkdir()
    assert export_cli.main(_source_args(inputs) + ["--out-dir", str(batches)]) == 0
    _reply_kept(batches, replies / "reply_0.txt", by_id, tag="first")
    old_set = tmp_path / "old_set.json"
    assert import_cli.main(_source_args(inputs) + ["--replies-dir", str(replies),
                                                   "--out", str(old_set)]) == 0
    old = load_paraphrase_set(old_set)
    assert {r["case_id"] for r in old["paraphrases"]} == set(every)
    # Re-freeze: one case removed, one reworded in place, one added.
    removed, reworded = every[0], every[1]
    old_cases = [{"id": cid, "question": by_id[cid].question} for cid in every]
    new_cases = [{"id": cid, "question": by_id[cid].question + (" now" if cid == reworded
                                                                 else "")}
                 for cid in every if cid != removed] + [{"id": "int-added", "question": "q"}]
    ids_out, set_out = tmp_path / "e2" / "ids.txt", tmp_path / "e2" / "carried.json"
    capsys.readouterr()
    assert carryover_cli.main([
        "--old-suite", str(_suite_file(tmp_path / "old.json", old_cases)),
        "--new-suite", str(_suite_file(tmp_path / "new.json", new_cases)),
        "--old-set", str(old_set), "--ids-out", str(ids_out), "--set-out", str(set_out)]) == 0
    printed = capsys.readouterr().out
    assert ids_out.read_text(encoding="utf-8").split() == sorted(["int-added", reworded])
    assert "to paraphrase: 2 (reworded=1" in printed
    for q in _all_questions(inputs):
        assert q not in printed
    carried = load_paraphrase_set(set_out)
    assert {r["case_id"] for r in carried["paraphrases"]} == set(every) - {removed, reworded}
    # Export the reworded case only (the synthetic "added" case has no intent behind it).
    ids_file = tmp_path / "ids_export.txt"
    ids_file.write_text(f"{reworded}\n", encoding="utf-8")
    b1, r1 = tmp_path / "b1", tmp_path / "r1"
    r1.mkdir()
    assert export_cli.main(_source_args(inputs) + ["--out-dir", str(b1),
                                                   "--case-ids", str(ids_file)]) == 0
    _reply_kept(b1, r1 / "reply_1.txt", by_id, tag="second")
    merged_out = tmp_path / "merged.json"
    capsys.readouterr()
    assert import_cli.main(_source_args(inputs) + ["--replies-dir", str(r1), "--out",
                                                   str(merged_out), "--merge-with",
                                                   str(set_out)]) == 0
    printed = capsys.readouterr().out
    merged = load_paraphrase_set(merged_out)
    assert {r["case_id"] for r in merged["paraphrases"]} == set(every) - {removed}
    assert len(merged["sources"]) == 2 and "merged:" in printed
    # Merging the same carried set into an import that already holds its ids is refused.
    assert import_cli.main(_source_args(inputs) + ["--replies-dir", str(replies), "--out",
                                                   str(tmp_path / "x.json"), "--merge-with",
                                                   str(set_out)]) == 1


def _reply_kept(batch_dir: Path, path: Path, by_id, tag: str) -> None:
    """Writes a reply whose first persona per case restates the question with a suffix that
    keeps its meaning (kept); the second persona echoes it (an exact duplicate)."""
    lines = ["# model: gemini-2.5-pro", "# date: 2026-10-20", "# prompt: v1"]
    for batch in sorted(batch_dir.glob("batch_*.txt")):
        body = batch.read_text(encoding="utf-8").split("\n### BATCH ", 1)[1]
        for row in body.splitlines()[1:]:
            if not row:
                continue
            q = json.loads(row)
            for i, persona in enumerate(q["personas"]):
                text = f"{q['question']} {tag} please" if i == 0 else q["question"]
                lines.append(json.dumps({"case_id": q["case_id"], "persona": persona,
                                         "paraphrase": text}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
