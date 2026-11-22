# html10k-strip-sections — ix-strip canonicalization and 10-K section detection

Phase 3 of the v2 plan (docs/design/XbrlRag_v2_Plan.md §5, rows *Canonicalize* and *Sections*
of §5.3; the leakage rule is §5.2). The HTML flow reads a 10-K the way an analyst does, with
the XBRL tags removed, and it is the baseline XbrlRag is measured against. If any XBRL reaches
it, the comparison is void.

This change is the first two stages of that flow. Both are pure functions over bytes, in a new
package `src/ingest/html10k/`:

1. `ix_strip_html` turns a primary document, iXBRL or not, into clean XML with every trace of
   XBRL removed and its visible text and table structure kept.
2. `detect_sections` finds Items 1, 1A, 7, 7A, 8 and the notes in that output, from the ToC
   when it has one and from headings when it does not.

**The XBRL flow does not change.** `_canonicalize_html` and `build_section_tree` keep their
exact behaviour, because chunk ids depend on canonical offsets.

What a survey of the latest 10-K of 32 development filers found, which shaped the rules below:

- ToCs in three shapes: every cell a link (AVGO, WMT), the link on the title only with
  `Item 1A.` in a plain cell (AMZN, COST, MSFT), and links on subsections too (UPS, CEG).
- TXN has no ToC. Its headings are `<div>ITEM 7. Management’s discussion…</div>`. AVGO, WMT,
  VST and CEG split the heading: `<td>ITEM 7.</td><td>MANAGEMENT’S…</td>`.
- MCD and INTC have page-number ToCs and topic-ordered bodies; MCD has two text links, so
  `build_section_tree` gives it three nodes. Both have a back-of-filing cross-reference index.
- NVDA, QCOM, NFLX and ORCL have a stub Item 8; the statements and notes come later, in Item 15
  for NVDA and QCOM.
- MSFT and UPS repeat `Item 7` or `NOTES TO…` as a running page header.
- A malformed iXBRL document (a bare `&nbsp;` is enough) falls to the HTML recovery parser,
  which lowercases `ix:nonfraction` and `contextref` and gives them no namespace.
- A Windows-1252 document makes `_canonicalize_html` raise `UnicodeDecodeError`.
- In the five filers checked for it (AAPL, AVGO, KO, MSFT, TXN), `display:none` outside the
  header wrapper occurs only on empty layout cells, so hidden content is `ix:header`.
- INTC's index lists `Item 1. | Business:` with no page in the row; the pages are on its
  sub-rows.

## Requirement

### A. `ix_strip_html(source_bytes, name="document") -> bytes`

1. **Output.** UTF-8 bytes that a strict XML parser accepts, whatever the input was.
2. **Decoding.** Bytes that are valid UTF-8 are read as UTF-8, otherwise as Windows-1252. A
   character in U+0080–U+009F (`&#146;`, `&#151;`, as older filings write punctuation) becomes
   its Windows-1252 equivalent (`’`, `—`).
3. **Parsing.** A well-formed document is parsed as XML, anything else by HTML recovery. Both
   paths give the same result for the same content: all the rules below hold on both, so XBRL
   markup is recognised by namespace on the first path and by lowercased prefixed name on the
   second. Element and attribute names in the output are lower-case on both, because an older
   document can be well-formed and upper-case (`<HTML><BODY><P>…</P></BODY></HTML>`).
