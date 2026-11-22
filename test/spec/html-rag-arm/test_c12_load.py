"""C12: the load. ix-strip, chunks and the payload contract are real, and only I/O is fake."""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.systems.long_context import ANNUAL_FORMS
from src.pipelines.html10k.load_to_vectordb_pipeline import (
    Html10kLoadPipeline,
    list_annual_filings,
)

ACCESSION = "0000000001-25-000001"
KEY = "AAA/2024/10-K/aaa-20241231.htm"
ROOT = "D:/filings/sec_edgar"
FILING = {
    "cik": "0000000001",
    "ticker": "AAA",
    "company_name": "Alpha Holdings",
    "accession_no": ACCESSION,
    "fiscal_year": 2024,
    "fiscal_period": "FY",
    "form_type": "10-K",
    "period_end": "2024-12-31",
}


def _document(body: str) -> bytes:
    return (
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"><head><title>t</title></head>'
        f"<body>{body}</body></html>"
    ).encode()


def _paragraphs(n: int) -> bytes:
    """n paragraphs of 1,000 characters: html10k-chunks requirement 7 closes a chunk at each."""
    return _document("".join(f"<p>{f'paragraph {i:03d} ':x<1000}</p>" for i in range(n)))


def _wide_table() -> bytes:
    """A table of about 1,500 characters: 28 rows of about 50, under a short header."""
    rows = "".join(
        f"<tr><td>Line item number {i:02d} of the long schedule</td><td>{1000 + i:,}</td></tr>"
        for i in range(28)
    )
    return _document(
        "<p>Alpha makes widgets.</p>"
        f"<table><tr><td></td><td>2024</td></tr>{rows}</table>"
    )


def _pipeline(make_config, memory_store, fake_embedder, fake_qdrant, *, objects=None,
              embedder=None, client=None, **settings):
    store = memory_store(objects if objects is not None else {KEY: _paragraphs(3)})
    embedder = embedder if embedder is not None else fake_embedder()
    client = client if client is not None else fake_qdrant()
    pipeline = Html10kLoadPipeline(
        store=store, embedder=embedder, client=client, config=make_config(**settings)
    )
    return pipeline, store, embedder, client


@pytest.fixture
def build(make_config, memory_store, fake_embedder, fake_qdrant):
    """Factory: (pipeline, store, embedder, client)."""

    def make(**kwargs):
        return _pipeline(make_config, memory_store, fake_embedder, fake_qdrant, **kwargs)

    return make


@pytest.mark.criterion("C12")
def test_a_filing_is_loaded(build):
    # oracle: requirement 28 — three paragraphs of 1,000 characters are three chunks
    pipeline, store, embedder, client = build()
    assert pipeline.load_filing(FILING, KEY) == "loaded"
    assert store.reads == [KEY]
    assert len(client.uploaded) == 3
    embedded = [item for batch in embedder.batches for item in batch]
    assert [str(point.id) for point in client.uploaded] == [key for key, _ in embedded]
    for point, (_, text) in zip(client.uploaded, embedded, strict=True):
        assert point.payload["accession_no"] == ACCESSION
        assert point.payload["fiscal_year"] == 2024
        assert sorted(point.vector) == ["text_dense", "text_sparse"]
        # html10k-chunks requirement 16's string, for a block in no section.
        assert text == f"AAA FY2024 :: {point.payload['text']}"


@pytest.mark.criterion("C12")
def test_the_load_passes_table_max_chars(build):
    # oracle: requirement 28 — the table's text is about 1,500 characters, by construction
    objects = {KEY: _wide_table()}
    pipeline, _, _, split = build(objects=objects, table_max_chars=1200)
    pipeline.load_filing(FILING, KEY)
    tables = [p.payload for p in split.uploaded if p.payload["contains_table"]]
    assert len(tables) >= 2
    assert all(p["parts"] == len(tables) for p in tables)
    assert all(len(p["text"]) <= 1200 for p in tables)

    pipeline, _, _, whole = build(objects=objects, table_max_chars=5000)
    pipeline.load_filing(FILING, KEY)
    assert [p.payload["parts"] for p in whole.uploaded if p.payload["contains_table"]] == [1]


