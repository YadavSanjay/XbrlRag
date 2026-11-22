"""C5: only a name on the locked reviewer allow-list can mark a resolution reviewed.

Oracle: requirement 1 of docs/specs/reviewed-resolutions.md (maintainer decision 2026-11-09)
and the locked fixture fixtures/reviewers.yaml, which lists exactly "Sanjay".
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import (
    check_resolutions,
    load_resolutions,
    load_reviewers,
    validate_review,
)

OUTSIDE = ["sanjay", "Sanjay ", " Sanjay", "SANJAY", "maintainer", "claude", "Sanjay Yadav", ""]
# A name as a reviewer would write it: non-empty, no surrounding whitespace.
NAMES = st.text(min_size=1, max_size=16).filter(lambda s: s.strip() == s)


@pytest.mark.criterion("C5")
def test_the_locked_list_is_the_maintainer(kit):
    # oracle: the locked fixture, read by hand — one reviewer, "Sanjay"
    assert load_reviewers(kit.reviewers) == ["Sanjay"]


@pytest.mark.criterion("C5")
def test_the_live_copy_equals_the_locked_list(kit):
    # oracle: requirement 1 — configs/eval/v2/reviewers.yaml loads equal to the locked list
    assert kit.live_reviewers.exists(), "configs/eval/v2/reviewers.yaml is missing"
    assert load_reviewers(kit.live_reviewers) == load_reviewers(kit.reviewers) == ["Sanjay"]


BAD = {
    "version 2": "version: 2\nreviewers: [Sanjay]\n",
    "no version": "reviewers: [Sanjay]\n",
    "unknown field": "version: 1\nreviewers: [Sanjay]\nadmins: [x]\n",
    "missing list": "version: 1\n",
    "empty list": "version: 1\nreviewers: []\n",
    "not a list": "version: 1\nreviewers: Sanjay\n",
    "blank name": "version: 1\nreviewers: [Sanjay, '  ']\n",
    "not a string": "version: 1\nreviewers: [Sanjay, 7]\n",
    "repeated": "version: 1\nreviewers: [Sanjay, Sanjay]\n",
}


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("case", sorted(BAD))
def test_malformed_lists_are_refused(kit, tmp_path, case):
    # oracle: requirement 1 — load_reviewers' ValueError cases
    assert load_reviewers(kit.reviewers) == ["Sanjay"]
    p = tmp_path / "reviewers.yaml"
    p.write_text(BAD[case], encoding="utf-8")
    with pytest.raises(ValueError):
        load_reviewers(p)


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("name", OUTSIDE)
def test_a_name_outside_the_list_is_refused(kit, name):
    # oracle: requirement 1 — `by` must equal a listed name exactly; with no list given the
    # live list (["Sanjay"]) applies, and check_resolutions applies it too
    assert validate_review(kit.statement_review(), ["Sanjay"])["by"] == "Sanjay"
    review = dict(kit.statement_review(), by=name)
    with pytest.raises(ValueError):
        validate_review(review, ["Sanjay"])
    with pytest.raises(ValueError):
        validate_review(review)
    entries = kit.loaded_entries()
    entries[1]["review"]["by"] = name
    original = load_resolutions(kit.original)
    with pytest.raises(ValueError):
        check_resolutions(entries, original)
    with pytest.raises(ValueError):
        check_resolutions(entries, original, ["Sanjay"])


@pytest.mark.criterion("C5")
@given(listed=st.lists(NAMES, min_size=1, max_size=4, unique=True), other=NAMES)
def test_every_listed_name_is_accepted_and_no_other(kit, listed, other):
    # oracle: requirement 1 — membership of the given list decides, exactly
    for name in listed:
        got = validate_review(dict(kit.note_review(), by=name), listed)
        assert got["by"] == name
    review = dict(kit.note_review(), by=other)
    if other in listed:
        assert validate_review(review, listed)["by"] == other
    else:
        with pytest.raises(ValueError):
            validate_review(review, listed)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_widening_the_list_admits_and_narrowing_refuses(kit):
    # oracle: requirement 2 — the reviewed entries (by Sanjay) pass exactly when the list
    # given to check_resolutions holds "Sanjay"
    assert validate_review(kit.statement_review(), ["Someone", "Sanjay"])["by"] == "Sanjay"
    original = load_resolutions(kit.original)
    entries: list[dict[str, Any]] = kit.loaded_entries()
    with pytest.raises(ValueError):
        check_resolutions(entries, original, ["Someone"])
    assert check_resolutions(entries, original, ["Someone", "Sanjay"]) == 5
    assert check_resolutions(entries[:1], original, ["Someone"]) == 1  # original needs no review
