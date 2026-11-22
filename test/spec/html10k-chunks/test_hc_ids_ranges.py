"""Chunk ids, byte ranges and links (docs/specs/html10k-chunks.md, requirements 12-13; C9, C10)."""

from __future__ import annotations

import uuid

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.chunking.html10k_chunking import chunk_filing

NAMESPACE = uuid.UUID("5d1c3f0e-7a42-4e6b-9b3d-2c8e1f6a4d70")
GROUPED = [
    ("Assets:", ["", ""]),
    ("Cash", ["1", "2"]),
    ("Liabilities:", ["", ""]),
    ("Debt", ["3", "4"]),
]


def _expected_id(accession, start, end, part):
    return str(uuid.uuid5(NAMESPACE, f"{accession}:{start}:{end}:{part}"))


def _body(kit):
    table = kit.grid(["2024", "2023"], GROUPED)
    return f'<p class="m0">first</p>{table}<p class="m2">last</p>'


@pytest.mark.criterion("C9")
def test_each_id_is_the_formula_over_the_range_and_part(kit):
    # oracle: requirement 12, computed here with Python's uuid5 on offsets read from the markers.
    doc = kit.stripped(_body(kit))
    chunks = chunk_filing(doc, kit.accession, table_max_chars=70)
    table_at, last_at, end = doc.index(b"<table"), kit.at(doc, 'class="m2"'), len(doc)
    assert [c.chunk_id for c in chunks] == [
        _expected_id(kit.accession, 0, table_at, 0),
        _expected_id(kit.accession, table_at, last_at, 0),
        _expected_id(kit.accession, table_at, last_at, 1),
        _expected_id(kit.accession, last_at, end, 0),
    ]


@pytest.mark.criterion("C9")
def test_the_accession_seeds_the_id_and_is_kept(kit):
    # oracle: requirement 12: two filings with equal offsets never share an id.
    doc = kit.stripped("<p>text</p>")
    first = chunk_filing(doc, "0000000000-26-000001")
    second = chunk_filing(doc, "0000000000-26-000002")
    assert first[0].chunk_id != second[0].chunk_id
    assert first[0].accession_no == "0000000000-26-000001"


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
def test_text_of_the_same_byte_length_keeps_the_ids(kit):
    # oracle: requirement 12: ids depend on offsets, not on text.
    first = kit.chunks("<p>aaaa</p>" + kit.grid(["2024"], [("Cash", ["1"])]))
    second = kit.chunks("<p>bbbb</p>" + kit.grid(["2024"], [("Land", ["2"])]))
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert [c.text for c in first] != [c.text for c in second]


@pytest.mark.criterion("C9")
@given(sizes=st.lists(st.integers(1, 60), min_size=1, max_size=8), limit=st.integers(60, 200))
def test_ids_are_unique_valid_and_stable(kit, sizes, limit):
    # oracle: requirement 12: a point id is a UUID, and no chunk overwrites another.
    table = kit.grid(["2024", "2023"], GROUPED)
    body = "".join(f"<p>{'x' * n}</p>{table if n % 3 == 0 else ''}" for n in sizes)
    first = kit.chunks(body, table_max_chars=limit, chunk_size_chars=40)
    second = kit.chunks(body, table_max_chars=limit, chunk_size_chars=40)
    assert first == second
    ids = [c.chunk_id for c in first]
    assert len(set(ids)) == len(ids)
    assert all(str(uuid.UUID(i)) == i for i in ids)


@pytest.mark.criterion("C10")
def test_ranges_and_links_of_a_small_filing(kit):
    # oracle: requirement 13; offsets read from the markers. The two parts share the table's range.
    doc = kit.stripped(_body(kit))
    chunks = chunk_filing(doc, kit.accession, table_max_chars=70)
    table_at, last_at = doc.index(b"<table"), kit.at(doc, 'class="m2"')
    assert [(c.char_start, c.char_end) for c in chunks] == [
        (0, table_at),
        (table_at, last_at),
        (table_at, last_at),
        (last_at, len(doc)),
    ]
    ids = [c.chunk_id for c in chunks]
    assert [c.prev_chunk_id for c in chunks] == [None, *ids[:-1]]
    assert [c.next_chunk_id for c in chunks] == [*ids[1:], None]


@pytest.mark.criterion("C10")
def test_offsets_count_utf8_bytes(kit):
    # oracle: requirement 13, as in Html10kSection. A curly quote is three bytes in UTF-8.
    doc = kit.stripped('<p>it’s’’’’ here</p><table><tr><td>Cash</td><td>1</td></tr></table>')
    chunks = chunk_filing(doc, kit.accession)
    assert chunks[1].char_start == doc.index(b"<table")


@pytest.mark.criterion("C10")
def test_a_document_with_nothing_in_it_has_no_chunks(kit):
    # oracle: requirement 13: no block, no chunk; it isn't an error.
    assert kit.chunks("") == []


@pytest.mark.criterion("C10")
def test_a_prose_chunk_range_holds_all_its_blocks(kit):
    # oracle: requirement 13, by hand. Three blocks are one chunk, up to the document's length.
    doc = kit.stripped("<p>a</p><p>b</p><p>c</p>")
    chunks = chunk_filing(doc, kit.accession)
    assert [(c.char_start, c.char_end) for c in chunks] == [(0, len(doc))]


@pytest.mark.criterion("C10")
@given(sizes=st.lists(st.integers(1, 60), min_size=1, max_size=8), limit=st.integers(60, 200))
def test_chunks_cover_the_document_contiguously(kit, sizes, limit):
    # oracle: requirement 13: every byte belongs to one chunk; only parts of a table share a range.
    table = kit.grid(["2024", "2023"], GROUPED)
    body = "".join(f"<p>{'x' * n}</p>{table if n % 3 == 0 else ''}" for n in sizes)
    doc = kit.stripped(body)
    chunks = chunk_filing(doc, kit.accession, table_max_chars=limit, chunk_size_chars=40)
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(doc)
    for left, right in zip(chunks, chunks[1:], strict=False):
        if left.table_index is not None and left.table_index == right.table_index:
            assert (left.char_start, left.char_end) == (right.char_start, right.char_end)
            assert right.part == left.part + 1
        else:
            assert left.char_end == right.char_start
            assert left.char_start < left.char_end
