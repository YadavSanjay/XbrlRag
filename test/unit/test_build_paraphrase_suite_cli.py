"""Unit tests for scripts/eval/build_paraphrase_suite.py (synthetic inputs, tmp dirs).

The spec tests (test/spec/paraphrase-suite-wiring/) cover src/eval/suites/paraphrase_suite.py;
these cover the CLI: it writes the build, refuses to overwrite without --force, prints counts
and hashes but never a question or a paraphrase, and exits 1 on bad or missing inputs.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from src.eval.core.case import EvalSuite
from src.eval.suites.paraphrases import canonical_bytes

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "build_paraphrase_suite_cli", REPO / "scripts" / "eval" / "build_paraphrase_suite.py")
assert _spec is not None and _spec.loader is not None
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

QUESTIONS = {"int-aaa-0001": "QQQ1 What was AAA's revenue in fiscal 2024?",
             "int-vvv-0001": "QQQ2 What was VVV's revenue in fiscal 2023?"}
PARAPHRASES = {"int-aaa-0001:terse_pm": "PPP1 AAA rev FY24",
               "int-aaa-0001:typo_prone": "PPP2 waht was AAA revnue FY2024",
               "int-vvv-0001:terse_pm": "PPP3 VVV rev FY23"}


def _hash(cases: list[dict]) -> str:
    text = json.dumps(cases, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _case(cid: str, split: str, value: float) -> dict:
    intent = cid.rsplit("-", 1)[0]
    return {"id": cid, "question": QUESTIONS[cid], "kind": "lookup", "suite": f"xbrl_v2_{split}",
            "expected_value": value, "paraphrase_group": intent, "rel_tol": 0.0001,
            "tags": [f"split:{split}", f"intent:{intent}", "family:formal"]}


def _suite(split: str, cases: list[dict]) -> dict:
    return {"name": f"xbrl_v2_{split}", "version": "1", "split": split,
            "n_cases": len(cases), "content_hash": _hash(cases), "cases": cases}


def _record(pid: str, axes: tuple[str, str, str]) -> dict:
    cid, persona = pid.split(":")
    return {"paraphrase_id": pid, "case_id": cid, "intent_id": cid.rsplit("-", 1)[0],
            "persona": persona, "paraphrase": PARAPHRASES[pid], "source_sha256": "c" * 64,
            "intent_axis": axes[0], "template_axis": axes[1], "persona_axis": axes[2],
            "split": "validation" if "validation" in axes else "dev"}


def _set() -> dict:
    records = [_record("int-aaa-0001:terse_pm", ("dev", "dev", "dev")),
               _record("int-aaa-0001:typo_prone", ("dev", "dev", "validation")),
               _record("int-vvv-0001:terse_pm", ("validation", "dev", "dev"))]
    pset = {"schema": "paraphrase-set/1", "prompt_version": "v1", "sealed": True,
            "paraphrases": records, "review": [], "duplicates": []}
    pset["content_hash"] = hashlib.sha256(canonical_bytes(pset)).hexdigest()
    return pset


@pytest.fixture
def inputs(tmp_path: Path) -> list[str]:
    dev = tmp_path / "dev.json"
    val = tmp_path / "val.json"
    pset = tmp_path / "set.json"
    dev.write_text(json.dumps(_suite("dev", [_case("int-aaa-0001", "dev", 10.0)])),
                   encoding="utf-8")
    val.write_text(json.dumps(_suite("validation", [_case("int-vvv-0001", "validation", 7.0)])),
                   encoding="utf-8")
    pset.write_bytes(canonical_bytes(_set()))
    return ["--suite", str(dev), "--suite", str(val), "--paraphrases", str(pset)]


def _no_text(output: str) -> None:
    for text in [*QUESTIONS.values(), *PARAPHRASES.values()]:
        assert text not in output
    assert "QQQ" not in output and "PPP" not in output


def test_writes_build_and_prints_counts_and_hash_only(inputs, tmp_path, capsys) -> None:
    out = tmp_path / "out" / "validation_paraphrased.json"
    assert cli.main(["--split", "validation", *inputs, "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    doc = json.loads(out.read_text(encoding="utf-8"))
    # by hand: one validation template + the persona-validation paraphrase of the dev case
    # + the validation case's paraphrase.
    assert doc["counts"] == {"template": 1, "paraphrase": 2}
    assert [c["id"] for c in doc["cases"]] == [
        "int-aaa-0001:typo_prone", "int-vvv-0001", "int-vvv-0001:terse_pm"]
    assert doc["content_hash"] in printed
    assert "3 cases, 1 template + 2 paraphrase" in printed
    assert "2 selected" in printed
    _no_text(printed)
    assert [c.id for c in EvalSuite.load(str(out)).cases] == [c["id"] for c in doc["cases"]]


def test_refuses_to_overwrite_without_force(inputs, tmp_path, capsys) -> None:
    out = tmp_path / "dev_paraphrased.json"
    out.write_text("keep me", encoding="utf-8")
    assert cli.main(["--split", "dev", *inputs, "--out", str(out)]) == 1
    assert "refusing to overwrite" in capsys.readouterr().out
    assert out.read_text(encoding="utf-8") == "keep me"
    assert cli.main(["--split", "dev", *inputs, "--out", str(out), "--force"]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["counts"] == {"template": 1,
                                                                    "paraphrase": 1}


def test_build_error_exits_1_without_text(inputs, tmp_path, capsys) -> None:
    # Only the dev suite is given, so no suite has the requested validation split.
    out = tmp_path / "x.json"
    argv = ["--split", "validation", *inputs[:2], *inputs[4:], "--out", str(out)]
    assert cli.main(argv) == 1
    printed = capsys.readouterr().out
    assert printed.startswith("error:")
    _no_text(printed)
    assert not out.exists()


def test_tampered_set_exits_1_without_text(inputs, tmp_path, capsys) -> None:
    pset = Path(inputs[-1])
    body = json.loads(pset.read_text(encoding="utf-8"))
    body["paraphrases"][0]["paraphrase"] = "PPP9 edited after the freeze"
    pset.write_text(json.dumps(body), encoding="utf-8")
    out = tmp_path / "x.json"
    assert cli.main(["--split", "dev", *inputs, "--out", str(out)]) == 1
    printed = capsys.readouterr().out
    assert "content_hash" in printed
    _no_text(printed)
    assert "PPP9" not in printed
    assert not out.exists()


def test_missing_or_non_json_input_exits_1(inputs, tmp_path, capsys) -> None:
    out = tmp_path / "x.json"
    missing = ["--suite", str(tmp_path / "nope.json"), *inputs[2:]]
    assert cli.main(["--split", "dev", *missing, "--out", str(out)]) == 1
    assert "no such file" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert cli.main(["--split", "dev", "--suite", str(bad), *inputs[2:], "--out", str(out)]) == 1
    assert "bad.json: not a JSON suite" in capsys.readouterr().out
    assert not out.exists()


def test_unknown_split_is_a_usage_error(inputs, tmp_path) -> None:
    with pytest.raises(SystemExit) as err:
        cli.main(["--split", "heldout_filers", *inputs, "--out", str(tmp_path / "x.json")])
    assert err.value.code == 2
