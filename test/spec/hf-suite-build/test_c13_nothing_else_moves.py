"""C13: the development builds do not move.

Oracle: requirement 13 of docs/specs/hf-suite-build.md and the v2-suite-build and
compound-shape specs (suite and file names, split tags); the metamorphic comparisons of a
development build with and without an overlay of absent filers, and before and after
held-out builds. (The retired v2-suite-build and compound-shape spec tests are the rest of
the byte-identity guarantee; ``pytest test/spec`` runs them.)
"""

from __future__ import annotations

import pytest

from src.eval.suites.heldout_build import merge_lexicon_overlay
from src.eval.suites.v2_sources import restrict_to_lexicon
from src.eval.suites.v2_suite import write_compound_suites, write_v2_suites

V2_FILES = ["intents.json", "xbrl_v2_dev.json", "xbrl_v2_manifest.json",
            "xbrl_v2_validation.json"]
COMPOUND_FILES = ["intents_compound.json", "xbrl_v2_compound_manifest.json",
                  "xbrl_v2_dev_compound.json", "xbrl_v2_validation_compound.json"]


def _dev_inputs(kit, lexicon):
    """The inventory a development build sees with ``lexicon``, and its universes."""
    inv, _left = restrict_to_lexicon(kit.inventory(), lexicon)
    return inv, {"covered": sorted(inv), "pair": ["DEVA", "DEVC"]}


def _write_dev(kit, lexicon, out):
    inv, uni = _dev_inputs(kit, lexicon)
    write_v2_suites(kit.dev_build(inv=inv, uni=uni, lexicon=lexicon, share=0.5), out)
    write_compound_suites(kit.dev_compound(inv=inv, uni=uni, lexicon=lexicon, share=0.5), out)
    return {p.name: p.read_bytes() for p in sorted(out.iterdir())}


@pytest.mark.criterion("C13")
def test_dev_names_and_tags_after_heldout_builds(kit, tmp_path):
    # oracle: v2-suite-build requirements 13 and 16, compound-shape requirement 7; and
    # requirement 13 here: restrict_to_lexicon with the merged lexicon keeps the held-out
    # filers and the other entries as the base lexicon does.
    kit.hf_build(["DEVD"])
    kit.hf_compound(["DEVD"])
    files = _write_dev(kit, kit.lexicon_full, tmp_path)
    assert sorted(files) == sorted(V2_FILES + COMPOUND_FILES)
    dev = kit.dev_build(share=0.5)
    assert (dev["dev"]["name"], dev["validation"]["name"]) == ("xbrl_v2_dev", "xbrl_v2_validation")
    assert {c["tags"][0] for c in dev["dev"]["cases"]} == {"split:dev"}
    assert {c["tags"][0] for c in dev["validation"]["cases"]} == {"split:validation"}

    base = kit.lexicon_base()
    merged = merge_lexicon_overlay(base, kit.HERE / "overlay.yaml")
    kept_base, left_base = restrict_to_lexicon(kit.inventory(), base)
    kept_merged, left_merged = restrict_to_lexicon(kit.inventory(), merged)
    assert left_base["tickers"] == ["DEVB", "DEVD"]
    assert left_merged["tickers"] == []
    assert sorted(kept_merged) == ["DEVA", "DEVB", "DEVC", "DEVD", "DEVE"]
    assert {t: kept_merged[t] for t in kept_base} == kept_base


@pytest.mark.criterion("C13")
@pytest.mark.metamorphic
def test_dev_bytes_unchanged_by_an_absent_overlay_and_by_heldout_builds(kit, tmp_path):
    # oracle: requirement 13: an overlay of filers outside the inventory, and held-out builds
    # run before, leave the development files byte-identical.
    base = kit.lexicon_base()
    plain = _write_dev(kit, base, tmp_path / "base")
    merged = merge_lexicon_overlay(base, kit.HERE / "overlay_absent.yaml")
    assert "DEVX" in merged.companies
    assert _write_dev(kit, merged, tmp_path / "merged") == plain

    before = _write_dev(kit, kit.lexicon_full, tmp_path / "before")
    kit.hf_build(["DEVB", "DEVD"])
    kit.hf_compound(["DEVB", "DEVD"])
    assert _write_dev(kit, kit.lexicon_full, tmp_path / "after") == before
    assert sorted(plain) == sorted(V2_FILES + COMPOUND_FILES)
