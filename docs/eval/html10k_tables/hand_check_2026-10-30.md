# Table oracle hand-check

Independent check of `table_oracle.jsonl` (164,358 records, 119 filings), per
`docs/specs/html10k-tables.md` Notes and plan section 5.3. Done by a separate agent that did not
write the generator. Dated 2026-10-30.

## Sample

- **Primary, as the Notes specify:** `random.Random(20261030).sample(sorted(records), 50)`, seed
  passed as a parameter, sorted on `(accession, table_index, source_row, source_cell)`. 50 cells
  from 47 filings: 13 negative, 3 nil, 4 percent.
- **Supplement, added by me (not in the Notes):** the Notes fix the draw, so no choice was open.
  The primary draw holds only 3 nils and 4 percents, so 15 more cells were added as a
  separate stratum: 5 nil, 5 negative, 5 percent, one per filing, none in a primary filing, seed
  20261031 (seed + 1). They are not a random sample of the population, and are reported apart.

## Method

- Locations were separated from the oracle values before reading. Each cell was rendered from the
  raw filing by my own walker as visible text, with no ix attribute read or shown (only `colspan`,
  `display:none` and raised-text styling). I read value, sign, nil and scale note from that dump.
- All readings were saved (`readings.json`, sha256
  `5688b736686a374035bc32a9698bd2619da33edaf261c6b79e5a9daa01acb5b1`) before the oracle values
  were opened. `tables.py` and `build_table_oracle.py` were not opened until then.
- Nil cells compare nil only, as the scorer does. Scale on a nil is shown but not scored.

## Result

| stratum | cells | agree | agree, unscored note | disagree |
|---|---|---|---|---|
| primary | 50 | 49 | 0 | 1 |
| supplement | 15 | 14 | 1 | 0 |

No value, scale, sign or nil disagreement on any cell, once P03 is read at its true cell. With 0 of
50 such errors in the seeded draw, the 95% upper bound on that error rate is about 5.8%. Ten of the
65 cells have a tag sign that differs from the print (for example `(519)` on a deferred tax
liability). In all ten my blind reading of the print matched the oracle's `negative`.

## Findings

**1. P03: the oracle's location does not match the raw filing. The oracle is right for scoring;
the spec's text is wrong. Not an ambiguity in the print.**

`0000050863-23-000006` (INTC FY2022), record 237/7/4. In the raw file, table 237 is a two-row page
footer with no row 7. The printed `438` is at raw table 240, r7 c4.

Cause: three tables sit inside `ix:header` in this filing. `build_records` skips tables under
`ix:header` and `ix_strip_html` removes the header, so both count 305 tables, against 308 in the
raw file. Run on this filing, `ix_strip_html` leaves `438` at exactly 237/7/4.

The spec Notes say ordinals "count every `<table>`" and that ix-strip "removes no structural
element, leaves them unchanged". Neither holds for this filing. The record's value, scale and sign
agree with the print, and the parser, reading stripped input, shares the oracle's frame. So no
rebuild is needed, but the Notes should say ordinals are those of the ix-stripped document.

Scope: of the 119 filings, only INTC FY2022 has tables under `ix:header` (3). A mechanical check
of every record against the raw file (does the cell at the location contain the record's `printed`
text?) passed 162,935 of 164,358; all 1,423 failures are in this filing, whose 52 oracle tables
all fit best at offset +3. That check is containment on text, not a reading, and is weak for dashes.

**2. S12: oracle scale 0 on a nil in a table printed in millions. No effect on the score.**

`0001564590-22-023675` (ORCL FY2022), 257/4/19. The nil tag carries no scale attribute, so the
record has the spec's default 0. Requirement 12 does not compare scale on a nil. Anyone using
`scale` on nil records for another purpose should not trust it.

## Limits

- Read from a visible-text dump of the HTML, not a browser render. Colour and layout effects were
  not seen. Column alignment was checked from span positions.
- P03's true cell was found using the oracle's `printed` text, so that one reading is not blind.
- Nine cells (P08, P13, P16, P17, P18, P21, P22, P44, S13) take their scale from a standing note
  far from the table, at the head of the notes. These notes were read, but an exception could not
  be ruled out beyond what the table itself prints.
- None of the 65 cells is among the 1,146 entries of `mismatches.jsonl`, so the parser scored exact
  on all of them, P03 included. That is a cross-check of the frame, not independent evidence.
