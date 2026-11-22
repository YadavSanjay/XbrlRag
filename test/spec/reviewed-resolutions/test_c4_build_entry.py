"""C4: the build's entry point admits reviewed additions and refuses unreviewed ones early.

Oracle: requirement 2 of docs/specs/reviewed-resolutions.md: ``KeyWitness.from_paths`` checks
the live list against the pinned copy of the original list before any FSDS row is read.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import validate_review
from src.eval.suites.v2_witness import KeyWitness


def _from_paths(kit, path) -> KeyWitness:
    """from_paths over the empty quarter, a refusal reported as a test failure."""
    try:
        return KeyWitness.from_paths([kit.empty_quarter], resolutions=path)
    except ValueError as exc:
        pytest.fail(f"the build refused an admissible resolution list: {exc}")


def _write(path, entries: list[dict[str, Any]]) -> None:
    path.write_text(yaml.safe_dump({"version": 1, "resolutions": entries}, sort_keys=False),
                    encoding="utf-8")


@pytest.mark.criterion("C4")
def test_the_reviewed_list_is_admitted(kit):
    # oracle: requirement 2 — the original entry plus four reviewed ones pass, loaded with
    # the normalized review (requirement 1)
    w = _from_paths(kit, kit.reviewed)
    assert w.resolutions == kit.loaded_entries()


@pytest.mark.criterion("C4")
def test_an_unreviewed_addition_still_fails_before_fsds(kit, tmp_path):
    # oracle: requirement 2 — refused before any FSDS directory is read (it does not exist);
    # the same entry with a complete review is admitted
    unreviewed = kit.raw_entries()[4]
    del unreviewed["review"]
    p = tmp_path / "unreviewed.yaml"
    _write(p, [unreviewed])
    with pytest.raises(ValueError):
        KeyWitness.from_paths([tmp_path / "no_fsds"], resolutions=p)
    reviewed = dict(unreviewed, review=validate_review(kit.statement_review()))
    q = tmp_path / "reviewed.yaml"
    _write(q, [reviewed])
    assert _from_paths(kit, q).resolutions[0]["review"]["date"] == "2026-11-10"


@pytest.mark.criterion("C4")
@given(keep=st.lists(st.booleans(), min_size=5, max_size=5))
def test_any_subset_is_admitted(kit, tmp_path_factory, keep):
    # oracle: requirement 2 — shrinking is always allowed; the loaded entries are the subset
    raw = [e for e, k in zip(kit.raw_entries(), keep, strict=True) if k]
    expected = [e for e, k in zip(kit.loaded_entries(), keep, strict=True) if k]
    p = tmp_path_factory.mktemp("subset") / "key_resolutions.yaml"
    for e in raw:
        if "review" in e:
            e["review"]["date"] = str(e["review"]["date"])
    _write(p, raw)
    assert _from_paths(kit, kit.reviewed).resolutions == kit.loaded_entries()
    assert _from_paths(kit, p).resolutions == expected
