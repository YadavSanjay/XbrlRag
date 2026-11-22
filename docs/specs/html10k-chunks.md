# html10k-chunks — the chunk and index stages of the HTML 10-K flow

Phase 3 of the v2 plan ([§5.3](../design/XbrlRag_v2_Plan.md), rows *Chunks* and *Index*; the
leakage rule is §5.2). `ix_strip_html`, `detect_sections` and `parse_tables` exist. This change
turns their output into chunks, and defines what those chunks look like in the Qdrant collection
`html10k_text`.

Out of scope: the retriever, the answerer, the `html_rag` arm, a load pipeline or CLI, and running
any embedding or load. The XBRL flow does not change: `xbrl_html_base_chunking.py` and
`xbrl_qdrant_vectordb_client.py` keep their behaviour.

What a scratch run over the latest 10-K of 32 development filers found, which shaped the rules
below (see Notes for how it was measured):

- A stub Item 8 has two shapes. NVDA and ORCL print the statements inside Item 15, before Item 16.
  QCOM and NFLX print Item 15 as a short list, then Item 16, and the statements and notes follow
  on F-pages, in QCOM's case after the Signatures. So a section that ends at the next item would
  leave QCOM's and NFLX's statements untagged.
- A `<table>` is often not a table: INTC has 62 and MCD 36 with no value column (bullets, page
  numbers, headings).
- 98 of 2,556 number-bearing tables have a caption that is only a scale note: NVDA prints the
  statement's title, then `(In millions, except per share data)`, then the table. The caption alone
  would lose the title.
- 287 of those tables have a caption over 300 characters, the longest 4,472: the nearest block is
  a paragraph.
- Rendered table bodies have a median of 311 characters, a 95th percentile of 1,939 and a maximum
  of 6,438. 118 exceed 2,000.

## Requirement

### A. Sections for chunking

1. **`detect_chunk_sections` adds `item_15`; `detect_sections` does not change.** It returns what
   `detect_sections` returns plus, when found, an `Html10kSection` with key `item_15`, sorted by
   `char_start`. Every other section is the same value `detect_sections` gives.
2. **Where `item_15` starts.** The first of these that exists, by the rules of
   `html10k-strip-sections`: the ToC entry whose item number is 15; the heading of item 15; the ToC
   entry with the boundary title `Exhibits`, for a topic-ordered filing with no item numbers. `source`
   and `label` are that entry's.
3. **Where it ends.** When `notes` was found and starts after `item_15`, it ends at `notes`'s
   `char_end`, so everything between Item 15 and the notes is inside it, Item 16 and Signatures
   included when the filing prints them there. Otherwise it ends at the first boundary after its
   start (Requirement 18 of that spec), or the document's length.
4. **Which section a block is in.** A block's position is its `char_start`, except a table's, which
   is the start of its first `<tr>`: AVGO, WMT, VST and CEG print a heading as a table row, and
   the section starts at that row. The sections containing the position, ordered by `char_start`,
   give `section_keys`; the last of them gives `section_key` and `section_label`. So a chunk of
   the notes has `section_key` `notes` and `section_keys` `("item_8", "notes")` or
   `("item_15", "notes")`.
5. **No section.** A block in no section is still chunked and indexed. `section_key` is `no_section`,
   `section_keys` is `("no_section",)` and `section_label` is empty. Nothing is dropped for having
   no section: topic-ordered filers (INTC, MCD) have no `item_1` or `item_7`, and Items 2, 3, 5 and
   9A are not keys.

### B. Chunks

6. **The walk.** `chunk_filing` walks `ix_strip_html` output in document order, as
   `_iter_chunkable_blocks` does: a *prose block* is a `div`, `p`, `li`, `td`, `th`, `dd`, `dt`,
   `blockquote`, `h1`-`h6`, `section` or `article` with no such element or table inside it, and it
   is never entered further. Its text is its non-empty text runs, stripped and joined by one space.
   Every `<table>` is a *table block*, in the same order and with the same ordinal as `parse_tables`,
   nested ones included. Nothing under a table is a prose block.