- This checks the oracle, not the parser, and says nothing about tags the generator dropped.
- 65 cells do not exclude a rare generator or tagging error. Only the bound above is supported.

## Cells: primary (seed 20261030)

| id | filer | accession | table/row/cell | printed | row; column | scale source | my reading | oracle | verdict |
|---|---|---|---|---|---|---|---|---|---|
| P01 | AMD FY2023 | 0000002488-24-000012 | 16/29/4 | 4,310 | Proceeds from maturity of short-term investments; Dec 31, 2022 | in table: (In millions) | 4310, scale 6 | 4310, scale 6 | agree |
| P02 | KO FY2023 | 0000021344-24-000009 | 105/26/13 | 196 | Income (loss) before income taxes; Global Ventures | caption: ... as follows (in millions): | 196, scale 6 | 196, scale 6 | agree |
| P03 | INTC FY2022 | 0000050863-23-000006 | 237/7/4 | 438 (at raw table 240 / stripped table 237, r7 c4); blind read at raw 237 found a 2-row page footer | 5.00%, due June 2049; Amount Dec 31, 2022 (raw table 240) | in table: (In Millions) | 438, scale 6 at the true cell (not blind: located from the oracle's printed text). Blind entry: stated location not found in the raw file | 438, scale 6 | DISAGREE on location, agree on value, scale, sign. See finding 1 |
| P04 | INTC FY2025 | 0000050863-26-000011 | 220/31/9 | 900 | 5.15%, due February 2034; Amount Dec 28, 2024 | in table: ($ In Millions) | 900, scale 6 | 900, scale 6 | agree |
| P05 | LLY FY2025 | 0000059478-26-000013 | 62/9/5 | 4.3 (no % on this row; first row of the column prints %) | Rate of compensation increase; Pension 2023 | percent: % on the column's first row, table is rates | 4.3, scale -2 | 4.3, scale -2 | agree |
| P06 | MCD FY2025 | 0000063908-26-000035 | 36/19/1 | 12,393 | Operating income; 2025 | stub: In millions, except per share data | 12393, scale 6 | 12393, scale 6 | agree |
| P07 | PG FY2023 | 0000080424-23-000073 | 61/5/4 | 2.7 % | Rate of compensation increase; Pension 2022 | percent: % printed in next cell | 2.7, scale -2 | 2.7, scale -2 | agree |
| P08 | TXN FY2025 | 0000097476-26-000059 | 52/10/1 | (10) | Accrued expenses and other liabilities; U.S. Defined Benefit, 2024 | standing, Note 1: all dollar and share amounts in tables in these notes are in millions | -10, scale 6 | -10, scale 6 | agree (tag sign differs from print; oracle follows the print) |
| P09 | UNP FY2025 | 0000100885-26-000037 | 42/7/7 | (3,173) | Cash dividends declared ($5.20 per share); Retained earnings | stub: Millions | -3173, scale 6 | -3173, scale 6 | agree (tag sign differs from print; oracle follows the print) |
| P10 | WMT FY2025 | 0000104169-25-000021 | 50/15/5 | 378 | Other comprehensive income (loss) attributable to Walmart; 2024 | stub: (Amounts in millions) | 378, scale 6 | 378, scale 6 | agree |
| P11 | WMT FY2025 | 0000104169-25-000021 | 52/23/16 | 83,861 | Balances as of January 31, 2024; Total Walmart Shareholders' Equity | stub: (Amounts in millions) | 83861, scale 6 | 83861, scale 6 | agree |
| P12 | WMT FY2026 | 0000104169-26-000055 | 80/4/5 | 445 | Increases related to prior year tax positions; 2025 | stub: (Amounts in millions) | 445, scale 6 | 445, scale 6 | agree |
| P13 | MRK FY2025 | 0000310158-26-000063 | 80/24/14 | 1,373 | Funded status December 31; Pension International 2024 | standing head of notes: ($ in millions except per share amounts) | 1373, scale 6 | 1373, scale 6 | agree |
| P14 | AAPL FY2022 | 0000320193-22-000108 | 26/27/4 | (14,545) | Cash used in investing activities; Sep 25, 2021 | caption: (In millions) | -14545, scale 6 | -14545, scale 6 | agree |
| P15 | MU FY2022 | 0000723125-22-000048 | 20/11/1 | 8,125 | Total comprehensive income; Sep 1, 2022 | caption: (In millions) | 8125, scale 6 | 8125, scale 6 | agree |
| P16 | MU FY2022 | 0000723125-22-000048 | 54/5/3 | 260 | Increases related to tax positions taken in current year; 2021 | standing head of notes: (All tabular amounts in millions, ...) | 260, scale 6 | 260, scale 6 | agree |
| P17 | MU FY2023 | 0000723125-23-000054 | 34/16/7 | — | 2033 A Notes; Net carrying amount Current, Aug 31, 2023 | standing: (All tabular amounts in millions, ...) | nil (scale 6) | nil (scale 6) | agree |
| P18 | MU FY2026 | 0000723125-26-000023 | 27/8/9 | 213 | 2033 B Notes; Long-Term, Sep 3, 2026 | standing: (All tabular amounts in millions, ...) | 213, scale 6 | 213, scale 6 | agree |
| P19 | QCOM FY2023 | 0000804328-23-000055 | 62/5/4 | 1,482 | United States revenues; 2022 | caption: ... as follows (in millions): | 1482, scale 6 | 1482, scale 6 | agree |
| P20 | CSCO FY2024 | 0000858877-24-000017 | 136/5/6 | $ 3,056 | Total long-lived assets; Jul 29, 2023 | caption: ... (in millions): | 3056, scale 6 | 3056, scale 6 | agree |
| P21 | COST FY2025 | 0000909832-25-000101 | 34/11/6 | $ (1) | Total short-term investments; Unrealized Losses, Net, 2024 | standing: (amounts in millions, except share, per share, and warehouse count data) | -1, scale 6 | -1, scale 6 | agree |
| P22 | COST FY2026 | 0000909832-26-000093 | 34/3/2 | $ 783 | Government and agency securities; Cost Basis, 2025 | standing: (amounts in millions, ...) | 783, scale 6 | 783, scale 6 | agree |
| P23 | LMT FY2023 | 0000936468-24-000010 | 53/14/9 | 59 | Participants' contributions; Retiree Medical 2023 | caption: ... (in millions): | 59, scale 6 | 59, scale 6 | agree |
| P24 | ORCL FY2023 | 0000950170-23-028914 | 47/29/8 | 4.00% | $1,250, 3.90%, due May 2035; Effective Interest Rate, 2023 | percent: % in the cell | 4.00, scale -2 | 4.00, scale -2 | agree |
| P25 | MSFT FY2023 | 0000950170-23-035122 | 37/10/3 | 77 | Foreign government bonds; Less than 12 Months Fair Value, June 30, 2023 | stub: (In millions) | 77, scale 6 | 77, scale 6 | agree |
| P26 | ORCL FY2025 | 0000950170-25-087926 | 37/10/11 | (3,668 + ) | Cash dividends declared ($1.36 per share); Accumulated Deficit | stub: (in millions, except per share data) | -3668, scale 6 | -3668, scale 6 | agree (tag sign differs from print; oracle follows the print) |
| P27 | AMZN FY2024 | 0001018724-25-000004 | 64/5/10 | $ 68,614 | Income (loss) before income taxes; 2024 | caption: ... (in millions): | 68614, scale 6 | 68614, scale 6 | agree |
| P28 | NVDA FY2023 | 0001045810-23-000017 | 22/51/10 | $ 249 | Cash paid for income taxes, net; Jan 31, 2021 | caption: (In millions) | 249, scale 6 | 249, scale 6 | agree |
| P29 | NVDA FY2024 | 0001045810-24-000029 | 22/94/17 | $ 27 | Balances, Jan 28, 2024; Accumulated Other Comprehensive Income (Loss) | in table: (In millions, except per share data) | 27, scale 6 | 27, scale 6 | agree |
| P30 | NVDA FY2024 | 0001045810-24-000029 | 50/4/7 | $ 46 | Balance at beginning of period; Jan 29, 2023 | in table: (In millions) | 46, scale 6 | 46, scale 6 | agree |
| P31 | NFLX FY2023 | 0001065280-24-000030 | 63/4/4 | — | OCI before reclassifications; Change in Unrealized Gains (Losses) on Cash Flow Hedges, 2021 | in table: (in thousands) | nil (scale 3) | nil (scale 3) | agree |
| P32 | UPS FY2024 | 0001090727-25-000019 | 91/11/17 | (1) | Amount reclassified from AOCI, FX contracts; Investment Income (Expense) and Other, 2023 | caption: ... (in millions): | -1, scale 6 | -1, scale 6 | agree |
| P33 | CRM FY2026 | 0001108524-26-000060 | 84/21/1 | (519) | Lease right-of-use assets; Jan 31, 2026 | caption: ... (in millions): | -519, scale 6 | -519, scale 6 | agree (tag sign differs from print; oracle follows the print) |
| P34 | META FY2023 | 0001326801-24-000012 | 66/5/1 | (264) | Decreases related to prior year tax positions; 2023 | caption: ... (in millions): | -264, scale 6 | -264, scale 6 | agree (tag sign differs from print; oracle follows the print) |
| P35 | META FY2024 | 0001326801-25-000017 | 27/16/16 | 39,098 | Net income (2023 block); Total Stockholders' Equity | caption: (In millions, except per share amounts) | 39098, scale 6 | 39098, scale 6 | agree |
| P36 | UNP FY2023 | 0001437749-24-003599 | 78/4/31 | 92 | Conversion, stock option exercises, ...; Total, 2021 block | stub: Millions | 92, scale 6 | 92, scale 6 | agree |
| P37 | MRK FY2023 | 0001628280-24-006850 | 46/38/7 | (2,319) | Payments on debt; 2021 | caption: ($ in millions) | -2319, scale 6 | -2319, scale 6 | agree (tag sign differs from print; oracle follows the print) |
| P38 | ABT FY2024 | 0001628280-25-007110 | 43/9/1 | 1,188 | 1.50% Notes, due 2026; 2024 | stub: (in millions) | 1188, scale 6 | 1188, scale 6 | agree |
| P39 | TSLA FY2025 | 0001628280-26-003952 | 63/9/6 | 670 | GILTI and Subpart F inclusion; 2023 | caption: ... (in millions): | 670, scale 6 | 670, scale 6 | agree |
| P40 | UPS FY2025 | 0001628280-26-008432 | 34/5/2 | $ 5,887 | Cash and cash equivalents; Dec 31, 2025 | caption: (In millions) | 5887, scale 6 | 5887, scale 6 | agree |
| P41 | KO FY2025 | 0001628280-26-010047 | 45/5/4 | (44) | FX contracts, Cost of goods sold; 2024 | caption: ... (in millions): | -44, scale 6 | -44, scale 6 | agree |
| P42 | GOOGL FY2023 | 0001652044-24-000022 | 106/7/18 | $ 75,519 | Total; Fair Value (Total), Dec 31, 2022 | caption: ... (in millions): | 75519, scale 6 | 75519, scale 6 | agree |
| P43 | VST FY2022 | 0001692819-23-000005 | 132/14/4 | 1 | Other noncurrent assets; Dec 31, 2021 | caption: (Millions of Dollars) | 1, scale 6 | 1, scale 6 | agree |
| P44 | VST FY2023 | 0001692819-24-000012 | 92/9/1 | (48) | Transfers into Level 3 (b); 2023 | standing, Note 1: all dollar amounts in tables in the notes in millions | -48, scale 6 | -48, scale 6 | agree |
| P45 | VST FY2024 | 0001692819-25-000013 | 103/20/1 | — | Payments; Nuclear Plant Decommissioning, 2023 block | in table: (in millions) | nil (scale 6) | nil (scale 6) | agree |
| P46 | VST FY2025 | 0001692819-26-000006 | 63/20/30 | $ 15 | Balances at December 31, 2023; Noncontrolling Interest | in table: (Millions of Dollars) | 15, scale 6 | 15, scale 6 | agree |
| P47 | AVGO FY2022 | 0001730168-22-000118 | 26/33/14 | (1,456) | Shares repurchased for tax withholdings; Additional Paid-in Capital | in table: (In millions) | -1456, scale 6 | -1456, scale 6 | agree (tag sign differs from print; oracle follows the print) |
| P48 | CEG FY2022 | 0001868275-23-000014 | 99/6/10 | (668) | ERCOT; RNF from external customers, 2021 | standing: (Dollars in millions, unless otherwise noted) | -668, scale 6 | -668, scale 6 | agree |
| P49 | CEG FY2023 | 0001868275-24-000014 | 165/4/17 | 111% | Option Model, Volatility percentage; 2022 arithmetic average | percent: % in the cell | 111, scale -2 | 111, scale -2 | agree |
| P50 | CEG FY2024 | 0001868275-25-000023 | 122/4/26 | $ 107 | Service cost; Total Pension and OPEB, 2024 | standing: (Dollars in millions, unless otherwise noted) | 107, scale 6 | 107, scale 6 | agree |

## Cells: supplement (seed 20261031, not part of the Notes' draw)

| id | filer | accession | table/row/cell | printed | row; column | scale source | my reading | oracle | verdict |
|---|---|---|---|---|---|---|---|---|---|
| S01 | KO FY2024 | 0000021344-25-000011 | 44/11/2 | $ (128) | FX contracts; Gain (Loss) in OCI, 2023 | caption: ... (in millions): | -128, scale 6 | -128, scale 6 | agree |
| S02 | INTC FY2023 | 0000050863-24-000010 | 167/25/5 | (609) | Restricted stock unit withholdings; Amount | stub: (In Millions, Except Per Share Amounts) | -609, scale 6 | -609, scale 6 | agree (tag sign differs from print; oracle follows the print) |
| S03 | LLY FY2024 | 0000059478-25-000067 | 65/2/1 | 0.70 % | Expected dividend yield; 2024 | percent: % in next cell | 0.70, scale -2 | 0.70, scale -2 | agree |
| S04 | PG FY2025 | 0000080424-25-000076 | 70/7/22 | $ (356) | Total derivatives designated as hedging; Fair Value (Liability), 2024 | standing: Amounts in millions of dollars except per share amounts ... | -356, scale 6 | -356, scale 6 | agree (tag sign differs from print; oracle follows the print) |
| S05 | PG FY2026 | 0000080424-26-000103 | 35/7/1 | 8% | Grooming; 2026 | percent: % in the cell | 8, scale -2 | 8, scale -2 | agree |
| S06 | WMT FY2023 | 0000104169-23-000020 | 68/5/6 | 3.125% | Debt repaid June 23, 2021; Interest Rate | percent: % in the cell | 3.125, scale -2 | 3.125, scale -2 | agree |
| S07 | HD FY2025 | 0000354950-25-000085 | 136/5/1 | 25.4 % | Expected volatility; Fiscal 2024 | percent: % in next cell | 25.4, scale -2 | 25.4, scale -2 | agree |
| S08 | ADBE FY2024 | 0000796343-25-000004 | 59/9/2 | — | Foreign exchange gains and losses; 2024 | stub: (in millions) | nil (scale 6) | nil (scale 6) | agree |
| S09 | TSLA FY2022 | 0000950170-23-001409 | 40/4/11 | — | Money market funds; Gross Unrealized Losses, Dec 31, 2021 | caption: ... (in millions): | nil (scale 6) | nil (scale 6) | agree |
| S10 | NFLX FY2024 | 0001065280-25-000044 | 35/27/2 | — | Preferred stock; Dec 31, 2024 | caption: (in thousands, except share and per share data) | nil (scale 3) | nil (scale 3) | agree |
| S11 | UPS FY2023 | 0001090727-24-000008 | 53/18/2 | $ — | Balance as of Dec 31, 2023; Corporate Bonds | caption: ... (in millions): | nil (scale 6) | nil (scale 6) | agree |
| S12 | ORCL FY2022 | 0001564590-22-023675 | 257/4/19 | — | Hardware, FY2019 plan; Others column, year ended May 31, 2020 | stub: (in millions) | nil (scale 6) | nil (scale 0) | Agree on nil. Oracle scale 0 vs printed millions: the nil tag has no scale attribute, and scale is not scored on a nil (req. 12). See finding 2 |
| S13 | MRK FY2024 | 0001628280-25-007732 | 60/11/25 | (773) | Increase in Sales from AOCL reclassifications; OCI 2022 | standing head of notes: ($ in millions except per share amounts) | -773, scale 6 | -773, scale 6 | agree |
| S14 | GOOGL FY2022 | 0001652044-23-000016 | 153/8/4 | 0.6 (no % on this row; first and last rows of the column print %) | Deferred tax asset valuation allowance; 2021 | percent: rate table, % on the column's first and last rows | 0.6, scale -2 | 0.6, scale -2 | agree |
| S15 | AVGO FY2023 | 0001730168-23-000096 | 27/31/14 | (481) | Repurchases of common stock; Additional Paid-in Capital | in table: (In millions) | -481, scale 6 | -481, scale 6 | agree (tag sign differs from print; oracle follows the print) |

## Correction to the spec's Notes

`docs/specs/html10k-tables.md` says ordinals count every `<table>`, and that ix-strip removes no
structural element. Neither holds for INTC FY2022, the only one of the 119 filings with tables
inside `ix:header`. Ordinals are those of the ix-stripped document, which is the frame the
generator and the parser both use, so the oracle is right and needs no rebuild.