@pytest.mark.criterion("C12")
def test_batches_are_bounded(build):
    # oracle: requirement 28 — five chunks in batches of two: 2, 2, 1
    objects = {KEY: _paragraphs(5)}
    pipeline, _, embedder, client = build(objects=objects, embed_batch_size=2)
    pipeline.load_filing(FILING, KEY)
    assert [len(batch) for batch in embedder.batches] == [2, 2, 1]
    assert len(client.uploaded) == 5


@pytest.mark.criterion("C12")
def test_a_repeat_is_skipped_with_no_embedding(build):
    # oracle: requirement 28 — a stopped load restarts without paying for what's done
    pipeline, _, embedder, client = build()
    pipeline.load_filing(FILING, KEY)
    batches = len(embedder.batches)
    assert pipeline.load_filing(FILING, KEY) == "skipped"
    assert len(embedder.batches) == batches
    assert len(client.uploaded) == 3
    assert client.delete_calls == []


@pytest.mark.criterion("C12")
def test_a_partial_filing_is_replaced(build, fake_qdrant, payload, conditions_of):
    # oracle: requirement 28 — one stale point of this accession goes, the three new ones stay;
    # another accession's point is not touched
    stale = payload("stale", accession_no=ACCESSION, start=9000, end=9100)
    other = payload("other", accession_no="9999999999-25-000001")
    client = fake_qdrant([stale, other])
    pipeline, _, _, client = build(client=client)
    assert pipeline.load_filing(FILING, KEY) == "loaded"
    assert len(client.delete_calls) == 1
    left = [p["chunk_id"] for p in client.payloads]
    assert stale["chunk_id"] not in left
    assert other["chunk_id"] in left
    assert len(left) == 4


@pytest.mark.criterion("C12")
def test_nothing_is_deleted_before_the_embeddings_exist(
    build, fake_embedder, fake_qdrant, payload
):
    # oracle: requirement 28 — a failed embedding leaves the old points where they are
    stale = payload("stale", accession_no=ACCESSION, start=9000, end=9100)
    pipeline, _, _, client = build(
        embedder=fake_embedder(error=RuntimeError("service down")), client=fake_qdrant([stale])
    )
    with pytest.raises(RuntimeError, match="service down"):
        pipeline.load_filing(FILING, KEY)
    assert client.delete_calls == []
    assert [p["chunk_id"] for p in client.payloads] == [stale["chunk_id"]]


@pytest.mark.criterion("C12")
def test_run_goes_on_past_a_bad_filing(build):
    # oracle: requirement 29 — a missing object and a None key fail; the third loads; the
    # collection is ensured once
    pipeline, _, _, client = build()
    missing = {**FILING, "accession_no": "0000000001-25-000002"}
    no_key = {**FILING, "accession_no": "0000000001-25-000003"}
    outcome = pipeline.run(
        [
            {"filing": missing, "source_key": "AAA/none.htm"},
            {"filing": no_key, "source_key": None},
            {"filing": FILING, "source_key": KEY},
        ]
    )
    assert outcome["loaded"] == [ACCESSION]
    assert outcome["skipped"] == []
    assert [f["accession_no"] for f in outcome["failed"]] == [
        "0000000001-25-000002", "0000000001-25-000003",
    ]
    assert all(f["error"] for f in outcome["failed"])
    assert client.created == ["html10k_text"]


def _row(ticker: str, year: int, entry_url: str, *, accession: str = "acc-1",
         period_end: object = None) -> tuple:
    end = period_end if period_end is not None else dt.date(year, 12, 31)
    return (f"cik-{ticker}", ticker, f"{ticker} Inc", accession, year, "FY", "10-K", end, entry_url)


