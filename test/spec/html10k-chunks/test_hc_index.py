"""The payload and the collection (docs/specs/html10k-chunks.md, requirements 14-18; C11-C13).

Qdrant is an I/O boundary, so the collection tests use a recording fake. Everything else is real.
"""

from __future__ import annotations

import json
import re
import uuid

import pytest
from hypothesis import given
from hypothesis import strategies as st
from qdrant_client.models import Distance, PayloadSchemaType

from src.embeddings.base import DenseEmbedding, SparseEmbedding
from src.vectorstores.html10k_qdrant_vectordb_client import (
    Html10kQdrantVectorDBClient,
    build_payload,
    embedding_input,
)

# Home Depot's filing ending 2025-02-02 is fiscal 2024 in filing_registry
# (docs/specs/fiscal-label-alignment.md, requirement 7). The accession is invented.
HD_FILING = {
    "cik": "0000354950",
    "ticker": "HD",
    "company_name": "Home Depot, Inc.",
    "accession_no": "0000000000-26-000001",
    "fiscal_year": 2024,
    "fiscal_period": "FY",
    "form_type": "10-K",
    "period_end": "2025-02-02",
}


class FakeQdrant:
    """Records what the client asks Qdrant to do."""

    def __init__(self, exists: bool = False) -> None:
        self.exists = exists
        self.created: list[dict] = []
        self.indexes: dict[str, PayloadSchemaType] = {}
        self.uploads: list[dict] = []

    def collection_exists(self, collection_name: str) -> bool:
        return self.exists

    def create_collection(self, **kwargs: object) -> None:
        self.created.append(kwargs)
        self.exists = True

    def create_payload_index(self, **kwargs: object) -> None:
        assert kwargs["collection_name"] == "html10k_text"
        self.indexes[kwargs["field_name"]] = kwargs["field_schema"]

    def upload_points(self, **kwargs: object) -> None:
        self.uploads.append(kwargs)


def _one(kit, body="<p>Hello.</p>"):
    return kit.chunks(body)[0]


def _table_chunk(kit):
    body = "<div>Item 8. Financial Statements</div>" + kit.grid(["2024"], [("Cash", ["$1"])])
    return next(c for c in kit.chunks(body) if c.kind == "table")


@pytest.mark.criterion("C11")
def test_the_payload_of_a_prose_chunk(kit):
    # oracle: requirement 14, written by hand. The chunk's values are the chunk's own.
    chunk = _one(kit)
    assert build_payload(chunk, HD_FILING) == {
        "chunk_id": chunk.chunk_id,
        "accession_no": "0000000000-26-000001",
        "text": "Hello.",
        "cik": "0000354950",
        "ticker": "HD",
        "company_name": "Home Depot, Inc.",
        "fiscal_year": 2024,
        "fiscal_period": "FY",
        "form_type": "10-K",
        "period_end": "2025-02-02",
        "section_key": "no_section",
        "section_keys": ["no_section"],
        "section_label": "",
        "html_char_start": 0,
        "html_char_end": chunk.char_end,
        "contains_table": False,
        "table_index": None,
        "part": 0,
        "parts": 1,
        "prev_chunk_id": None,
        "next_chunk_id": None,
    }


@pytest.mark.criterion("C11")
def test_the_payload_of_a_table_chunk(kit):
    # oracle: requirement 14, by hand.
    chunk = _table_chunk(kit)
    payload = build_payload(chunk, HD_FILING)
    assert payload["contains_table"] is True
    assert payload["table_index"] == 0
    assert payload["section_key"] == "item_8"
    assert payload["section_keys"] == ["item_8"]
    assert payload["section_label"] == "Item 8. Financial Statements"
    assert payload["text"] == chunk.text


@pytest.mark.criterion("C11")
def test_the_label_is_the_one_passed_in_not_the_period_end_year(kit):
    # oracle: fiscal-label-alignment requirement 7: HD's year ending 2025-02-02 is fiscal 2024.
    assert build_payload(_one(kit), HD_FILING)["fiscal_year"] == 2024


