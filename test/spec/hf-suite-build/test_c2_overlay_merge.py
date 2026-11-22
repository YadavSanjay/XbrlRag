"""C2: ``merge_lexicon_overlay`` adds the held-out filers in memory and never changes a base one.

Oracle: requirement 2 of docs/specs/hf-suite-build.md. ``lexicon_full.yaml`` is written by
hand as ``lexicon_base.yaml`` plus ``overlay.yaml``'s two filers and loaded with the existing
``load_lexicon``; the refused overlays are written in the test.
"""

from __future__ import annotations

import copy
import datetime as dt

import pytest

from src.eval.suites.heldout_build import merge_lexicon_overlay
from src.eval.suites.intents import load_lexicon

BAD_OVERLAYS = {
    # another top-level key
    "metrics": ("companies:\n  DEVQ: {short: Devq, legal: Devq Inc.}\n"
                "metrics:\n  Revenue: {label: revenue}\n", "DEVQ"),
    "universes": ("companies:\n  DEVQ: {short: Devq, legal: Devq Inc.}\n"
                  "universes:\n  covered: [the companies]\n", "DEVQ"),
    # re-names a filer the base lexicon names
    "rename": "companies:\n  DEVA: {short: Other, legal: Other Inc.}\n",
    # a period-ends ticker the overlay's companies do not name
    "orphan_end": ("companies:\n  DEVB: {short: Devb, legal: Devb Corporation}\n"
                   "period_ends:\n  DEVQ: {2024: \"2024-12-31\"}\n", "DEVQ"),
    # a surface string with a brace (load_lexicon's rule)
    "brace": ("companies:\n  DEVQ: {short: \"Dev{q}\", legal: Devq Inc.}\n", "DEVQ"),
}


@pytest.mark.criterion("C2")
def test_base_plus_overlay_is_the_full_lexicon(kit):
    # oracle: lexicon_full.yaml, written by hand (requirement 2).
    base = kit.lexicon_base()
    before = copy.deepcopy((base.companies, base.period_ends))
    merged = merge_lexicon_overlay(base, kit.HERE / "overlay.yaml")
    full = load_lexicon(kit.HERE / "lexicon_full.yaml")
    assert merged.companies == full.companies
    assert merged.period_ends == full.period_ends
    assert merged.period_ends["DEVD"][2024] == dt.date(2024, 12, 28)
    assert merged.metrics == full.metrics
    assert merged.universes == full.universes
    assert merged.dimensions == full.dimensions
    assert merged == full
    assert list(merged.companies)[:3] == ["DEVA", "DEVC", "DEVE"]  # base entries first
    assert (base.companies, base.period_ends) == before  # the base object is unchanged
    assert "DEVB" not in base.companies


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("case", sorted(BAD_OVERLAYS))
def test_bad_overlays_refused_without_naming_overlay_filers(kit, tmp_path, case):
    # oracle: requirement 2: other keys, a base ticker, an orphan period end and a brace are
    # refused, and the message names no overlay ticker or surface string.
    spec = BAD_OVERLAYS[case]
    text, secret = (spec, "DEVA") if isinstance(spec, str) else spec
    path = tmp_path / f"{case}.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError) as info:
        merge_lexicon_overlay(kit.lexicon_base(), path)
    message = str(info.value)
    assert secret not in message
    assert "Dev{q" not in message and "Other Inc" not in message


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_disjoint_overlays_merge_in_either_order(kit, tmp_path):
    # oracle: requirement 2: an overlay only adds entries, so two disjoint overlays commute,
    # and together they give the hand-written full lexicon.
    one = tmp_path / "one.yaml"
    one.write_text("companies:\n  DEVB: {short: Devb, legal: Devb Corporation}\n"
                   "period_ends:\n  DEVB: {2023: \"2023-12-31\", 2024: \"2024-12-31\"}\n",
                   encoding="utf-8")
    two = tmp_path / "two.yaml"
    two.write_text("companies:\n  DEVD: {short: Devd, legal: Devd Ltd.}\n"
                   "period_ends:\n  DEVD: {2023: \"2023-12-30\", 2024: \"2024-12-28\"}\n",
                   encoding="utf-8")
    ab = merge_lexicon_overlay(merge_lexicon_overlay(kit.lexicon_base(), one), two)
    ba = merge_lexicon_overlay(merge_lexicon_overlay(kit.lexicon_base(), two), one)
    assert ab.companies == ba.companies
    assert ab.period_ends == ba.period_ends
    assert ab == load_lexicon(kit.HERE / "lexicon_full.yaml")
