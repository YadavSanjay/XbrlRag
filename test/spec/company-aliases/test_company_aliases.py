"""Spec tests for company-aliases: resolve common company names and acronyms through a
committed, validated alias table (docs/specs/company-aliases.md).

Pure functions plus a YAML file — no DB, no LLM. Synthetic alias files are written to
pytest's tmp_path by the tests themselves, so their contents are visible here.
"""

from __future__ import annotations

import string

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.entity_aliases import (
    load_company_aliases,
    match_company_alias,
    normalize_company_mention,
)

# Oracle: SEC EDGAR company records — Taiwan Semiconductor Manufacturing Co (TSM)
# CIK 1046179; Alphabet Inc. CIK 1652044; Meta Platforms, Inc. CIK 1326801.
TSMC_CIK = "0001046179"
ALPHABET_CIK = "0001652044"
META_CIK = "0001326801"

# A small hand-built table, already in normalized form, for the pure-function tests.
TABLE = {
    "tsmc": TSMC_CIK,
    "taiwan semiconductor": TSMC_CIK,
    "google": ALPHABET_CIK,
    "facebook": META_CIK,
}

VALID_FILE = """\
companies:
  - cik: "0001046179"
    aliases: ["TSMC", "Taiwan Semiconductor", "Taiwan Semi"]
  - cik: "0001652044"
    aliases: ["Google"]
"""


def _write(tmp_path, text: str):
    path = tmp_path / "company_aliases.yaml"
    path.write_text(text, encoding="utf-8")
    return path


@st.composite
def decorated(draw, alias: str) -> str:
    """The alias as a router might extract it: any case, padded, spaced out, with a
    possessive and/or surrounding punctuation — none of which changes the company."""
    case = draw(st.sampled_from([str.lower, str.upper, str.title]))
    words = case(alias).split(" ")
    gaps = [" " * draw(st.integers(min_value=1, max_value=3)) for _ in words[1:]]
    body = words[0] + "".join(g + w for g, w in zip(gaps, words[1:], strict=True))
    possessive = draw(st.sampled_from(["", "'s", "’s", "'S"]))
    left = draw(st.sampled_from(["", " ", '"', "("]))
    right = draw(st.sampled_from(["", " ", ".", ",", '"', ")"]))
    return f"{left}{body}{possessive}{right}"


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_normalization_and_matching_point_cases():
    # oracle: the C1 rules applied by hand — case-fold, ’ -> ', collapse whitespace,
    # strip surrounding whitespace/punctuation, drop a trailing possessive 's
    assert normalize_company_mention("  TSMC’s. ") == "tsmc"
    assert normalize_company_mention("Taiwan   Semiconductor") == "taiwan semiconductor"
    for mention in ("TSMC", " tsmc ", "TSMC's", "TSMC’s"):
        assert match_company_alias(mention, TABLE) == TSMC_CIK, mention
    assert match_company_alias("Google", TABLE) == ALPHABET_CIK


@pytest.mark.criterion("C1")
@given(st.sampled_from(sorted(TABLE)).flatmap(lambda a: st.tuples(st.just(a), decorated(a))))
def test_any_decoration_of_an_alias_resolves_to_its_cik(pair):
    # oracle: property — case, spacing, a possessive and surrounding punctuation do not
    # change which company a mention names
    alias, mention = pair
    assert normalize_company_mention(mention) == alias
    assert match_company_alias(mention, TABLE) == TABLE[alias]


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_no_partial_prefix_or_fuzzy_matching():
    # oracle: requirement — exact match only; "TSM Corp" and "TSMCX" are not "tsmc",
    # and fuzzy resolution stays with pg_trgm
    assert match_company_alias("TSMC", TABLE) == TSMC_CIK  # the exact alias does match
    for mention in ("TSM Corp", "TSMCX", "TSM", "Taiwan", "Semiconductor", "", "   "):
        assert match_company_alias(mention, TABLE) is None, repr(mention)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(
    st.sampled_from(sorted(TABLE)),
    st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=4),
)
def test_extending_an_alias_with_letters_breaks_the_match(alias, suffix):
    # oracle: metamorphic — appending letters/digits to an alias yields a different name,
    # unless the result is itself an alias in the table
    assert match_company_alias(alias, TABLE) == TABLE[alias]
    extended = f"{alias}{suffix}"
    expected = TABLE.get(extended.lower())
    assert match_company_alias(extended, TABLE) == expected


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_valid_file_loads_and_bad_files_raise(tmp_path):
    # oracle: requirement — the valid file's aliases come back normalized; a conflicting
    # alias or a non-10-digit CIK must fail loud rather than resolve to the wrong filer
    loaded = load_company_aliases(_write(tmp_path, VALID_FILE))
    assert loaded == {
        "tsmc": TSMC_CIK,
        "taiwan semiconductor": TSMC_CIK,
        "taiwan semi": TSMC_CIK,
        "google": ALPHABET_CIK,
    }

    conflict = VALID_FILE + '  - cik: "0001326801"\n    aliases: ["tsmc"]\n'
    with pytest.raises(ValueError):
        load_company_aliases(_write(tmp_path, conflict))

    unpadded = 'companies:\n  - cik: "1046179"\n    aliases: ["TSMC"]\n'
    with pytest.raises(ValueError):
        load_company_aliases(_write(tmp_path, unpadded))


