"""Spec test C12 for eval-intents: the committed v2 phrasebook and lexicon
(docs/specs/eval-intents.md)."""

from __future__ import annotations

import re

import pytest
import yaml

from src.eval.suites.intents import load_lexicon, load_phrasebook


def _paths(kit):
    base = kit.repo / "configs" / "eval" / "v2"
    return base / "phrasebook.yaml", base / "lexicon.yaml"


@pytest.mark.criterion("C12")
def test_c12_config_covers_every_shape(kit):
    # oracle: the spec's list of 14 shapes and three registers.
    pb_path, lex_path = _paths(kit)
    pb = load_phrasebook(pb_path)
    lex = load_lexicon(lex_path)
    shapes = {s for t in pb.templates for s in t.shapes}
    assert set(kit.shapes) <= shapes
    assert len(kit.shapes) == 14
    assert {t.register for t in pb.templates} == {"terse", "conversational", "formal"}
    assert lex.companies and lex.metrics


@pytest.mark.criterion("C12")
@pytest.mark.metamorphic
def test_c12_config_templates_respect_placeholder_table(kit):
    # oracle: the spec's placeholder table (kit.fill), checked against the raw YAML independently
    # of the loader; the loader's view must agree with the raw file.
    pb_path, _ = _paths(kit)
    pb = load_phrasebook(pb_path)
    raw = yaml.safe_load(pb_path.read_text(encoding="utf-8"))["templates"]
    assert [(t.id, tuple(t.shapes), t.text) for t in pb.templates] == [
        (r["id"], tuple(r["shapes"]), r["text"]) for r in raw]
    for r in raw:
        used = set(re.findall(r"\{([a-z_0-9]+)\}", r["text"]))
        for shape in r["shapes"]:
            fillable, required = kit.fill[shape]
            assert required <= used <= fillable, (r["id"], shape)
