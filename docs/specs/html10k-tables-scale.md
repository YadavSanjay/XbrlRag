# html10k-tables-scale — how filings print scale, as general rules

The table parser ([html10k-tables](html10k-tables.md)) reads 99.30% on the development filers it
was tuned on and 94.45% on wave 2 filers it had never seen (BA, CAT, DIS, JNJ, PEP, TMUS, V, XOM;
16 filings, 33,682 cells, CAT lowest at 85.83%). Of the 1,871 wave 2 errors, 1,841 are scale
errors. The 94.45% is the honest number, and the gap is the point of this change: the parser's
rules fit what the development filers print, not how filings print scale.

This change fixes those errors as general rules in `src/ingest/html10k/tables.py`, and states
before the fix how the result will be measured, since wave 2 is used up by reading it.

Out of scope: see Notes. No new function or field; `ParsedTable`, `Cell` and the oracle format do
not change.

## Requirement

**No rule is keyed to a filer or justified by one filing alone.** Each new note wording has
evidence from at least two filers, or a stated reason why it is the general form. Notes lists the
evidence for every rule. The rules below extend requirements 4, 9 and 10 of
[html10k-tables](html10k-tables.md), whose tests are retired and stay green; none is changed.

1. **A note above the first row of values.** A cell that is itself a note, in any row above the
   first row of values, states the table's note, whichever row it is in and however many title
   rows lie above it. It ranks with the other notes inside the table (requirement 4). A row
   holding only such a note is not a group row: it scopes no rows below it.

2. **Page furniture and a table that runs over a page.**
   - *Furniture* is a block that prints only a page number (`94`, `F-12`), or that is printed
     verbatim at least three times in the document and states no scale. A table with at most one
     printed cell is furniture too (filers put page numbers in one).
   - Furniture is not a caption and does not stand between two tables. The caption of a table is
     the nearest block that is not furniture, when no table lies between.
   - A table is a *continuation* when no block but furniture lies between it and the table before
     it. A continuation with no note of its own takes the note that table used, ahead of any
     standing note. Its `caption` stays empty.
   - A note that repeats is still a note: it is never furniture.

3. **What is not a standing note** (limits requirement 10).
   - A block that goes on to a second sentence after its note describes what is beside it, such
     as a chart (`(Dollars in billions. Percentages in chart are as a percent to total sales)`).
     It stands for nothing.
   - A block that opens with a footnote marker is never a standing note, whatever it ends with.

4. **A scale stated by a footnote.** A block between a table and the next table that opens with
   a marker (`(a)`, `1`), and whose text either begins with a scale statement (`(a)In thousands.`,
   `(b)In thousands, based on the most recent valuation`) or holds a sentence saying amounts are
   in a scale (`... Amounts are in millions of dollars.`), gives that scale to every cell of that
   table carrying the marker: in the column header, the row label or the cell. It ranks above the
   label, group, column and table scale of `Cell.scale`, below the percent and per-share rules.
   Cells without the marker are not affected, nor is any other table, and the block is neither a
   caption nor a standing note.

5. **Note wordings as forms, not lists.** These replace two single-filer rules:
   - `Tables present dollars in millions` becomes a form: up to four words of any kind, then
     `dollars` or `amounts`, then `in <scale>`, at the head of a block. The words `tables` and
     `present` leave the list of note lead words. Prose stays prose: the form still needs what
     the head of a note needs after the scale word (end of text, punctuation, `except`, `and`,
     `of dollars`).
   - `shares, which are reflected in thousands` becomes a form: `shares`, up to 30 characters of
     the same clause, then `in <scale>`. `which` and `reflected` leave the pattern.

6. **What an `except` list reaches.**
   - `except share ...`, `except shares ...` reach a cell that counts shares, by requirement 7.
     A cell counts shares if its row label does, or the group row above it does
     (`Weighted-average shares outstanding`, `Number of units:`, `Effect of dilutive securities:`).
   - A row label counts shares when it says `shares` in the plural, says `dilutive`, or says
     `number of` units, options or awards. `Share-based compensation` does not.
   - Any other term matches a label in the plural, as before, and a column header in either
     number (`except conversion rate`, `Conversion Rate Into Class A Common Stock`).
   - A term holding `per` (`par value per share`) names a per-share figure and reaches no row.
   - A note in a column's header stack has the same reach for its own column.

