"""C9: nothing else moves — the main build is unchanged by the ratio ids in the lexicon and
by a compound build over the same sources; no compound shape enters the main shape lists;
compound files written beside the main files leave them untouched.

Oracle: the metamorphic comparison of two main builds, and the v2-suite-build spec's shape
list and quota rule (35/15/10) restated by hand.
"""

from __future__ import annotations

import pytest

import src.eval.suites.v2_suite as v2
from src.eval.suites.v2_suite import (
    GENERATED_SHAPES,
    SHAPE_CLASSES,
    build_v2_suites,
    shape_quotas,
    write_compound_suites,
    write_v2_suites,
)

MAIN_SHAPES = (
    "lookup", "compute.ratio",
    "screen.top", "screen.bottom", "screen.sum", "screen.median", "peer_relative",
    "compute.yoy_growth", "compute.difference", "compute.cagr", "trend", "point_in_time",
)


def _main(kit, lexicon):
    docs = kit.companyfacts_docs()
    return build_v2_suites(kit.inventory(), kit.universes(), kit.sec(docs), kit.store(docs),
                           kit.phrasebook_main(), lexicon,
                           quotas={s: 100 for s in MAIN_SHAPES}, per_intent=2, share=0.5,
                           salt=kit.SALT)


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
def test_main_build_unchanged_by_ratio_lexicon_and_compound_build(kit):
    # oracle: requirement 8 — the main build on the same inputs is the same with or without
    # the four ratio ids in the lexicon, and running the compound build in between changes
    # nothing in it
    before = _main(kit, kit.lexicon_main())
    compound = kit.build(share=0.5)
    assert compound["dev"]["n_cases"] + compound["validation"]["n_cases"] == 15
    assert {compound[s]["name"] for s in ("dev", "validation")} == {
        "xbrl_v2_dev_compound", "xbrl_v2_validation_compound"}
    after = _main(kit, kit.lexicon())
    assert kit.canonical(after) == kit.canonical(before)
    origins = {c["origin"] for s in ("dev", "validation") for c in after[s]["cases"]}
    assert not {o for o in origins if o.startswith("v2:compound")}


@pytest.mark.criterion("C9")
def test_shape_lists_and_quotas_unchanged(kit):
    # oracle: the v2-suite-build spec's 12 generated shapes and its quota rule: 60 = 35/15/10
    # by class -> lookup 18, ratio 17; five cross-filer shapes 3 each; five cross-period 2 each
    assert GENERATED_SHAPES == MAIN_SHAPES
    assert [shapes for _c, _w, shapes in SHAPE_CLASSES] == [
        MAIN_SHAPES[:2], MAIN_SHAPES[2:7], MAIN_SHAPES[7:]]
    q = shape_quotas(60)
    assert q == {"lookup": 18, "compute.ratio": 17, "screen.top": 3, "screen.bottom": 3,
                 "screen.sum": 3, "screen.median": 3, "peer_relative": 3,
                 "compute.yoy_growth": 2, "compute.difference": 2, "compute.cagr": 2,
                 "trend": 2, "point_in_time": 2}
    compound_shapes = getattr(v2, "COMPOUND_SHAPES", None)
    assert compound_shapes == ("compound.rank_by_ratio", "compound.leader_growth")
    assert not set(compound_shapes) & (set(GENERATED_SHAPES) | set(q))


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
def test_compound_files_leave_the_main_files_untouched(kit, tmp_path):
    # oracle: requirement 7-8 — the compound writer writes its four files beside the main
    # four into the same directory and changes none of the main files' bytes
    main_paths = write_v2_suites(_main(kit, kit.lexicon()), tmp_path)
    main_bytes = {k: p.read_bytes() for k, p in main_paths.items()}
    write_compound_suites(kit.build(share=0.5), tmp_path)
    assert {k: p.read_bytes() for k, p in main_paths.items()} == main_bytes
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([
        "xbrl_v2_dev.json", "xbrl_v2_validation.json", "xbrl_v2_manifest.json",
        "intents.json", "xbrl_v2_dev_compound.json", "xbrl_v2_validation_compound.json",
        "xbrl_v2_compound_manifest.json", "intents_compound.json"])