7. **Prose, as today.** Prose blocks accumulate into one chunk: texts joined by one space. After a
   block is added, when the sum of the block lengths is at least `chunk_size_chars`, the chunk is
   closed. A chunk is also closed before a block whose `section_keys` differ, and before a data
   table. A block is never split, so a long one is one chunk over the size.
8. **Layout tables are prose.** A table is a *layout table* when it has no body row, or when no cell
   holds a number or a nil and no column has a header. Its text, taken from the markup like any
   prose block's so that nothing the parser put in a header is lost, joins the prose stream as one
   block. A layout table with no text adds nothing. Every other table is a *data table*.
9. **A data table is one chunk**, unless requirement 11 splits it, with this text, lines joined by
   `\n`, leaving out a line that has nothing to say:

   ```text
   Section: Item 8. Financial Statements and Supplementary Data
   Table: CONSOLIDATED BALANCE SHEETS
   Note: (In millions, except par value)
   | (Stub) | Year Ended Dec 31, 2024 | Year Ended Dec 31, 2023 |
   | --- | --- | --- |
   | Assets |  |  |
   | Cash | $1,234 | $1,000 |
   ```

   - `Section:` is `section_label`, or `none` for no section. It is always present.
   - `Table:` is the caption, per requirement 10.
   - The first markdown row has one cell for the labels, then one per column. The label cell is the
     text of the header-row cells left of the first value column, joined by one space; that is
     where a top-left `(In millions)` is printed. A column's cell is its `Column.headers` joined
     by one space.
   - A body row is its label, then each `Cell.text`. Rows are `| ` + cells joined by ` | ` + ` |`.
     An empty cell is empty. A group row stays, with empty cells.
   - A footnote marker follows its label or number in brackets after one space: `7,392 [2]`,
     `Goodwill [a]`. Parentheses would read as a negative.
   - A `|` in any text is written `\|`.
10. **Caption and note.** A chunk shows what the filing prints.
    - **Numbers are never computed.** Every number is `Cell.text`, as printed. `Cell.value`,
      `Cell.scale`, `Cell.scaled_value`, `ParsedTable.scale` and `Column.period` never reach the text.
      The scale parser has known errors, and the LLM must read what the filing prints.
    - **Title.** When the caption begins with a scale statement (a standing note), it says nothing
      about the table, so `Table:` is the nearest block before it, if no table lies between, and the
      caption itself goes on the `Note:` line.
    - **Note.** `Note:` otherwise shows the printed standing note in force for this table, unless
      the table's own header or its caption states a scale, as a parsed scale's precedence has it
      (Requirement 4 of `html10k-tables`); those are printed in the chunk already, and a stale
      standing note beside them would mislead.
    - **Long captions.** A caption or title over 300 characters keeps its last 300 characters,
      from the first word that fits whole, after a leading `…`. The whole paragraph is in a prose
      chunk, and the end is the part that introduces the table.
11. **Large tables split by row groups.** A data table whose text exceeds `table_max_chars` splits.
    - A *row group* is a run of group rows (a label and no values) and the rows after it, up to the
      next group row. Rows before the first group row are a group. Consecutive group rows stay
      together, so `Assets` and `Current assets:` are never parted.
    - Groups fill a part in order while its full text fits within `table_max_chars`, and each part
      repeats the `Section:`, `Table:` and `Note:` lines and both header rows.
    - A group that doesn't fit in a part of its own splits between rows; each part after the first
      starts with the group's group rows again.
    - A row is never cut: a single row too long for a part is a part of its own, over the limit.
    - `part` is 0-based, and `parts` is the count. All parts share the table's byte range.
12. **Chunk ids** are `str(uuid.uuid5(NAMESPACE, f"{accession_no}:{char_start}:{char_end}:{part}"))`
    with `NAMESPACE = uuid.UUID("5d1c3f0e-7a42-4e6b-9b3d-2c8e1f6a4d70")`. A prose chunk has
    `part` 0. The id is a valid Qdrant point id, depends on no text and no fiscal label, and two
    runs over the same bytes give the same ids. The namespace differs from the XBRL flow's, whose
    offsets are into other bytes.