7. **A column header decides between a count and money.** Going up the column's stack from the
   lowest header, the first that names either settles it, ahead of the row label:
   - money: `at cost`, `amount`, `value`, `capital`, `earnings`, `income`, `loss`, `equity`,
     `deficit`, `total`;
   - a count: `shares`, `number of ...`, `stock outstanding`, or a header that begins `units`,
     `options`, `awards`, `RSUs`, `PSUs`.

   So `Shares withheld ...` is dollars under `Par Value and Additional Paid-in Capital`, and
   `Shares` under `Common Stock and Additional Paid-in Capital` counts shares.

8. **A label that prints a number of shares holds dollars.** The count is in the label, so the
   number beside it is not a count: `Treasury stock, at cost, 47 million shares`,
   `Common stock held in treasury (3,793 million shares in 2024)`. This joins `par`, `authorized`
   and adds `at cost` to what stops a label counting shares.

9. **A share count too large for its note.** When a cell counts shares, nothing states a scale
   for shares, the table's scale is millions or more, and a share-count cell of that column prints
   100,000 or more, the column is in units: scale 0. 100,000 million shares is more than any
   filer has outstanding. Only share-count cells are looked at: dollars in the same column prove
   nothing.

10. **A figure per award or per option** is per-share in requirement 4's sense:
    `Weighted-average fair value per stock award granted`.

11. **Percent in words.** A row label ending `(percent)` or `(percentage)` says so, as `(%)` does.

12. **A row that prints `%` once.** A row with a printed `%` and no currency symbol is a *percent
    row*. In it, a number with a fractional part, below 1,000, and with the decimals of the
    printed `%` cell is a percent too. Between two percent rows with the same decimals and no
    currency symbol between them, the same holds for every row. A whole number never takes the
    percent this way: `9,520` beside `10%`, or `244` beside `1.2%`, is an amount.

13. **A number of 1,000 or more is not a rate.** The rate-column rule of requirement 9 of
    html10k-tables needs also that no cell of the column prints 1,000 or more. A filer that
    prints no `$` no longer turns a column of dollars into percents with one `33%` at its foot.

## Acceptance criteria

- **C1**: Table and cell scale are exact when the note sits in a row above the first row of values, under any number of title rows, and that row scopes no group. Oracle: hand-read CAT excerpts; closed form.
- **C2**: Continuations take the note of the table before, ahead of a standing note; furniture is neither caption nor separator; prose ends a run; a repeated note stays a note. Oracle: hand-read synthetic documents; closed form.
- **C3**: A note with a second sentence and a footnote block set no standing note, and a lone note still does. Oracle: hand-read documents; closed form.
- **C4**: A marked column or row takes its footnote's scale; unmarked cells and other tables do not. Oracle: hand-read PEP and CAT excerpts; closed form; removing the footnote.
- **C5**: The two general forms read every wording of their form, and prose stays prose. Oracle: the wordings of LLY, DIS and AAPL, plus wordings no filer here prints; closed form.
- **C6**: An except list reaches share rows by group, singular terms in column headers and column-level notes, and no `Share-based` row or `par value per share` term. Oracle: hand-read wordings; closed form.
- **C7**: The lowest column header that names a count or money decides, over the row label. Oracle: hand-read TMUS and JNJ excerpts; closed form.
- **C8**: A label printing a number of shares is a dollar row. Oracle: wordings of the DIS, JNJ, TMUS and XOM balance sheets; closed form.
- **C9**: A share column reaching 100,000 under a note of millions is in units; below that, and for dollars in the same column, the note stands. Oracle: hand-read BA excerpts; closed form.
- **C10**: A figure per award or option with cents has scale 0. Oracle: TMUS wording; closed form.
- **C11**: `(percent)` in a label gives a percent, and dollar rows beside it keep their scale. Oracle: XOM wording; closed form.
- **C12**: Every matching number of a `%` row, and of the rows between two, is a percent with its printed value; whole numbers and comma-grouped numbers are not. Oracle: hand-read JNJ excerpts; closed form.
- **C13**: A column with a number of 1,000 or more is no rate column, and a true rate column stays one. Oracle: hand-read XOM excerpt; closed form; html10k-tables requirement 9.

