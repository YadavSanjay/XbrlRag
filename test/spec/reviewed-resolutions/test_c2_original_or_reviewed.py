"""C2: the original list only shrinks; any other entry needs a complete review record.

Oracle: requirement 2 of docs/specs/reviewed-resolutions.md, by hand. The original list is
key-statement-witness's locked file (one ORCL entry), read as committed.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import check_resolutions, load_resolutions, validate_review


def _check(current: list[dict[str, Any]], locked: list[dict[str, Any]]) -> int:
    """check_resolutions, a refusal reported as a test failure (requirement 2 admits it)."""
    try:
        return check_resolutions(current, locked)
    except ValueError as exc:
        pytest.fail(f"check_resolutions refused an admissible list: {exc}")


@pytest.fixture(scope="module")
def original(kit) -> list[dict[str, Any]]:
    return load_resolutions(kit.original)


@pytest.mark.criterion("C2")
def test_the_constructed_list_passes(kit, original):
    # oracle: requirement 2 — the ORCL entry equals the original; the four others are reviewed
    assert len(original) == 1
    assert _check(kit.loaded_entries(), original) == 5


@pytest.mark.criterion("C2")
def test_an_unreviewed_addition_is_refused_and_a_reviewed_one_passes(kit, original):
    # oracle: requirement 2 — an entry outside the original list passes only with a review
    added = dict(original[0], fiscal_year=2025, value=11_000_000_000)
    with pytest.raises(ValueError):
        check_resolutions([*original, added], original)
    reviewed = dict(added, review=validate_review(kit.statement_review()))
    assert _check([*original, reviewed], original) == 2


FIELDS = {"family": "Revenue", "cik": "0001341440", "fiscal_year": 2023,
          "concept": "us-gaap:DeferredRevenue", "value": 9_313_000_000, "reading": "drop",
          "reason": "another reason"}


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("field", sorted(FIELDS))
def test_an_original_changed_in_one_field_needs_a_review(kit, original, field):
    # oracle: requirement 2 — changed in any field it is no longer the original entry
    changed = dict(original[0], **{field: FIELDS[field]})
    with pytest.raises(ValueError):
        check_resolutions([changed], original)
    assert _check([dict(changed, review=kit.note_review())], original) == 1


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("drop", ["by", "date", "bucket", "evidence"])
def test_an_incomplete_review_is_refused(kit, original, drop):
    # oracle: requirement 2 — the review must pass validate_review
    entries = kit.loaded_entries()
    assert _check(entries, original) == 5
    del entries[2]["review"][drop]
    with pytest.raises(ValueError):
        check_resolutions(entries, original)


@pytest.mark.criterion("C2")
@given(keep=st.lists(st.booleans(), min_size=5, max_size=5))
def test_every_subset_passes_with_its_length(kit, original, keep):
    # oracle: requirement 2 — removing entries, the original included, is always allowed
    entries = kit.loaded_entries()
    subset = [e for e, k in zip(entries, keep, strict=True) if k]
    assert _check(subset, original) == sum(keep)
    assert _check(entries, original) == 5


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_removing_the_review_of_any_added_entry_refuses(kit, original):
    # oracle: requirement 2 — the reviewed entries are admitted by their review alone
    entries = kit.loaded_entries()
    assert _check(entries, original) == 5
    for i in range(1, 5):
        stripped = [dict(e) for e in entries]
        del stripped[i]["review"]
        with pytest.raises(ValueError):
            check_resolutions(stripped, original)