@pytest.mark.criterion("C12")
def test_list_annual_filings(registry_conn):
    # oracle: requirement 30, by hand; sorted, a date made a string, the key made relative
    conn = registry_conn(
        [
            _row("BBB", 2024, "D:/filings/sec_edgar/BBB/2024/10-K/b.htm", accession="acc-b"),
            _row("AAA", 2024, "D:\\filings\\sec_edgar\\AAA\\2024\\10-K\\a.htm", accession="acc-a"),
            _row("CCC", 2024, "E:/elsewhere/c.htm", accession="acc-c"),
        ]
    )
    entries = list_annual_filings(conn, ROOT)
    assert entries == [
        {"filing": {"cik": "cik-AAA", "ticker": "AAA", "company_name": "AAA Inc",
                    "accession_no": "acc-a", "fiscal_year": 2024, "fiscal_period": "FY",
                    "form_type": "10-K", "period_end": "2024-12-31"},
         "source_key": "AAA/2024/10-K/a.htm"},
        {"filing": {"cik": "cik-BBB", "ticker": "BBB", "company_name": "BBB Inc",
                    "accession_no": "acc-b", "fiscal_year": 2024, "fiscal_period": "FY",
                    "form_type": "10-K", "period_end": "2024-12-31"},
         "source_key": "BBB/2024/10-K/b.htm"},
        {"filing": {"cik": "cik-CCC", "ticker": "CCC", "company_name": "CCC Inc",
                    "accession_no": "acc-c", "fiscal_year": 2024, "fiscal_period": "FY",
                    "form_type": "10-K", "period_end": "2024-12-31"},
         "source_key": None},
    ]
    sql = " ".join(conn.sql[0].split())
    assert "fiscal_period = 'FY'" in sql
    assert "superseded_by IS NULL" in sql
    assert "form_type = ANY(%s)" in sql
    assert len(conn.params[0]) == 1
    assert sorted(conn.params[0][0]) == sorted(ANNUAL_FORMS)


@pytest.mark.criterion("C12")
def test_the_scope_narrows_the_list(registry_conn):
    # oracle: requirement 30, by hand
    rows = [
        _row("AAA", 2023, f"{ROOT}/AAA/2023/a.htm", accession="a23"),
        _row("AAA", 2024, f"{ROOT}/AAA/2024/a.htm", accession="a24"),
        _row("BBB", 2024, f"{ROOT}/BBB/2024/b.htm", accession="b24"),
    ]
    picked = list_annual_filings(registry_conn(rows), ROOT, tickers=["AAA"], fiscal_years=[2024])
    assert [e["filing"]["accession_no"] for e in picked] == ["a24"]


@pytest.mark.criterion("C12")
@pytest.mark.metamorphic
def test_the_label_is_the_registrys(registry_conn):
    # oracle: fiscal-label-alignment — Home Depot's fiscal 2024 ends 2025-02-02; the label is
    # never derived from the date
    rows = [_row("HD", 2024, f"{ROOT}/HD/2025/10-K/h.htm", period_end=dt.date(2025, 2, 2))]
    filing = list_annual_filings(registry_conn(rows), ROOT)[0]["filing"]
    assert (filing["fiscal_year"], filing["period_end"]) == (2024, "2025-02-02")


@pytest.mark.criterion("C12")
@given(n=st.integers(1, 8), size=st.integers(1, 4))
def test_any_filing_loads_whole_in_bounded_batches(
    make_config, memory_store, fake_embedder, fake_qdrant, n, size
):
    # oracle: requirement 28 — computed here: n paragraphs are n chunks, cut into batches
    pipeline, _, embedder, client = _pipeline(
        make_config, memory_store, fake_embedder, fake_qdrant,
        objects={KEY: _paragraphs(n)}, embed_batch_size=size,
    )
    pipeline.load_filing(FILING, KEY)
    assert all(1 <= len(batch) <= size for batch in embedder.batches)
    assert sum(len(batch) for batch in embedder.batches) == n
    assert len(client.uploaded) == n