4. **Removed with their content:** `ix:header` (so `ix:hidden`, `ix:references`,
   `ix:resources`, the `xbrli:` contexts and units and the `link:` references inside it),
   `style`, `script`, `link`, comments and processing instructions. The wrapper element of
   `ix:header` (Workiva's `<div style="display:none">`) stays, empty. No other element is
   removed: a `display:none` layout cell stays, because table structure is kept.
5. **Unwrapped, keeping text and children in place:** every other inline-XBRL element, in any
   inline-XBRL namespace and under any prefix: `ix:nonFraction`, `ix:nonNumeric`,
   `ix:fraction`, `ix:numerator`, `ix:denominator`, `ix:continuation`, `ix:footnote`,
   `ix:exclude`, `ix:tuple`, `ix:relationship`.
6. **Attributes.** `style` and `on*` handlers go, as in the canonicalizer (`style` can carry a
   fact id: `-sec-ix-hidden:f-12`). XBRL attributes (`contextRef`, `unitRef`, `decimals`,
   `precision`, `scale`, `sign`, `format`, `continuedAt`, `xsi:nil` and the like) never
   survive. Namespace-qualified attributes go, except `xml:lang`. All other attributes stay:
   `id`, `name`, `href`, `class`, `colspan`, `rowspan`, `align`, `width`.
7. **Namespace declarations.** None survive except the default XHTML one.
8. **Links to fact ids.** An `href="#x"` whose target `x` was the id of a removed element loses
   the `href`; the `a` element and its text stay.
9. **Whitespace.** After unwrapping, every text run outside `head` is normalised the way the
   canonicalizer normalises prose: each run of whitespace or NBSP becomes one ASCII space. This
   applies to former fact text too, unlike the canonicalizer: tagging must not change the
   output. Text is not otherwise trimmed.
10. **No value is reinterpreted.** Visible text is what the filing displays. `scale`, `sign`
    and `format` are never applied; a nil fact leaves its cell empty.
11. **Tag independence.** A document and the same document with its inline-XBRL markup taken
    out by hand (the header's wrapper left empty) give byte-identical output.

### B. `detect_sections(stripped_html) -> list[Html10kSection]`

The input is `ix_strip_html` output. A section is a half-open **UTF-8 byte** range into that
input, the same convention as `SectionNode`. The result is sorted by `char_start`, holds at
most one section per key, and has no entry for a section not found. Nothing found gives `[]`,
as for a Part III-only 10-K/A; it is not an error.

12. **Keys and titles.** Comparison is case-insensitive, on whitespace-collapsed text, treats
    `'` and `’` alike and `&` as `and`, and ignores trailing `.` or `:`.

    | Key | Item | Title (full match) |
    |---|---|---|
    | `item_1` | 1 | `Business`, `Business and Properties`, `Description of Business` |
    | `item_1a` | 1A | `Risk Factors` |
    | `item_7` | 7 | `Management's Discussion and Analysis`, optionally followed by ` of …` |
    | `item_7a` | 7A | `Quantitative and Qualitative Disclosure[s] About Market Risk[s]` |
    | `item_8` | 8 | `[Consolidated ]Financial Statements[ and Supplementary Data]` |
    | `notes` | | `[Combined ]Notes to [the ][Consolidated ][Combined ]Financial Statements` |

    A **boundary title** starts with one of: `Unresolved Staff Comments`, `Cybersecurity`,
    `Properties`, `Legal Proceedings`, `Mine Safety Disclosures`, `Market for`, `[Reserved]`,
    `Reserved`, `Selected Financial Data`, `Changes in and Disagreements`, `Controls and
    Procedures`, `Other Information`, `Disclosure Regarding Foreign Jurisdictions`,
    `Directors`, `Executive Compensation`, `Security Ownership`, `Certain Relationships`,
    `Principal Account`, `Exhibits`, `Form 10-K Summary`, `Signature`.

13. **ToC links.** A link is an element with `href="#target"` where `target` is the `id` or
    `name` of an element in the document. A link with any other target contributes nothing.
    Its **row** is the enclosing `tr`; outside a table, the link stands alone. A row or link
    with any cell reading `Page <n>` or `Pages <n>…` (a cross-reference index) contributes
    nothing. Its **label** is the row's non-empty cells that are not page references (digits,
    a digit range, or `F-<n>`), joined by a space; a link outside a table uses its own text. A
    page-number link therefore gets its label from its row, however many text links the
    filing also has.
14. **Mapping an entry.** An item number at the start of the label (`Item 1A.`, `ITEM 7`,
    `PART II, ITEM 7.`) decides the entry's item, whatever the rest says. Otherwise the label,
    in full, must match a key's title or start with a boundary title. An entry that does
    neither is a subsection (`Overview`, `Risk Factors Summary`, `Business Summary`, `Note 1`,
    `Table of Contents`) and has no effect. For each key the **first** entry in document order
    wins, so a body link to a subsection never moves a section. The section's `char_start` is
    the opening `<` of the target element and `source` is `"toc"`.
15. **Headings.** A key with no ToC entry is looked for in headings, key by key. A **block** is
    a `tr`, or a `p`, `div` or `h1`–`h6` with no such element inside it and no `tr` ancestor.
    Its text is collapsed, with a `tr`'s cells joined by a space. A block is an **item
    candidate** when it has at most 200 characters and reads, in order: an optional
    `PART <roman>` with optional punctuation; `Item <n>[<letter>]`, or `Items <n> and <m>` which
    is item `<n>`; an optional `.`, `:`, or dash; and a rest, where the rest is empty or, for
    the keys of Requirement 12, a full title match; for any other item, it starts with an
    uppercase letter or `[`. A block is a **notes candidate** when it is, in full, the `notes`
    title. Neither kind counts when it ends in a page reference, `Page <n>`, `Pages <n>…` or
    `Not applicable`, or contains `(continued)`, or when it is a `tr` of a table in which any
    row has a cell reading `Page <n>` or `Pages <n>…` (a cross-reference index).
16. **Contents blocks.** Five or more consecutive item candidates, with no item repeated and
    fewer than 20 characters of visible text between each and the next, are an unlinked table of
    contents, and none of them counts. A repeated item ends the run, so the real `Item 1`
    heading right after such a block is not part of it.
17. **Which heading.** The first remaining candidate of an item in document order is its
    heading; later ones are running headers. The `notes` heading is the first notes candidate
    at or after `item_8`'s start, when `item_8` was found. `char_start` is the opening `<` of
    the block and `source` is `"heading"`.
18. **Ends.** A section's `char_end` is the first **boundary** after its `char_start`, or the
    document's length. The boundaries are: the target of the first ToC entry of each item or
    boundary title, so a later body link is not one; the heading of every item not resolved by
    the ToC; and each block reading in full `Signature` or `Signatures`. A `notes` entry or
    heading is not a boundary, so `notes` sits inside `item_8` when the filing puts it there. No
    other two sections overlap.
19. **Label.** The ToC entry's label, or the heading block's text.

## Acceptance criteria

All matches are exact.

- **C1 (no XBRL survives)**: For a Workiva-shaped fixture with a header, hidden facts, a
  reference to a hidden fact and a link to a fact id, the output has no element outside XHTML
  or no namespace, no comment, no XBRL attribute, no prefixed namespace declaration, and none
  of the fixture's fact ids, context ids, unit ids or concept names. The same holds when the
  document is malformed and takes the recovery path.

  Property: for generated documents with any nesting of inline-XBRL elements, under the `ix`
  prefix or another, well-formed or not, the same holds.

  Oracle: Requirements 3–8; plan §5.2. The ids are built into the fixture.
- **C2 (visible text and tables)**: For a fixture with `ix:nonNumeric` text blocks around
  tables, nested tags, a continuation, a footnote, an excluded page marker and a fraction, the
  paragraphs and each table's cell grid, `colspan` and `rowspan` equal the hand-derived values.

  Property: a generated document and the same document with the inline-XBRL markup left out
  give byte-identical output (Requirement 11), on both parser paths.

  Oracle: Requirements 5, 9, 11; expected values derived by hand and shown in the test.
- **C3 (hidden content)**: Text under `ix:hidden`, `ix:references` and `ix:resources` is gone,
  the wrapper stays empty, a `display:none` layout cell stays, and prose that merely looks like
  XBRL (`Appendix: A`, the word `contextRef`) is kept unchanged.

  Property: for any hidden and visible tokens, hidden tokens never appear and visible ones do.

  Oracle: Requirement 4.
- **C4 (values are not reinterpreted)**: A scaled fact, a negative shown in parentheses, a
  zero shown as a dash and a nil fact render as the filing displays them: `1,234`, `(56)`, `—`
  and an empty cell.

  Metamorphic: changing a fact's `scale`, `sign`, `decimals` or `format` leaves the output
  byte-identical.

  Oracle: Requirement 10.
- **C5 (older filings)**: An upper-case, unclosed, Windows-1252 HTML 10-K with no XBRL gives
  strict-XML output with the hand-derived text and cell grid, `’` and `—` from both raw bytes
  and `&#146;`/`&#151;` references, and its `name` anchors intact.

  Property: for any text from the Windows-1252 punctuation set, the raw byte and the numeric
  reference both give the Unicode character.

  Oracle: Requirement 2; the Windows-1252 code page.
- **C6 (the XBRL flow is unchanged)**: `_canonicalize_html` on a small iXBRL document gives the
  hand-derived bytes, with `ix:` markup and fact text intact, while `ix_strip_html` on the same
  document removes them; `build_section_tree` on that canonical output gives the anchors' byte
  offsets.

  Metamorphic: for any fact text, `_canonicalize_html` keeps it byte for byte, double spaces
  and NBSP included, while `ix_strip_html` normalises it.

  Oracle: the module docstring of `html_canonicalizer.py`; Requirement 9.
- **C7 (ToC path)**: A fixture with link-all rows, title-only links, a page reference in a plain
  cell, subsection links, a Risk Factors Summary row, statement and note entries and body
  cross-references gives each key's exact range from anchor to next item-level anchor, with
  `source="toc"` and its label. A page-number ToC with two text links and a cross-reference
  index in a topic-ordered filing gives the sections it names, in document order. An older
  `<A HREF>`/`<A NAME>` ToC works.

  Property: for any subset of items and any mix of row shapes, each section holds its own body
  token and no other item's.

  Oracle: Requirements 13, 14, 18; offsets are read from the anchors in the test.
- **C8 (heading path)**: A fixture with no ToC gives each key's exact range from its heading,
  with `source="heading"`, in the shapes `ITEM 7. Title`, a split `Item 7.` + title in a table
  row, a title-less `ITEM 7.` block, `PART II, ITEM 7.`, `Items 1 and 2.`, and on an older
  non-iXBRL filing. Byte offsets count UTF-8 bytes, not characters.

  Property: for any subset of items and any mix of heading shapes, each section holds its own
  body token and no other item's.

  Oracle: Requirements 15, 17, 18.
- **C9 (what is not a heading)**: None of these moves or creates a section: a sentence starting
  `Item 7 of this report`, a cross-reference inside a sentence, a running header (`Item 7`,
  `Item 7. … (continued)`), an unlinked ToC with page numbers, an unlinked ToC without page
  numbers, a cross-reference index row (`Item 7A | … | Page 22`), a row of such an index with
  no page of its own (`Item 1. | Business:`), an index row for the notes.

  Metamorphic: adding any of these to a filing, outside a heading, leaves every key and start
  of the sections unchanged, apart from the shift in bytes.

  Oracle: Requirements 15–17.
- **C10 (notes)**: `notes` is found by ToC entry or heading, inside `item_8` when the filing
  puts it there and running to Item 9. With a stub Item 8 and the statements in Item 15, `item_8`
  is the stub and `notes` starts at its heading in Item 15 and ends at Signatures. `Combined
  Notes…`, `Notes to the Consolidated…` and `Notes to financial statements` are all found.

  Property: for any notes-title variant, `notes` holds the note bodies and not Item 9.

  Oracle: Requirements 12, 15, 17, 18.
- **C11 (boundaries and absence)**: Item 1A ends at Item 1B and Item 8 at Item 9, though neither
  is a key. A dangling ToC link falls back to the heading for that key alone, and a ToC-sourced
  neighbour ends at it. A missing Item 7A gives no `item_7a`. A Part III-only amendment gives
  `[]`.

  Property: for any generated filing, sections are sorted, keys are unique, `0 <= char_start <
  char_end <= len(document)`, no two overlap except `notes` in `item_8`, and the function is
  deterministic.

  Oracle: Requirements 15, 18 and the section preamble of B.

## Interface

- `src.ingest.html10k.ix_strip.ix_strip_html(source_bytes: bytes, name: str = "document") -> bytes`
- `src.ingest.html10k.sections.Html10kSection` (`NamedTuple`): `key: str`, `label: str`,
  `source: str`, `char_start: int`, `char_end: int`
- `src.ingest.html10k.sections.detect_sections(stripped_html: bytes) -> list[Html10kSection]`

`name` is used only in log messages, as in `_canonicalize_html`. The implementer may share code
with `html_canonicalizer.py` and `xbrl_html_dom_parser.py`, but their behaviour for the XBRL
flow must not change (C6).

## Notes

- **Fixtures are synthetic**, for an invented filer, shaped after the survey above. Expected
  values are derived by hand and shown in the tests. Tests read only committed fixtures.
- **`SectionTree` is not returned.** The plan's §5.3 names it as the reuse, but it is built from
  a file path, its text-versus-page ToC switch is the XBRL flow's, and it has no key for a
  section. Reusing its helpers is the implementer's choice.
- **`style` goes,** as in the canonicalizer, so bold and alignment are not available downstream;
  the heading path does not depend on them.
- **Out of scope:** table parsing (`html10k-tables`), chunking, indexing, retrieval, the arm, a
  CLI and writing artifacts to the `FilingStore`.
- **Known limits.** A topic-ordered filing has no `item_1` or `item_7` if neither its ToC nor its
  headings use the standard titles (MCD's `Business Summary`, INTC's `Overview`). Filing Item 15
  is not a key, so a stub Item 8 leaves the primary statements of NVDA or QCOM outside every
  section. A section incorporated by reference from Exhibit 13 is found as the
  short stub it is. `Items 7 and 7A` is item 7 only.