@pytest.mark.criterion("C11")
@pytest.mark.parametrize(
    "bad",
    [
        {k: v for k, v in HD_FILING.items() if k != "fiscal_year"},
        {**HD_FILING, "xbrl_fact_ids": [1]},
        {**HD_FILING, "fiscal_year": None},
        {**HD_FILING, "fiscal_year": "2024"},
        {**HD_FILING, "fiscal_year": True},
        {**HD_FILING, "accession_no": "0000000000-26-000002"},
    ],
    ids=["missing", "unknown", "none", "text", "bool", "other-filing"],
)
def test_a_bad_filing_is_refused(kit, bad):
    # oracle: requirement 15: a wrong label must fail at the boundary, not reach the index.
    with pytest.raises(ValueError):
        build_payload(_one(kit), bad)


@pytest.mark.criterion("C11")
def test_the_embedding_input_mirrors_the_xbrl_flow(kit):
    # oracle: requirement 16, written by hand.
    chunk = _table_chunk(kit)
    payload = build_payload(chunk, HD_FILING)
    expected = f"HD FY2024 Item 8. Financial Statements :: {chunk.text}"
    assert embedding_input(payload) == expected


@pytest.mark.criterion("C11")
def test_the_embedding_input_with_no_section_and_no_ticker(kit):
    # oracle: requirement 16, by hand: the company falls back in order, and no label leaves a gap.
    filing = {**HD_FILING, "ticker": ""}
    assert embedding_input(build_payload(_one(kit), filing)) == (
        "Home Depot, Inc. FY2024 :: Hello."
    )
    filing = {**HD_FILING, "ticker": "", "company_name": ""}
    assert embedding_input(build_payload(_one(kit), filing)) == "0000354950 FY2024 :: Hello."


@pytest.mark.criterion("C11")
@pytest.mark.metamorphic
@given(year=st.integers(2000, 2040))
def test_the_label_changes_only_the_label(kit, year):
    # oracle: requirement 15: the label is pass-through metadata, and ids never depend on it.
    chunk = _one(kit)
    base = build_payload(chunk, HD_FILING)
    other = build_payload(chunk, {**HD_FILING, "fiscal_year": year})
    assert other == {**base, "fiscal_year": year}
    assert embedding_input(other) == f"HD FY{year} :: Hello."


@pytest.mark.criterion("C11")
def test_no_new_module_reads_a_database(kit, source):
    # oracle: requirement 15: the caller reads filing_registry; these modules never do.
    assert build_payload(_one(kit), HD_FILING)["fiscal_year"] == 2024
    for relative in (
        "src/chunking/html10k_chunking.py",
        "src/vectorstores/html10k_qdrant_vectordb_client.py",
    ):
        text = source(relative)
        assert "psycopg2" not in text
        assert "src.db" not in text


@pytest.mark.criterion("C12")
def test_ensure_collection_creates_the_vectors_and_indexes(kit):
    # oracle: requirement 17, written by hand. The same vector names as the XBRL text collection.
    fake = FakeQdrant()
    Html10kQdrantVectorDBClient(fake).ensure_collection()
    assert len(fake.created) == 1
    created = fake.created[0]
    assert created["collection_name"] == "html10k_text"
    assert set(created["vectors_config"]) == {"text_dense"}
    assert created["vectors_config"]["text_dense"].size == 1024
    assert created["vectors_config"]["text_dense"].distance == Distance.COSINE
    assert set(created["sparse_vectors_config"]) == {"text_sparse"}
    keyword = PayloadSchemaType.KEYWORD
    assert fake.indexes == {
        "cik": keyword,
        "ticker": keyword,
        "accession_no": keyword,
        "fiscal_period": keyword,
        "form_type": keyword,
        "section_key": keyword,
        "section_keys": keyword,
        "fiscal_year": PayloadSchemaType.INTEGER,
        "contains_table": PayloadSchemaType.BOOL,
    }


