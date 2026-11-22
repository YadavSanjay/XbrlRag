# html10k-tables-scale-2 — notes printed as running lines and in tables of no numbers

The table parser missed the target on the fresh set of [html10k-tables-scale](html10k-tables-scale.md):
`fresh1` read 78.09% pooled, CVX 33.23%, HON 43.73%, the other six 98.3-99.8%. That is the published
clean reading and stays so. The maintainer declared `fresh1` used up on 2026-10-31, so its errors
could be read for this change.

Both failures are one fault. Each filer states its scale once per page, in a place the parser reads
only for the table directly under it, so every table with a heading or a sentence in between has no
scale. Separately, on HON the note the parser did read could fail to reach the chunk text, which is
what the `html_rag` model reads.

This change states those places as general rules in `src/ingest/html10k/tables.py`, with three
smaller rules for what a note does not reach, and fixes before the fix how the result is measured.
No new function or field; `ParsedTable`, `PrintedTable`, `Cell` and the oracle format do not change.
`src/chunking/html10k_chunking.py` does not change, but **the text of some chunks does**; see
*Chunk text and the reload*.

## Requirement

**No rule is keyed to a filer or justified by one filing alone.** Each has evidence from at least
two filers, or a stated reason why it is the general form; Notes gives both. These rules extend
requirements 4, 9 and 10 of [html10k-tables](html10k-tables.md) and requirements 2, 3 and 9 of
[html10k-tables-scale](html10k-tables-scale.md), whose tests are retired and stay green.

1. **A block that is one parenthesis stating a scale is a note.** When a whole block is one
   parenthesis and states a scale anywhere in it (`(Dollars in tables in millions, except per
   share amounts)`, `(Dollars and shares in millions, except per-share data)`), it is a note at
   the head of that block, with its reach: it stands for the tables after it, until another
   standing note replaces it or the table states its own, and a repeat is not furniture. The limits
   of requirement 3 of html10k-tables-scale hold: a second sentence stands for nothing
   (`(Dollars in billions. Percentages in chart ...)`). A parenthesis about shares alone
   (`(Shares in millions)`) stands for nothing, because it says nothing about the table's dollars.
   This is a rule about blocks: a table cell is read as before.

2. **A note directly above a table is that table's note, however often it prints.** When such a
   block is the nearest one above a table, `PrintedTable.note` holds it, and it is not the title.
   The title is the nearest block above it that is not a running line. So the chunk's `Note:` line
   states it, in every part of a table that splits.

3. **A table of no numbers that states a note.** A table that prints no number and no nil, a year
   not being a number, and that has a cell beginning with a note (`Millions of dollars, except
   per-share amounts`, `(millions of dollars)`), acts as a block for what comes after it: its note
   stands, as in rule 1, whether it has one cell or many. The last such cell wins. The table is
   still a table with its own `table_index`. A table that prints numbers states a note for itself
   alone, as before, and for a continuation of it, and stands for nothing after that.
   `PrintedTable.note` of a table under the standing note holds it, so its `Note:` line does too.

4. **A number too large for its note.** Under a note of millions or more, a whole number printing
   100,000,000 or more is in units: scale 0. A hundred trillion dollars is more than any filer
   reports, and this holds for any cell, where requirement 9 of html10k-tables-scale looks only at
   cells that count shares. A scale stated by the row's label or group, or by a footnote, still
   wins. A note of thousands is unaffected, and so is a number printing decimals: the rule is about
   full counts, and `test_a_cell_always_matches_its_own_oracle` of html10k-tables holds such a
   number to its note.

5. **Cents under a note of thousands or millions.** A number printing exactly two decimals, in a
   row or column that prints a currency symbol, with no percent, under a note of thousands or
   millions, is dollars and cents: scale 0, whatever its label says. A note of billions keeps its
   scale, since `$1.34` billion is how filers print it. So does a label or group that states a
   scale, and a number with one, three or no decimals. A number in neither a row nor a column with a
   currency symbol keeps its scale.

