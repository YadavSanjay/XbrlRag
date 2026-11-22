# lookup-reporting-currency — a single-filer lookup answers in the filer's reporting currency

D3 failure class C6 (status task P1). Every TSMC (TSM, CIK 0001046179) single-filer case of
the v2 dev suite fails on both providers: 14 of 14 (lookup 7, compute.ratio 5, trend 2) in
`eval-eefe3508621a` (anthropic) and `eval-5e602d782e6f` (deepseek). The lookup stops with
`AMBIGUOUS`: "several units at the latest period and no reporting currency among them:
['TWD', 'USD']". The same mechanism fails 14 TSM cases of the live v1 lookup-agreement test
(`test/integration/test_v1_suite_lookup_agreement_live.py`).

**Mechanism.** TSMC files a 20-F in TWD and tags a USD convenience translation for the
latest period (on the balance sheet also for the prior year-end). The lookup chooses one unit
with `choose_lookup_unit` ([`reporting-currency-coverage`](reporting-currency-coverage.md)):
among the units at the latest period, the one tagged at the most distinct period ends. That
rule worked while the lookup's candidates were one filing's rows, comparatives included. Since
[`restatement-s1`](restatement-s1.md) the flat lookup reads every filing and keeps only the
year's own column, so every candidate sits at **one** period end. TWD and USD then cover one
period each, the rule ties, and the lookup refuses. Example, cash FY2022 (Notes): the S1
candidates are TWD 1,342,814,100,000 (fact 91728) and USD 43,854,200,000 (fact 83985), both at
2023-01-01.

**What the answer must be.** S6 ([`XbrlRag_Eval_Semantics.md`](../design/XbrlRag_Eval_Semantics.md)):
"A single-filer answer is in the filer's reporting currency; a convenience translation is used
only when the question asks for another currency." The frozen v2 keys agree: every TSM
single-filer key is a TWD fact from SEC companyfacts (`unit: TWD`), and its ratio keys are
TWD over TWD.

**One definition.** The reporting currency is already defined once: `choose_lookup_unit` over
a filer's observations of the measure with no requested unit. The flat screen
(`select_reporting_currency`, [`screen-reporting-currency`](screen-reporting-currency.md)) and
the key builder (K2b rule K-R1) both use it. This change does not add a second definition; it
gives the lookup the observations the rule needs (the rows of the measure it read, before the
S1 column selection) and keeps the candidates in that unit.

## Requirement

1. **The answer unit.** `answer_unit(candidate_units, observations)` returns the single unit a
   lookup with no requested unit answers in:
   - the candidates carry no unit (all None): None;
   - the candidates carry exactly one unit: that unit;
   - otherwise the filer's reporting currency, `choose_lookup_unit(observations)` with no
     requested unit, when one of the candidates carries it; else None (no single unit).

   Units are compared in their shown form (`display_unit`): the caller passes shown units in
   both arguments.
2. **The lookup's observations.** For the flat lookup without `as_of`, the observations are
   `(shown unit, period end)` of every row the S1 query read for the measure (the filer, the
   concept family, the period type and duration kind, the dimension filter; every filing,
   every period, every unit), before `_select_s1` narrows them to the year's column. For an
   `as_of` lookup they are the pinned filing's rows the query read. Wherever the lookup
   resolves a unit with no requested unit, it uses `answer_unit` with these observations, and
   keeps only the candidates in the returned unit. None keeps today's refusal: `AMBIGUOUS`
   with the "several units ... no reporting currency" note.
3. **Stated, never swapped.** The result's `unit` is the reporting currency (`TWD`), its
   value and citation are a fact in that unit, and a resolution note names the unit chosen as
   the reporting currency and the units set aside (today's note:
   `unit TWD (reporting currency); set aside ['USD']`). A USD convenience translation is never
   the answer unless the step requests `unit: "USD"`. A requested unit is handled exactly as
   today (`choose_lookup_unit` with the requested unit).
4. **Ratios.** Two lookups of one filer in its reporting currency give a ratio with no unit:
   `expr_unit` returns `pure` and the calculator's value is the TWD quotient. A compute over
   inputs in two currencies (for example one input requested in USD) still refuses:
   `expr_unit` raises `CalculatorError` (units-currency-integrity).
5. **General.** Nothing depends on a CIK, ticker or currency code. A USD filer with a stray
   foreign-currency row at its latest period answers in USD; a filer reporting in any other
   currency with a USD translation answers in its own currency.

## Acceptance criteria