The accuracy targets below are measured by an operator run, not a spec test, since spec tests read
no `data/`.

## Interface

No new signature. These two keep theirs and change behaviour:

```python
# src/ingest/html10k/tables.py
parse_table(table_html: str, *, caption: str = "", scale_note: str = "", table_index: int = 0) -> ParsedTable
parse_tables(document_html: str) -> list[ParsedTable]
```

`parse_table` gets rules 1 and 5 to 13 on one table. Rules 2, 3 and 4 need the document, so they
are reached only through `parse_tables`; the implementer may give private helpers whatever form
they need. The tests call only these two.

## Notes

**Measuring the result, fixed before the fix.** Wave 2 is used up by this change: the rules come
from reading its errors, so it can only ever be a tuned number. The result is measured on a fresh
set nobody has tuned on:

- **The set:** the FY2024 and FY2025 10-Ks of CVX, PFE, VZ, NKE, HON, TGT, AMGN and DE, downloaded
  to `data/eval/html10k_oracle/fresh1/` (filings in `fresh1/edgar/`), outside the store. None is
  in the held-out draw and none is in the store (checked 2026-10-30, by name only). Nobody
  downloads or reads these filers before the implementation is merged.
- **The run:** `scripts/html10k/build_table_oracle.py --edgar-root .../fresh1/edgar --out-dir
  .../fresh1`, then `scripts/html10k/measure_table_accuracy.py` with `--mismatches`, on tag-free
  input, as html10k-tables describes. It runs once, on the merged code.
- **The target:** fresh-set pooled accuracy at least 97%, no filer below 90%, and the development
  filers not below 99.0%.
- **Reporting:** the fresh number first, pooled and per filer with error counts. Wave 2 is reported
  too, labelled as tuned, and so is dev.
- **If the fresh reading misses the target, it is reported as it stands, and that set is not then
  tuned on.** Its mismatches are not read to fix the parser. Any later fix is a new change, with
  a new fresh set named before it starts.

**The causes found**, by reading the wave 2 filings. Cell counts are wave 2 errors, in the class
(unit, oracle scale → parsed scale):

| Class | Cells | Cause | Rule |
|---|---|---|---|
| USD 6→0 | 993 | CAT prints `(Dollars in millions except per share data)` in a third row, as a one-cell row under title rows, so it was read as a group row (896). The rest are shares-worded money rows under an except list (TMUS, DIS, JNJ), or a continuation | 1, 2, 6, 7, 8 |
| USD 6→9 | 203 | JNJ: `(Dollars in billions. Percentages in chart ...)` above a chart stood for the rest of the filing, and tables running over a page have no note | 2, 3 |
| shares 0→6 | 176 | TMUS: no reach for group rows and share columns (84). BA: a standing note in dollars above option tables in full numbers (72). CAT: a footnote ending `Amounts are in millions of dollars` became a standing note (20) | 3, 6, 7, 9 |
| USD 6→−2 | 136 | XOM prints no `$`, so one `33%` made a column of dollars a rate column | 13 |
| pure −2→6 | 113 | XOM says `(percent)` in words (78); JNJ prints `%` only in a row's first column (35) | 11, 12 |
| pure −2→9 | 54 | The same JNJ table under the same wrong standing note | 3, 12 |
| USD 3→6 | 49 | PEP: `(a)In thousands.` below the table | 4 |
| shares 3→6 | 38 | The same PEP footnotes | 4 |

**The evidence for each rule.** Counted by switching each rule off in a throwaway prototype and
scoring it on wave 2 and dev: the cells that rule alone fixes. The rule count is a floor, since
two rules can fix one cell.

