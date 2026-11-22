"""C7: ``render_check_report`` — one line per case plus status counts, nothing leaked.

Oracle: requirement 8 of docs/specs/set-b-tooling.md. Resolutions are hand-built in
``resolve_case``'s output shape (requirement 7). To prove the report never echoes a
question, note or amount, each resolution also carries those as extra keys with
distinctive strings (as a careless upstream might pass them along).
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.core import render_check_report


def _res(cid, status, classes, inputs=None, hint=None, **leak):
    out = {"id": cid, "status": status, "inputs": inputs or [], "classes": classes,
           "candidates_hint": hint or []}
    out.update(leak)
    return out


def _mixed():
    """Six cases: 3 verified, 1 ambiguous, 1 not_found, 1 arith_mismatch."""
    return [
        _res("alpha", "verified", [[11]]),
        _res("bravo", "verified", [[21], [22]],
             inputs=[{"name": "current", "status": "verified", "classes": [[21]]},
                     {"name": "prior", "status": "verified", "classes": [[22]]}]),
        _res("charlie", "ambiguous", [[31], [32], [33]],
             hint=[{"concept": "OperatingIncomeLoss", "member_label": None},
                   {"concept": "NetIncomeLoss", "member_label": None},
                   {"concept": "ComprehensiveIncome", "member_label": None}]),
        _res("delta", "not_found", [[41], []],
             inputs=[{"name": "current", "status": "verified", "classes": [[41]]},
                     {"name": "priorxq", "status": "not_found", "classes": []}]),
        _res("echo", "arith_mismatch", [[51], [52]],
             inputs=[{"name": "numerator", "status": "verified", "classes": [[51]]},
                     {"name": "denominator", "status": "verified", "classes": [[52]]}]),
        _res("foxtrot", "verified", [[61], [62], [63], [64]]),
    ]


def _case_line(report, cid):
    return [ln for ln in report.splitlines() if re.search(rf"\b{cid}\b", ln)]


@pytest.mark.criterion("C7")
def test_one_line_per_case_with_status_and_class_count(kit):
    # oracle: requirement 8 — each case has a line with its id, its status and its number
    # of classes (alpha 1, bravo 2, charlie 3, delta 2, echo 2, foxtrot 4)
    report = render_check_report(_mixed())
    want = {"alpha": ("verified", 1), "bravo": ("verified", 2), "charlie": ("ambiguous", 3),
            "delta": ("not_found", 2), "echo": ("arith_mismatch", 2), "foxtrot": ("verified", 4)}
    for cid, (status, n) in want.items():
        lines = [ln for ln in _case_line(report, cid)
                 if re.search(rf"\b{status}\b", ln) and re.search(rf"\b{n}\b", ln)]
        assert lines, f"no line for {cid}: {report!r}"
    for cid in ("alpha", "bravo", "foxtrot"):
        assert len(_case_line(report, cid)) == 1


@pytest.mark.criterion("C7")
def test_status_counts(kit):
    # oracle: requirement 8 — counts per status, by hand from _mixed: verified 3,
    # ambiguous 1, not_found 1, arith_mismatch 1; the count lines carry no case id
    report = render_check_report(_mixed())
    ids = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")
    summary = [ln for ln in report.splitlines() if not any(re.search(rf"\b{i}\b", ln)
                                                          for i in ids)]
    text = "\n".join(summary)
    for status, n in (("verified", 3), ("ambiguous", 1), ("not_found", 1),
                      ("arith_mismatch", 1)):
        assert re.search(rf"\b{status}\b\W*{n}\b", text) or re.search(
            rf"\b{n}\b\W*{status}\b", text), f"{status} {n} missing: {text!r}"


@pytest.mark.criterion("C7")
def test_non_verified_cases_show_input_name_and_hint(kit):
    # oracle: requirement 8 — a non-verified case adds the input name (delta's "priorxq")
    # and the candidates_hint (charlie's three concept local names)
    report = render_check_report(_mixed())
    assert "priorxq" in report
    for local in ("OperatingIncomeLoss", "NetIncomeLoss", "ComprehensiveIncome"):
        assert local in report


@pytest.mark.criterion("C7")
def test_no_question_note_or_amount_leaks(kit):
    # oracle: requirement 8 — the report never contains a question, a note or an expected
    # amount; the leaked keys below carry distinctive strings that must not appear
    leaks = []
    for i, r in enumerate(_mixed()):
        q, n, a = f"Qvzx question text {i}?", f"Nqwy note text {i}", f"98765432{i}"
        r.update({"question": q, "note": n, "expected": a, "expected_value": float(a)})
        leaks.append(r)
    report = render_check_report(leaks)
    assert "alpha" in report
    assert "charlie" in report
    for bad in ("Qvzx", "Nqwy", "question text", "note text", "98765432"):
        assert bad not in report


_ALPHABET = "ÆØÅæøåßÞþðÐ"


@pytest.mark.criterion("C7")
@given(questions=st.lists(st.text(alphabet=_ALPHABET, min_size=3, max_size=30),
                          min_size=6, max_size=6))
def test_random_questions_never_appear(kit, questions):
    # oracle: requirement 8 — no question appears in the report, whatever its text; the
    # questions use characters no id, status, hint or count contains, and each is
    # wrapped in a marker so even a short one is distinctive
    rs = _mixed()
    for r, q in zip(rs, questions, strict=True):
        r["question"] = f"§{q}§"
    report = render_check_report(rs)
    assert "foxtrot" in report
    for r in rs:
        assert r["question"] not in report
    assert not any(ch in report for ch in _ALPHABET + "§")