@pytest.mark.criterion("C3")
@given(
    st.one_of(
        st.text(alphabet=string.digits, min_size=1, max_size=9),
        st.text(alphabet=string.digits, min_size=11, max_size=14),
        st.text(alphabet=string.ascii_letters, min_size=10, max_size=10),
    )
)
def test_any_malformed_cik_is_rejected(tmp_path_factory, bad_cik):
    # oracle: property — a CIK is exactly ten digits; anything shorter, longer or
    # non-numeric is rejected
    path = tmp_path_factory.mktemp("aliases") / "company_aliases.yaml"
    path.write_text(f'companies:\n  - cik: "{bad_cik}"\n    aliases: ["Acme"]\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_company_aliases(path)


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
def test_committed_table_maps_the_known_aliases():
    # oracle: SEC EDGAR company records (TSM 1046179, Alphabet 1652044, Meta 1326801)
    table = load_company_aliases()
    assert match_company_alias("TSMC", table) == TSMC_CIK
    assert match_company_alias("Taiwan Semiconductor", table) == TSMC_CIK
    assert match_company_alias("Google", table) == ALPHABET_CIK
    assert match_company_alias("Facebook", table) == META_CIK


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_every_committed_alias_is_normalized_and_resolves_in_any_case():
    # oracle: metamorphic — each stored key is already normalized, maps to a 10-digit
    # CIK, and resolves the same when the mention is upper-cased
    table = load_company_aliases()
    assert len(table) >= 4
    for alias, cik in table.items():
        assert normalize_company_mention(alias) == alias, alias
        assert len(cik) == 10 and cik.isdigit(), (alias, cik)
        assert match_company_alias(alias.upper(), table) == cik, alias


# --------------------------------------------------------------------------- C5
@pytest.mark.criterion("C5")
def test_normalization_is_idempotent_point_cases():
    # oracle: property of the C1 rules, checked by hand on two mentions
    once = normalize_company_mention("  Taiwan   Semiconductor’s, ")
    assert once == "taiwan semiconductor"
    assert normalize_company_mention(once) == once
    assert match_company_alias("TAIWAN SEMICONDUCTOR", TABLE) == TSMC_CIK


@pytest.mark.criterion("C5")
@given(st.text(alphabet=string.printable, max_size=30))
def test_idempotent_and_case_insensitive_for_any_ascii_mention(mention):
    # oracle: property — normalizing twice changes nothing, and neither upper-casing nor
    # pre-normalizing a mention changes which CIK it resolves to (ASCII only: Unicode
    # case mapping is not round-trip safe)
    once = normalize_company_mention(mention)
    assert normalize_company_mention(once) == once
    expected = match_company_alias(mention, TABLE)
    assert match_company_alias(mention.upper(), TABLE) == expected
    assert match_company_alias(once, TABLE) == expected
    # anchor: the property is not satisfied vacuously by a matcher that never matches
    assert match_company_alias(" Tsmc ", TABLE) == TSMC_CIK
