"""Spec tests C1-C2 for paraphrase-suite-wiring: which cases a build holds, and what each
paraphrase case carries (docs/specs/paraphrase-suite-wiring.md)."""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.graders.dispatch import grade_case
from src.eval.suites.paraphrase_suite import build_paraphrase_suite

DEV_IDS = [
    "int-aaa-0001", "int-aaa-0001:terse_pm", "int-aaa-0002", "int-aaa-0002:buried_ask",
    "int-bbb-0001", "int-bbb-0001:typo_prone", "int-ccc-0001",
    "int-ccc-0001:conversational_retail",
]
VAL_IDS = [
    "int-aaa-0001:sell_side_shorthand", "int-aaa-0003", "int-aaa-0003:conversational_retail",
    "int-vvv-0001", "int-vvv-0001:sell_side_shorthand", "int-vvv-0001:terse_pm",
]
CHANGED = {"id", "question", "suite", "tags", "paraphrase"}


def _build(kit, split):
    return build_paraphrase_suite(split, kit.suites(), kit.sets())


def _by_id(doc):
    return {c["id"]: c for c in doc["cases"]}


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_c1_dev_build_cases(kit):
    # oracle: by hand from the conftest sets: the four dev template cases plus the four set-A
    # records with three dev axes; nothing of set B (all validation); ids sorted.
    doc = _build(kit, "dev")
    assert [c["id"] for c in doc["cases"]] == DEV_IDS
    assert doc["name"] == "xbrl_v2_dev_paraphrased"
    assert doc["version"] == "1"
    assert doc["split"] == "dev"
    assert doc["n_cases"] == 8
    assert doc["counts"] == {"template": 4, "paraphrase": 4}


@pytest.mark.criterion("C1")
def test_c1_validation_build_cases(kit):
    # oracle: by hand: the two validation template cases, the persona-validation paraphrase of
    # the dev case int-aaa-0001 (set A) and set B's three records.
    doc = _build(kit, "validation")
    assert [c["id"] for c in doc["cases"]] == VAL_IDS
    assert doc["name"] == "xbrl_v2_validation_paraphrased"
    assert doc["split"] == "validation"
    assert doc["n_cases"] == 6
    assert doc["counts"] == {"template": 2, "paraphrase": 4}


@pytest.mark.criterion("C1")
def test_c1_inputs_record_every_suite_and_set(kit):
    # oracle: the document rule: every given suite (sorted by name) and every given set (sorted
    # by content hash) with its selected count, counted by hand (dev: A 4, B 0; validation: A 1,
    # B 3). Hashes recomputed with hashlib in the conftest.
    dev_s, val_s = kit.dev_suite(), kit.validation_suite()
    a, b = kit.set_a(), kit.set_b()
    suites = [{"name": "xbrl_v2_dev", "split": "dev",
               "content_hash": kit.suite_hash(dev_s["cases"])},
              {"name": "xbrl_v2_validation", "split": "validation",
               "content_hash": kit.suite_hash(val_s["cases"])}]

    def sets(n_a, n_b):
        rows = [{"content_hash": kit.set_hash(a), "prompt_version": "v1", "n_selected": n_a},
                {"content_hash": kit.set_hash(b), "prompt_version": "v1", "n_selected": n_b}]
        return sorted(rows, key=lambda r: r["content_hash"])

    assert _build(kit, "dev")["inputs"] == {"suites": suites, "paraphrase_sets": sets(4, 0)}
    assert _build(kit, "validation")["inputs"] == {"suites": suites,
                                                   "paraphrase_sets": sets(1, 3)}


@pytest.mark.criterion("C1")
def test_c1_persona_validation_paraphrase_stays_out_of_dev(kit):
    # oracle: the selection rule: int-aaa-0001:sell_side_shorthand has persona axis
    # validation, so its split is validation although its source case is a dev case.
    pid = "int-aaa-0001:sell_side_shorthand"
    assert pid not in _by_id(_build(kit, "dev"))
    assert pid in _by_id(_build(kit, "validation"))


@pytest.mark.criterion("C1")
def test_c1_review_and_duplicates_never_used(kit):
    # oracle: by hand: the review and duplicate entries' ids appear in neither build.
    unused = {"int-bbb-0001:terse_pm", "int-ccc-0001:buried_ask", "int-vvv-0001:buried_ask",
              "int-aaa-0003:typo_prone"}
    ids = set(_by_id(_build(kit, "dev"))) | set(_by_id(_build(kit, "validation")))
    assert not unused & ids