@pytest.mark.criterion("C12")
def test_an_existing_collection_is_not_created_again(kit):
    # oracle: requirement 17: creating only when missing keeps a loaded collection.
    fake = FakeQdrant(exists=True)
    Html10kQdrantVectorDBClient(fake).ensure_collection()
    assert fake.created == []


def _vectors(ids):
    return {
        chunk_id: (DenseEmbedding([0.25, 0.5]), SparseEmbedding([7], [0.75]))
        for chunk_id in ids
    }


@pytest.mark.criterion("C12")
def test_upload_writes_one_point_per_payload(kit):
    # oracle: requirement 17, by hand: the id, both named vectors, and the payload unchanged.
    body = "<p>a</p>" + kit.grid(["2024"], [("Cash", ["$1"])])
    payloads = [build_payload(c, HD_FILING) for c in kit.chunks(body)]
    ids = [p["chunk_id"] for p in payloads]
    fake = FakeQdrant()
    Html10kQdrantVectorDBClient(fake).upload_chunks(payloads, _vectors(ids))
    assert len(fake.uploads) == 1
    upload = fake.uploads[0]
    assert upload["collection_name"] == "html10k_text"
    assert upload["wait"] is True
    points = upload["points"]
    assert [p.id for p in points] == [p["chunk_id"] for p in payloads]
    assert [p.payload for p in points] == payloads
    vector = points[0].vector
    assert vector["text_dense"] == pytest.approx([0.25, 0.5], abs=1e-12)  # stored as given
    sparse = vector["text_sparse"]
    indices = sparse["indices"] if isinstance(sparse, dict) else sparse.indices
    values = sparse["values"] if isinstance(sparse, dict) else sparse.values
    assert list(indices) == [7]
    assert list(values) == pytest.approx([0.75], abs=1e-12)  # stored as given


@pytest.mark.criterion("C12")
def test_a_mismatch_between_payloads_and_vectors_writes_nothing(kit):
    # oracle: requirement 17: a point with an empty payload could never be filtered or read.
    payload = build_payload(_one(kit), HD_FILING)
    client_fake = FakeQdrant()
    client = Html10kQdrantVectorDBClient(client_fake)
    with pytest.raises(ValueError):
        client.upload_chunks([payload], {})
    with pytest.raises(ValueError):
        client.upload_chunks([payload], _vectors([payload["chunk_id"], str(uuid.uuid4())]))
    assert client_fake.uploads == []


@pytest.mark.criterion("C12")
@pytest.mark.metamorphic
def test_the_order_of_the_vectors_does_not_change_the_points(kit):
    # oracle: requirement 17: points follow the payloads.
    body = "<p>a</p>" + kit.grid(["2024"], [("Cash", ["$1"])])
    payloads = [build_payload(c, HD_FILING) for c in kit.chunks(body)]
    ids = [p["chunk_id"] for p in payloads]
    first, second = FakeQdrant(), FakeQdrant()
    Html10kQdrantVectorDBClient(first).upload_chunks(payloads, _vectors(ids))
    Html10kQdrantVectorDBClient(second).upload_chunks(payloads, _vectors(reversed(ids)))
    assert [p.id for p in first.uploads[0]["points"]] == [p.id for p in second.uploads[0]["points"]]


IX = 'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"'


def _tagged(fact_id: str, context: str, concept: str) -> str:
    fact = (
        f'<ix:nonFraction id="{fact_id}" name="{concept}" contextRef="{context}" unitRef="usd-unit"'
        f' decimals="-6" scale="6" format="ixt:num-dot-decimal">1,234</ix:nonFraction>'
    )
    return (
        f'<div {IX}><ix:header><ix:hidden><ix:nonNumeric id="{fact_id}-hidden" name="dei:Security"'
        f' contextRef="{context}">Hidden</ix:nonNumeric></ix:hidden></ix:header>'
        f'<p>Revenue was <a href="#{fact_id}">${fact}</a> million.</p>'
        f"<table><tr><td></td><td>2024</td></tr><tr><td>Revenue</td><td>${fact}</td></tr></table>"
        "</div>"
    )


