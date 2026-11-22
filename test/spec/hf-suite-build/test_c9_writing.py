"""C9: the held-out writers write exactly their three files, in the main writer's format.

Oracle: requirement 8 of docs/specs/hf-suite-build.md; the format restated in conftest
(``file_text``: v2-suite-build requirement 16).
"""

from __future__ import annotations

import pytest

from src.eval.core.case import EvalSuite
from src.eval.suites.heldout_build import write_heldout_compound_suites, write_heldout_suites

MAIN = {"heldout_filers": "heldout_hf.json", "manifest": "heldout_hf_manifest.json",
        "intents": "heldout_hf_intents.json"}
COMPOUND = {"heldout_filers": "heldout_hf_compound.json",
            "manifest": "heldout_hf_compound_manifest.json",
            "intents": "heldout_hf_compound_intents.json"}


@pytest.mark.criterion("C9")
def test_files_names_and_bytes(kit, tmp_path):
    # oracle: requirement 8, the format restated in conftest.
    main = kit.hf_build(["DEVD"])
    compound = kit.hf_compound(["DEVD"])
    paths = write_heldout_suites(main, tmp_path)
    assert {k: p.name for k, p in paths.items()} == MAIN
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(MAIN.values())
    cpaths = write_heldout_compound_suites(compound, tmp_path)
    assert {k: p.name for k, p in cpaths.items()} == COMPOUND
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == sorted([*MAIN.values(), *COMPOUND.values()])
    assert not any(n.startswith("xbrl_v2_") for n in names)
    for build, written in ((main, paths), (compound, cpaths)):
        for key, path in written.items():
            assert path.parent == tmp_path
            assert path.read_bytes() == kit.file_text(build[key])
        loaded = EvalSuite.load(str(written["heldout_filers"]))
        assert len(loaded.cases) == build["heldout_filers"]["n_cases"] > 0


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
def test_two_builds_write_identical_bytes(kit, tmp_path):
    # oracle: requirement 6.6: nothing depends on the process or the clock.
    one, two = tmp_path / "one", tmp_path / "two"
    write_heldout_suites(kit.hf_build(["DEVB", "DEVD"]), one)
    write_heldout_suites(kit.hf_build(["DEVD", "DEVB"]), two)
    write_heldout_compound_suites(kit.hf_compound(["DEVB", "DEVD"]), one)
    write_heldout_compound_suites(kit.hf_compound(["DEVD", "DEVB"]), two)
    files = sorted(p.name for p in one.iterdir())
    assert files == sorted([*MAIN.values(), *COMPOUND.values()])
    for name in files:
        assert (one / name).read_bytes() == (two / name).read_bytes()