@pytest.mark.criterion("C1")
@given(flags=st.lists(st.booleans(), min_size=8, max_size=8))
def test_c1_property_split_follows_axes(kit, flags):
    # oracle: the axis rule: a record's split is validation iff any axis is; a build of split s
    # holds exactly the records of split s, each once; a dev paraphrase has three dev axes.
    sets = kit.sets()
    records = [p for s in sets for p in s["paraphrases"]]
    for flag, p in zip(flags, records, strict=True):
        p["persona_axis"] = "validation" if flag else "dev"
        axes = (p["intent_axis"], p["template_axis"], p["persona_axis"])
        p["split"] = "validation" if "validation" in axes else "dev"
    for s in sets:
        kit.seal_set(s)
    by_split = {"dev": set(), "validation": set()}
    for p in records:
        by_split[p["split"]].add(p["paraphrase_id"])
    for split in ("dev", "validation"):
        doc = build_paraphrase_suite(split, kit.suites(), sets)
        ids = [c["id"] for c in doc["cases"] if ":" in c["id"]]
        assert sorted(ids) == sorted(by_split[split])
        assert len(ids) == len(set(ids))
    for p in records:
        if p["paraphrase_id"] in by_split["dev"]:
            assert (p["intent_axis"], p["template_axis"], p["persona_axis"]) == ("dev",) * 3


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_c2_paraphrase_case_is_its_source_but_five_keys(kit):
    # oracle: the case rule: a deep copy of the source record; only id, question, suite, tags
    # and the new paraphrase object differ. Sources read from the conftest data.
    for split in ("dev", "validation"):
        doc = _build(kit, split)
        for case in doc["cases"]:
            if ":" not in case["id"]:
                continue
            source = kit.source(case["id"].split(":")[0])
            assert set(case) == set(source) | {"paraphrase"}
            for k in set(source) - CHANGED:
                assert case[k] == source[k], (case["id"], k)
            assert case["question"] == kit.texts[case["id"]]
            assert case["suite"] == f"xbrl_v2_{split}_paraphrased"


@pytest.mark.criterion("C2")
def test_c2_tags_and_provenance_by_hand(kit):
    # oracle: written by hand from the tag rule: split replaced by the record's split, family
    # by paraphrase, then persona, paraphrase_of and paraphrase_set appended.
    a = kit.set_a()
    case = _by_id(_build(kit, "validation"))["int-aaa-0001:sell_side_shorthand"]
    assert case["tags"] == [
        "split:validation", "intent:int-aaa", "family:paraphrase", "template:lookup-formal-1",
        "key_source:sec_companyfacts", "key_status:verified", "shape:lookup",
        "persona:sell_side_shorthand", "paraphrase_of:int-aaa-0001",
        f"paraphrase_set:{a['content_hash']}",
    ]
    assert case["paraphrase"] == {"set": a["content_hash"],
                                  "paraphrase_id": "int-aaa-0001:sell_side_shorthand",
                                  "case_id": "int-aaa-0001", "persona": "sell_side_shorthand",
                                  "source_sha256": "a" * 64}
    assert case["paraphrase_group"] == "int-aaa"
    assert case["expected_value"] == pytest.approx(100000000.0, rel=0)  # exact copy, no maths
    screen = _by_id(_build(kit, "dev"))["int-ccc-0001:conversational_retail"]
    assert screen["tags"] == [
        "split:dev", "intent:int-ccc", "family:paraphrase", "template:top-formal-1",
        "key_source:sec_companyfacts", "key_status:verified", "shape:screen.top",
        "persona:conversational_retail", "paraphrase_of:int-ccc-0001",
        f"paraphrase_set:{a['content_hash']}",
    ]
    assert screen["expected_rows"] == kit.source("int-ccc-0001")["expected_rows"]
    assert screen["top_n"] == 2
    assert screen["key"] == kit.source("int-ccc-0001")["key"]


@pytest.mark.criterion("C2")
def test_c2_template_cases_unchanged_but_suite(kit):
    # oracle: the case rule: a template case is its record with suite set to the build's name.
    for split in ("dev", "validation"):
        doc = _build(kit, split)
        for case in doc["cases"]:
            if ":" in case["id"]:
                continue
            source = kit.source(case["id"])
            assert case == dict(source, suite=f"xbrl_v2_{split}_paraphrased")


@pytest.mark.criterion("C2")
def test_c2_paraphrase_graded_against_its_own_key(kit):
    # oracle: the source case's value (or rows) from the conftest; another case's value is a
    # different number, so the real grader must fail it.
    other = {"int-aaa": "int-bbb-0001", "int-bbb": "int-aaa-0001", "int-ccc": "int-aaa-0001",
             "int-vvv": "int-aaa-0001"}
    for split in ("dev", "validation"):
        for record in _build(kit, split)["cases"]:
            if ":" not in record["id"]:
                continue
            case = EvalCase.from_dict(record)
            source = kit.source(record["paraphrase"]["case_id"])
            assert grade_case(case, kit.right_answer(source)).passed, record["id"]
            assert not grade_case(case, kit.wrong_answer(source)).passed, record["id"]
            foreign = kit.source(other[record["paraphrase_group"]])
            assert not grade_case(case, kit.right_answer(foreign)).passed, record["id"]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_c2_metamorphic_source_value_change_moves_only_its_paraphrases(kit):
    # oracle: the case rule: a paraphrase copies its source's key, so changing int-bbb-0001's
    # value changes exactly int-bbb-0001 and its one paraphrase, nothing else.
    base = _by_id(_build(kit, "dev"))
    dev = kit.dev_suite()
    for c in dev["cases"]:
        if c["id"] == "int-bbb-0001":
            c["expected_value"] = 0.3
            c["alt_values"] = [30.0]
            c["key"]["inputs"][0]["value"] = 0.3
    kit.seal_suite(dev)
    moved = _by_id(build_paraphrase_suite("dev", [dev, kit.validation_suite()], kit.sets()))
    changed = {cid for cid in base if base[cid] != moved[cid]}
    assert changed == {"int-bbb-0001", "int-bbb-0001:typo_prone"}
    assert moved["int-bbb-0001:typo_prone"]["expected_value"] == pytest.approx(0.3, rel=0)  # copy
    assert copy.deepcopy(moved["int-bbb-0001:typo_prone"]["key"]) == moved["int-bbb-0001"]["key"]
