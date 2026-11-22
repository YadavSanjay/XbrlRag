"""Spec tests C11 for eval-paraphrases: the import report and the no-text rule for sealed
sets and for every error message (docs/specs/eval-paraphrases.md)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.paraphrases import (
    ParaphraseSource,
    export_batches,
    import_replies,
    parse_reply,
    render_import_report,
)

S1, S2, S3, S4 = ("int-aaaaaaaaaaaa-11111111", "int-aaaaaaaaaaaa-22222222",
                  "int-bbbbbbbbbbbb-33333333", "int-cccccccccccc-44444444")
REVIEW_LINES = [
    (f"review {S1} conversational_retail metric_missing,semantic_drift",
     "  text: Hey, how much did Apple sell in calendar 2024?"),
    (f"review {S3} terse_pm period_missing,semantic_drift",
     "  text: Microsoft revenue trend, last year vs fiscal 2022"),
    (f"review {S4} conversational_retail metric_missing,universe_missing,semantic_drift",
     "  text: Which three of the S&P 100 sold the most in fiscal 2024?"),
    (f"review {S4} typo_prone period_missing,metric_missing",
     "  text: Top 3 big tech companes by revenu in 2024"),
]
DUP_LINES = [
    f"duplicate {S2} sell_side_shorthand of {S1}:sell_side_shorthand",
    f"duplicate {S2} typo_prone of {S2}",
    f"duplicate {S3} buried_ask of {S3}",
    f"duplicate {S4} terse_pm of {S4}:sell_side_shorthand",
]


def _sources(kit):
    return [ParaphraseSource(**kw) for kw in kit.source_kwargs()]


def _import(kit, sealed, paths=None, sources=None):
    return import_replies(sources if sources is not None else _sources(kit),
                          paths or [kit.reply_a, kit.reply_b], prompt_version="v1", share=0.0,
                          salt="s", sealed=sealed)


def _all_texts(kit):
    texts = [kw["question"] for kw in kit.source_kwargs()]
    for path in (kit.reply_a, kit.reply_b):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("{"):
                texts.append(json.loads(line)["paraphrase"].strip())
    return texts


@pytest.mark.criterion("C11")
def test_c11_report_point(kit):
    # oracle: the report format written out by hand for the fixture import at share=0
    # (8 kept: 5 dev = S1 x4 + S4 x1, 3 validation = S2 x1 + S3 x2; 4 review; 4 duplicates).
    open_set = _import(kit, sealed=False)
    head = ["paraphrases: 8", "review: 4", "duplicates: 4", "split: dev=5 validation=3"]
    expected = head + [f"content_hash: {open_set['content_hash']}"]
    for line, text in REVIEW_LINES:
        expected += [line, text]
    expected += DUP_LINES
    assert render_import_report(open_set) == "\n".join(expected) + "\n"

    sealed = _import(kit, sealed=True)
    assert sealed["sealed"] is True
    expected = head + [f"content_hash: {sealed['content_hash']}"]
    expected += [line for line, _ in REVIEW_LINES] + DUP_LINES
    report = render_import_report(sealed)
    assert report == "\n".join(expected) + "\n"
    for text in _all_texts(kit):
        assert text not in report


@pytest.mark.criterion("C11")
def test_c11_errors_carry_no_text_point(kit):
    # oracle: the no-text rule: each error below is provoked by a distinctive string that the
    # message must not repeat.
    secret = "QXZ-secret-wording about Apple revenue in fiscal 2024"
    head = "# model: gemini-2.5-pro\n# date: 2026-10-20\n# prompt: v1\n"
    with pytest.raises(ValueError) as e:
        parse_reply(head + '{"case_id": "c-1", "persona": "terse_pm", "paraphrase": '
                    + json.dumps(secret) + "\n")
    assert "QXZ" not in str(e.value)
    with pytest.raises(ValueError) as e:
        parse_reply(head + f"{secret}\n")
    assert "QXZ" not in str(e.value)
    with pytest.raises(ValueError) as e:
        parse_reply(head + json.dumps({"case_id": "c-1", "persona": "pirate",
                                       "paraphrase": secret}) + "\n")
    assert "QXZ" not in str(e.value)
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        rec = {"case_id": S1, "persona": "terse_pm", "paraphrase": secret}
        for name, records in (("unknown.txt", [{**rec, "case_id": "int-unknown"}]),
                              ("twice.txt", [rec, {**rec, "paraphrase": secret + " again"}])):
            with pytest.raises(ValueError) as e:
                _import(kit, sealed=True, paths=[kit.write_reply(d, name, records)])
            assert "QXZ" not in str(e.value)
    with pytest.raises(ValueError) as e:
        export_batches([ParaphraseSource("c-1", secret, **_minimal()),
                        ParaphraseSource("c-1", secret + "!", **_minimal())],
                       instructions=kit.instructions_v9, prompt_version="v9")
    assert "QXZ" not in str(e.value)
    with pytest.raises(ValueError) as e:
        ParaphraseSource("c-1", secret, **{**_minimal(), "intent_axis": "holdout"})
    assert "QXZ" not in str(e.value)


def _minimal():
    return dict(intent_id="int-1", intent_axis="dev", template_axis="dev",
                company_forms=["Apple"], years=[2024], metric_forms=[["revenue"]])


_SECRET = st.text(min_size=1, max_size=30).map(lambda s: "QXZ" + s)


@pytest.mark.criterion("C11")
@given(_SECRET, st.sampled_from(["", " calendar", " FY24", " big tech"]))
def test_c11_sealed_report_property(kit, secret, suffix):
    # oracle: whatever a paraphrase or question says, a sealed report holds counts, ids and
    # reasons only, and a malformed line's text never reaches the error message.
    src = ParaphraseSource("c-1", "Apple revenue in fiscal 2024 " + secret, **_minimal(),
                           forbidden_forms=["big tech"])
    with tempfile.TemporaryDirectory() as tmp:
        path = kit.write_reply(Path(tmp), "r.txt", [
            {"case_id": "c-1", "persona": "terse_pm",
             "paraphrase": f"Apple revenue FY24 {secret}{suffix}"},
            {"case_id": "c-1", "persona": "typo_prone", "paraphrase": f"{secret} calendar"},
        ])
        pset = import_replies([src], [path], prompt_version="v1", share=0.3, salt="s",
                              sealed=True)
    report = render_import_report(pset)
    assert report.startswith("paraphrases: ")
    assert "review c-1 typo_prone " in report
    assert "QXZ" not in report
    with pytest.raises(ValueError) as e:
        parse_reply("# model: m\n# date: 2026-10-20\n# prompt: v1\n"
                    '{"case_id": "c-1", "persona": "terse_pm", "paraphrase": '
                    + json.dumps(secret) + "\n")
    assert "QXZ" not in str(e.value)