def _plain() -> str:
    return (
        "<div><p>Revenue was $1,234 million.</p>"
        "<table><tr><td></td><td>2024</td></tr><tr><td>Revenue</td><td>$1,234</td></tr></table>"
        "</div>"
    )


def _index_text(kit, body):
    payloads = [build_payload(c, HD_FILING) for c in kit.chunks(body)]
    texts = [embedding_input(p) for p in payloads]
    return payloads, json.dumps(payloads, ensure_ascii=False) + "\n".join(texts)


@pytest.mark.criterion("C13")
def test_no_xbrl_reaches_the_index(kit):
    # oracle: plan 5.2. Every identifier is built into the document, and the printed number stays.
    payloads, text = _index_text(kit, _tagged("f-12", "ctx-2024", "us-gaap:Revenues"))
    for leaked in ("ix:", "f-12", "ctx-2024", "usd-unit", "us-gaap", "dei:", "Hidden"):
        assert leaked not in text
    assert "1,234" in text
    assert {"text", "chunk_id"} <= set(payloads[0])


@pytest.mark.criterion("C13")
def test_no_payload_key_names_xbrl_or_facts(kit):
    # oracle: plan 5.2; the XBRL payload's fields are xbrl_*, has_xbrl_facts and the like.
    payload = build_payload(_one(kit), HD_FILING)
    assert not [k for k in payload if re.search("xbrl|fact", k, re.IGNORECASE)]


@pytest.mark.criterion("C13")
def test_no_index_field_names_xbrl_or_facts(kit):
    # oracle: plan 5.2: a filter on one could only ever read the other flow's data.
    fake = FakeQdrant()
    Html10kQdrantVectorDBClient(fake).ensure_collection()
    assert fake.indexes
    assert not [k for k in fake.indexes if re.search("xbrl|fact", k, re.IGNORECASE)]


@pytest.mark.criterion("C13")
def test_the_new_modules_import_no_xbrl_code(kit, source):
    # oracle: plan 5.2: the flow never touches the fact store or the xbrl_* payload fields.
    # Sharing DOM helpers with the XBRL ingest and chunker modules is allowed (requirement 18).
    assert build_payload(_one(kit), HD_FILING)["text"] == "Hello."
    pattern = re.compile(
        r"^\s*(?:from|import)\s+(\S*(?:fact_?store|xbrl_qdrant|xbrl_bge|retriever\.xbrl)\S*)",
        re.MULTILINE,
    )
    for relative in (
        "src/chunking/html10k_chunking.py",
        "src/vectorstores/html10k_qdrant_vectordb_client.py",
        "src/ingest/html10k/sections.py",
    ):
        assert pattern.findall(source(relative)) == []


@pytest.mark.criterion("C13")
@given(
    fact=st.from_regex(r"fid[0-9a-f]{8}", fullmatch=True),
    context=st.from_regex(r"cid[0-9a-f]{8}", fullmatch=True),
    concept=st.from_regex(r"acme:Concept[A-Z]{6}", fullmatch=True),
)
def test_any_fact_id_stays_out_of_the_index(kit, fact, context, concept):
    # oracle: plan 5.2: whatever the identifiers, none survives.
    _, text = _index_text(kit, _tagged(fact, context, concept))
    for leaked in (fact, context, concept, "ix:"):
        assert leaked not in text


@pytest.mark.criterion("C13")
@pytest.mark.metamorphic
def test_a_tagged_document_and_its_tag_free_twin_give_equal_payloads(kit):
    # oracle: plan 5.2; html10k-strip-sections requirement 11: tagging changes nothing downstream.
    tagged, _ = _index_text(kit, _tagged("f-1", "c-1", "us-gaap:Revenues"))
    plain, _ = _index_text(kit, _plain())
    assert [p["text"] for p in tagged] == [p["text"] for p in plain]
    assert [p["contains_table"] for p in tagged] == [p["contains_table"] for p in plain]
