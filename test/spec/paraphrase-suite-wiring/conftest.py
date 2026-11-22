"""Synthetic suites, paraphrase sets and oracles for the paraphrase-suite-wiring spec tests.

Spec: docs/specs/paraphrase-suite-wiring.md. Everything is hand-written Python data in the
shape the v2 suite builder and the paraphrase import write (made-up tickers, CIKs, values and
texts). Hashes are computed here with ``hashlib`` and ``json`` from the two hashing rules, never
by the code under test. The only fake is the answering system: ``answer(value, rows)`` builds the
``SystemAnswer`` a system would return. No network, no data directory, no clock.
"""

from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.eval.core.system import SystemAnswer

PERSONAS = ("sell_side_shorthand", "terse_pm", "conversational_retail", "typo_prone",
            "buried_ask")


# ---------------------------------------------------------------------------- hashing


def suite_hash(cases: list[dict]) -> str:
    """v2-suite-build rule 15: ``sha256:`` + hex over the canonical JSON of the cases."""
    text = json.dumps(cases, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def set_hash(pset: dict) -> str:
    """eval-paraphrases rule 6: bare hex over the canonical bytes, without ``content_hash``."""
    body = {k: v for k, v in pset.items() if k != "content_hash"}
    raw = (json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def seal_suite(doc: dict) -> dict:
    doc["n_cases"] = len(doc["cases"])
    doc["content_hash"] = suite_hash(doc["cases"])
    return doc


def seal_set(pset: dict) -> dict:
    pset["content_hash"] = set_hash(pset)
    return pset


# ---------------------------------------------------------------------------- suites


def _key(value: float, accession: str) -> dict:
    return {"status": "verified", "source": "sec_companyfacts", "policies": ["S1", "S2", "S6"],
            "definition": None, "same_filing": None, "disagreements": [],
            "inputs": [{"accession": accession, "value": value, "key_source": "sec_companyfacts",
                        "status": "verified"}]}


def _tags(split: str, intent: str, family: str, template: str, shape: str) -> list[str]:
    return [f"split:{split}", f"intent:{intent}", f"family:{family}", f"template:{template}",
            "key_source:sec_companyfacts", "key_status:verified", f"shape:{shape}"]


def _lookup(cid: str, intent: str, split: str, family: str, template: str, question: str,
            value: float, ticker: str, cik: int, fy: int) -> dict:
    return {"id": cid, "question": question, "kind": "lookup", "suite": f"xbrl_v2_{split}",
            "expected_value": value, "paraphrase_group": intent, "ticker": ticker, "cik": cik,
            "fy": fy, "concept": "us-gaap:OperatingIncomeLoss", "unit": "USD",
            "origin": "v2:lookup", "rel_tol": 0.0001,
            "tags": _tags(split, intent, family, template, "lookup"),
            "key": _key(value, f"0000{cik}-25-000001")}


def dev_cases() -> list[dict]:
    """Four dev cases: two template phrasings of ``int-aaa``, a ratio, a top-2 screen."""
    ratio = {"id": "int-bbb-0001", "question": "What was BBB's net margin in FY2024?",
             "kind": "compute", "suite": "xbrl_v2_dev", "expected_value": 0.25,
             "alt_values": [25.0], "paraphrase_group": "int-bbb", "ticker": "BBB",
             "cik": 900202, "fy": 2024, "origin": "v2:compute.ratio", "rel_tol": 0.0001,
             "tags": _tags("dev", "int-bbb", "terse", "ratio-terse-1", "compute.ratio"),
             "key": _key(0.25, "0000900202-25-000001")}
    ratio["key"]["definition"] = "net_margin"
    rows = [{"ticker": "CCB", "value": 900.0, "fiscal_year": 2024, "period_end": "2024-12-31"},
            {"ticker": "CCA", "value": 500.0, "fiscal_year": 2024, "period_end": "2024-09-28"}]
    screen = {"id": "int-ccc-0001",
              "question": "Which 2 covered companies had the highest revenue in fiscal 2024?",
              "kind": "screen", "suite": "xbrl_v2_dev", "expected_rows": rows, "top_n": 2,
              "paraphrase_group": "int-ccc", "fy": 2024, "unit": "USD",
              "origin": "v2:screen.top", "rel_tol": 0.0001,
              "tags": _tags("dev", "int-ccc", "formal", "top-formal-1", "screen.top"),
              "key": _key(900.0, "0000900301-25-000001")}
    return [
        _lookup("int-aaa-0001", "int-aaa", "dev", "formal", "lookup-formal-1",
                "What was AAA's operating income in fiscal year 2024?", 100000000.0, "AAA",
                900101, 2024),
        _lookup("int-aaa-0002", "int-aaa", "dev", "conversational", "lookup-conv-1",
                "How much operating income did AAA make in FY2024?", 100000000.0, "AAA",
                900101, 2024),
        ratio,
        screen,
    ]


def validation_cases() -> list[dict]:
    """Two validation cases: a validation intent, and a validation template of ``int-aaa``."""
    return [
        _lookup("int-vvv-0001", "int-vvv", "validation", "formal", "lookup-formal-1",
                "What was VVV's operating income in fiscal year 2023?", 7000000.0, "VVV",
                900404, 2023),
        _lookup("int-aaa-0003", "int-aaa", "validation", "terse", "lookup-terse-9",
                "AAA operating income FY2024", 100000000.0, "AAA", 900101, 2024),
    ]


def make_suite(split: str, cases: list[dict]) -> dict:
    return seal_suite({"name": f"xbrl_v2_{split}", "version": "1", "split": split,
                       "cases": cases})


# ---------------------------------------------------------------------------- sets


def _para(case_id: str, intent: str, persona: str, text: str, axes: tuple[str, str, str],
          sha: str) -> dict:
    split = "validation" if "validation" in axes else "dev"
    return {"paraphrase_id": f"{case_id}:{persona}", "case_id": case_id, "intent_id": intent,
            "persona": persona, "paraphrase": text, "source_sha256": sha,
            "intent_axis": axes[0], "template_axis": axes[1], "persona_axis": axes[2],
            "split": split}


SHA_A = "a" * 64
SHA_B = "b" * 64
D, V = "dev", "validation"

# Distinctive texts, so a test can check that no error message carries one.
TEXT = {
    "int-aaa-0001:terse_pm": "ZQXA1 AAA op inc FY24?",
    "int-aaa-0002:buried_ask": "ZQXA2 Long story, but what was AAA's operating income in FY2024?",
    "int-aaa-0001:sell_side_shorthand": "ZQXA3 AAA EBIT-ish line, op income FY24",
    "int-bbb-0001:typo_prone": "ZQXA4 waht was BBB's net margn in FY2024",
    "int-ccc-0001:conversational_retail": "ZQXA5 hey, top 2 covered companies by revenue FY2024?",
    "int-bbb-0001:terse_pm": "ZQXR1 BBB NM FY24",
    "int-vvv-0001:terse_pm": "ZQXB1 VVV op inc FY23?",
    "int-aaa-0003:conversational_retail": "ZQXB2 so what did AAA earn operating-wise in FY2024?",
    "int-vvv-0001:sell_side_shorthand": "ZQXB3 VVV FY23 op income",
    "int-vvv-0001:buried_ask": "ZQXR2 Asking for a friend: VVV CY23 op income",
}


def set_a() -> dict:
    """The dev-side set: four dev paraphrases, one persona-validation one, a review, a dup."""
    t = TEXT
    paraphrases = [
        _para("int-aaa-0001", "int-aaa", "terse_pm", t["int-aaa-0001:terse_pm"], (D, D, D), SHA_A),
        _para("int-aaa-0002", "int-aaa", "buried_ask", t["int-aaa-0002:buried_ask"], (D, D, D),
              SHA_A),
        _para("int-aaa-0001", "int-aaa", "sell_side_shorthand",
              t["int-aaa-0001:sell_side_shorthand"], (D, D, V), SHA_A),
        _para("int-bbb-0001", "int-bbb", "typo_prone", t["int-bbb-0001:typo_prone"], (D, D, D),
              SHA_A),
        _para("int-ccc-0001", "int-ccc", "conversational_retail",
              t["int-ccc-0001:conversational_retail"], (D, D, D), SHA_A),
    ]
    review = [{"paraphrase_id": "int-bbb-0001:terse_pm", "case_id": "int-bbb-0001",
               "persona": "terse_pm", "paraphrase": t["int-bbb-0001:terse_pm"],
               "reasons": ["metric_missing"], "source_sha256": SHA_A}]
    duplicates = [{"paraphrase_id": "int-ccc-0001:buried_ask", "case_id": "int-ccc-0001",
                   "persona": "buried_ask", "duplicate_of": "int-ccc-0001", "kind": "near",
                   "source_sha256": SHA_A}]
    return _set(paraphrases, review, duplicates, SHA_A, "salt-dev")


def set_b() -> dict:
    """The validation-side set: three validation paraphrases, a review, a dup."""
    t = TEXT
    paraphrases = [
        _para("int-vvv-0001", "int-vvv", "terse_pm", t["int-vvv-0001:terse_pm"], (V, D, D), SHA_B),
        _para("int-aaa-0003", "int-aaa", "conversational_retail",
              t["int-aaa-0003:conversational_retail"], (D, V, D), SHA_B),
        _para("int-vvv-0001", "int-vvv", "sell_side_shorthand",
              t["int-vvv-0001:sell_side_shorthand"], (V, D, V), SHA_B),
    ]
    review = [{"paraphrase_id": "int-vvv-0001:buried_ask", "case_id": "int-vvv-0001",
               "persona": "buried_ask", "paraphrase": t["int-vvv-0001:buried_ask"],
               "reasons": ["semantic_drift"], "source_sha256": SHA_B}]
    duplicates = [{"paraphrase_id": "int-aaa-0003:typo_prone", "case_id": "int-aaa-0003",
                   "persona": "typo_prone", "duplicate_of": "int-aaa-0003", "kind": "exact",
                   "source_sha256": SHA_B}]
    return _set(paraphrases, review, duplicates, SHA_B, "salt-val")


def _set(paraphrases: list[dict], review: list[dict], duplicates: list[dict], sha: str,
         salt: str) -> dict:
    pset = {"schema": "paraphrase-set/1", "prompt_version": "v1", "sealed": False,
            "split": {"share": 0.15, "salt": salt},
            "sources": [{"file": "reply.txt", "sha256": sha, "model": "Synthetic Model",
                         "date": "2026-11-02", "prompt_version": "v1"}],
            "paraphrases": sorted(paraphrases, key=lambda p: p["paraphrase_id"]),
            "review": review, "duplicates": duplicates}
    return seal_set(pset)


# ---------------------------------------------------------------------------- answers


def answer(value: float | None = None, rows: list[dict] | None = None) -> SystemAnswer:
    """What a system returns: the value (or rows), cited on one fact."""
    return SystemAnswer(text="synthetic answer",
                        numeric_values=[] if value is None else [value],
                        rows=copy.deepcopy(rows or []), fact_ids=[1])


def right_answer(record: dict) -> SystemAnswer:
    """The answer that matches *record*'s own key."""
    if record.get("expected_rows") is not None:
        return answer(rows=record["expected_rows"])
    return answer(value=record["expected_value"])


def wrong_answer(record: dict) -> SystemAnswer:
    """An answer that matches no case's key (scalars x 3, rows reversed with values x 3)."""
    if record.get("expected_rows") is not None:
        rows = [dict(r, value=r["value"] * 3) for r in reversed(record["expected_rows"])]
        return answer(rows=rows)
    return answer(value=record["expected_value"] * 3)


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """Fresh copies of every fixture plus the hash and answer helpers."""

    def dev_suite() -> dict:
        return make_suite("dev", dev_cases())

    def validation_suite() -> dict:
        return make_suite("validation", validation_cases())

    def suites() -> list[dict]:
        return [dev_suite(), validation_suite()]

    def sets() -> list[dict]:
        return [set_a(), set_b()]

    def source(case_id: str) -> dict[str, Any]:
        for c in dev_cases() + validation_cases():
            if c["id"] == case_id:
                return c
        raise KeyError(case_id)

    return SimpleNamespace(
        dev_suite=dev_suite, validation_suite=validation_suite, suites=suites, sets=sets,
        set_a=set_a, set_b=set_b, source=source, make_suite=make_suite,
        suite_hash=suite_hash, set_hash=set_hash, seal_suite=seal_suite, seal_set=seal_set,
        answer=answer, right_answer=right_answer, wrong_answer=wrong_answer,
        texts=dict(TEXT), personas=PERSONAS,
    )
