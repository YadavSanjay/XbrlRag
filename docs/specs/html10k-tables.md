# html10k-tables — one HTML table into a structured grid, and its scorer

v2 plan [§5](../design/XbrlRag_v2_Plan.md) calls tables the hard part of the HTML 10-K flow,
and the phase 3 exit ([§8](../design/XbrlRag_v2_Plan.md)) needs the table parser at **95% or
better, exact, on the iXBRL-derived cell oracle**. This change is the table stage only:
`src/ingest/html10k/tables.py`, which parses tables, and the scorer that compares a parse with
the oracle.

Out of scope: ix-strip, sections, chunking, the index, retrieval and the `html_rag` arm.
Generating the oracle from `data/` is an operator step (see Notes), not a spec test.

## Requirement

1. **The parser reads printed text and table structure only** (plan §5.2). It takes tag-free
   HTML. It never reads `ix:` elements or attributes, the fact store or an `xbrl_*` field. If
   tagging markup does reach it, unknown inline elements are transparent: the result is the
   same as without them. It never takes a value, scale or sign from an attribute.

2. **A table becomes a `ParsedTable`**: columns, then rows of cells, one cell per column.
   - The label column is not a value column. It is the leftmost content; a label that spans
     several `<td>` positions is one label.
   - One value position is often several `<td>`: `$` + `1,234` + `)` + `%`. They merge into one
     `Cell`, and a column's position is found from the header spans and the numbers below them,
     never from the raw `<td>` index. Rows in one table may split the same column differently.
   - `colspan` and `rowspan` are honoured. A label with a `rowspan` is repeated in each row it
     covers.
   - Blank spacer rows are dropped. A row with a label and no values stays, with empty cells:
     it is a *group row*, and its label scopes the rows below it up to the next group row.
   - A cell keeps its location in the source: `source_row` is the `<tr>` ordinal in the table,
     `source_cell` the `<td>`/`<th>` ordinal in that row of the cell that holds the number,
     dash or text. Both are 0-based, and neither is span-expanded.

3. **Headers and periods.** `Column.headers` stacks the header texts above a column. A spanning
   header repeats under each column it covers. `Column.period` is:
   - `YYYY-MM-DD` when the headers state a full date, even when it is built from two levels
     (`Year Ended December 31,` above or beside `2023`) or sits in the top-left stub cell;
   - `YYYY` for a year alone (`2024`, `Fiscal 2024`);
   - `None` for anything else, including a comparison (`2024 vs. 2023`).

4. **Scale.** `ParsedTable.scale` comes from the first of these that states one:
   a note inside the table (typically the top-left cell), then `caption`, then `scale_note`,
   otherwise 0. `Cell.scale` is the table's, unless, in this order:
   1. the cell is a percent: −2;
   2. its row is per-share (its own label, or a group row whose scope is per-share): 0;
   3. its own label, then its group row, states a scale (`(000's)`, `(in thousands)`): that one;
   4. its column header states a scale (`Sales (in millions)`): that one;
   5. the note names a separate scale for shares (`dollars in millions, shares in thousands`)
      and the row is a share count: that one;
   6. the note's `except` list names what the row counts
      (`except share, per share, and warehouse count data`): 0.

   A group row such as `Share and Per Share (Note 8):` is not per-share in scope: a share-count
   row under it keeps the table's scale. Numbers in prose are not notes:
   `Capital expenditures were $4,710 million in 2024.` states no scale.

5. **Currency.** `Cell.currency` is the code of a symbol printed with that cell (in its own
   `<td>` or a split one), else `None`; there is no inference across cells.
   `ParsedTable.currency` comes from a note in the table or caption, otherwise from the first
   symbol printed in the body, otherwise `None`. Symbols: `$` and `US$` USD, `€` EUR, `£` GBP,
   `¥` JPY, `NT$` TWD.

