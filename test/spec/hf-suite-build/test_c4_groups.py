"""C4: ``heldout_groups`` applies the committed definitions to the dev plus held-out tags.

Oracle: requirement 4 of docs/specs/hf-suite-build.md, applied by hand. The tags and the
groups document are synthetic, in the shapes ``load_sector_tags`` and
``load_universe_groups`` return (docs/specs/sector-tags.md).
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.heldout_build import heldout_groups

INDUSTRIES = ("Retail", "Semiconductors", "Software", "Autos")
SECTORS = ("Consumer Discretionary", "Information Technology", "Consumer Staples")


def _tags(filers: dict) -> dict:
    return {"as_of": "2026-11-10", "source": "synthetic", "filers": filers}


DEV = _tags({
    "0000800001": {"ticker": "DEVA", "gics_sector": "Consumer Staples", "industry": "Retail"},
    "0000800003": {"ticker": "DEVC", "gics_sector": "Information Technology",
                   "industry": "Semiconductors"},
    "0000800005": {"ticker": "DEVE", "gics_sector": "Consumer Discretionary",
                   "industry": "Autos"},
})
HELD = _tags({
    "0000800002": {"ticker": "DEVB", "gics_sector": "Consumer Discretionary",
                   "industry": "Retail"},
    "0000800004": {"ticker": "DEVD", "gics_sector": "Information Technology",
                   "industry": "Software"},
})
DOC = {
    "as_of": "2026-11-10",
    "sectors_file": "configs/entities/sectors.yaml",
    "definitions": {"retailers": {"industries": ["Retail"]},
                    "semiconductors": {"industries": ["Semiconductors"]},
                    "tech": {"sectors": ["Information Technology"]},
                    "named": {"tickers": ["DEVA", "DEVE"]}},
    "groups": {"retailers": ["DEVA"], "semiconductors": ["DEVC"], "tech": ["DEVC"],
               "named": ["DEVA", "DEVE"]},
}


@pytest.mark.criterion("C4")
def test_heldout_filers_join_the_groups_their_tags_select():
    # oracle: requirement 4, by hand: DEVB (Retail) joins retailers, DEVD (Information
    # Technology, Software) joins tech only; the tickers group is unchanged.
    groups = heldout_groups(DOC, DEV, HELD)
    assert groups == {"retailers": ["DEVA", "DEVB"], "semiconductors": ["DEVC"],
                      "tech": ["DEVC", "DEVD"], "named": ["DEVA", "DEVE"]}
    assert list(groups) == ["retailers", "semiconductors", "tech", "named"]


@pytest.mark.criterion("C4")
def test_overlapping_tags_and_stale_groups_refused():
    # oracle: requirement 4: a CIK or a ticker in both tag sets, and committed groups that
    # differ from the dev part of the build, are refused.
    same_cik = copy.deepcopy(HELD)
    same_cik["filers"]["0000800001"] = {"ticker": "DEVQ", "gics_sector": "Consumer Staples",
                                        "industry": "Retail"}
    same_ticker = copy.deepcopy(HELD)
    same_ticker["filers"]["0000800009"] = {"ticker": "DEVA", "gics_sector": "Consumer Staples",
                                           "industry": "Retail"}
    stale = copy.deepcopy(DOC)
    stale["groups"]["retailers"] = ["DEVA", "DEVC"]
    for doc, held in ((DOC, same_cik), (DOC, same_ticker), (stale, HELD)):
        with pytest.raises(ValueError):
            heldout_groups(doc, DEV, held)


@pytest.mark.criterion("C4")
@given(b=st.tuples(st.sampled_from(SECTORS), st.sampled_from(INDUSTRIES)),
       d=st.tuples(st.sampled_from(SECTORS), st.sampled_from(INDUSTRIES)))
def test_each_group_is_committed_plus_the_selected_heldout_filers(b, d):
    # oracle: requirement 4: a group is its committed members plus exactly the held-out
    # filers whose tag the definition's rule selects.
    held = _tags({
        "0000800002": {"ticker": "DEVB", "gics_sector": b[0], "industry": b[1]},
        "0000800004": {"ticker": "DEVD", "gics_sector": d[0], "industry": d[1]},
    })
    tags = {"DEVB": b, "DEVD": d}
    groups = heldout_groups(DOC, DEV, held)
    for gid, rule in DOC["definitions"].items():
        (kind, values), = rule.items()
        if kind == "tickers":
            extra = set()
        else:
            field = 0 if kind == "sectors" else 1
            extra = {t for t, tag in tags.items() if tag[field] in values}
        assert groups[gid] == sorted(set(DOC["groups"][gid]) | extra)
