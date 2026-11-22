"""Spec tests for sector-tags: GICS sector and industry tags per filer, and the named
universe groups derived from them (docs/specs/sector-tags.md).

Pure functions plus committed YAML. Synthetic files use made-up CIKs and tickers (AAA, BBB,
...) and are written to pytest's tmp_path by the tests, so their contents are visible here.
The committed-config tests read only committed files: the S&P 100 constituents list, the
held-out pool, the wave lists and the v2 lexicon. No DB, no network, no clock.
"""

from __future__ import annotations

import copy
import csv
import datetime as dt
import json
import re
import string
from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.ingest.sector_tags import plan_sector_tag_updates
from src.retriever.xbrl.entity_sectors import (
    build_groups,
    check_groups,
    load_sector_tags,
    load_universe_groups,
    resolve_group_ciks,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# oracle: the eleven GICS sector names (S&P/MSCI GICS structure, 2023 revision).
GICS_SECTORS = (
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Energy",
    "Financials",
    "Health Care",
    "Industrials",
    "Information Technology",
    "Materials",
    "Real Estate",
    "Utilities",
)
INDUSTRIES = ("Semiconductors", "Software", "Retail", "Pharmaceuticals", "Railroads")
ID_PATTERN = re.compile(r"[a-z][a-z0-9_]*")

# A valid synthetic sectors file, as a Python structure the tests dump to YAML.
VALID_DOC: dict[str, Any] = {
    "as_of": "2026-10-29",
    "source": "synthetic test file",
    "filers": [
        {"cik": "0000000001", "ticker": "AAA", "gics_sector": "Information Technology",
         "industry": "Semiconductors"},
        {"cik": "0000000002", "ticker": "BBB", "gics_sector": "Information Technology",
         "industry": "  Software  "},
        {"cik": "0000000003", "ticker": "CCC", "gics_sector": "Consumer Staples",
         "industry": "Retail"},
        {"cik": "0000000004", "ticker": "DDD", "gics_sector": "Consumer Discretionary",
         "industry": "Retail", "note": "added by hand"},
    ],
}

# What load_sector_tags must return for VALID_DOC, written by hand from Requirement 1
# (note dropped, surrounding whitespace trimmed).
VALID_TAGS: dict[str, Any] = {
    "as_of": "2026-10-29",
    "source": "synthetic test file",
    "filers": {
        "0000000001": {"ticker": "AAA", "gics_sector": "Information Technology",
                       "industry": "Semiconductors"},
        "0000000002": {"ticker": "BBB", "gics_sector": "Information Technology",
                       "industry": "Software"},
        "0000000003": {"ticker": "CCC", "gics_sector": "Consumer Staples",
                       "industry": "Retail"},
        "0000000004": {"ticker": "DDD", "gics_sector": "Consumer Discretionary",
                       "industry": "Retail"},
    },
}

VALID_GROUPS_DOC: dict[str, Any] = {
    "as_of": "2026-10-29",
    "sectors_file": "configs/entities/sectors.yaml",
    "definitions": {
        "chips": {"industries": ["Semiconductors"]},
        "shops": {"industries": ["Retail"]},
        "tech": {"sectors": ["Information Technology"]},
        "pair": {"tickers": ["DDD", "AAA"]},
    },
    "groups": {
        "chips": ["AAA"],
        "shops": ["CCC", "DDD"],
        "tech": ["AAA", "BBB"],
        "pair": ["AAA", "DDD"],
    },
}
ALL_SYNTHETIC = {"AAA", "BBB", "CCC", "DDD"}
LEXICON = {"covered": ["the covered companies"], "chips": ["chipmakers"],
           "shops": ["retailers"], "tech": ["tech companies"], "pair": ["the pair"]}

# oracle: entity_master as read (read-only) on 2026-10-29; CIKs are SEC EDGAR's.
CORPUS_CIKS = {
    "AAPL": "0000320193", "AMD": "0000002488", "AMZN": "0001018724", "AVGO": "0001730168",
    "CEG": "0001868275", "GOOGL": "0001652044", "INTC": "0000050863", "META": "0001326801",
    "MSFT": "0000789019", "MU": "0000723125", "NVDA": "0001045810", "ORCL": "0001341439",
    "TSLA": "0001318605", "TSM": "0001046179", "VST": "0001692819",
}
# oracle: spec C8/C9 (reviewed industry labels and the big_tech definition).
SEMIS = ["AMD", "AVGO", "INTC", "MU", "NVDA", "QCOM", "TSM", "TXN"]
RETAIL = ["AMZN", "COST", "HD", "WMT"]
BIG_TECH = ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"]


def _write(directory: Path, doc: Any, name: str = "file.yaml") -> Path:
    path = directory / name
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def _constituents() -> dict[str, str]:
    path = REPO_ROOT / "docs" / "eval" / "heldout_v2" / "sp100_constituents.csv"
    with path.open(encoding="utf-8", newline="") as fh:
        return {row["symbol"]: row["sector"] for row in csv.DictReader(fh)}


def _pool() -> dict[str, Any]:
    path = REPO_ROOT / "docs" / "eval" / "heldout_v2" / "heldout_v2_pool.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _waves() -> dict[str, list[str]]:
    path = REPO_ROOT / "configs" / "eval" / "v2" / "heldout_exclusions.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _lexicon_universes() -> dict[str, list[str]]:
    path = REPO_ROOT / "configs" / "eval" / "v2" / "lexicon.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {str(k): list(v or []) for k, v in (doc.get("universes") or {}).items()}


def _is_iso_date(text: Any) -> bool:
    try:
        return dt.date.fromisoformat(str(text)).isoformat() == str(text)
    except ValueError:
        return False


@st.composite
def tag_tables(draw, min_size: int = 1, max_size: int = 8) -> dict[str, Any]:
    """A tags dict as load_sector_tags returns it: distinct CIKs and tickers."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    ciks = draw(st.lists(st.integers(min_value=1, max_value=9_999_999_999), min_size=n,
                         max_size=n, unique=True))
    tickers = draw(st.lists(st.text(alphabet=string.ascii_uppercase, min_size=1, max_size=5),
                            min_size=n, max_size=n, unique=True))
    filers = {}
    for cik, ticker in zip(ciks, tickers, strict=True):
        filers[f"{cik:010d}"] = {
            "ticker": ticker,
            "gics_sector": draw(st.sampled_from(GICS_SECTORS[:4])),
            "industry": draw(st.sampled_from(INDUSTRIES)),
        }
    return {"as_of": "2026-10-29", "source": "synthetic", "filers": filers}


def _reordered(tags: dict[str, Any]) -> dict[str, Any]:
    return {**tags, "filers": dict(reversed(list(tags["filers"].items())))}


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_valid_file_loads_to_the_documented_shape(tmp_path):
    # oracle: Requirement 1 — VALID_TAGS is written by hand from VALID_DOC: CIK-keyed,
    # note dropped, " Software " trimmed
    assert load_sector_tags(_write(tmp_path, VALID_DOC)) == VALID_TAGS


@pytest.mark.criterion("C1")
@given(st.permutations(range(len(VALID_DOC["filers"]))))
def test_entry_order_does_not_change_the_result(tmp_path_factory, order):
    # oracle: property — the tags are keyed by CIK, so the file's entry order is irrelevant
    doc = copy.deepcopy(VALID_DOC)
    doc["filers"] = [doc["filers"][i] for i in order]
    path = _write(tmp_path_factory.mktemp("sectors"), doc)
    assert load_sector_tags(path) == VALID_TAGS


# --------------------------------------------------------------------------- C2
def _mutations() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []

    def variant(label: str, change) -> None:
        doc = copy.deepcopy(VALID_DOC)
        change(doc)
        out.append((label, doc))

    variant("unpadded cik", lambda d: d["filers"][0].update(cik="1"))
    variant("letter in cik", lambda d: d["filers"][0].update(cik="000000000A"))
    variant("wrong-case sector",
            lambda d: d["filers"][0].update(gics_sector="information technology"))
    variant("unknown sector", lambda d: d["filers"][0].update(gics_sector="Tech"))
    variant("duplicate cik", lambda d: d["filers"].append(
        {"cik": "0000000001", "ticker": "EEE", "gics_sector": "Energy", "industry": "Oil"}))
    variant("duplicate ticker", lambda d: d["filers"].append(
        {"cik": "0000000005", "ticker": "AAA", "gics_sector": "Energy", "industry": "Oil"}))
    variant("blank industry", lambda d: d["filers"][0].update(industry="   "))
    variant("empty industry", lambda d: d["filers"][0].update(industry=""))
    variant("missing source", lambda d: d.pop("source"))
    variant("empty source", lambda d: d.update(source=""))
    variant("missing as_of", lambda d: d.pop("as_of"))
    variant("impossible as_of", lambda d: d.update(as_of="2026-13-01"))
    variant("non-ISO as_of", lambda d: d.update(as_of="29/10/2026"))
    variant("unknown top-level key", lambda d: d.update(extra="x"))
    variant("unknown entry key", lambda d: d["filers"][0].update(sector="Energy"))
    return out


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("doc", [d for _, d in _mutations()], ids=[k for k, _ in _mutations()])
def test_each_config_error_raises(tmp_path, doc):
    # oracle: Requirement 1 / C2 — every listed malformation must fail loud
    assert load_sector_tags(_write(tmp_path, VALID_DOC, "base.yaml")) == VALID_TAGS
    with pytest.raises(ValueError):
        load_sector_tags(_write(tmp_path, doc))


@pytest.mark.criterion("C2")
@given(
    st.one_of(
        st.text(alphabet=string.digits, min_size=1, max_size=9),
        st.text(alphabet=string.digits, min_size=11, max_size=14),
        st.text(alphabet=string.ascii_letters, min_size=10, max_size=10),
    ),
    st.text(min_size=1, max_size=30).filter(lambda s: s.strip() not in GICS_SECTORS),
)
def test_any_malformed_cik_or_sector_is_rejected(tmp_path_factory, bad_cik, bad_sector):
    # oracle: property — a CIK is exactly ten digits; a sector is one of the eleven names
    directory = tmp_path_factory.mktemp("sectors")
    doc = copy.deepcopy(VALID_DOC)
    doc["filers"][0]["cik"] = bad_cik
    with pytest.raises(ValueError):
        load_sector_tags(_write(directory, doc, "cik.yaml"))
    doc = copy.deepcopy(VALID_DOC)
    doc["filers"][0]["gics_sector"] = bad_sector
    with pytest.raises(ValueError):
        load_sector_tags(_write(directory, doc, "sector.yaml"))


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_rules_resolve_to_sorted_tickers_and_bad_rules_raise():
    # oracle: Requirement 3, applied by hand to VALID_TAGS (AAA/BBB are IT, AAA is the only
    # Semiconductors filer, CCC/DDD are Retail)
    assert build_groups(VALID_TAGS, VALID_GROUPS_DOC["definitions"]) == VALID_GROUPS_DOC["groups"]
    assert build_groups(VALID_TAGS, {"dup": {"tickers": ["DDD", "AAA", "DDD"]}}) == {
        "dup": ["AAA", "DDD"]}
    assert build_groups(VALID_TAGS, {"two": {"sectors": ["Consumer Staples",
                                                         "Consumer Discretionary"]}}) == {
        "two": ["CCC", "DDD"]}
    bad_rules = [
        {},
        {"sectors": ["Information Technology"], "industries": ["Retail"]},
        {"regions": ["Americas"]},
        {"tickers": ["ZZZ"]},
        {"industries": ["Railroads"]},  # selects nobody
    ]
    for rule in bad_rules:
        with pytest.raises(ValueError):
            build_groups(VALID_TAGS, {"g": rule})


@pytest.mark.criterion("C3")
@given(tag_tables(), st.sampled_from(["sectors", "industries", "tickers"]), st.data())
def test_build_groups_matches_an_independent_selection(tags, kind, data):
    # oracle: property — the selection recomputed here from the rule; reordering filers
    # or definitions changes nothing
    filers = tags["filers"].values()
    if kind == "sectors":
        values = data.draw(st.lists(st.sampled_from(GICS_SECTORS[:4]), min_size=1, max_size=2))
        expected = sorted({f["ticker"] for f in filers if f["gics_sector"] in values})
    elif kind == "industries":
        values = data.draw(st.lists(st.sampled_from(INDUSTRIES), min_size=1, max_size=2))
        expected = sorted({f["ticker"] for f in filers if f["industry"] in values})
    else:
        values = data.draw(st.lists(st.sampled_from(sorted(f["ticker"] for f in filers)),
                                    min_size=1, max_size=4))
        expected = sorted(set(values))
    definitions = {"g": {kind: values}, "all_tickers": {
        "tickers": [f["ticker"] for f in filers]}}
    if not expected:
        with pytest.raises(ValueError):
            build_groups(tags, definitions)
        return
    want = {"g": expected, "all_tickers": sorted(f["ticker"] for f in filers)}
    assert build_groups(tags, definitions) == want
    flipped = dict(reversed(list(definitions.items())))
    assert build_groups(_reordered(tags), flipped) == want


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
def test_groups_file_loads_and_malformed_files_raise(tmp_path):
    # oracle: Requirement 3 and S3 — the valid file comes back as written; each listed
    # malformation fails loud
    assert load_universe_groups(_write(tmp_path, VALID_GROUPS_DOC)) == VALID_GROUPS_DOC

    def bad(change) -> dict[str, Any]:
        doc = copy.deepcopy(VALID_GROUPS_DOC)
        change(doc)
        return doc

    bad_docs = [
        bad(lambda d: d.update(as_of="2026-02-30")),
        bad(lambda d: d.update(as_of="yesterday")),
        bad(lambda d: (d["definitions"].update(Chips=d["definitions"].pop("chips")),
                       d["groups"].update(Chips=d["groups"].pop("chips")))),
        bad(lambda d: (d["definitions"].update(covered={"sectors": ["Information Technology"]}),
                       d["groups"].update(covered=["AAA", "BBB"]))),
        bad(lambda d: d["groups"].pop("pair")),
        bad(lambda d: d["definitions"].pop("pair")),
        bad(lambda d: d["groups"].update(shops=["CCC", "CCC", "DDD"])),
        bad(lambda d: d["groups"].update(shops=[])),
    ]
    for doc in bad_docs:
        with pytest.raises(ValueError):
            load_universe_groups(_write(tmp_path, doc))


@pytest.mark.criterion("C4")
@given(st.text(alphabet=string.printable, max_size=12).filter(
    lambda s: ID_PATTERN.fullmatch(s) is None))
def test_any_id_outside_the_pattern_is_rejected(tmp_path_factory, bad_id):
    # oracle: property — group ids match ^[a-z][a-z0-9_]*$
    doc = copy.deepcopy(VALID_GROUPS_DOC)
    doc["definitions"][bad_id] = {"tickers": ["AAA"]}
    doc["groups"][bad_id] = ["AAA"]
    with pytest.raises(ValueError):
        load_universe_groups(_write(tmp_path_factory.mktemp("groups"), doc))


# --------------------------------------------------------------------------- C5
@pytest.mark.criterion("C5")
def test_check_groups_reports_each_fault_by_name():
    # oracle: Requirement 4 — a consistent setup has no problem; each injected fault yields
    # a problem naming the group id (and the ticker for a corpus fault)
    assert check_groups(VALID_GROUPS_DOC, VALID_TAGS, ALL_SYNTHETIC, LEXICON) == []

    drifted = copy.deepcopy(VALID_GROUPS_DOC)
    drifted["groups"]["shops"] = ["CCC"]
    problems = check_groups(drifted, VALID_TAGS, ALL_SYNTHETIC, LEXICON)
    assert len(problems) == 1 and "shops" in problems[0]

    problems = check_groups(VALID_GROUPS_DOC, VALID_TAGS, ALL_SYNTHETIC - {"CCC"}, LEXICON)
    assert len(problems) == 1 and "CCC" in problems[0] and "shops" in problems[0]

    unphrased = {k: v for k, v in LEXICON.items() if k != "tech"}
    problems = check_groups(VALID_GROUPS_DOC, VALID_TAGS, ALL_SYNTHETIC, unphrased)
    assert len(problems) == 1 and "tech" in problems[0]
    problems = check_groups(VALID_GROUPS_DOC, VALID_TAGS, ALL_SYNTHETIC, {**LEXICON, "tech": []})
    assert len(problems) == 1 and "tech" in problems[0]

    with_covered = copy.deepcopy(VALID_GROUPS_DOC)
    with_covered["definitions"]["covered"] = {"sectors": ["Information Technology"]}
    with_covered["groups"]["covered"] = ["AAA", "BBB"]
    problems = check_groups(with_covered, VALID_TAGS, ALL_SYNTHETIC, LEXICON)
    assert problems and all("covered" in p for p in problems)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(tag_tables(min_size=2), st.data())
def test_dropping_a_member_or_a_phrase_is_reported(tags, data):
    # oracle: metamorphic — groups built here by industry (independent of build_groups);
    # removing one member from the corpus, or one id from the lexicon, must surface it,
    # and restoring it clears the problem
    by_industry: dict[str, list[str]] = {}
    for f in tags["filers"].values():
        by_industry.setdefault(f["industry"].lower(), []).append(f["ticker"])
    groups = {gid: sorted(members) for gid, members in by_industry.items()}
    definitions = {gid: {"industries": [gid.capitalize()]} for gid in groups}
    doc = {"as_of": "2026-10-29", "sectors_file": "configs/entities/sectors.yaml",
           "definitions": definitions, "groups": groups}
    corpus = {f["ticker"] for f in tags["filers"].values()}
    lexicon = {gid: [f"the {gid} group"] for gid in groups}
    assert check_groups(doc, tags, corpus, lexicon) == []

    gid = data.draw(st.sampled_from(sorted(groups)))
    member = data.draw(st.sampled_from(groups[gid]))
    problems = check_groups(doc, tags, corpus - {member}, lexicon)
    assert any(member in p and gid in p for p in problems)

    trimmed = {k: v for k, v in lexicon.items() if k != gid}
    problems = check_groups(doc, tags, corpus, trimmed)
    assert problems and all(gid in p for p in problems)
    assert check_groups(doc, tags, corpus, {**trimmed, gid: lexicon[gid]}) == []


# --------------------------------------------------------------------------- C6
@pytest.mark.criterion("C6")
def test_group_resolves_to_sorted_ciks():
    # oracle: Requirement 6, VALID_TAGS by hand — CCC is 0000000003, DDD 0000000004,
    # AAA 0000000001
    groups = VALID_GROUPS_DOC["groups"]
    assert resolve_group_ciks("shops", groups, VALID_TAGS) == ["0000000003", "0000000004"]
    assert resolve_group_ciks("pair", groups, VALID_TAGS) == ["0000000001", "0000000004"]
    with pytest.raises(ValueError):
        resolve_group_ciks("nope", groups, VALID_TAGS)
    with pytest.raises(ValueError):
        resolve_group_ciks("covered", {**groups, "covered": ["AAA"]}, VALID_TAGS)
    with pytest.raises(ValueError):
        resolve_group_ciks("ghost", {"ghost": ["ZZZ"]}, VALID_TAGS)


@pytest.mark.criterion("C6")
@given(tag_tables(), st.data())
def test_resolved_ciks_are_exactly_the_members(tags, data):
    # oracle: property — one CIK per member, sorted, each mapping back to a member ticker
    tickers = sorted(f["ticker"] for f in tags["filers"].values())
    members = data.draw(st.lists(st.sampled_from(tickers), min_size=1, unique=True))
    ciks = resolve_group_ciks("grp", {"grp": members}, tags)
    assert len(ciks) == len(members)
    assert ciks == sorted(ciks)
    assert {tags["filers"][c]["ticker"] for c in ciks} == set(members)


# --------------------------------------------------------------------------- C7
def _apply(rows: list[dict[str, Any]], plan) -> list[dict[str, Any]]:
    updates = {cik: (sector, industry) for cik, sector, industry in plan}
    out = []
    for row in rows:
        if row["cik"] in updates:
            sector, industry = updates[row["cik"]]
            row = {"cik": row["cik"], "gics_sector": sector, "industry": industry}
        out.append(row)
    return out


@pytest.mark.criterion("C7")
def test_plan_updates_only_what_differs():
    # oracle: Requirement 5 by hand — 01 already right; 02 unset; 03 stale industry; 09 not
    # in the tags but tagged (cleared); 08 not in the tags and already null; 04 has no row
    rows = [
        {"cik": "0000000009", "gics_sector": "Energy", "industry": "Oil"},
        {"cik": "0000000003", "gics_sector": "Consumer Staples", "industry": "Retailing"},
        {"cik": "0000000001", "gics_sector": "Information Technology",
         "industry": "Semiconductors"},
        {"cik": "0000000008", "gics_sector": None, "industry": None},
        {"cik": "0000000002", "gics_sector": None, "industry": None},
    ]
    assert plan_sector_tag_updates(VALID_TAGS, rows) == [
        ("0000000002", "Information Technology", "Software"),
        ("0000000003", "Consumer Staples", "Retail"),
        ("0000000009", None, None),
    ]


@pytest.mark.criterion("C7")
@given(tag_tables(), st.data())
def test_plan_is_complete_idempotent_and_order_free(tags, data):
    # oracle: property — the expected plan is recomputed here; applying it and planning
    # again yields nothing; shuffling the rows changes nothing
    pair = st.one_of(st.just((None, None)),
                     st.tuples(st.sampled_from(GICS_SECTORS[:4]), st.sampled_from(INDUSTRIES)))
    rows = []
    for cik in data.draw(st.lists(st.sampled_from(sorted(tags["filers"])), unique=True)):
        sector, industry = data.draw(pair)
        rows.append({"cik": cik, "gics_sector": sector, "industry": industry})
    for extra in data.draw(st.lists(st.integers(min_value=1, max_value=9_999_999_999),
                                    max_size=3, unique=True)):
        cik = f"{extra:010d}"
        if cik not in tags["filers"]:
            sector, industry = data.draw(pair)
            rows.append({"cik": cik, "gics_sector": sector, "industry": industry})
    # anchor: one row that always needs an update, so an empty plan cannot pass
    stale_cik = "0000000000"
    tags = {**tags, "filers": {**tags["filers"], stale_cik: {
        "ticker": "ZZZZZZ", "gics_sector": "Energy", "industry": "Railroads"}}}
    rows.append({"cik": stale_cik, "gics_sector": None, "industry": None})

    expected = []
    for row in rows:
        tag = tags["filers"].get(row["cik"])
        want = (tag["gics_sector"], tag["industry"]) if tag else (None, None)
        if (row["gics_sector"], row["industry"]) != want:
            expected.append((row["cik"], *want))
    expected.sort()

    plan = plan_sector_tag_updates(tags, rows)
    assert plan == expected
    assert plan_sector_tag_updates(tags, _apply(rows, plan)) == []
    shuffled = data.draw(st.permutations(rows))
    assert plan_sector_tag_updates(tags, list(shuffled)) == expected


# --------------------------------------------------------------------------- C8
@pytest.mark.criterion("C8")
def test_committed_sectors_pin_the_corpus_and_reviewed_labels():
    # oracle: entity_master CIKs (SEC EDGAR), the pool file's wave-1 CIKs, GICS sectors for
    # the three filers off the S&P 100 list, and the spec's reviewed industry labels
    tags = load_sector_tags()
    by_ticker = {f["ticker"]: (cik, f) for cik, f in tags["filers"].items()}
    for ticker, cik in CORPUS_CIKS.items():
        assert ticker in by_ticker and by_ticker[ticker][0] == cik, ticker
    pool_ciks = {e["symbol"]: e["cik"] for e in _pool()["excluded"]}
    for ticker in _waves()["wave1"]:
        assert ticker in by_ticker and by_ticker[ticker][0] == pool_ciks[ticker], ticker
    assert by_ticker["TSM"][1]["gics_sector"] == "Information Technology"
    assert by_ticker["CEG"][1]["gics_sector"] == "Utilities"
    assert by_ticker["VST"][1]["gics_sector"] == "Utilities"
    labels = {**dict.fromkeys(SEMIS, "Semiconductors"), **dict.fromkeys(RETAIL, "Retail"),
              "LLY": "Pharmaceuticals", "MRK": "Pharmaceuticals", "UNP": "Railroads"}
    for ticker, industry in labels.items():
        assert by_ticker[ticker][1]["industry"] == industry, ticker


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
def test_every_committed_tag_agrees_with_the_committed_sources():
    # oracle: the S&P 100 list (sectors), the held-out pool (CIKs, candidates) and the wave
    # lists — every entry, not just the pinned ones
    tags = load_sector_tags()
    assert _is_iso_date(tags["as_of"]) and tags["source"].strip()
    raw = yaml.safe_load((REPO_ROOT / "configs" / "entities" / "sectors.yaml")
                         .read_text(encoding="utf-8"))
    notes = {str(e["ticker"]).strip(): str(e.get("note") or "").strip() for e in raw["filers"]}
    sectors = _constituents()
    pool = _pool()
    pool_ciks = {e["symbol"]: e["cik"] for e in pool["candidates"] + pool["excluded"]}
    held_out = {e["symbol"] for e in pool["candidates"]}
    waves = _waves()
    dev = set(CORPUS_CIKS) | set(waves["wave1"]) | set(waves["wave2"])
    assert len(tags["filers"]) >= len(CORPUS_CIKS) + len(waves["wave1"])
    for cik, f in tags["filers"].items():
        ticker = f["ticker"]
        assert ticker in dev and ticker not in held_out, ticker
        assert f["gics_sector"] in GICS_SECTORS and f["industry"].strip(), ticker
        if ticker in sectors:
            assert f["gics_sector"] == sectors[ticker], ticker
        else:
            assert notes.get(ticker), f"{ticker}: off the S&P 100 list, needs a note"
        if ticker in pool_ciks:
            assert cik == pool_ciks[ticker], ticker


# --------------------------------------------------------------------------- C9
@pytest.mark.criterion("C9")
def test_committed_groups_hold_the_named_universes():
    # oracle: spec C9 — the reviewed industry labels of C8 and the big_tech definition
    doc = load_universe_groups()
    assert doc["sectors_file"] == "configs/entities/sectors.yaml"
    assert doc["groups"]["semiconductors"] == SEMIS
    assert doc["groups"]["retailers"] == RETAIL
    assert doc["groups"]["big_tech"] == BIG_TECH


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
def test_committed_groups_are_regenerable_and_consistent():
    # oracle: Requirement 3–4 — the committed groups equal a fresh build from the committed
    # tags; every member is a tagged filer, none is held out; the lexicon phrases every id
    doc = load_universe_groups()
    tags = load_sector_tags()
    assert _is_iso_date(doc["as_of"])
    assert build_groups(tags, doc["definitions"]) == doc["groups"]
    tagged = {f["ticker"] for f in tags["filers"].values()}
    held_out = {e["symbol"] for e in _pool()["candidates"]}
    for gid, members in doc["groups"].items():
        assert set(members) <= tagged and not set(members) & held_out, gid
    assert check_groups(doc, tags, tagged, _lexicon_universes()) == []
    assert {"semiconductors", "retailers", "big_tech"} <= set(doc["groups"])
