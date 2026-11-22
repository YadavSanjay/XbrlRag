"""Spec tests C3 for paraphrase-suite-wiring: every listed input error raises, and no message
carries a question or a paraphrase (docs/specs/paraphrase-suite-wiring.md)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.paraphrase_suite import build_paraphrase_suite

A1 = "int-aaa-0001:terse_pm"


def _record(pset, pid):
    return next(p for p in pset["paraphrases"] if p["paraphrase_id"] == pid)


def _case(suite, cid):
    return next(c for c in suite["cases"] if c["id"] == cid)


def _all_texts(kit, suites, sets):
    texts = set(kit.texts.values())
    texts |= {c["question"] for s in suites for c in s.get("cases", [])}
    texts |= {p["paraphrase"] for s in sets for p in s.get("paraphrases", [])}
    return texts


# Each mutation takes (kit, suites, sets) and returns (split, suites, sets) for the call.
def _bad_split_heldout(kit, suites, sets):
    return "heldout_filers", suites, sets


def _bad_split_unknown(kit, suites, sets):
    return "train", suites, sets


def _no_suites(kit, suites, sets):
    return "dev", [], sets


def _no_sets(kit, suites, sets):
    return "dev", suites, []


def _suite_split_unknown(kit, suites, sets):
    suites[1]["split"] = "external"
    return "dev", suites, sets


def _suite_names_clash(kit, suites, sets):
    suites[1]["name"] = "xbrl_v2_dev"
    return "dev", suites, sets


def _suite_hash_wrong(kit, suites, sets):
    _case(suites[0], "int-bbb-0001")["expected_value"] = 0.5
    return "dev", suites, sets


def _case_split_tag_wrong(kit, suites, sets):
    case = _case(suites[0], "int-ccc-0001")
    case["tags"][0] = "split:validation"
    kit.seal_suite(suites[0])
    return "dev", suites, sets


def _case_id_twice(kit, suites, sets):
    twin = dict(_case(suites[0], "int-bbb-0001"))
    twin["tags"] = ["split:validation", *twin["tags"][1:]]
    suites[1]["cases"].append(twin)
    suites[1]["cases"].sort(key=lambda c: c["id"])
    kit.seal_suite(suites[1])
    return "dev", suites, sets


def _no_suite_of_split(kit, suites, sets):
    return "dev", [suites[1]], sets


def _set_schema_wrong(kit, suites, sets):
    sets[0]["schema"] = "paraphrase-set/2"
    kit.seal_set(sets[0])
    return "dev", suites, sets


def _set_hash_missing(kit, suites, sets):
    del sets[0]["content_hash"]
    return "dev", suites, sets


def _set_hash_wrong(kit, suites, sets):
    _record(sets[0], A1)["paraphrase"] = "ZQXT tampered text after the freeze"
    return "dev", suites, sets


def _sets_same_hash(kit, suites, sets):
    return "dev", suites, [sets[0], kit.set_a()]


def _paraphrase_id_in_two_sets(kit, suites, sets):
    sets[1]["paraphrases"].append(dict(_record(sets[0], A1)))
    sets[1]["paraphrases"].sort(key=lambda p: p["paraphrase_id"])
    kit.seal_set(sets[1])
    return "dev", suites, sets


def _paraphrase_id_not_case_persona(kit, suites, sets):
    _record(sets[0], A1)["persona"] = "typo_prone"
    kit.seal_set(sets[0])
    return "dev", suites, sets


def _persona_unknown(kit, suites, sets):
    rec = _record(sets[0], A1)
    rec["persona"] = "pirate"
    rec["paraphrase_id"] = "int-aaa-0001:pirate"
    kit.seal_set(sets[0])
    return "dev", suites, sets


def _axis_unknown(kit, suites, sets):
    _record(sets[0], A1)["template_axis"] = "test"
    kit.seal_set(sets[0])
    return "dev", suites, sets


def _split_disagrees_with_axes(kit, suites, sets):
    _record(sets[0], "int-aaa-0001:sell_side_shorthand")["split"] = "dev"
    kit.seal_set(sets[0])
    return "dev", suites, sets


def _validation_split_without_validation_axis(kit, suites, sets):
    _record(sets[0], A1)["split"] = "validation"
    kit.seal_set(sets[0])
    return "validation", suites, sets


def _case_in_no_suite(kit, suites, sets):
    rec = _record(sets[0], A1)
    rec["case_id"] = "int-zzz-0001"
    rec["paraphrase_id"] = "int-zzz-0001:terse_pm"
    kit.seal_set(sets[0])
    return "dev", suites, sets


def _intent_differs_from_tag(kit, suites, sets):
    _record(sets[0], A1)["intent_id"] = "int-bbb"
    kit.seal_set(sets[0])
    return "dev", suites, sets


def _intent_differs_from_group(kit, suites, sets):
    _case(suites[0], "int-aaa-0001")["paraphrase_group"] = "int-xxx"
    kit.seal_suite(suites[0])
    return "dev", suites, sets


def _dev_record_of_validation_case(kit, suites, sets):
    rec = _record(sets[1], "int-aaa-0003:conversational_retail")
    rec["template_axis"] = "dev"
    rec["split"] = "dev"
    kit.seal_set(sets[1])
    return "dev", suites, sets


MUTATIONS = [
    _bad_split_heldout, _bad_split_unknown, _no_suites, _no_sets, _suite_split_unknown,
    _suite_names_clash, _suite_hash_wrong, _case_split_tag_wrong, _case_id_twice,
    _no_suite_of_split, _set_schema_wrong, _set_hash_missing, _set_hash_wrong,
    _sets_same_hash, _paraphrase_id_in_two_sets, _paraphrase_id_not_case_persona,
    _persona_unknown, _axis_unknown, _split_disagrees_with_axes,
    _validation_split_without_validation_axis, _case_in_no_suite, _intent_differs_from_tag,
    _intent_differs_from_group, _dev_record_of_validation_case,
]


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("mutate", MUTATIONS, ids=[m.__name__.lstrip("_") for m in MUTATIONS])
def test_c3_each_error_raises_without_text(kit, mutate):
    # oracle: the spec's error list, one hand-made violation each on otherwise valid inputs;
    # the no-text rule: no question or paraphrase of the inputs appears in the message.
    split, suites, sets = mutate(kit, kit.suites(), kit.sets())
    texts = _all_texts(kit, suites, sets)
    with pytest.raises(ValueError) as err:
        build_paraphrase_suite(split, suites, sets)
    message = str(err.value)
    for text in texts:
        assert text not in message


@pytest.mark.criterion("C3")
def test_c3_valid_inputs_do_not_raise(kit):
    # oracle: the error list: the unmodified fixtures violate none of it.
    for split in ("dev", "validation"):
        assert build_paraphrase_suite(split, kit.suites(), kit.sets())["cases"]


@pytest.mark.criterion("C3")
def test_c3_other_split_records_not_checked(kit):
    # oracle: the selection rule: only records of the requested split are checked, so a broken
    # validation record (unknown persona, case in no suite) does not stop the dev build.
    sets = kit.sets()
    rec = _record(sets[1], "int-vvv-0001:terse_pm")
    rec["persona"] = "pirate"
    rec["case_id"] = "int-nowhere"
    kit.seal_set(sets[1])
    doc = build_paraphrase_suite("dev", kit.suites(), sets)
    assert doc["counts"] == {"template": 4, "paraphrase": 4}


@pytest.mark.criterion("C3")
@given(text=st.text(alphabet="QWXZJKqwxzjk", min_size=12, max_size=24))
def test_c3_property_no_text_in_errors(kit, text):
    # oracle: the no-text rule: a random distinctive paraphrase in a record whose case is in no
    # suite never appears in the error message.
    sets = kit.sets()
    rec = _record(sets[0], A1)
    rec["paraphrase"] = text
    rec["case_id"] = "int-zzz-0001"
    rec["paraphrase_id"] = "int-zzz-0001:terse_pm"
    kit.seal_set(sets[0])
    with pytest.raises(ValueError) as err:
        build_paraphrase_suite("dev", kit.suites(), sets)
    assert text not in str(err.value)