13. **Byte ranges.** Offsets are UTF-8 byte offsets into the stripped document, as in
    `Html10kSection`. A chunk starts at the opening `<` of its first block. Its `char_end` is the
    next chunk's `char_start`, or the document's length, and the first chunk's `char_start` is 0,
    as in `XBRLHTMLChunker._finalize`. So every byte belongs to one chunk, and parts of one table,
    which share a range, are the only exception. `prev_chunk_id` and `next_chunk_id` link chunks in
    order, parts included; the ends are `None`.

### C. The index

14. **`build_payload(chunk, filing)` is the payload contract.** It is exactly these keys:

    | Key | Value |
    |---|---|
    | `chunk_id`, `accession_no`, `text` | From the chunk |
    | `cik`, `ticker`, `company_name`, `fiscal_year`, `fiscal_period`, `form_type`, `period_end` | From `filing` |
    | `section_key`, `section_label` | From the chunk |
    | `section_keys` | From the chunk, as a list |
    | `html_char_start`, `html_char_end` | The chunk's `char_start`, `char_end` |
    | `contains_table` | True for a data table's chunk |
    | `table_index`, `part`, `parts` | From the chunk; `table_index` is `None` for prose |
    | `prev_chunk_id`, `next_chunk_id` | From the chunk |

15. **The fiscal label is passed in.** `filing` is a mapping with exactly the keys `cik`, `ticker`,
    `company_name`, `accession_no`, `fiscal_year`, `fiscal_period`, `form_type`, `period_end`: the
    `filing_registry` row, whose `fiscal_year` is the label both flows share
    (`fiscal-label-alignment`). The caller reads it. These modules read no database, and never
    derive a year from `period_end`. `build_payload` raises `ValueError` on a missing or unknown key,
    a `fiscal_year` that is not an `int`, or an `accession_no` other than the chunk's.
16. **`embedding_input(payload)`** is `{company} FY{fiscal_year} {section_label} :: {text}`, as
    `XBRLBGEEmbeddingGenerator.build_text_embedding_input` builds it, where `company` is the first
    non-empty of `ticker`, `company_name`, `cik`. With no section label it is
    `{company} FY{fiscal_year} :: {text}`.
17. **`Html10kQdrantVectorDBClient` holds the collection.** It wraps a Qdrant client passed in.
    - `ensure_collection()` creates `html10k_text` when it doesn't exist: dense `text_dense`, 1024
      dimensions, cosine; sparse `text_sparse`; the same names as the XBRL text collection. Then it
      creates these payload indexes: keyword on `cik`, `ticker`, `accession_no`, `fiscal_period`,
      `form_type`, `section_key`, `section_keys`; integer on `fiscal_year`; bool on `contains_table`.
    - `upload_chunks(payloads, vectors)` writes one point per payload through
      `client.upload_points(collection_name=..., points=..., wait=True)`, with `id` the `chunk_id`,
      vectors `text_dense` and `text_sparse`, and the payload unchanged. `vectors` maps `chunk_id` to
      `(DenseEmbedding, SparseEmbedding)`. A payload with no vector or a vector with no payload raises
      `ValueError` before anything is written.
18. **Leakage (plan §5.2).** Nothing in the index comes from XBRL. No payload key contains `xbrl` or
    `fact`, and no payload value or embedding input contains `ix:` markup, a fact id, a context or
    unit id, or a concept name. The new code imports no fact-store module, nor the XBRL flow's
    retriever, embedding or Qdrant client modules. Sharing DOM helpers with the XBRL ingest and
    chunker modules is fine, as `sections.py` already does: they read bytes, not facts.

## Acceptance criteria

All matches are exact.