| Rule | Fixes alone | Reach, or the reason it is general |
|---|---|---|
| 1 Note row | CAT 1,029 | One filer in the count, but no new wording: it is `test_scale_in_a_second_header_row` of html10k-tables without the limit on which row. Header rows were never the rule; rows above the values are |
| 2 Continuation | JNJ 203, ORCL 35, TMUS 25, CAT 7, HD 5 | Five filers |
| 2 Repeated block as furniture | TMUS 25 | One filer needs it to separate tables, but CAT, JNJ, PEP and XOM print running lines too. The form is structural: no text is listed |
| 3 Second sentence | JNJ 13 | One filer. Reason: a note that stands is one statement; a note that goes on is about its neighbour |
| 3 Footnote block | CAT 20 (read from the class, not switched off alone) | One filer. Reason: the same structure as rule 4, which has two filers; a footnote speaks of its marker |
| 4 Footnote scale | PEP 106, CAT 4 | Two filers |
| 5 Forms | dev LLY 1,214 lost if `tables present` goes with no form | LLY `Tables present`, DIS `Tabular`, and AAPL's `which are reflected`, against `shares in thousands` in JNJ, LLY, PG, CEG |
| 6 Group reach and `dilutive` | TMUS 27, BA 12 | Two filers; `dilutive` in TMUS and AAPL |
| 6 Singular term and column-level note | V 23 (read from the class) | One filer. Reason: the header of a column is a name, and a name is singular. Labels keep the plural, because matching them in the singular broke 101 dev cells (COST, PG, MU, NVDA) |
| 7 Column decides | LLY 24, TMUS 18, NFLX 4 | Three filers |
| 8 Printed count | XOM 4 | The wording is in DIS, JNJ, TMUS and XOM; `par` and `authorized` caught most of them already |
| 9 Too large for the note | VST 137, CEG 46, BA 39, ABT 18, TSLA 13 | Five filers. It costs PG 8 cells on dev |
| 10 Per award | TMUS 6 | `per Option` and `per Unit` in BA and PEP. Same statement, another noun |
| 11 `(percent)` | XOM 78 | One filer. Reason: the same statement as `(%)`, which the rule already reads |
| 12 Percent row | JNJ 89 | One filer, and the weakest reason here: the habit of printing `%` once is the one requirement 9 of html10k-tables records for columns (KO, COST), turned across a row |
| 13 Comma in a rate column | XOM 136 | One filer, but it narrows an existing rule rather than adding one. Reason: no filer prints a rate of 1,000% or more |

**The two single-filer rules.** `Tables present dollars in millions` (LLY) is generalised, not
removed: removing it loses 1,214 dev cells. `shares, which are reflected in thousands` (AAPL) is
generalised too. Both fall under requirement 5. **No retired test of html10k-tables needs a change:
all of them pass against the prototype.**

**Feasibility.** The prototype, in the spec author's scratch folder and not committed, passes every
test here and every retired test of html10k-tables. It reads 99.71% on wave 2 (lowest filer BA at
99.02%) and 99.51% on dev. The wave 2 figure is tuned by construction: it shows the rules can be
met together and says nothing about unseen filers. The fresh run is what answers that.

**Fixtures** are small snippets under `test/spec/html10k-tables-scale/fixtures/`, each an abridged
public-domain SEC filing or synthetic, and each says which in its opening comment. A fixture never
comes from a fresh-set or held-out filer. Expected values are read by hand and shown in the test.

**What stays out of scope**, with its wave 2 cells:

- A printed `$0` tagged scale 0 (BA, 3): a zero has no scale on the page.
- Rates and dividends in a text or date column (PEP 16, DIS 6, CAT 4, JNJ 4 `missing`): those are
  column-finding errors, not scale errors.
- XOM `Units settled in stock`, tagged in dollars by the filer's unit (18).
- `Exercise multiple` in a rate column (DIS, 6), and a `Basic` group that re-opens a per-share
  scope lower in a table (DIS, 7): one filer each, and no general form found.
- BA's `Changes in Share Balances` (33): `Common stock` columns under a caption that says `shares`.
  A caption does not reach cells here; widening it risks more than it fixes.
- Notes in a footnote row inside the table: no evidence yet.
- The dev errors not named here (VST, UNP, COST, AMZN and others), unless a rule above reaches them.