- **C1**: `answer_unit` follows Requirement 1. Point cases:
  - TSMC cash FY2022: candidates `TWD`, `USD` (both at 2023-01-01), observations of the
    measure from all four 20-Fs (TWD at 7 distinct period ends, USD at 5) give `TWD`;
  - TSMC diluted EPS: candidates `TWD/shares`, `USD/shares` with TWD/shares at more period
    ends give `TWD/shares`;
  - one candidate unit gives that unit, whatever the observations;
  - two candidate currencies whose observations tie give None;
  - candidates `USD`, `EUR` whose observations name `TWD` as the reporting currency give
    None;
  - unitless candidates give None.

  Oracle: Requirement 1 applied by hand; the stored facts in `fixtures/tsm_rows.json`.
- **C2**: For any candidate units and observations, `answer_unit` agrees with the rule written
  out in the test (latest period, units at it, most distinct period ends, a tie is no unit).
  Properties: the order of candidates and observations does not matter; repeating an
  observation changes nothing; adding an earlier period end for the returned reporting
  currency never changes the result; renaming every currency code by one bijection renames
  the result the same way (no currency is special). **Shared definition:** whenever
  `select_reporting_currency` excludes a filer for currency X over the same observations and X
  is among the candidate units, `answer_unit` returns X; when it keeps the filer as a USD
  reporter and USD is a candidate unit, `answer_unit` returns USD.
  Oracle: Requirement 1; screen-reporting-currency R1–R2.
- **C3**: `XbrlFactstoreLookup.lookup` over a fake database holding the fixture rows (the I/O
  boundary) answers each TSM single-filer lookup of the dev keys in TWD, OK, with the key's
  value to its precision (decimals -5: half of 100,000 either way):

  | family | fy | value (TWD) | key accession |
  |---|---|---|---|
  | CashAndEquivalents | 2022 | 1,342,814,100,000 | 0001193125-25-083423 |
  | RetainedEarnings | 2022 | 2,594,891,200,000 | 0001193125-24-099840 |
  | PropertyPlantAndEquipment | 2024 | 3,234,980,100,000 | 0001193125-25-083423 |
  | GrossProfit | 2022 / 2023 / 2024 | 1,348,354,800,000 / 1,175,110,600,000 / 1,624,353,600,000 | 0001193125-25-083423 |
  | ResearchAndDevelopment | 2022 / 2023 | 163,262,200,000 / 182,370,200,000 | 0001193125-25-083423 |
  | Revenue | 2022 / 2023 | 2,263,891,300,000 / 2,161,735,800,000 | 0001193125-25-083423 |

  The citation is a TWD fact of the fixture with that value; a note names TWD as the reporting
  currency and USD as set aside. Metamorphic: deleting every USD row leaves the answer (value,
  unit, fact id) unchanged; a step with `unit: "USD"` still answers the USD convenience value of
  the same period (Requirement 3). Oracle: the frozen v2 dev keys (SEC companyfacts, quoted
  above); S6.
- **C4**: Ratios and trends. R&D over revenue from two lookups: `expr_unit` is `pure` and the
  value matches the key to 3 significant figures (FY2023 182,370,200,000 / 2,161,735,800,000
  = 0.08436...; FY2022 163,262,200,000 / 2,263,891,300,000 = 0.07212...). The gross-profit
  trend FY2022–FY2024 is three OK TWD lookups with the key's values. Mixed: with the R&D step
  requesting `unit: "USD"` and revenue unrequested, `expr_unit` raises `CalculatorError`.
  Oracle: the dev keys' inputs (`int-a967bc81f5f3`, `int-e4e6c46945f0`, `int-5c685209cc7a`);
  S6 ratio rule; units-currency-integrity.
- **C5**: Generality. Over synthetic fakes: (a) a USD filer whose rows of a measure are USD at
  three year-ends and one EUR row at its latest year-end only answers in USD (today:
  `AMBIGUOUS`); (b) the TSM rows moved to another CIK and ticker, with TWD renamed to EUR
  throughout, answer in EUR with the same values. Oracle: Requirement 5; the rule by hand.

## Interface

- `src.retriever.xbrl.reporting_currency.answer_unit(candidate_units: Sequence[str | None], observations: Sequence[tuple[str | None, str | None]]) -> str | None`
- Changed behaviour, no new signature: `XbrlFactstoreLookup.lookup` (its unit choice,
  `_restrict_to_one_unit`, and the observations passed to it).

## Notes