- **C1 (prose)**: Blocks accumulate and close as in requirement 7: at the size, at a section change, before a data table, and never split. Metamorphic: for any block lengths and size, the chunk texts equal the rule computed in the test. Oracle: requirement 7, by hand; the rule of `XBRLHTMLChunker.chunk_filing`.
- **C2 (table text)**: A data table's chunk text equals the hand-written lines of requirement 9 for stacked headers, a stub cell, group rows, footnotes and a `|`. Property: for any labels and printed numbers, each row appears as one line, and each number as printed. Oracle: requirement 9, by hand.
- **C3 (printed, not computed)**: Title and note lines equal the hand-written ones for a stub-cell note, a note-only caption, a sentence caption, a standing note, a stale standing note and no note; a long caption keeps its end. No computed value appears. Metamorphic: changing the note's scale word changes no row line. Oracle: requirement 10; the filing's printed text.
- **C4 (split)**: A large table splits at row groups into the hand-written parts, repeating the prelude and headers; a group too large splits between rows and repeats its group rows; a long row is one part, whole. Property: for any table and limit, parts keep every row once and in order, each part starts with the same lines, and a part over the limit has one body row besides its group rows. Oracle: requirement 11, by hand.
- **C5 (layout tables)**: A bullet table, a heading table and a one-row text table join the prose stream with their text; an empty table adds nothing; a headed text table and an unheaded table of numbers are data tables. Property: any layout-table token appears in exactly one prose chunk. Oracle: requirement 8, by hand.
- **C6 (item_15)**: `item_15` has the hand-derived range when the statements are inside Item 15, when they follow Item 16 and Signatures, when the notes are in Item 8, when the only entry is an unnumbered `Exhibits`, and by heading; with no Item 15 nothing is added. Metamorphic: without `item_15` the result equals `detect_sections`. Oracle: requirements 1-3; the two stub-Item-8 shapes above.
- **C7 (section tags)**: Chunks carry the `section_key`, `section_keys` and `section_label` of requirement 4 in each stub-Item-8 shape and with notes in Item 8; a heading printed as a table row starts its section. Property: for any stub shape, statements and notes never have `no_section`. Oracle: requirement 4, by hand.
- **C8 (no section)**: Every block of a filing with no sections, and of a topic-ordered filing with sections missing, is indexed with the marker of requirement 5, and a table shows `Section: none`. Property: for any subset of sections, every token appears in exactly one chunk. Oracle: requirement 5, by hand.
- **C9 (chunk ids)**: Each id equals the formula computed in the test. Property: ids are unique and stable across runs, and don't change when text of the same byte length does. Oracle: requirement 12; Python's `uuid5`.
- **C10 (ranges)**: The first chunk starts at 0, the last ends at the document's length, each ends where the next block starts, parts share a range, and links run in order. Property: the same for any document. Oracle: requirement 13; offsets read from markers in the document.
- **C11 (payload)**: `build_payload` returns exactly the hand-written mapping, with `fiscal_year` passed through for Home Depot's filing ending 2025-02-02 (fiscal 2024, not 2025), and refuses each bad `filing`; `embedding_input` equals the hand-written string. Metamorphic: changing `fiscal_year` changes only `fiscal_year` and the `FY` in the input. Oracle: requirements 14-16; `fiscal-label-alignment` requirement 7.
- **C12 (collection)**: Against a fake client, `ensure_collection` makes the vectors and indexes of requirement 17 and is idempotent; `upload_chunks` writes the hand-derived points and refuses a mismatch. Metamorphic: the order of `vectors` doesn't change the points. Oracle: requirement 17.
- **C13 (leakage)**: From an iXBRL document to payload and embedding input, no `ix:`, fact id, context id, unit id or concept name survives, and no key names XBRL or facts; the new modules import no fact store. Property: for any fact ids, the same. Metamorphic: a document and its tag-free twin give equal chunk text. Oracle: plan §5.2.

## Interface