6. **A count of things.** Under a note of a scale, a cell with no currency symbol keeps no scale
   when its row label, or the lowest header of its column, begins `Number of` and goes on to
   anything but shares, units, options, awards, securities, RSUs or PSUs (those remain the share
   counts of requirement 7 of html10k-tables-scale, within three words). `Number of projects`,
   `Number of wells`, `Number of receivable contracts` count whole things. A scale stated by the
   label, group, or a note in the column's header, still wins (`Number of stores (in thousands)`).

## Acceptance criteria

- **C1**: A block that is one parenthesis stating a scale stands for the tables after it until replaced, for every form, and a repeat stays a note; a second sentence, shares alone, or more text around the parenthesis stands for nothing. Oracle: hand-read synthetic documents with wordings from HON and LLY; closed form.
- **C2**: Such a block directly above a table is its `note`, not its title, and reaches a `Note:` line in every part of the chunk, even when it prints on every page. Oracle: hand-read synthetic documents; closed form.
- **C3**: A table of no numbers with a note stands for the tables after it, in one cell or many, and reaches their `Note:` line; a table of numbers stands for nothing after its continuation, and a table's own note outranks it. Oracle: hand-read synthetic documents with wordings from CVX and XOM; closed form.
- **C4**: Under a note of millions or more, a whole number of 100,000,000 or more has scale 0 and one below it keeps the note; a note of thousands, a stated label and a number with decimals are unaffected. Oracle: hand-read synthetic table with wordings from CVX and BA; closed form.
- **C5**: A number of exactly two decimals in a money row or column under thousands or millions has scale 0; billions, other decimals, percents, stated labels and lines with no currency symbol keep theirs. Oracle: hand-read synthetic tables with wordings from AMGN, NKE and DE; closed form.
- **C6**: A cell counting things other than shares has scale 0 by its label or column header; share counts, money and stated scales keep theirs. Oracle: hand-read synthetic tables with wordings from CVX, XOM and DE; closed form.

The accuracy targets below are measured by an operator run, not a spec test, since spec tests read
no `data/`.

## Interface

No new signature. These keep theirs and change behaviour:

```python
# src/ingest/html10k/tables.py
parse_table(table_html: str, *, caption: str = "", scale_note: str = "", table_index: int = 0) -> ParsedTable
parse_tables(document_html: str) -> list[ParsedTable]
parse_printed_tables(document_html: str) -> list[PrintedTable]
# src/chunking/html10k_chunking.py: the code does not change; its output does
chunk_filing(stripped_html: bytes, accession_no: str, *, chunk_size_chars: int = ..., table_max_chars: int = 2000) -> list[Html10kChunk]
```

`parse_table` gets rules 4, 5 and 6 on one table. Rules 1, 2 and 3 need the document, so they are
reached only through the document functions; the implementer may give private helpers whatever
form they need. The tests call only these four.

## Notes

### The fresh set, named before any mismatch was read

**`fresh2`: MA, LIN, AMT, SO, GILD, T, MMM, COP.** FY2024 and FY2025 10-Ks, downloaded to
`data/eval/html10k_oracle/fresh2/` (filings in `fresh2/edgar/`), outside the store.

The rule, fixed and computed before any `fresh1` mismatch was opened and using no filing:

1. **Pool:** every symbol of [`sp100_constituents.csv`](../eval/heldout_v2/sp100_constituents.csv),
   minus the 33 development filers of `xbrl_db`, GOOG (the share-class duplicate of GOOGL), wave
   2, `fresh1`, and the eight held-out filers and four alternates in
   [the draw record](../eval/heldout_v2/README.md) (ANET, PANW, CMCSA, IBM, LRCX, AMAT, LOW, UBER,
   MDLZ, ISRG, TMO, BKNG). `xbrl_heldout` holds the 33 development filers and the eight drawn, so
   this covers it; that database was not queried, the list is the committed record and the v2 plan.
   That leaves 45. The left-out list of the v2 plan stays in the pool: its reasons are accounting
   models and recast history, neither of which touches a parser of printed tables.
2. **Key:** the SHA-256 of `"xbrlrag-html10k-scale-2|" + sha256(csv) + "|" + symbol`, where
   `sha256(csv)` is `04fc31a79811a141ebf48c9dcea3cddb9fbb31635ce485d8dc2f24a64a0fe127`. Symbols are
   ranked by key within their GICS sector, as the file gives it.
