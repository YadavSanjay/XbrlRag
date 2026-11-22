"""C16: nothing in development touches a held-out filer, and an H-F run reaches them.

HOLD is synthetic. No test here names a real held-out ticker; the committed draw is read only
through a count and an opaque CIK.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sec_payloads import FTS_URL, HOLD_10K, HOLD_CIK, frame_url, json_body

from src.eval.sec_tools.toolbox import (
    SecToolbox,
    default_withheld_path,
    load_universes,
    load_withheld_ciks,
)

ROOT = Path(__file__).resolve().parents[3]
FRAME = {"taxonomy": "us-gaap", "tag": "Revenues", "unit": "USD", "period": "CY2023"}
HOLD_FILINGS = [("list_filings", {"cik": "9"})]


@pytest.mark.criterion("C16")
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("list_filings", {"cik": "9"}),
        ("list_concepts", {"cik": HOLD_CIK}),
        ("get_concept", {"cik": "9", "taxonomy": "us-gaap", "tag": "Revenues"}),
        ("read_filing", {"cik": "9", "accession": HOLD_10K}),
        ("search_filing", {"cik": "9", "accession": HOLD_10K, "query": "x"}),
        ("list_filing_documents", {"cik": "9", "accession": HOLD_10K}),
        ("search_filings_text", {"query": "x", "ciks": ["9"]}),
        ("get_frame", {**FRAME, "ciks": ["9"]}),
    ],
)
def test_a_withheld_filer_gives_withheld_with_no_request(make_toolbox, tool, arguments):
    # oracle: requirement 28
    toolbox, sec = make_toolbox(withheld=(HOLD_CIK,))
    outcome = toolbox.execute(tool, arguments)
    assert outcome.error_code == "withheld"
    assert sec.calls == []


@pytest.mark.criterion("C16")
def test_frame_rows_vanish_and_are_counted(make_toolbox):
    # oracle: requirement 28 — HOLD's 999 is the frame's top row
    toolbox, _ = make_toolbox(withheld=(HOLD_CIK,))
    payload = toolbox.execute("get_frame", FRAME).payload
    assert [r["val"] for r in payload["rows"]] == [300, 120, 80]
    assert payload["total"] == 3
    assert payload["withheld"] == 1
    assert "Held Corp" not in json.dumps(payload)


@pytest.mark.criterion("C16")
def test_search_hits_vanish_and_are_counted(make_toolbox):
    # oracle: requirement 28
    toolbox, sec = make_toolbox(withheld=(HOLD_CIK,))
    payload = toolbox.execute("search_filings_text", {"query": "x"}).payload
    assert HOLD_10K not in json.dumps(payload)
    assert payload["withheld"] == 1
    assert sec.urls[-1].startswith(FTS_URL)


@pytest.mark.criterion("C16")
def test_find_company_does_not_show_a_withheld_filer(make_toolbox):
    # oracle: requirement 28 — a ticker lands in not_found, and a name search is silent on it
    toolbox, _ = make_toolbox(withheld=(HOLD_CIK,))
    payload = toolbox.execute("find_company", {"tickers": ["HOLD", "AAA"]}).payload
    assert [c["ticker"] for c in payload["companies"]] == ["AAA"]
    assert payload["not_found"] == ["HOLD"]
    named = toolbox.execute("find_company", {"query": "Held"}).payload
    assert named["companies"] == []
    assert "Held Corp" not in json.dumps(named)


@pytest.mark.criterion("C16")
def test_lifting_shows_them(make_toolbox):
    # oracle: requirement 28 — the same toolbox, a held-out run's own case
    toolbox, _ = make_toolbox(withheld=(HOLD_CIK,))
    payload = toolbox.execute("get_frame", FRAME, lift_withheld=True).payload
    assert payload["rows"][0]["val"] == 999
    assert "withheld" not in payload


@pytest.mark.criterion("C16")
def test_a_held_out_case_reaches_the_filer(make_arm, make_case):
    # oracle: requirement 28 — the tag lifts it, and needs no flag; the fake SEC 404s the filer,
    # so reaching it reads not_found and sends a request
    arm, adapter, sec = make_arm([HOLD_FILINGS], withheld=(HOLD_CIK,))
    answer = arm.answer(make_case(tags=["split:heldout_filers"]))
    assert json.loads(adapter.texts(1, "tool")[0])["error"] == "not_found"
    assert sec.calls
    assert answer.agent["withheld"] == 0


@pytest.mark.criterion("C16")
@pytest.mark.parametrize(
    "tags",
    [[], ["split:dev"], ["split:validation"], ["split:external"], ["split:temporal"],
     ["split:legacy"]],
)
def test_no_other_split_reaches_it(make_arm, make_case, tags):
    # oracle: requirement 28 — only split:heldout_filers lifts it
    arm, adapter, sec = make_arm([HOLD_FILINGS], withheld=(HOLD_CIK,))
    answer = arm.answer(make_case(tags=tags))
    assert json.loads(adapter.texts(1, "tool")[0])["error"] == "withheld"
    assert sec.calls == []
    assert answer.agent["withheld"] == 1


def _pool(path: Path, candidates: list[dict]) -> Path:
    excluded = [{"cik": "0000000099", "reason": "corpus", "symbol": "IN"}]
    path.write_text(json.dumps({"candidates": candidates, "excluded": excluded}),
                    encoding="utf-8")
    return path


@pytest.mark.criterion("C16")
def test_load_withheld_ciks_reads_a_pool(tmp_path):
    # oracle: requirement 28 — a synthetic pool in the committed shape; `excluded` is development
    pool = _pool(tmp_path / "pool.json", [
        {"cik": "0000000011", "symbol": "X1", "stratum": "D"},
        {"cik": "12", "symbol": "X2", "stratum": "S"},
    ])
    assert load_withheld_ciks(pool) == frozenset({"0000000011", "0000000012"})


@pytest.mark.criterion("C16")
def test_a_missing_or_empty_pool_raises(tmp_path):
    # oracle: requirement 28 — the guard fails loud, never open
    with pytest.raises(ValueError):
        load_withheld_ciks(tmp_path / "absent.json")
    empty = _pool(tmp_path / "empty.json", [])
    with pytest.raises(ValueError):
        load_withheld_ciks(empty)


@pytest.mark.criterion("C16")
def test_the_committed_pool_is_the_default(tmp_path, monkeypatch, fake_sec):
    # oracle: requirement 28 — a guard on the real file, by count and an opaque CIK
    assert default_withheld_path() == ROOT / "docs" / "eval" / "heldout_v2" / "heldout_v2_pool.json"
    ciks = load_withheld_ciks(default_withheld_path())
    assert len(ciks) >= 20
    assert all(len(c) == 10 and c.isdigit() for c in ciks)

    monkeypatch.setenv("XBRLRAG_SEC_SNAPSHOT_DIR", str(tmp_path))
    monkeypatch.setenv("XBRLRAG_SEC_AS_OF", "2024-06-30")
    monkeypatch.setenv("SEC_EDGAR_IDENTITY", "Spec Tester spec@example.com")
    monkeypatch.delenv("XBRLRAG_SEC_FROZEN", raising=False)
    monkeypatch.setenv("XBRLRAG_SEC_WITHHELD_CIKS", "77, 0000000078")
    sec = fake_sec()
    toolbox = SecToolbox.from_env(transport=sec)
    for cik in (min(ciks), "77", "78"):
        assert toolbox.execute("list_filings", {"cik": cik}).error_code == "withheld"
    assert sec.calls == []
    assert toolbox.provenance()["as_of"] == "2024-06-30"
    assert toolbox.provenance()["fetched"] == 0


@pytest.mark.criterion("C16")
def test_from_env_needs_a_date(tmp_path, monkeypatch, fake_sec):
    # oracle: requirement 11 — no default date: a run must say what it stands on
    monkeypatch.setenv("XBRLRAG_SEC_SNAPSHOT_DIR", str(tmp_path))
    monkeypatch.setenv("SEC_EDGAR_IDENTITY", "Spec Tester spec@example.com")
    monkeypatch.delenv("XBRLRAG_SEC_AS_OF", raising=False)
    with pytest.raises(ValueError):
        SecToolbox.from_env(transport=fake_sec())


@pytest.mark.criterion("C16")
def test_load_universes_reads_the_committed_configs():
    # oracle: the committed configs; a corpus filer's own CIK, and the guard against the draw
    universes = load_universes(
        ROOT / "configs" / "eval" / "v2" / "groups.yaml",
        ROOT / "configs" / "entities" / "sectors.yaml",
    )
    covered = {c["ticker"]: c["cik"] for c in universes["covered"]}
    assert covered["AAPL"] == "0000320193"
    assert universes["groups"]["big_tech"] == ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA",
                                               "TSLA"]
    assert universes["as_of"]
    assert not set(covered.values()) & load_withheld_ciks(default_withheld_path())


@pytest.mark.criterion("C16")
@given(flags=st.lists(st.booleans(), min_size=1, max_size=30))
def test_any_mix_shows_exactly_the_others(make_toolbox, fake_sec, flags):
    # oracle: requirement 28 — the rows are numbered, and flags say which are withheld
    data = [
        {"accn": f"a-{i}", "cik": 100 + i, "entityName": f"Filer {i}", "loc": "US-NY",
         "end": "2023-12-31", "val": i}
        for i in range(len(flags))
    ]
    sec = fake_sec()
    sec.pages[frame_url("Revenues", "USD", "CY2023")] = (200, json_body({
        "taxonomy": "us-gaap", "tag": "Revenues", "ccp": "CY2023", "uom": "USD",
        "label": "Revenues", "description": "d", "pts": len(data), "data": data,
    }))
    withheld = tuple(str(100 + i).zfill(10) for i, flag in enumerate(flags) if flag)
    toolbox, _ = make_toolbox(sec, withheld=withheld)
    payload = toolbox.execute("get_frame", {**FRAME, "order": "asc"}).payload
    assert [r["val"] for r in payload["rows"]] == [i for i, f in enumerate(flags) if not f]
    assert payload.get("withheld", 0) == len(withheld)