```python
# src/ingest/html10k/sections.py
detect_chunk_sections(stripped_html: bytes) -> list[Html10kSection]

# src/chunking/html10k_chunking.py
class Html10kChunk(NamedTuple):  # chunk_id, accession_no, text, kind, section_key, section_keys,
                                 # section_label, char_start, char_end, table_index, part, parts,
                                 # prev_chunk_id, next_chunk_id
chunk_filing(stripped_html: bytes, accession_no: str, *, chunk_size_chars: int = CHUNK_SIZE_IN_CHARS,
             table_max_chars: int = 2000) -> list[Html10kChunk]

# src/vectorstores/html10k_qdrant_vectordb_client.py
build_payload(chunk: Html10kChunk, filing: Mapping[str, object]) -> dict[str, object]
embedding_input(payload: Mapping[str, object]) -> str
class Html10kQdrantVectorDBClient:
    __init__(self, client: QdrantClient) -> None
    ensure_collection(self) -> None
    upload_chunks(self, payloads: Sequence[Mapping[str, object]],
                  vectors: Mapping[str, tuple[DenseEmbedding, SparseEmbedding]]) -> None
```

`kind` is `"prose"` or `"table"`. `section_keys` is a tuple. `Html10kChunk` is a NamedTuple with its
fields in this order. A spec-time class may hold only a docstring and stubs, so the stub declares
it empty, the implementer adds the fields, and spec tests get chunks only from `chunk_filing`.

## Notes

**`detect_sections` is unchanged**, as the human asked to prefer. Its tests and its use by anything
else keep their meaning, and a new function carries the key. `Html10kSection` needs no change.

**`ParsedTable` carries neither the stub text nor the standing note**, and requirements 9 and 10
print both. The implementer may add what's needed to `tables.py`, for example a new function that
returns them per table. Every existing result of `parse_table` and `parse_tables` stays the same, as
the retired `html10k-tables` tests require.

**Why item 15 ends at the notes and not at Item 16.** QCOM: Item 15 starts at byte 323,139, Item 16
at 367,642, Signatures at 367,949, the balance sheets at 377,218 and the notes at 431,772, to the
end of the document. NFLX is the same with the Signatures after the notes. Ending at the next item
leaves their statements in no section. The cost is that a stub filer's Item 16 and signature block
are tagged `item_15`, which is harmless. In a filer with notes in Item 8, `notes` starts before
Item 15, so its `item_15` is the exhibit list and ends at the next boundary.

**The spike.** A scratch script read the latest 10-K on disk of each of 32 development filers (TSM
has no 10-K), through `ix_strip_html`, `detect_sections` and `parse_tables`, and found one parsed
table per `<table>` in every filing. Of number-bearing tables outside every section, the rule of
requirements 2-3 takes NVDA from 11 to 5, QCOM 11 to 3, NFLX 9 to 3, ORCL 14 to 8, and
VST 32 to 9. No held-out filer was read. The scratch code is not committed, and no expected value
in the tests comes from it.

**Known limits.**
- Tables in Items 2, 3, 5, 9A and the like remain `no_section`, by requirement 5; the keys are
  those of `html10k-strip-sections` plus `item_15`. INTC has 54 such tables.
- The nearest-block caption is `html10k-tables`'s. When a title is printed in a layout table the
  caption is empty, so a filer like MCD can lose titles. 151 of 2,556 tables have no caption.
- A long caption whose scale statement sits mid-paragraph can lose it in the cut.
- `Note:` depends on the parser recognising a note. An unrecognised one in a table's own header
  is still printed there; an unrecognised standing note is not shown.
- `table_max_chars` of 2000 is a judgement: the embedder takes 8,192 tokens, so no table is
  truncated at any size, and the limit serves ranking and the reranker. On the same 32 filings a
  scratch run split 241 tables. The retriever spec may revisit it.
- The parser reads the first row of a multi-row text table as a header, so such a layout table
  (two rows of bullets, a cover page's name and number) is a data table. It stays indexed, in
  markdown.

**Implementer, also do:** add `html10k_chunking` to the feature manifest if the gate asks for it, and
a line in `docs/design/` when the module-level guide of plan §5.4 is written. Run all of `test/spec`:
the retired `html10k-strip-sections` and `html10k-tables` tests must keep passing.