6. **Values and sign.** `Cell.value` is the printed number as a `Decimal` with its printed sign:
   parentheses, split parentheses (`(1,646` + `)`), `$(654)`, `($1,006)`, a leading hyphen or
   U+2212. It is not scaled; `Cell.scaled_value` applies the scale. The parser reports what
   is printed: `(169)` beside "Interest expense" is −169, whatever the concept's balance type.

7. **Nil.** A cell whose content is only a dash (em, en or hyphen), with or without `$` or `%`,
   has `is_nil=True` and `value=None`. It never becomes 0. A printed `0` is the number 0. Text
   such as `N/A` or `NM` is neither: `value=None`, `is_nil=False`, and `text` keeps it. An empty
   position has `value=None`, `is_nil=False` and no source location.

8. **Footnotes.** A marker beside a number or label goes to `footnotes` and never into the value:
   a `(1)` or `(a)` in the next `<td>` or the same one, `*`, `†`, and superscripts, either
   `<sup>` or a raised `<span>` (`position:relative;top:-3.15pt`, the form filing agents use).
   `7,392` with a superscript `2` is 7392. A `(1)` that is the only content of a value position
   is the number −1. Label markers leave `Row.label` and go to `Row.footnotes`.

9. **Percent.** A cell is a percent, with `is_percent=True` and scale −2, and `value` as printed
   (`10.92`), when any of these holds:
   - it prints `%` (`%` in the same or next `<td>`, `(1.4)%`, split or not);
   - its column header says so (`% of Net Sales`, `Percentage Change`);
   - its row label says so (`Operating margin (%)`);
   - a cell in its column prints `%` and no cell in its column prints a currency symbol: a
     rate table often prints `%` only on its first and last rows.

   A `%` row in a column of dollars stays alone: `Gross margin percentage` is a percent and the
   `$` rows above it are not.

10. **The document walk.** `parse_tables` returns one `ParsedTable` per `<table>` in document
    order, layout tables included, so `table_index` is the plain ordinal and matches the
    oracle's. The `caption` is the text of the nearest block before the table, when no table
    lies between. Standing notes carry forward:
    - A block whose text *begins* with a scale statement (`(amounts in millions, except per
      share data)`, `Amounts in millions of dollars except ...`, `($ in millions)`) is a
      *standing note*. It stays in force, as `scale_note`, until the next standing note. Filers
      print one at the head of the notes, or on each page, and nothing beside each table.
    - A sentence that *ends* in one (`... as follows (in thousands):`) applies only to the next
      table, as its caption.
    - A note inside a table applies to that table only.

11. **The oracle record.** One JSON object per line, with exactly these keys:

    ```json
    {"accession": "0000000000-24-000001", "table_index": 12, "source_row": 11, "source_cell": 1,
     "value": "169", "negative": true, "scale": 6, "nil": false, "printed": "(169)",
     "concept": "us-gaap:InterestExpense", "unit": "USD", "tagged_negative": false}
    ```

    | key | meaning |
    |---|---|
    | `accession`, `table_index`, `source_row`, `source_cell` | The location; the same ordinals as a parse, all 0-based |
    | `value` | The printed magnitude as a decimal *string*, never negative, never a JSON number. `null` for a nil |
    | `negative` | The **printed** sign: parentheses or a minus around the tagged number, in its cell or a split one |
    | `scale` | The tag's `scale` attribute, default 0. −2 for a percent |
    | `nil` | True for a printed dash on a nil or `fixed-zero` tag |
    | `printed`, `concept` | For the hand-check |
    | `unit`, `tagged_negative` | For diagnosis; not scored. `tagged_negative` is the tag's own `sign` attribute |

    `nil=true` requires `value=null`, and `nil=false` a value. `parse_oracle_record` raises
    `ValueError` on a missing or unknown key, a JSON number or a negative in `value`, a `nil`
    and `value` that disagree, or a non-integer ordinal or scale.