3. **Selection:** first the top rank of each sector left out so far: Financials, Real Estate and
   Utilities (the three the held-out pool excluded) and Materials (which no development, wave 2 or
   `fresh1` filer is in). Then, walking the rest of the pool by key, the first filer of each sector
   not yet taken, until there are eight.

| Slot | Sector | Filer | Sector's rank order in the pool |
|---|---|---|---|
| Left out | Financials | MA | MA, MS, C, BLK, GS, BAC, JPM, COF, BRK.B, USB, BNY, SCHW, WFC, AXP |
| Left out | Materials | LIN | LIN |
| Left out | Real Estate | AMT | AMT |
| Left out | Utilities | SO | SO, NEE, DUK |
| Rest | Health Care | GILD | GILD, DHR, BMY, UNH, CVS, MDT, ABBV |
| Rest | Communication Services | T | T |
| Rest | Industrials | MMM | MMM, FDX, RTX, GE, EMR, GEV, GD |
| Rest | Energy | COP | COP |

**Eligibility.** A filer without an inline-XBRL 10-K for each of FY2024 and FY2025 is replaced by
the next rank of its sector, by the filing index alone. Nothing else replaces a filer. In
particular **multi-document status neither selects nor drops one**: it is not looked at. The oracle
and the parser read the primary document, as they did for `fresh1`
(`build_table_oracle.py` takes `manifest["primary_document"]`). A filer whose tables sit in an
exhibit stays in the set, is reported with its cell count, and if it has fewer than 500 cells it is
reported as *not measured*, by name, rather than as a pass.

None of these filers has been read for this change: nobody downloads or reads them before the
implementation is merged.

### Measuring the result, fixed before the fix

- **The run:** `scripts/html10k/build_table_oracle.py --edgar-root .../fresh2/edgar --out-dir
  .../fresh2`, then `scripts/html10k/measure_table_accuracy.py` with `--mismatches`, on tag-free
  input, as html10k-tables describes. It runs once, on the merged code.
- **The target:** fresh-set pooled accuracy at least 97%, no filer below 90%, and development and
  wave 2 not regressing: development not below its 99.57%, wave 2 not below its 99.71%, both as
  read at `5b512f0` on the oracles of [html10k-tables-scale](html10k-tables-scale.md).
- **Chunk text, reported with it.** For each filing, a *money table* is a table with an oracle cell
  of unit USD and scale 3 or more. The reading is the share of money tables with a scale word
  (`thousands`, `millions`, `billions`, or `000` as a heading) in the text of every chunk part. The
  bar is at least 97% pooled and no filer below 90%. A miss here is a miss of this change, reported
  the same way. The implementer adds the small script that reads it under `scripts/html10k/`.
- **Reporting:** the fresh number first, pooled and per filer with error counts, cells and tables.
  Development and wave 2 are reported too, the latter labelled as tuned. `fresh1` is quoted, if at
  all, as 78.09%, the clean reading. **A rerun of `fresh1` on this code is a tuned number and is
  never quoted as the result.**
- **If the fresh reading misses the target, it is reported as it stands, and that set is not then
  tuned on.** Its mismatches are not read to fix the parser. Any later fix is a new change, with a
  new fresh set named before it starts.

### The causes found

Read from `fresh1/mismatches.jsonl` and the filings. 7,132 of 7,147 errors are scale.

**HON** (3,372 errors, 147 tables). The filing prints `(Dollars in tables in millions, except per
share amounts)` as a running line, 57 times in the FY2024 document, under `NOTES TO CONSOLIDATED
FINANCIAL STATEMENTS—(Continued)`. `_note_scale` reads it, so it is kept as a note and works for a
table straight below it. It is never a *standing* note, because `_standing_note` asks for a note at
the head of the block, and neither the lead words nor the `dollars in <scale>` form allow `in
tables` between. Every table with a heading or a sentence after the running line gets scale 0: 3,329
are USD 6→0, and the 42 shares 6→0 share the cause (`Common stock, par value`, `Reacquired stock`).
No table-level wording is involved. Rule 1 alone fixes 3,347.