- **Evidence (xbrl_db, read-only, 2026-11-11).**
  - Every TSM single-filer lookup in the live store is `AMBIGUOUS` today with the note above
    (CashAndEquivalents FY2022–FY2025, GrossProfit FY2022–FY2025, Revenue and R&D FY2023, run
    through `XbrlFactstoreLookup.from_env()`, read-only). S1 candidates: cash FY2022 TWD 91728
    (FY2025 20-F) and USD 83985 (FY2023 20-F) at 2023-01-01; gross profit FY2022 TWD 87109
    (FY2024 20-F) and USD 79597 (FY2022 20-F) at 2023-01-01; gross profit FY2025 TWD 90955
    and USD 90956 at 2026-01-01.
  - Units by period on the primary statements (presented, non-dimensional rows), per 20-F:
    TWD at 4 distinct period ends (544–595 rows), USD at 2 (163–174 rows), in each of the
    four filings (FY2022 `0001193125-23-107214`, FY2023 `0001193125-24-099840`, FY2024
    `0001193125-25-083423`, FY2025 `0001628280-26-025362`). Over all TSM flat rows: TWD 3,700
    rows at 9 period ends in 38 families; USD 670 rows at 5 period ends; `shares*TWD` 104,
    `shares*USD` 32.
  - DEI: no reporting-currency fact. `mv_dei_metadata` has no currency column; the 20-Fs
    carry no `ifrs-full:DescriptionOfPresentationCurrency`; only
    `ifrs-full:ClosingForeignExchangeRate` (32.79 in the FY2024 20-F, 31.37 in FY2025), the
    convenience rate. A DEI route is therefore not available; the coverage rule is.
  - Other filers with more than one ISO currency in flat rows: GOOGL (one EUR row, no concept
    family) and TSM. No other filer reaches the multi-unit path today.
- **Fixture.** `test/spec/lookup-reporting-currency/fixtures/tsm_rows.json`: every
  non-dimensional `mv_financial_facts` row of TSM for CashAndEquivalents, GrossProfit,
  RetainedEarnings, PropertyPlantAndEquipment, ResearchAndDevelopment, Revenue and
  EarningsPerShareDiluted (212 rows, four 20-Fs), with the S1 columns. Values are the
  filings' tagged values (public domain). The test oracles are the frozen v2 dev keys
  (SEC companyfacts), not the fixture or the code.
- **Fake database contract.** Any query on `mv_financial_facts` gets every fixture row matching
  the equality parameters it passes among `cik`, `concept_family`, `period_type`,
  `duration_kind` (the S1 query's own filters); the `concept_role` column probe gets no row
  (no coverage columns); `publish_manifest` is absent; any other query gets no rows. So the
  observations must come from rows the lookup already reads, or from another
  `mv_financial_facts` query with those filters, never from a unit predicate.
- **Expected movement.** All 14 TSM single-filer dev cases per provider (28 case-runs)
  should leave `REFUSED`/`INCORRECT`; the 7 lookups and 2 trends should become CORRECT on
  value, the 5 ratios CORRECT if the plan computes the ratio (C8 is a separate class). The
  14 TSM v1 lookup-agreement failures should pass. Cross-filer screens are unchanged (the flat
  screen already excludes TSM for currency). Non-TSM single-filer cases: no change expected
  (no other filer reaches the multi-unit path; C5 (a) is the guard).
- **Validation plan.** (1) $0 deterministic: run every dev and validation TSM single-filer key
  input through `XbrlFactstoreLookup.lookup` read-only against `xbrl_db` (family, fy, no unit)
  and compare value and unit with the key; expect all OK in TWD at the key's precision. Never
  `xbrl_heldout`. (2) The live v1 lookup-agreement test. (3) A DeepSeek slice: the 14 TSM dev
  cases plus a seeded regression sample (`build_slice_suite.py`), cap set by the maintainer
  (expected well under $0.50), compared with `compare_runs.py` against `eval-5e602d782e6f`.
- **Overlap.** `s4-lookup-deferred-revenue-debt` is being implemented in
  `xbrl_factstore_lookup.py` / `xbrl_factstore_screen.py` (new `defined_measure.py`). The
  stub lives in a new module to avoid that file; the wiring in `_restrict_to_one_unit` and
  `_query_flat` (Requirement 2) is implementer work in `xbrl_factstore_lookup.py`: implement
  after s4 merges, or rebase on it. `reporting_currency.py` must not import
  `xbrl_factstore_lookup` at module level if the lookup imports it (cycle); a lazy import
  or moving `choose_lookup_unit` there with a re-export keeps one definition.
- **Out of scope.** The multidim, partition, segment and geographic lookup paths keep their
  unit handling (none failed here); the YoY screen; a reporting-currency change between
  filings (the latest period decides, as in the screen); currency conversion (never).