12. **The scorer.** `score_tables` looks each oracle cell up by accession, then
    `ParsedTable.table_index`, then `(source_row, source_cell)`. A cell is **exact** when:
    - the oracle is nil and the parsed cell is nil (scale and sign are not compared); or
    - both are numeric, magnitudes are equal as decimals (`1234.50` = `1234.5`), scales are
      equal, and signs are equal (a zero has no sign).

    Otherwise it is one or more errors. A value error is a different magnitude, or a nil against
    anything else, a text cell included. Scale and sign errors are counted only when both sides
    are numeric. An oracle cell with no parsed cell at its location is `missing`, including
    every cell of an accession absent from `parsed`, so a filing that failed to parse lowers the
    score rather than leaving it. An empty oracle or a repeated location raises `ValueError`, so
    an accuracy can't come from nothing.

## Acceptance criteria

- **C1**: The grid is right for spanning and split cells: value columns, row labels, values and source locations match the hand-read snippet exactly, every row has one cell per column, and doubling every colspan or inserting blank rows changes nothing. Oracle: hand-read snippets.
- **C2**: Stacked headers, header row count and column periods match exactly for two-period, three-period, spanning, stub-cell and period-free headers. Oracle: hand-read snippets; generated dates.
- **C3**: Table and cell scale match exactly, by the precedence in requirement 4, and `scaled_value` is value times ten to the scale. Oracle: hand-read snippets; closed form.
- **C4**: Cell and table currency match exactly; a symbol changes no value. Oracle: hand-read snippets.
- **C5**: Values and printed signs match exactly as decimals for every printed form, split forms included. Oracle: hand-read snippets; generated numbers.
- **C6**: A dash is nil and never 0; a printed 0 is 0; text and empty positions are neither. Oracle: hand-read snippets.
- **C7**: Footnote markers are captured and never change a value, and a lone `(1)` in a value position is −1. Oracle: hand-read snippets; removing the markers.
- **C8**: Percent cells have scale −2 and the printed value, by the rules in requirement 9, and dollar rows beside them keep theirs. Oracle: hand-read snippets.
- **C9**: `parse_tables` yields one table per `<table>` with the plain ordinal, and the nearest caption; standing, sentence and in-table notes reach the right tables. Oracle: hand-read synthetic document.
- **C10**: Leakage: parsing is unchanged when tags with false attributes are wrapped around the numbers, and the module reads no tagging markup, fact store or `xbrl_*` field. Oracle: the plan's §5.2 rule; tag-free twin of each snippet.
- **C11**: `parse_oracle_record` returns the typed cell for a valid record and raises `ValueError` on each malformed one. Oracle: the record format in requirement 11.
- **C12**: `score_tables` counts exact, missing, value, scale and sign errors as in requirement 12, within 1e-12 on accuracy, refuses an empty or duplicated oracle, and counts an absent filing as missing. Oracle: hand-built tables with a hand count.

## Interface

```python
# src/ingest/html10k/tables.py
class Cell(NamedTuple):         # text, value, scale, is_nil, is_percent, currency, footnotes,
                                # source_row, source_cell
    scaled_value: Decimal | None   # property
class Row(NamedTuple):          # label, footnotes, cells, source_row
class Column(NamedTuple):       # headers, period
class ParsedTable(NamedTuple):  # table_index, caption, scale, currency, header_row_count,
                                # columns, rows
class OracleCell(NamedTuple):   # accession, table_index, source_row, source_cell, value, negative,
                                # scale, nil, printed, concept, unit, tagged_negative
class TableScore(NamedTuple):   # total, exact, missing, value_errors, scale_errors, sign_errors,
                                # mismatches
    accuracy: float             # property

parse_table(table_html: str, *, caption: str = "", scale_note: str = "", table_index: int = 0) -> ParsedTable
parse_tables(document_html: str) -> list[ParsedTable]
parse_oracle_record(record: Mapping[str, object]) -> OracleCell
score_tables(parsed: Mapping[str, Sequence[ParsedTable]], oracle: Iterable[OracleCell]) -> TableScore
```

## Notes