**CVX** (3,595 errors, 104 tables). The filing prints its page heading as a little table of two
rows and no numbers: `Notes to the Consolidated Financial Statements | Financial Table of Contents`,
then `Millions of dollars, except per-share amounts`, 79 times in the two filings. The next table
inherits it only as a continuation, which is why the statements themselves read right: any block in
between (`Note 3`, a sentence) ends the run, and every note table has one. 3,517 are USD 6→0.
Rule 3 alone fixes 3,525. The rest are a statement of equity that prints full share counts under
the same note (rule 4, 30 cells) and counts of projects and wells (rule 6, 22 cells).

**The other six filers** have no fault of this size. Their errors are per-share figures with an
ordinary label (`Closing price of our common stock`, `Service-based`, `Weighted average option
price`) under a note of millions: AMGN 24, PFE 35, DE 15, NKE 10 (rule 5).

### Does the scale reach chunk text?

The chunk proxy quoted before this change (share of table chunks with a scale word: HON 19.0%, CVX
64.8%, other six 55-98%) mixes in tables that need no scale. The reading that matters is the money
table one above, and it is sharper:

| Set | Money tables with no scale in some part, at `5b512f0` | After the rules, on the throwaway prototype |
|---|---|---|
| HON | 154 of 180 (85.6%) | 0 of 180 |
| CVX | 98 of 114 (86.0%) | 0 of 114 |
| The other six of `fresh1` | 3 of 715 (0.4%) | 3 of 715 |
| Wave 2 | 2 of 1,026 | 2 of 1,026 |
| Development | 4 of 5,494 | 4 of 5,494 |

So: yes on six filers, no on these two, and on those two it is the same fault. A chunk's `Note:` line
comes from the note in force, so a note the parser cannot stand gives no line. HON adds one fault of
its own, rule 2: 12 of its 154 tables had the parser reading the note straight above, and still no
`Note:` line, because that block neither began with a note nor could be the title of a table (a line
printed five times or more is never a title). The 3 + 2 + 4 left are single tables with no note in
any place these rules name.

HON's filing-wide statement does not have to be fetched from elsewhere: the running line above each
table holds it. The statement of equity and the like, which print nothing of their own, inherit it
by rule 1.

### Chunk text and the reload

**Chunk text changes, though `html10k_chunking.py` does not:** `_table_texts` prints
`PrintedTable.note`, and rules 1 to 3 change that note on these filings. Embedded text changes
there, so those points must be replaced. Counted on all 151 10-Ks on disk (119 development, 16 wave
2, 16 `fresh1`) by comparing every table chunk's text between `5b512f0` and the prototype:

| Filing | Accession | Chunks whose text changes | Chunks before → after |
|---|---|---|---|
| CVX FY2024 | 0000093410-25-000009 | 102 | 550 → 551 |
| CVX FY2025 | 0000093410-26-000078 | 111 | 580 → 582 |
| HON FY2024 | 0000773840-25-000010 | 121 | 463 |
| HON FY2025 | 0000773840-26-000013 | 128 | 507 |
| XOM FY2024 | 0000034088-25-000010 | 11 | 488 |
| XOM FY2025 | 0000034088-26-000045 | 11 | 469 |
| LLY FY2025 | 0000059478-26-000013 | 3 | 361 |

The other 144 filings do not change at all. **Reload plan:**

1. **Development: LLY FY2025 only.** Rule 2 moves one wording from the `Table:` line to the `Note:`
   line in three chunks; the words stay in the text. If the filing is already in `html10k_text`,
   delete its points and reload it, because a filing whose point count matches is skipped. Chunk
   ids do not change, as they depend on offsets and part.
2. **Wave 2: XOM FY2024 and FY2025,** when wave 2 enters the load: nothing to do if it has not.
3. **CVX and HON** are not in the store or the partial load (no HON or CVX by the agreed order).
   They are named here because their count of points differs (CVX) and their text is what rule 1
   and 3 are for, if they ever enter.
4. The implementer repeats this comparison on the merged code, on every 10-K then on disk, and
   reports the list. That list, not this table, decides what is reloaded.

### The evidence for each rule

Counted by switching each rule off in a throwaway prototype, on `fresh1`, wave 2 and development.
The prototype is in the spec author's scratch folder, not committed.

