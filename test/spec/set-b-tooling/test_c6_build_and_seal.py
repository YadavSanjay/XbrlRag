"""C6: ``build_suite``, ``canonical_bytes`` and ``sha256_hex``.

Oracle: requirements 9 and 10 of docs/specs/set-b-tooling.md and the FIPS 180-2 SHA-256
examples. Every expected ``EvalCase`` dict is written out by hand from requirement 9; the
resolutions are hand-built in ``resolve_case``'s output shape (requirement 7).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.core import build_suite, canonical_bytes, sha256_hex

M = 1e6
NOTE = "Pxw private note that must never be copied 4417"

_NUMERIC = ("expected_value", "rel_tol")


def _cases(kit):
    """One authoring case per kind (plus a second compute without %), all synthetic."""
    lookup = kit.lookup("L1", ticker="MSFT", fy=2025, expected="245,122", scale="millions",
                        unit="USD", concept="Revenue", note=NOTE,
                        question="Synthetic held-out lookup wording L1?")
    dim = kit.dimensional("D1", ticker="NVDA", fy=2024, expected="1,234.5", scale="millions",
                          unit="USD", member="Data Center", note=NOTE,
                          question="Synthetic held-out dimensional wording D1?")
    yoy = kit.compute("C1", ticker="MSFT", fy=2025, formula="yoy", expected="12.5%",
                      inputs=[kit.inp("current", "1,125"), kit.inp("prior", "1,000", fy=2024)],
                      note=NOTE, question="Synthetic held-out yoy wording C1?")
    ratio = kit.compute("C2", ticker="AVGO", fy=2026, formula="ratio", expected="2.5",
                        inputs=[kit.inp("numerator", "50", unit="USD"),
                                kit.inp("denominator", "20", unit="shares")],
                        note=NOTE, question="Synthetic held-out ratio wording C2?")
    bd = kit.breakdown("B1", ticker="AMD", fy=2025, unit="USD",
                       rows=[{"member": "Gamma", "expected": "5", "scale": "millions"},
                             {"member": "Alpha", "expected": "7", "scale": "millions"}],
                       note=NOTE, question="Synthetic held-out breakdown wording B1?")
    return [lookup, dim, yoy, ratio, bd]


def _res(cid, classes, status="verified", inputs=None):
    return {"id": cid, "status": status, "inputs": inputs or [], "classes": classes,
            "candidates_hint": []}


def _resolutions():
    return [
        _res("L1", [[11, 14]]),
        _res("D1", [[31]]),
        _res("C1", [[51, 52], [61]]),
        _res("C2", [[71], [72, 73]]),
        _res("B1", [[91], [92, 93]]),
    ]


LABELS = {31: "Data Center", 91: "Gamma Segment", 92: "Alpha Segment",
          93: "Alpha Segment (restated)"}

_COMMON = {"suite": "heldout_b_v1", "origin": "handwritten:set_b"}

# The hand-written EvalCase dicts (requirement 9).
WANT = {
    "b-L1": {"id": "b-L1", "question": "Synthetic held-out lookup wording L1?",
             "kind": "lookup", "ticker": "MSFT", "fy": 2025, **_COMMON,
             "tags": ["set-b", "lookup"],
             "expected_value": 245122 * M, "unit": "USD",          # "245,122" x 1e6
             "expected_fact_ids": [11], "fact_id_classes": [[11, 14]], "rel_tol": 1e-4},
    "b-D1": {"id": "b-D1", "question": "Synthetic held-out dimensional wording D1?",
             "kind": "dimensional", "ticker": "NVDA", "fy": 2024, **_COMMON,
             "tags": ["set-b", "dimensional"],
             "expected_value": 1234.5 * M, "unit": "USD",          # "1,234.5" x 1e6
             "expected_fact_ids": [31], "fact_id_classes": [[31]], "rel_tol": 1e-4},
    "b-C1": {"id": "b-C1", "question": "Synthetic held-out yoy wording C1?",
             "kind": "compute", "ticker": "MSFT", "fy": 2025, **_COMMON,
             "tags": ["set-b", "compute"],
             "expected_value": 0.125, "alt_values": [12.5],         # "12.5%": 0.125, x100
             "expected_fact_ids": [51, 61], "fact_id_classes": [[51, 52], [61]],
             "rel_tol": 1e-2, "unit": "USD"},
    "b-C2": {"id": "b-C2", "question": "Synthetic held-out ratio wording C2?",
             "kind": "compute", "ticker": "AVGO", "fy": 2026, **_COMMON,
             "tags": ["set-b", "compute"],
             "expected_value": 2.5, "alt_values": [],               # no %, no alt
             "expected_fact_ids": [71, 72], "fact_id_classes": [[71], [72, 73]],
             "rel_tol": 1e-2, "unit": "USD"},                       # the FIRST input's unit
    "b-B1": {"id": "b-B1", "question": "Synthetic held-out breakdown wording B1?",
             "kind": "breakdown", "ticker": "AMD", "fy": 2025, **_COMMON,
             "tags": ["set-b", "breakdown"],
             "expected_rows": [{"entity": "Gamma Segment", "value": 5 * M},
                               {"entity": "Alpha Segment", "value": 7 * M}],
             "order_sensitive": False,
             "expected_fact_ids": [91, 92], "fact_id_classes": [[91], [92, 93]],
             "rel_tol": 1e-4},
}


def _assert_case(got, want):
    assert sorted(got) == sorted(want)
    for k, v in want.items():
        if k in _NUMERIC:
            # tolerance: the values are short exact decimals; rel=1e-12 is float idiom
            assert got[k] == pytest.approx(v, rel=1e-12)
        elif k == "alt_values":
            # tolerance: as above
            assert got[k] == pytest.approx(v, rel=1e-12)
        elif k == "expected_rows":
            assert [r["entity"] for r in got[k]] == [r["entity"] for r in v]
            assert [sorted(r) for r in got[k]] == [["entity", "value"]] * len(v)
            # tolerance: as above
            assert [r["value"] for r in got[k]] == pytest.approx([r["value"] for r in v],
                                                                 rel=1e-12)
        else:
            assert got[k] == v, k


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("cid", ["b-L1", "b-D1", "b-C1", "b-C2", "b-B1"])
def test_exact_eval_case_per_kind(kit, cid):
    # oracle: requirement 9 — each EvalCase dict is written out by hand in WANT
    suite = build_suite(_cases(kit), _resolutions(), dict(LABELS))
    by_id = {c["id"]: c for c in suite["cases"]}
    assert cid in by_id
    _assert_case(by_id[cid], WANT[cid])


@pytest.mark.criterion("C6")
def test_suite_header_order_and_no_note(kit):
    # oracle: requirement 9 — name heldout_b, version v1, n_cases 5, cases sorted by id
    # (B1 < C1 < C2 < D1 < L1); `note` is never copied, so neither the key nor its text
    # appears anywhere in the canonical bytes
    cases = list(reversed(_cases(kit)))
    res = list(reversed(_resolutions()))
    suite = build_suite(cases, res, dict(LABELS))
    assert suite["name"] == "heldout_b"
    assert suite["version"] == "v1"
    assert suite["n_cases"] == 5
    assert [c["id"] for c in suite["cases"]] == ["b-B1", "b-C1", "b-C2", "b-D1", "b-L1"]
    assert all("note" not in c for c in suite["cases"])
    data = canonical_bytes(suite)
    assert NOTE.encode("utf-8") not in data
    assert b"Pxw" not in data
    assert b"Synthetic held-out lookup wording L1?" in data


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("status", ["ambiguous", "not_found", "arith_mismatch"])
def test_unverified_case_raises_or_is_excluded(kit, status):
    # oracle: requirement 9 — any non-verified case raises ValueError listing its id;
    # with allow_unverified it is left out and listed in "excluded"; n_cases counts the rest
    res = _resolutions()
    res[3] = _res("C2", [], status=status)
    with pytest.raises(ValueError) as err:
        build_suite(_cases(kit), res, dict(LABELS))
    assert "C2" in str(err.value)
    suite = build_suite(_cases(kit), res, dict(LABELS), allow_unverified=True)
    assert suite["n_cases"] == 4
    assert [c["id"] for c in suite["cases"]] == ["b-B1", "b-C1", "b-D1", "b-L1"]
    assert suite["excluded"] == ["C2"]


@pytest.mark.criterion("C6")
def test_canonical_bytes_hand_written():
    # oracle: requirement 10 — json.dumps(sort_keys=True, indent=2, ensure_ascii=False)
    # + "\n" as UTF-8, written out by hand: keys sorted, two-space indent, é kept raw
    suite = {"b": 1, "a": ["é", 2.5]}
    want = '{\n  "a": [\n    "é",\n    2.5\n  ],\n  "b": 1\n}\n'.encode()
    assert canonical_bytes(suite) == want
    assert canonical_bytes({"b": 1, "a": ["é", 2.5]}) == canonical_bytes(suite)


@pytest.mark.criterion("C6")
def test_sha256_fips_180_2_examples():
    # oracle: FIPS 180-2 appendix B examples — "abc" (B.1, also quoted in the spec) and
    # the 448-bit two-block message (B.2)
    assert sha256_hex(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    assert sha256_hex(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq") == (
        "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1")


@pytest.mark.criterion("C6")
@given(perm=st.permutations(list(range(5))))
def test_shuffled_inputs_give_identical_bytes(kit, perm):
    # oracle: requirements 9 and 10 — cases are sorted by id and the bytes are canonical,
    # so the input order (cases with their resolutions) cannot change the bytes or the hash;
    # the reference build is pinned to n_cases 5
    ref = build_suite(_cases(kit), _resolutions(), dict(LABELS))
    assert ref["n_cases"] == 5
    cases, res = _cases(kit), _resolutions()
    shuffled = build_suite([cases[i] for i in perm], [res[i] for i in perm], dict(LABELS))
    assert canonical_bytes(shuffled) == canonical_bytes(ref)
    assert sha256_hex(canonical_bytes(shuffled)) == sha256_hex(canonical_bytes(ref))
    assert len(sha256_hex(canonical_bytes(ref))) == 64
