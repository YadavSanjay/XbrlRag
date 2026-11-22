# Evaluation semantics

What a financial question means, fixed in writing before any answer key is built: which filing
answers a restated year, what "fiscal 2024" is across filers, which companies a ranking covers,
how a ratio is defined, and how units, scale and sign are compared. It is for anyone building an
answer key, a grader or a pipeline stage that the v2 evaluation measures
([architecture](XbrlRag_Eval_v2_Architecture.md)).

Every v2 key records the rules it used by id (`S1`–`S6`), and every answer is graded against
the same rules. Code and specs cite these ids, so the numbering is stable. A change to a rule
is a change to every key that cites it: the suites are rebuilt and diffed, and runs before and
after the change are not compared.

Each rule has up to four parts: **Rule** (the meaning), **Keys** (how the answer key applies
it), **Product** (what the pipeline does) and **Gap today** (where the pipeline does not yet
follow the rule). The keys follow the rule, not today's code.

| Rule | Covers |
|---|---|
| [S1](#s1-restated-figures) | Restated figures: the latest filing that reports a period answers it |
| [S2](#s2-fiscal-periods-across-filers) | Fiscal periods: the filer's own label, calendar wording, shares outstanding |
| [S3](#s3-which-companies-a-cross-filer-question-covers) | The universe of a cross-filer question, and named exclusions |
| [S4](#s4-derived-ratios) | One definition per derived metric |
| [S5](#s5-comparable-concepts) | When a filer's concept is comparable enough to rank |
| [S6](#s6-units-scale-and-sign) | Currency, scale, sign and precision |

## S1. Restated figures

**Rule.** "Revenue for fiscal 2022" means the value for that period in the **latest filing
in the corpus that reports it**. When the FY2024 10-K recasts FY2022, the recast value is
the answer. In a comparison (growth, change, CAGR, trend) **each value follows this rule on
its own**: every period is answered by the latest filing that reports it, so two legs may come
from different filings. The keys and the system both work this way.

**As originally reported** is answered only when the question asks for it ("as originally
reported", "in the FY2022 10-K", "before the restatement"). Point-in-time questions (plan
§3.4) test both readings on the same period.

**Keys.** From companyfacts, the entries for a concept, unit and period (`start`, `end`)
are deduplicated on the latest `filed` date. The `fy` field is the *filing's* fiscal year,
not the fact's, and is never used to pick a period. Each key stores the accession it came
from.

**Product.** The non-dimensional lookup and the flat screen answer the FY N filing's own
column (ending exactly on its DEI `DocumentPeriodEndDate`) as the latest later-labelled
canonical filing reports it, a later 10-Q's comparative of a year-end balance included;
`as_originally_reported` answers the original filing's own column; a year with no own
column is `NOT_FOUND`, never a prior-year comparative. `mv_yoy_comparison` pairs the current
column with the same filing's comparative and flags the prior-filing fallback with
`same_filing` (`src/retriever/xbrl/restatement.py`, [restatement-s1](../specs/restatement-s1.md)).
A Part III-only 10-K/A (cover facts only) is never the key.

The multidimensional lookup, the segment, geographic and region screens and the single-filer
breakdown follow the same rule for dimensional facts ([dimensional-s1](../specs/dimensional-s1.md)): a
dimensional fact by its full dimension set, a breakdown as one filing's split.

**Gap today.** The derived screens (`mv_derived_metrics`) still read the FY N filing only.
`check_against_sec.py` matches on period and accession but
does not dedupe on `filed`. Two amendment rules coexist: the store makes an amendment
canonical only when it carries the primary statements (`has_primary_statements`,
annual-amendment-handling), while the keys ignore only a cover-only amendment
(`carries_financial_data`). They agree on every corpus amendment; a 10-K/A that re-files a
single note without the statements would win in the keys but stay non-canonical in the
store (its original answers). Aligning them is an open decision.

## S2. Fiscal periods across filers

**Rule.** "Fiscal 2024" means the year the **filer itself labels** fiscal 2024 (its DEI
`DocumentFiscalYearFocus`), whatever month it ends in. The exception: when two annual filings
of one filer with different period ends would share a label, the later one takes the prior
label + 1 (or more, across a gap in the filings;
[fiscal-label-alignment](../specs/fiscal-label-alignment.md)). A cross-filer screen ranks each
filer on its own fiscal 2024. When the ranked filers' year-ends are more than three months
apart, the answer says so and gives each filer's period end.

**Calendar wording** ("calendar 2024", "in 2024" with no "fiscal") uses the SEC frames
rule: the annual period that overlaps calendar 2024 the most. A bare "2024" in a
question about a single filer is its fiscal 2024.

**Keys.** Fiscal keys come from companyfacts per filer, not from frames, because frames are
calendar-aligned. Calendar keys come from frames.

**Shares outstanding.** "Shares outstanding for fiscal 2025" means the balance sheet's
`us-gaap:CommonStockSharesOutstanding` at the fiscal year end. The cover page of the same
10-K (`dei:EntityCommonStockSharesOutstanding`, dated a few weeks later) is a **disclosed
alternate reading**: an answer giving it is graded correct and the grade names the reading
(`matched_readings`, `"key"` or `"cover_page"`), each within the ordinary tolerance on its own.
A trend row accepts only its own year's cover. A filer-year with no
`CommonStockSharesOutstanding` (one that tags only issued shares, or tags outstanding shares
only on a dimension such as `StatementEquityComponentsAxis`) has no shares-outstanding key:
the cover count never becomes a key on its own and issued minus treasury is never computed
into one; an inventory concept other than `CommonStockSharesOutstanding` drops the intent as
`concept_mismatch` ([shares-outstanding-reading](../specs/shares-outstanding-reading.md)).

**Product.** Screens and the suite use the filer's label, and a screen's answer gives each
filer's period end when the year-ends are more than three months apart
(`src/retriever/xbrl/screen_disclosure.py`, [screen-universe](../specs/screen-universe.md)).

**Gap today.** Calendar wording is not answered by the frames rule: a "calendar YYYY" phrase is
flagged ambiguous for a filer whose year does not end in December
(`EntityExtractor.resolve_periods`).

## S3. Which companies a cross-filer question covers

**Rule.** A question with no stated scope, or "covered companies", ranks every filer in the
corpus that has the period. A named group ("big tech", "the S&P 100", a sector) is resolved
from a committed groups file with an as-of date and intersected with the corpus. The answer
always states:

- the universe it ranked, by count and name or group ("among the 26 covered companies");
- the filers in that universe it could not rank, and why (period not filed, metric not
  tagged, different currency; see S5 and S6).

It never claims to rank a group the corpus only partly holds without saying so ("26 of the
S&P 100").

**Keys the statement witness cannot vouch for.** The v2 key builder excludes a member, and
discloses it, with two reasons that describe the evidence rather than the filer
([`key-fallback-witness`](../specs/key-fallback-witness.md)):

- `fsds_not_published`: no FSDS filing can witness the key (neither the filing that supplies
  the value nor the filer's own annual report for the year is in SEC's published Financial
  Statement Data Sets yet). Counted in the manifest's `fsds` block; never a build failure.
- `fallback_blocked`: the key's own filing is not in the FSDS data, the filer's own annual
  report was read instead, and its statement line does not support the key (a rejected,
  ambiguous or disagreeing line) with no reviewed resolution covering it. Held, not a block.

Two more exclusions describe the member's figure rather than the evidence
([`key-currency-and-definition`](../specs/key-currency-and-definition.md)):

- `currency`: a member some path would rank reports the year's annual report in another
  currency than USD (S6), so its USD figure is a convenience translation.
- `element_outside_family`: the member's line is keyed on a standard element whose taxonomy
  definition is outside the family's measure, whatever its label says; the elements are
  listed one by one, each with a review record, in `configs/eval/v2/element_fit.yaml`.

**Grading.** The universe statement and the named exclusions are graded (plan §3.5); a
correct ranking with a silent drop counts as wrong. A member a reviewed resolution drops
(`resolved_drop`, its reviewed value recorded on the exclusion) may be ranked or left out,
and is not required to be named; members whose key values are exactly equal may be listed
in any order ([`grading-dont-care-and-ties`](../specs/grading-dont-care-and-ties.md)). A declining sum or median answer is not matched on a value that equals one of its listed rows, except when a sentence naming the aggregate, and not declining, states that value as its one row-matching number ([`grading-stated-aggregate`](../specs/grading-stated-aggregate.md)).

**Product.** A screen step may name its universe (`covered` or a group of
`configs/eval/v2/groups.yaml`), and the answer gets a deterministic sentence, built from the
screen's data after the faithfulness verifier, stating the universe, the ranked filers and each
exclusion with its reason (`src/retriever/xbrl/screen_disclosure.py`,
[screen-universe](../specs/screen-universe.md)).

## S4. Derived ratios

**Rule.** Each derived metric has one definition, stated in the answer when the value is
not a tagged fact. The definitions:

| Metric | Definition |
|---|---|
| Gross margin | (Revenue − Cost of revenue) / Revenue |
| Operating margin | Operating income / Revenue |
| Net margin | Net income / Revenue |
| R&D intensity | R&D expense / Revenue |
| Effective tax rate | The tagged rate; else income tax expense / pretax income |
| Free cash flow | Cash from operations − capital expenditure |
| FCF margin | Free cash flow / Revenue |
| EBITDA | Operating income + depreciation and amortization (labelled as this proxy) |
| Interest coverage | Operating income / interest expense |
| Current ratio | Current assets / current liabilities |
| Quick ratio | (Current assets − inventory) / current liabilities |
| Equity | Stockholders' equity attributable to the parent, as tagged |
| Debt (`debt_total`) | Every borrowing line the balance sheet presents (short-term borrowings, commercial paper, current maturities, long-term debt), summed; finance-lease obligations only where a debt line includes them; operating leases never |
| Deferred revenue (`deferred_revenue_total`) | Contract liabilities, current plus non-current: both presented lines; else the tagged total when not below the current line, else current plus the tagged non-current part, else the current line alone |
| Net income (`net_income_parent`) | Attributable to the parent; the consolidated figure only where the filing reports no different parent figure |
| Comprehensive income (`comprehensive_income_parent`) | Total comprehensive income attributable to the parent, same rule |
| Net debt | Debt − cash and cash equivalents |
| Debt to equity | Debt / Equity |
| ROE | Net income attributable to the parent / **average** equity (opening and closing) |
| ROA | Net income / average total assets |
| ROIC | Operating income × (1 − effective tax rate) / (Debt + average equity) |
| Growth | (Current − prior) / \|prior\|; not meaningful when prior ≤ 0 |
| CAGR (n years) | (Current / value n years earlier)^(1/n) − 1; only when both are positive |

Averages use the opening and closing balances. When the opening balance is missing, the
closing balance is used and the answer **says so** ("on year-end equity; the prior year is
not in the corpus"). An answer that uses the fallback without saying so is graded wrong.

**Keys.** Computed in the test from independently keyed inputs (S1 applies to each input),
never by the pipeline's calculator or `mv_derived_metrics`. Each key records the
definition id and whether it used the average or the closing balance. A Debt, deferred
revenue, net income or comprehensive income case records its definition id as
`key.definition` ([key-definitions](../specs/key-definitions.md)); a disclosed alternate
reading (`note_total` for Debt without a presented current line, `current_presented` for
deferred revenue presented only as its current line) is graded as one.

**Gap today.** `mv_derived_metrics` derives equity as total assets − total liabilities
(which includes non-controlling interest). It names a closing-balance fallback in its
`formula`, and the derived screen applies the currency rule (S6) when it ranks. The v1
SQL-derived suite reads that view, so its keys share its equity choice.

## S5. Comparable concepts

**Rule.** A per-filer-year **coverage card** (plan §4.5) records, for each canonical metric,
whether the filer tags it with a standard element, a custom extension, or not at all. A
filer whose value comes only from an extension or a component concept is ranked only when
the card maps it to the canonical metric; otherwise it is an exclusion under S3, named in
the answer.

**Keys.** A key cites the card entry it relied on.

**Gap today.** The card is built (`mv_coverage_card`, `src/ingest/sec_edgar/coverage/`,
[coverage-card](../specs/coverage-card.md)), but neither the screens nor the key builder read
it yet: keys rely on the statement witness (S3) instead, and a screen drops a component-only
filer with a resolution note rather than through the card.

## S6. Units, scale and sign

**Rule.**

- **Currency.** A single-filer answer is in the filer's reporting currency; a convenience
  translation is used only when the question asks for another currency. A cross-filer
  screen of an amount (revenue, capex, a per-share value) ranks in USD; a filer that does
  not report it in USD is excluded under S3 and named. A screen of a ratio (a margin, a
  rate, a return) ranks every filer whose inputs share one currency, whatever that
  currency is, because the ratio has no unit; a filer whose inputs mix currencies is
  excluded and named. Dimensionless units (`pure`, `Rate` and the
  like) count as no unit. The system does not convert currencies.
- **Scale.** Keys hold full values. An answer may state any scale ("$391.0 billion"); the
  grader normalises before comparing, at the key's precision (below).
- **Sign.** As presented in the statement: expenses and capital expenditure are positive
  amounts, and a loss is a negative net income. "Capex fell" refers to the amount. The
  grader reads a down word governing a number as negative ("declined 5.2%" is -5.2), and
  for an income-type lookup a loss word too ("a net loss of $1.2 billion" is -1.2e9).
- **Precision.** A key keeps the fact's `decimals`; a fact with `INF` decimals or a nil
  value is never treated as zero. A v2 answer is compared at the key's precision by metric
  kind: a reported amount or share count at its `decimals`, per-share money at the cent, an
  aggregate at its coarsest input, a ratio to 3 significant figures, a growth rate or CAGR
  to 0.1 percentage point; a key without `decimals` falls back to the case's `rel_tol`.
  A growth key whose question asks for an amount ("change", or "how much" followed by a
  movement verb such as grow, increase or fall) without a percent cue also accepts the
  absolute change (later − earlier of its legs), compared as an aggregate
  ([pick-skips-questions](../specs/pick-skips-questions.md),
  [grader-pick-echo-grow](../specs/grader-pick-echo-grow.md)). An answer right only at a coarser precision than that ("$1.2 billion" for 1,234,000,000
  tagged to the million) is `IMPRECISE`, neither correct nor wrong. Spec:
  [grading-precision](../specs/grading-precision.md).

**Keys.** Each key records its unit and the fact's decimals. For a cross-filer amount screen,
the currency a member reports in is its filing's reporting currency: the currency of the
year's annual report, read over every currency fact of that filing (the latest end's only
currency, else the one tagged at the most period ends), not one concept's
([`key-currency-and-definition`](../specs/key-currency-and-definition.md), requirement 1).

**Product.** A flat or derived screen keeps a filer only when it reports the amount in USD
and names the others as `currency` exclusions (`select_reporting_currency` in
`src/retriever/xbrl/xbrl_factstore_screen.py`,
[screen-reporting-currency](../specs/screen-reporting-currency.md)).
