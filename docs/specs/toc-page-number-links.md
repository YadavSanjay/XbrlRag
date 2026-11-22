# toc-page-number-links — section tree for a filing whose ToC links are page numbers

Intel's 10-K and 10-Q tables of contents are tables whose links carry only the page number:
the section name sits in another cell of the same row (`<td>Risk Factors</td> … <td><a
href="#i…_169">37</a></td>`). `build_section_tree` takes a section's label from its ToC link
text and drops links whose text is all digits (`xbrl_html_dom_parser.py`, step 2). For Intel
every ToC link is dropped, so each filing gets a ROOT-only tree (2–3 nodes): Item 1A Risk
Factors is never found, and faultline's `section_hint: "Risk Factors"` search returns nothing
for INTC. All six INTC filings (10-K FY2022–2025, 10-Q 2026 Q1–Q2) are affected.

Intel also has a "Form 10-K Cross-Reference Index" at the back: rows such as
`Item 1A. | Risk Factors | Pages 37 - 51` whose page-number links point at **page breaks**
inside sections, not at section starts. Taking labels from those rows splits real sections
with stray nodes (prototype, 2026-10-17).

The other 76 filings in the corpus have a text-labelled ToC. Some of them (NVDA 2026 10-Qs)
also have page-number links to anchors no text link reaches; turning those into sections
would change trees that are already chunked and indexed, so this change leaves them alone.

## Requirement

1. **Page-number ToC.** A ToC link whose text is a page reference — digits, or a range of
   two digit groups joined by `-` or `–` (`37`, `81-87`, `53 – 67`) — takes its label from its
   table row: the text of the row's first cell that is non-empty and not itself a page
   reference (whitespace collapsed).
2. **When it applies.** Only when the filing has no text-labelled ToC: fewer than two
   distinct link targets are reached by a link whose own text is not a page reference.
   (Intel's only text links are its "Table of Contents" back-links, all to one target.)
   A filing with a text-labelled ToC builds exactly the tree it builds today.
3. **One label per target.** A target reached only by page-reference links is labelled from
   the first such link in document order. A target that also has a text link keeps its
   text-link label.
4. **Cross-reference index rows contribute nothing.** A row with any cell reading
   `Page <n>` or `Pages <n>…` (case-insensitive) gives no label, so its links create no
   section.
5. Everything downstream is unchanged: a labelled target becomes a section node exactly as a
   text-linked target does today (offsets, level inference, de-duplication, ROOT sentinel).

## Acceptance criteria

- **C1**: In a document whose ToC rows are `<label cell> … <page-number link>` (plus
  "Table of Contents" back-links), every linked section is a node labelled with its row's
  label, in document order, starting at its anchor's byte offset; a byte inside a section's
  body resolves (`find_section`) to that section's label. Page ranges (`53 - 67`,
  `81–87`) count as page numbers. Exact match.

  Property: for any 1–8 distinct labels with random page numbers, the non-ROOT labels are
  the ToC's back-link label followed by the labels in order, and each body resolves to its
  own label.

  Oracle: Requirement 1–3; the document is built in the test, so the expected labels and
  offsets are known by construction.
- **C2**: Adding a cross-reference index (rows with a `Page …`/`Pages …` cell, linking to
  page-break anchors inside existing sections and to a new anchor) changes nothing: same
  labels, same starts, and a byte after a page break still resolves to the enclosing
  section. Exact match.

  Metamorphic: for any page-number document, adding an index leaves the tree identical.

  Oracle: Requirement 4.
- **C3**: A text-labelled ToC is unaffected. A document whose ToC links carry the labels
  (with page-number links alongside, and one extra row whose only link is a page number to
  an otherwise unlinked anchor) yields exactly the text-linked sections; the extra anchor is
  not a section. Its page-number twin (same sections, label in a cell, page-number links)
  yields the same labels.

  Metamorphic: for any labels, adding page-number-only links to a text-labelled document
  leaves its tree identical, and the twin's labels equal the text document's.

  Oracle: Requirement 2; the text-ToC behaviour is today's.

## Interface

No new functions. The behaviour changes inside `build_section_tree`
(`src/ingest/sec_edgar/xbrl/xbrl_html_dom_parser.py`). This spec is in bug-fix mode, with no
stubs.

## Notes

- Fixtures are synthetic XHTML built in the test (Workiva-style empty `<div id="i…_N"/>`
  anchors, as Intel's filings use), written to `tmp_path`. No filing text is committed.
- Prototype (scratch, 2026-10-17) of these rules: the 76 existing canonical filings' trees
  are identical (section paths and byte ranges); all six INTC filings find "Risk Factors"
  (10-K bodies 104–111 KB; the 10-Qs' Item 1A is a one-line cross-reference to the 10-K).
- **Out of scope:** the NVDA 2026 10-Q page-number-only links (would add six
  financial-statement sub-sections; a change to already-indexed filings, needs its own
  re-chunk plan); labelling sections from the cross-reference index's Item numbers.
- **Validation after the merge:** rebuild trees for all existing canonical filings and diff
  against the current parser (must be identical); INTC FY2022–2026 trees contain "Risk
  Factors"; then the INTC ingest and vector load, and the `/api/v1/narrative/search`
  acceptance check with `section_hint: "Risk Factors"`.
