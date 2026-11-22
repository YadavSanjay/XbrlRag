"""C1: ``taxonomy_key`` maps a concept namespace URI to its companyfacts key.

Oracle: requirement 3 of docs/specs/sec-companyfacts-check.md.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.sec_check.core import taxonomy_key


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(
    ("namespace", "expected"),
    [
        ("http://fasb.org/us-gaap/2024", "us-gaap"),
        ("https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full", "ifrs-full"),
        ("http://fasb.org/srt/2024", "srt"),
        ("http://xbrl.sec.gov/dei/2024", "dei"),
    ],
)
def test_standard_taxonomies_map_to_their_keys(namespace, expected):
    # oracle: requirement 3 / C1 — the four standard namespaces and their companyfacts keys
    assert taxonomy_key(namespace) == expected


@pytest.mark.criterion("C1")
def test_filer_extension_maps_to_none():
    # oracle: requirement 3 / C1 — anything else is a filer extension and gives None; the
    # us-gaap URI is checked alongside so a constant-None answer cannot pass
    assert taxonomy_key("http://www.apple.com/20240928") is None
    assert taxonomy_key("http://fasb.org/us-gaap/2024") == "us-gaap"


@pytest.mark.criterion("C1")
@given(year=st.integers(min_value=1990, max_value=2199))
def test_any_us_gaap_year_suffix_maps_to_us_gaap(year):
    # oracle: requirement 3 — "a URI containing fasb.org/us-gaap gives us-gaap", whatever
    # the taxonomy year after it
    assert taxonomy_key(f"http://fasb.org/us-gaap/{year}") == "us-gaap"
    assert taxonomy_key(f"https://fasb.org/us-gaap/{year}") == "us-gaap"


@pytest.mark.criterion("C1")
@given(
    year=st.integers(min_value=1990, max_value=2199),
    month=st.integers(min_value=1, max_value=12),
    name=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=3, max_size=12),
)
def test_dated_standard_and_extension_uris(year, month, name):
    # oracle: requirement 3 — srt/dei/ifrs-full keep their key for any date; a
    # www.<name>.com/<date> filer URI contains none of the standard substrings, so None
    assert taxonomy_key(f"http://fasb.org/srt/{year}") == "srt"
    assert taxonomy_key(f"http://xbrl.sec.gov/dei/{year}") == "dei"
    assert taxonomy_key(f"https://xbrl.ifrs.org/taxonomy/{year}-{month:02d}-01/ifrs-full") == \
        "ifrs-full"
    assert taxonomy_key(f"http://www.{name}.com/{year}{month:02d}28") is None
