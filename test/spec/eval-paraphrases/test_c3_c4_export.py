"""Spec tests C3-C4 for eval-paraphrases: persona assignment, batch export and the versioned
instruction block (docs/specs/eval-paraphrases.md)."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src import PROJECT_ROOT
from src.eval.suites.paraphrases import (
    ParaphraseSource,
    assign_personas,
    export_batches,
    instructions_path,
)


def _src(case_id, question="What was Apple's revenue in fiscal 2024?"):
    return ParaphraseSource(case_id, question, intent_id="int-x", intent_axis="dev",
                            template_axis="dev", company_forms=["AAPL"], years=[2024],
                            metric_forms=[["revenue"]])


def _line(case_id, question, personas):
    return json.dumps({"case_id": case_id, "personas": list(personas), "question": question},
                      ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_c3_assign_personas_point(kit):
    # oracle: the k personas with the smallest sha1(f"{case_id}|{persona}"), canonical order,
    # computed in the test with hashlib (conftest.oracle_personas).
    for cid in ("int-aaaaaaaaaaaa-11111111", "c-a", "x"):
        for k in range(1, 6):
            got = assign_personas(cid, k)
            assert tuple(got) == kit.oracle_personas(cid, k)
            assert len(got) == k
    assert tuple(assign_personas("c-a", 5)) == kit.personas
    for k in (0, 6, -1):
        with pytest.raises(ValueError):
            assign_personas("c-a", k)


@pytest.mark.criterion("C3")
def test_c3_export_exact_text_point(kit):
    # oracle: the batch format written out by hand: instructions, a blank line, the BATCH
    # header, then one json.dumps line per source in case_id order.
    qa, qb, qc = "Apple’s revenue in FY24?", "MSFT opex FY23", "Top 3 big tech by revenue?"
    sources = [_src("c-b", qb), _src("c-c", qc), _src("c-a", qa)]
    got = export_batches(sources, instructions=kit.instructions_v9, prompt_version="v9",
                         batch_size=2, personas_per_case=2)
    head = "# paraphrase-instructions v9\nRewrite each question.\nReply in JSON lines.\n\n"
    p = {c: kit.oracle_personas(c, 2) for c in ("c-a", "c-b", "c-c")}
    assert got == [
        head + "### BATCH 1 OF 2 | PROMPT v9 | 2 QUESTIONS\n"
        + _line("c-a", qa, p["c-a"]) + _line("c-b", qb, p["c-b"]),
        head + "### BATCH 2 OF 2 | PROMPT v9 | 1 QUESTIONS\n" + _line("c-c", qc, p["c-c"]),
    ]
    assert "Apple’s" in got[0]                      # ensure_ascii=False keeps U+2019 as is
    one = export_batches([_src("c-a", qa)], instructions="# paraphrase-instructions v9",
                         prompt_version="v9", personas_per_case=3)
    assert one == ["# paraphrase-instructions v9\n\n### BATCH 1 OF 1 | PROMPT v9 | 1 QUESTIONS\n"
                   + _line("c-a", qa, kit.oracle_personas("c-a", 3))]
    assert export_batches([], instructions=kit.instructions_v9, prompt_version="v9") == []


@pytest.mark.criterion("C3")
def test_c3_export_errors_point(kit):
    # oracle: the export's ValueError list (bad sizes, duplicate case_id, version mismatch).
    ok = [_src("c-a"), _src("c-b")]
    kw = dict(instructions=kit.instructions_v9, prompt_version="v9")
    for bad in (dict(batch_size=0), dict(batch_size=-3), dict(personas_per_case=0),
                dict(personas_per_case=6)):
        with pytest.raises(ValueError):
            export_batches(ok, **kw, **bad)
    with pytest.raises(ValueError):
        export_batches([_src("c-a"), _src("c-a", "another question")], **kw)
    with pytest.raises(ValueError):
        export_batches(ok, instructions=kit.instructions_v9, prompt_version="v1")
    with pytest.raises(ValueError):
        export_batches(ok, instructions="\n" + kit.instructions_v9, prompt_version="v9")
    with pytest.raises(ValueError):
        export_batches(ok, instructions="# paraphrase-instructions v9 extra", prompt_version="v9")


_HEADER = re.compile(r"^### BATCH (\d+) OF (\d+) \| PROMPT v9 \| (\d+) QUESTIONS$")


@pytest.mark.criterion("C3")
@given(st.sets(st.text(alphabet="abcdef0123456789-", min_size=1, max_size=10), min_size=1,
               max_size=25),
       st.integers(1, 7), st.integers(1, 5), st.randoms(use_true_random=False))
def test_c3_export_partition_property(kit, ids, batch_size, k, rnd):
    # oracle: ceil(n / batch_size) batches cut from the case_id-sorted list; order of the
    # input never matters; each source appears exactly once with its assigned personas.
    ids = sorted(ids)
    shuffled = list(ids)
    rnd.shuffle(shuffled)
    kw = dict(instructions=kit.instructions_v9, prompt_version="v9", batch_size=batch_size,
              personas_per_case=k)
    got = export_batches([_src(c, f"question {c}") for c in ids], **kw)
    again = export_batches([_src(c, f"question {c}") for c in shuffled], **kw)
    assert got == again
    n = math.ceil(len(ids) / batch_size)
    assert len(got) == n
    seen = []
    for i, text in enumerate(got, start=1):
        assert text.startswith(kit.instructions_v9.rstrip("\n") + "\n\n")
        body = text[len(kit.instructions_v9.rstrip("\n")) + 2:].splitlines()
        m = _HEADER.match(body[0])
        assert m and int(m.group(1)) == i and int(m.group(2)) == n
        rows = [json.loads(x) for x in body[1:]]
        assert int(m.group(3)) == len(rows) == min(batch_size, len(ids) - (i - 1) * batch_size)
        for r in rows:
            assert list(r) == ["case_id", "personas", "question"]
            assert tuple(r["personas"]) == kit.oracle_personas(r["case_id"], k)
            assert r["question"] == f"question {r['case_id']}"
        seen += [r["case_id"] for r in rows]
    assert seen == ids


# --------------------------------------------------------------------------- C4
PERSONA_IDS = ("sell_side_shorthand", "terse_pm", "conversational_retail", "typo_prone",
               "buried_ask")


@pytest.mark.criterion("C4")
def test_c4_instructions_v1_point(kit):
    # oracle: the instruction-block requirement (path, first line, personas, meaning rules,
    # reply keys) read by hand from the spec.
    p = instructions_path("v1")
    assert Path(p) == Path(PROJECT_ROOT) / "prompts" / "templates" / "eval_paraphrase" / "v1" \
        / "instructions.txt"
    text = (kit.repo / "prompts" / "templates" / "eval_paraphrase" / "v1"
            / "instructions.txt").read_text(encoding="utf-8")
    assert text.splitlines()[0] == "# paraphrase-instructions v1"
    low = text.lower()
    for persona in PERSONA_IDS:
        assert persona in text, persona
    for word in ("company", "period", "metric", "operation", "scope", "fiscal", "calendar"):
        assert word in low, word
    for key in ("case_id", "persona", "paraphrase"):
        assert f'"{key}"' in text, key
    batches = export_batches([_src("c-a")], instructions=text, prompt_version="v1")
    assert len(batches) == 1 and batches[0].startswith(text.rstrip("\n") + "\n\n### BATCH 1 OF 1")
    for bad in ("1", "v", "V1", "v1.0", "v1/../v2", "", " v1", "v1 "):
        with pytest.raises(ValueError):
            instructions_path(bad)


@pytest.mark.criterion("C4")
@given(st.one_of(st.integers(0, 10**6).map(lambda n: ("ok", f"v{n}")),
                 st.text(max_size=8).filter(lambda s: re.fullmatch(r"v[0-9]+", s) is None)
                 .map(lambda s: ("bad", s))))
def test_c4_version_property(case):
    # oracle: a version is accepted iff it matches ^v\d+$, and names the directory.
    kind, version = case
    if kind == "ok":
        p = Path(instructions_path(version))
        assert p.parts[-5:] == ("prompts", "templates", "eval_paraphrase", version,
                                "instructions.txt")
    else:
        with pytest.raises(ValueError):
            instructions_path(version)
