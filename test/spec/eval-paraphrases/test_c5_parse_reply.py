"""Spec tests C5 for eval-paraphrases: the reply-file format (docs/specs/eval-paraphrases.md)."""

from __future__ import annotations

import json
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.paraphrases import parse_reply

HEAD = "# model: gemini-2.5-pro\n# date: 2026-10-20\n# prompt: v1\n"
REC = '{"case_id": "c-1", "persona": "terse_pm", "paraphrase": "AAPL FY24 revenue"}\n'


@pytest.mark.criterion("C5")
def test_c5_fixture_a_point(kit):
    # oracle: replies/reply_a.txt read by hand: 3 header lines, a code fence on line 4,
    # records on lines 5-7 and 9-15 (line 8 blank), a closing fence on line 16.
    got = parse_reply(kit.reply_a.read_text(encoding="utf-8"))
    assert (got["model"], got["date"], got["prompt_version"]) == ("gemini-2.5-pro", "2026-10-20",
                                                                   "v1")
    recs = got["records"]
    assert [r["line"] for r in recs] == [5, 6, 7, 9, 10, 11, 12, 13, 14, 15]
    assert all(set(r) == {"line", "case_id", "persona", "paraphrase"} for r in recs)
    assert (recs[0]["case_id"], recs[0]["persona"], recs[0]["paraphrase"]) == (
        kit.s1, "sell_side_shorthand", "AAPL FY24 top line?")
    assert (recs[-1]["case_id"], recs[-1]["persona"], recs[-1]["paraphrase"]) == (
        kit.s4, "typo_prone", "Top 3 big tech companes by revenu in 2024")
    assert [r["persona"] for r in recs][3:5] == ["typo_prone", "buried_ask"]


@pytest.mark.criterion("C5")
def test_c5_fixture_b_point(kit):
    # oracle: replies/reply_b.txt read by hand: header written "#model:" and "# date :" (the
    # header regex allows both), records on lines 4-9, the first paraphrase stored stripped.
    got = parse_reply(kit.reply_b.read_text(encoding="utf-8"))
    assert (got["model"], got["date"], got["prompt_version"]) == ("mistral-large-2", "2026-10-21",
                                                                   "v1")
    recs = got["records"]
    assert [r["line"] for r in recs] == [4, 5, 6, 7, 8, 9]
    assert recs[0]["paraphrase"] == "Apple’s net sales in FY 24, pls"
    assert [r["case_id"] for r in recs] == [kit.s1, kit.s3, kit.s4, kit.s1, kit.s3, kit.s2]


@pytest.mark.criterion("C5")
def test_c5_bom_crlf_point():
    # oracle: a leading U+FEFF is dropped and a trailing \r is removed from every line.
    text = "﻿" + (HEAD + REC).replace("\n", "\r\n")
    got = parse_reply(text)
    assert got["model"] == "gemini-2.5-pro" and got["prompt_version"] == "v1"
    assert got["records"] == [{"line": 4, "case_id": "c-1", "persona": "terse_pm",
                               "paraphrase": "AAPL FY24 revenue"}]


BAD = {
    "no header": REC,
    "blank first line": "\n" + HEAD + REC,
    "missing date": "# model: m\n# prompt: v1\n" + REC,
    "missing prompt": "# model: m\n# date: 2026-10-20\n" + REC,
    "duplicate model": "# model: m\n" + HEAD + REC,
    "unknown header key": HEAD + "# temperature: 0.7\n" + REC,
    "empty model": "# model:   \n# date: 2026-10-20\n# prompt: v1\n" + REC,
    "date not real": HEAD.replace("2026-10-20", "2026-02-30") + REC,
    "date short form": HEAD.replace("2026-10-20", "26-10-20") + REC,
    "date with time": HEAD.replace("2026-10-20", "2026-10-20T10:00") + REC,
    "model gpt": HEAD.replace("gemini-2.5-pro", "GPT-5") + REC,
    "model chatgpt": HEAD.replace("gemini-2.5-pro", "chatgpt-4o-latest") + REC,
    "model openai": HEAD.replace("gemini-2.5-pro", "OpenAI reasoning") + REC,
    "model o-series": HEAD.replace("gemini-2.5-pro", "o3-pro") + REC,
    "model claude": HEAD.replace("gemini-2.5-pro", "Claude Sonnet") + REC,
    "model anthropic": HEAD.replace("gemini-2.5-pro", "anthropic-x") + REC,
    "model deepseek": HEAD.replace("gemini-2.5-pro", "DeepSeek-V3.2") + REC,
    "no records": HEAD + "\n```\n```\n",
    "not json": HEAD + "case_id=c-1 persona=terse_pm\n",
    "comment in body": HEAD + REC + "# trailing note\n",
    "json array": HEAD + '["c-1", "terse_pm", "AAPL FY24 revenue"]\n',
    "missing key": HEAD + '{"case_id": "c-1", "persona": "terse_pm"}\n',
    "extra key": HEAD + '{"case_id": "c-1", "persona": "terse_pm", "paraphrase": "x y",'
                        ' "note": "n"}\n',
    "unknown persona": HEAD + '{"case_id": "c-1", "persona": "pirate", "paraphrase": "x y"}\n',
    "persona wrong case": HEAD + '{"case_id": "c-1", "persona": "Terse_PM", "paraphrase": "x"}\n',
    "empty paraphrase": HEAD + '{"case_id": "c-1", "persona": "terse_pm", "paraphrase": "  "}\n',
    "empty case_id": HEAD + '{"case_id": " ", "persona": "terse_pm", "paraphrase": "x"}\n',
    "non-string case_id": HEAD + '{"case_id": 7, "persona": "terse_pm", "paraphrase": "x"}\n',
}


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("name", sorted(BAD))
def test_c5_format_errors_point(name):
    # oracle: each text breaks exactly one rule of the reply-file format.
    with pytest.raises(ValueError):
        parse_reply(BAD[name])


_FORBIDDEN_MODEL = re.compile(r"gpt|openai|claude|anthropic|deepseek")
_MODELS = st.text(alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJ0123456789-._ ", min_size=1,
                  max_size=20).map(str.strip).filter(
    lambda m: m and not _FORBIDDEN_MODEL.search(m.lower())
    and re.match(r"o[0-9]", m.lower()) is None)
_RECORDS = st.lists(
    st.tuples(st.text(alphabet="abcdef0123456789-", min_size=1, max_size=12),
              st.sampled_from(["sell_side_shorthand", "terse_pm", "conversational_retail",
                               "typo_prone", "buried_ask"]),
              st.text(min_size=1, max_size=40).filter(lambda s: s.strip() != "")),
    min_size=1, max_size=8)


@pytest.mark.criterion("C5")
@given(_MODELS, st.dates(), st.integers(1, 99), _RECORDS)
def test_c5_round_trip_property(model, date, version, records):
    # oracle: any header and records written in the format parse back to the same values
    # (paraphrases stripped), with 1-based line numbers after the 3 header lines.
    lines = [f"# model: {model}", f"# date: {date.isoformat()}", f"# prompt: v{version}"]
    lines += [json.dumps({"case_id": c, "persona": p, "paraphrase": t}) for c, p, t in records]
    got = parse_reply("\n".join(lines) + "\n")
    assert got["model"] == model
    assert got["date"] == date.isoformat()
    assert got["prompt_version"] == f"v{version}"
    assert got["records"] == [
        {"line": 4 + i, "case_id": c, "persona": p, "paraphrase": t.strip()}
        for i, (c, p, t) in enumerate(records)
    ]