**The classes are NamedTuples, with their fields in the order listed above.** The stubs declare
them empty, since a spec-time class may hold only a docstring and stubs; the implementer adds the
fields. Spec tests therefore never construct one. They get tables from `parse_table` and oracle
cells from `parse_oracle_record`, compare by `==`, and use `_replace`.

**Fixtures** are small committed snippets under `test/spec/html10k-tables/fixtures/`. Each is
either an excerpt of a public-domain SEC filing by a development filer, with the styling removed
and its structure (spans, split cells, numbers) kept, or synthetic. The file's opening comment
says which. Expected values are read by hand from the snippet and shown in the test.

**The sign in the oracle is the printed sign, not the tag's.** The plan (§5.3) says a tagged cell
carries value, scale, sign and unit, but the `sign` attribute of a tag is the sign of the XBRL
value, not of the print. `(169)` beside "Interest expense" is tagged `169` with no `sign`, since
the concept is already an expense. A spike over the FY2024 10-Ks of five development filers (COST,
KO, MSFT, HD, UNP) found the two differ on about 14% of 6,340 tagged table cells. The check was
crude: it looks only for a leading parenthesis or minus, and it counts numbers inside label cells.
A parser can only see the print, so scoring it on the tag's sign would fail correct parses. The
oracle's `negative` is therefore read from the characters around the tag, and the tag's own sign
stays in `tagged_negative` for diagnosis. This makes the sign part of the oracle less independent
than value and scale: it comes from the same page, by a rule that has the tag to mark exactly where
the number is. The hand-check covers it.

**Building the oracle** (`scripts/html10k/build_table_oracle.py`, written by the implementer, output
gitignored under `data/`). For each 10-K of a development filer on disk, never a held-out filer,
walk each `ix:nonFraction` inside a `<td>` or `<th>` of a `<table>` and write one record, unless:
- the tag is nested in another `ix:nonFraction` (keep the outer one);
- the cell holds more than one tag, or printed letters outside the tag (a label such as
  `less allowances of $506 and $502`, a range such as `35 - 40`, `2.70% Senior notes`);
- the tag is nil and the cell prints nothing;
- two tags give the same location different values or scales (count these and report them).

`value` comes from the tag's text with its `format` applied, `scale` from its `scale` attribute, and
`nil` is true for a nil or `ixt:fixed-zero` tag that prints a dash. Ordinals count every `<table>`,
`<tr>` and `<td>`/`<th>` in document order, so ix-strip, which removes no structural element, leaves
them unchanged. The generator reports how many tags it dropped, by reason: a rule that drops too
much would flatter the score.

**Measuring the 95%.** Strip each filing with the ix-strip mode, run `parse_tables`, and call
`score_tables` with all the filings. The phase 3 exit reads `TableScore.accuracy` pooled over every
oracle cell of the development filers, on their FY2024 10-Ks at least. Report it with the error
counts by kind and the accuracy per filer, because pooling lets a large filer hide a small one's
failure. Run it on tag-free input, so the score can't lean on tags.

**The hand-check guards the oracle** (plan §5.3): where the tagging is wrong, the oracle is wrong,
and the parser's score says nothing. Draw about 50 oracle cells with
`random.Random(20261030).sample(sorted(records), 50)`, sorted on the location, and commit the
sample's locations. Someone who did not write the generator, a human or a separate agent, then
reads each cell off the rendered filing and records its value, scale (from the caption) and sign
against the record. Every disagreement is explained. A generator bug is fixed and the oracle
rebuilt. A filer's tagging error is excluded by location, with a reason, in a committed list. The
check must end with no unexplained disagreement before the 95% is quoted, and the number quoted
states the seed.

**Known limits.** Percent and per-share rules read labels and headers, so an unusual label can miss.
That is what the oracle run is for, and misses are fixed in `src/` against it. Tables that restate
their header mid-way (a table split across pages) are not specified here; if the oracle run shows
them to matter, that is a new change. Nested tables count in the ordinals, and only each table's own
rows are parsed.
