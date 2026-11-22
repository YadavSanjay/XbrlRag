"""C1: ``require_outside`` refuses a path inside the repository and returns any other resolved.

Oracle: requirement 1 of docs/specs/hf-suite-build.md: the root and anything under it (per
path component, after ``os.path.normcase``) are inside; a sibling whose name merely extends
the root's is outside. The paths are built in the test under a temporary directory.
"""

from __future__ import annotations

import os

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.suites.heldout_build import require_outside

PART = st.text(alphabet="abcdefghij", min_size=1, max_size=6)


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    root = tmp_path_factory.mktemp("c1")
    (root / "repo").mkdir()
    return root


@pytest.mark.criterion("C1")
def test_inside_refused_outside_returned_resolved(base):
    # oracle: requirement 1, by hand: repo, repo/a/b and repo/x/../y are inside; repo2 and
    # other/x are outside and come back resolved.
    repo = base / "repo"
    inside = [repo, repo / "a" / "b", repo / "x" / ".." / "y", str(repo) + os.sep + "c"]
    if os.name == "nt":
        inside.append(str(repo).upper() + "\\d")  # normcase: Windows paths ignore case
    for path in inside:
        with pytest.raises(ValueError):
            require_outside(path, repo)
    for path in (base / "repo2", base / "other" / "x", base / "repo2" / "repo"):
        assert require_outside(path, repo) == path.resolve()
    assert require_outside(base / "repo" / ".." / "repo2", str(repo)) == (base / "repo2").resolve()


@pytest.mark.criterion("C1")
@settings(max_examples=40)
@given(parts=st.lists(PART, min_size=1, max_size=4))
def test_any_parts_under_root_refused_elsewhere_returned(base, parts):
    # oracle: requirement 1: root/<parts> is under the root; elsewhere/<parts> never is.
    repo = base / "repo"
    with pytest.raises(ValueError):
        require_outside(repo.joinpath(*parts), repo)
    other = base / "elsewhere"
    assert require_outside(other.joinpath(*parts), repo) == other.joinpath(*parts).resolve()
