"""C4: ``disclosure_checks`` — universe, named exclusions and the year-end spread (S2, S3).

Oracle: requirement 7 of docs/specs/v2-grading.md. The v2 record ranks DEVB and DEVA over a
universe "covered" of 4 members (DEVC, DEVT excluded; period ends 2024-12-31 and
2024-09-28, disclosure flagged); the H-X screen has universe DEVA..DEVD with DEVD excluded,
so its counts are 4 and 3. Every expectation is read off those by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.v2 import disclosure_checks

FULL_V2 = ("Among the 4 covered companies, DEVB ranks first and DEVA second. DEVC and DEVT "
           "could not be ranked. Fiscal years end December 31, 2024 (DEVB) and "
           "September 28, 2024 (DEVA).")


@pytest.mark.criterion("C4")
def test_v2_full_disclosure_and_silences(kit):
    # oracle: requirement 7 — all three checks for the full text; the silent text names no
    # count, not every member, no universe name, omits DEVT and the dates.
    case = kit.case(kit.v2_screen())
    assert disclosure_checks(case, FULL_V2) == {"universe": True, "exclusions": True,
                                                "period_ends": True}
    silent = "DEVB ranks first and DEVA second; DEVC was excluded."
    assert disclosure_checks(case, silent) == {"universe": False, "exclusions": False,
                                               "period_ends": False}


@pytest.mark.criterion("C4")
def test_v2_universe_ways(kit):
    # oracle: requirement 7 — the universe is stated by a count (4 members or 2 ranked), a
    # number word, the universe name, or every member named.
    case = kit.case(kit.v2_screen(disclose_period_ends=False))
    by = {
        "Of 2 ranked filers DEVB leads.": True,
        "Of four filers DEVB leads.": True,
        "Among covered filers DEVB leads.": True,
        "DEVB, DEVA, DEVC and DEVT were considered.": True,
        "DEVB leads DEVA.": False,
        "In FY2024 DEVB had 4.5 billion more and 14 segments.": False,
    }
    for text, want in by.items():
        got = disclosure_checks(case, text)
        assert set(got) == {"universe", "exclusions"}, text
        assert got["universe"] is want, text


@pytest.mark.criterion("C4")
def test_exclusions_names_and_aliases(kit):
    # oracle: requirements 6-7 — tickers match case-sensitively on alnum boundaries; an alias
    # names its ticker through states_phrase.
    case = kit.case(kit.v2_screen(disclose_period_ends=False))
    assert disclosure_checks(case, "DEVC and DEVT were excluded.")["exclusions"] is True
    assert disclosure_checks(case, "devc and devt were excluded.")["exclusions"] is False
    assert disclosure_checks(case, "DEVCX and DEVT were excluded.")["exclusions"] is False
    aliases = {"DEVT": ["Dev Tee Corp"]}
    text = "DEVC and dev tee corp. were excluded."
    assert disclosure_checks(case, text, aliases)["exclusions"] is True
    assert disclosure_checks(case, text)["exclusions"] is False


@pytest.mark.criterion("C4")
def test_period_end_forms(kit):
    # oracle: requirement 7 — each listed date form, day without a leading zero.
    case = kit.case(kit.v2_screen())
    for a, b in [("2024-12-31", "2024-09-28"), ("Dec 31, 2024", "Sep 28, 2024"),
                 ("31 December 2024", "28 September 2024"), ("31 Dec 2024", "28 Sep 2024")]:
        assert disclosure_checks(case, f"Periods end {a} and {b}.")["period_ends"] is True, a
    assert disclosure_checks(case, "Periods end 2024-12-31.")["period_ends"] is False
    assert disclosure_checks(case, "Periods end in December and September.")[
        "period_ends"] is False


@pytest.mark.criterion("C4")
def test_hx_screen_and_no_data(kit):
    # oracle: requirement 7 — H-X counts are 4 (universe) and 3 (ranked); no name, no
    # period ends; a case without disclosure data gives {}.
    case = kit.case(kit.hx_screen())
    assert disclosure_checks(case, "Of the 3 ranked filers DEVA leads; DEVD is untagged.") == {
        "universe": True, "exclusions": True}
    assert disclosure_checks(case, "DEVA leads DEVB.") == {"universe": False,
                                                           "exclusions": False}
    assert disclosure_checks(kit.case(kit.hx_trend()), FULL_V2) == {}
    assert disclosure_checks(kit.case(kit.v2_lookup()), FULL_V2) == {}


@pytest.mark.criterion("C4")
@given(prefix=st.text(alphabet="abcdefghijklmnopqrstuvwxyz .,", max_size=60))
def test_appending_the_disclosure_satisfies_every_check(kit, prefix):
    # oracle: requirement 7 — FULL_V2 states the count, every exclusion and both dates, and
    # appending text can only add mentions.
    case = kit.case(kit.v2_screen())
    got = disclosure_checks(case, prefix + " " + FULL_V2)
    assert got == {"universe": True, "exclusions": True, "period_ends": True}


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@pytest.mark.parametrize("drop", ["DEVC", "DEVT"])
def test_dropping_one_exclusion_flips_only_exclusions(kit, drop):
    # oracle: requirement 7 — the count still states the universe and the dates stay, so
    # only "exclusions" changes.
    case = kit.case(kit.v2_screen())
    before = disclosure_checks(case, FULL_V2)
    after = disclosure_checks(case, FULL_V2.replace(drop, "one filer"))
    assert before == {"universe": True, "exclusions": True, "period_ends": True}
    assert after == {**before, "exclusions": False}