| Rule | Fixes alone | Reach, or the reason it is general |
|---|---|---|
| 1 Parenthesis block | HON 3,347 | Two filers print the form: HON (`Dollars in tables ...`, `Dollars in tables and graphs ...`) and LLY (`Dollars and shares in millions ...`, 3 blocks). LLY had no error, because its notes sit straight above its tables. The rule names no word: it is the structure of the block. Of the wordings of this shape in 151 filings, the one it refuses is JNJ's, with a second sentence |
| 2 Note directly above | HON 12 tables' text; LLY 3 chunks moved | Two filers. Same reason as rule 1: the note is the note whichever form it takes |
| 3 Table of no numbers | CVX 3,525, at a cost of XOM 4 (given back by rule 6) | Two filers print the form: CVX 83 cells, XOM 16 (`(millions of dollars)`) |
| 4 Too large for the note | CVX 30, BA 18 | Two filers. It is requirement 9 of html10k-tables-scale without the need to know the row counts shares |
| 5 Cents | PFE 35, AMGN 24, TSLA 21, CEG 20, UPS 18, DE 15, VST 15, NKE 10, LLY 10, ABT 9, KO 9, DIS 7 | Twelve filers, 193 cells, and no cell made wrong in 151 filings: among two-decimal numbers in money lines under thousands or millions, none is tagged with that scale. Billions do print cents: TSLA, VST and TXN, 29 cells, which is why the rule stops at millions |
| 6 Count of things | CVX 22, XOM 10, MRK 4, DE 3 | Four filers |

**Dropped on the evidence:** a rule that a table under a reprint of the blocks above the table
before continues it, for PFE's supplemental cash flow page (24 cells). It fixed nothing when tried.

**Cells made wrong: 36, all by a note now being read.** 7,184 cells are fixed. 36 that were right
by accident, because no note was found, now take the note: HON asbestos claims and dollars per claim
(24: `Claims filed`, no general form found), and in XOM a `Total` row under `Number of ...` rows (6)
and `Grant price (dollars)` in a filer that prints no `$` (6).

**Feasibility.** The prototype passes every test here and every retired test of html10k-tables,
html10k-tables-scale and html10k-chunks. It reads 99.60% on `fresh1` (CVX 99.67%, HON 99.58%), 99.80%
on wave 2 and 99.64% on development, against 78.11%, 99.71% and 99.57% for `5b512f0` on the same
oracles. **The `fresh1` and wave 2 figures are tuned by construction and are not the result of this
change:** they show the rules can be met together and say nothing about unseen filers. `5b512f0`
reads 78.11% here against the published 78.09% (four PFE cells): the parser changed after the
measured commit `2f1721f`, in `3303531` and `7da5a68`. The published figure stays the clean reading.

**Fixtures** are synthetic, for an invented filer, under `test/spec/html10k-tables-scale-2/fixtures/`
or written in the tests, with short public-domain wordings where a test names a form. A fixture
never comes from `fresh2` or a held-out filer. Expected values are read by hand and shown in the
test.

### What stays out of scope, with its `fresh1` cells

- HON's claims and dollars per claim (24), and XOM's `Total` under a run of counts: no general form.
- A footnote or caption wording seen in one filer: TGT `Represents the number of ... units, in
  thousands.` (20), DE `in thousands of shares and dollars per share follow` (14).
- PFE's supplemental cash flow page, which has no note and no form above it that continues (24).
- Rates printed with no `%` and no `percent` in the label: TGT, DE, VZ, each with its own wording
  (18), and PFE's rate in a text column (10). The first are one wording each, and `rate` alone also
  names dollar rows (`Tax at statutory rate`). The last is the column-finding class out of scope in
  html10k-tables-scale.
- CVX share counts below 100,000,000 in the statement of equity (18): nothing marks them.
- `A$` as a currency (VZ, 1), one tag that disagrees with its printed note (HON, 1), and the rest of
  the development errors that no rule here reaches.
- A filing-wide statement written as a sentence in a note on accounting policies. No error here comes
  from one, so no such rule is justified yet; `fresh2` may show one, and then it is a later change.
