# units-currency-integrity — every number keeps its unit through derived screens and compute

Area C of the test strategy (nuances N7 and N8): units and currency in compute and screens.
Three verified defects let a number in the wrong unit reach an answer with an audited look:

1. **Derived screens rank across currencies.** `mv_derived_metrics` has no unit column, and
   `XbrlFactstoreScreen._query_derived` ranks every row as if it were USD. TSM's FY2025 free
   cash flow is **TWD** 1,002,565,100,000 (operating cash flow TWD 2,274,975,600,000, fact
   91562, less capex TWD 1,272,410,500,000, fact 91586; 20-F `0001628280-26-025362`), so a
   FY2025 FCF screen ranks TSM first, ten times Apple's USD 98,767,000,000, and every USD
   filer one place too low. The flat screen already excludes a filer that reports in
   another currency ([`screen-reporting-currency`](screen-reporting-currency.md)); the
   derived screen does not.
2. **Compute mixes units.** `PlanExecutor._default_compute` and `XbrlCalculatorTool` see
   bare floats. "Apple's FY2024 revenue minus TSMC's" evaluates
   391,035,000,000 (USD) − 2,894,307,700,000 (TWD) = −2,503,272,700,000 and returns it as
   an OK number. Nothing stops a dollar amount being added to a share count either.
3. **An AMBIGUOUS value feeds a compute.** `XbrlFactstoreLookup` returns `value=best.value`
   with status AMBIGUOUS, and `PlanExecutor.execute` binds `result.value` whatever the
   status, so a compute over an ambiguous input returns an OK number built on a guess.

Policy: [`XbrlRag_Eval_Semantics.md`](../design/XbrlRag_Eval_Semantics.md) S6 — a
cross-filer screen ranks in USD and names a filer that does not report in USD; the system
does not convert currencies. S4 lists "does not filter on unit" as a gap of
`mv_derived_metrics`.

## Requirement

### R1. A derived screen ranks in one currency

1. **The unit of a derived row** is `derived_metric_unit(metric_name, units)`, where
   `units` is the stored unit of every component fact the row cites in
   `component_fact_ids` (a JSON object whose values are a fact id or a list of
   alternative fact ids, or a plain list of fact ids; every id counts). A unit is a
   **currency** when it matches `^[A-Z]{3}$` (ISO 4217 shape). With `C` the set of
   currencies among `units`:
   - `units` empty, any unit None, an unknown metric name, or `len(C) > 1` → None;
   - **monetary** metrics `EBITDA`, `FreeCashFlow`, `NetDebt`: every unit is the one
     currency in `C` → that currency; otherwise None;
   - **per-share** metrics `BookValuePerShare`, `DilutedEpsDerived`,
     `FreeCashFlowPerShare`: `len(C) == 1`, at least one unit is `"shares"`, and every
     unit is that currency or `"shares"` → `"<currency>/shares"`; otherwise None;
   - every other metric in `DERIVED_METRICS` is a **ratio**: `"pure"` (`len(C) <= 1`;
     non-currency units such as `pure`, `Rate` or `shares` do not block it).

   A dimensionless unit (Requirement 4's `DIMENSIONLESS_UNITS`) is treated exactly like
   `pure` here: replacing `pure` by `Rate` in any input never changes the result.
2. **Which rows are ranked.** For each row the derived query returns at the requested
   fiscal year, the screen looks up the units of its component facts in the fact store
   (a query separate from the `mv_derived_metrics` query; see Notes):
   - no component fact found at all → ranked as today, with `unit` None;
   - unit None → not ranked; exclusion
     `{"ticker", "reason": "unit_mismatch", "units": <sorted distinct component units>}`;
   - a currency other than `USD`, or `"<currency>/shares"` with a currency other than
     `USD` → not ranked; exclusion `{"ticker", "reason": "currency", "currency": <code>}`;
   - `"USD"`, `"USD/shares"` or `"pure"` → ranked, and the ranked row's `unit` is that
     unit (today it is None).

   Rows for an older fiscal year are left to `select_fiscal_year`'s `excluded`, as in the
   flat screen.
3. **Disclosed like the flat screen.** The exclusions are given exclusions of
   `screen-universe`'s disclosure (`to_dict()["disclosure"]["exclusions"]`, sorted by
   ticker). Ranks of the remaining filers run 1..n without a gap.

   **Ratio screens include every filer, whatever its currency (policy decided
   2026-11-07).** A ratio whose inputs share one currency has no currency, so it is
   comparable across filers: a ratio screen ranks a non-USD filer alongside the USD
   filers, in one ranking, and does not exclude it for its currency. TSM's FCF margin (all
   TWD) is ranked among the USD filers; a TSM row whose inputs mix TWD and USD is not
   (R1.2, `unit_mismatch`). Monetary and per-share screens still rank in USD only (S6).

### R2. A compute refuses mixed units

4. **The unit of an expression** is `expr_unit(expr, units)`. A **dimensionless unit** is
   one of the closed list `DIMENSIONLESS_UNITS = {"pure", "Rate"}` (exact, case-sensitive
   strings): `pure` is the XBRL standard unit for a ratio, and `Rate` is the custom
   measure AMD, AVGO and MU use for tax-rate reconciliations and covenant ratios. These
   are the only dimensionless units in the corpus (Notes); no percent-style unit
   (`Percent`, `percent`, ...) exists in it, so none is listed. Each input unit is parsed
   into a map from base unit to exponent:
   - a dimensionless unit → `{}`; `None` → refused (unknown unit);
   - `"A/B"` → numerator factors `A.split("*")` at +1, denominator factors at −1;
   - otherwise `u.split("*")`; the store's rendering of a per-share unit, exactly the two
     factors `shares` and a currency `X` (`"shares*USD"`, `"shares*TWD"`), means
     `X/shares` (`{X: 1, shares: -1}`); any other product is each factor at +1.

   In every form, a factor that is a dimensionless unit or `1` is ignored. Any other
   unit that is not a currency and not `shares` and is not listed (`Integer`, `segment`,
   `patent`, `MW`, a lower-case `rate`, ...) keeps today's behaviour: it is a base unit
   of its own, so `n * 2` over `Integer` is `"Integer"` and `n - 1` is refused. The
   implementation may expose the list as `xbrl_calculator_tool.DIMENSIONLESS_UNITS`;
   no criterion reads the name.

   Numeric literals are `pure`. `*` adds exponent maps, `/` subtracts them, unary `+`/`-`
   keep them, and binary `+`/`-` require equal maps. Refused with `CalculatorError` when:
   an input's unit is None or a referenced id has no entry; `+`/`-` join different maps;
   **two or more currencies** appear among the referenced inputs, in any operation (no
   currency conversion, S6); or the expression is invalid for `evaluate`.

   The canonical result: factors with a positive exponent, sorted (Python string order),
   each repeated by its exponent, joined by `*`; then, when some exponent is negative,
   `/` and the negative factors in the same form. No factor at all → `"pure"`; only
   negative factors → `"1/<denominator>"`. Examples: `"USD"`, `"pure"`, `"USD/shares"`.
5. **The executor applies it.** A compute step's result carries `unit` = `expr_unit(...)`
   over the units of the steps it references (a lookup's unit, a derived lookup's unit,
   or an earlier compute's unit). When `expr_unit` refuses, the step is `ERROR` with
   value None and an `error` that names the conflicting units (both currency codes for a
   currency conflict). Value arithmetic is unchanged: multiplying every currency input by
   `k > 0` multiplies the result by `k**e`, where `e` is the currency's exponent in the
   result unit (a sum or difference scales by `k`, a ratio is unchanged, a per-share
   value scales by `k`).

### R3. A non-OK value never enters a compute

6. A compute step that references a step whose status is not OK (AMBIGUOUS, NOT_FOUND,
   ERROR, NOT_MEANINGFUL, NOT_IMPLEMENTED) is `ERROR`, value None, with an `error` that
   names every such step id. This check comes first: before the unit check, the n/m check
   (`pct-change-nm`) and evaluation, so an AMBIGUOUS non-positive base is ERROR, not
   NOT_MEANINGFUL. It holds even when the non-OK lookup reports a value (the lookup keeps
   reporting its best candidate's value for the trace). A compute over a refused compute
   is refused in turn.

Order inside a compute step: (6) non-OK inputs → (4/5) units → n/m → evaluate.

## Acceptance criteria

- **C1**: `expr_unit` follows Requirement 4. Point cases, from stored units:
  `rev - cost` over USD/USD → `"USD"`; `ni / rev` → `"pure"`;
  `(ocf - capex) / sh` with USD, USD, `shares` → `"USD/shares"`; `eps * sh` with
  `"shares*USD"` (AAPL FY2025 fact 14430) and `shares` → `"USD"`; `(a - b) / b * 100` →
  `"pure"`; `aapl - tsm` with USD/TWD → refused; `aapl / tsm` with USD/TWD → refused;
  `ni + sh` with USD/`shares` → refused; `eps + ni` (USD/shares vs USD) → refused; an
  input with unit None → refused; `100 / sh` → `"1/shares"`. Dimensionless units:
  `oi * (1 - etr)` with USD and `Rate` → `"USD"`; `etr - stat` with `Rate` (AMD FY2025
  fact 115827) and `pure` (fact 115791) → `"pure"`; `etr * 100` over `Rate` → `"pure"`;
  `oi + etr` with USD/`Rate` → refused; `n * 2` over `Integer` → `"Integer"` and
  `n - 1` over `Integer` → refused (unlisted, kept as a base unit). Properties: for random
  expressions over random units it agrees with the exponent algebra written out in the
  test; relabelling one currency as another (consistently) relabels the result and never
  changes refusal; the order of `units` entries does not matter; replacing `pure` by any
  listed dimensionless unit in any input never changes the result unit or refusal.
  Oracle: Requirement 4 (dimensional analysis); the store's units in Notes.
- **C2**: `PlanExecutor.execute` applies units to compute steps (Requirement 5), over a
  fact store faked at its boundary:
  - AAPL FY2024 revenue (USD 391,035,000,000, fact 13253) minus TSM FY2024 revenue
    (TWD 2,894,307,700,000, fact 87103) is ERROR, value None, error names `USD` and
    `TWD`; with TSM's USD convenience translation (USD 88,268,000,000, fact 87104) it is
    OK, USD 302,767,000,000, unit `"USD"`;
  - AAPL FY2025 FCF per share (111,482,000,000 − 12,715,000,000) / 15,004,697,000 =
    6.58240549609… with unit `"USD/shares"`; diluted EPS 7.46 (`shares*USD`) × 15,004,697,000
    shares = USD 111,935,039,620 with unit `"USD"`; net margin
    112,010,000,000 / 416,161,000,000 = 0.269150641… `"pure"`; net income + shares is
    ERROR;
  - a chain: a compute over an OK compute uses the earlier compute's unit;
  - AMD FY2025 ROIC-style return (10-K `0000002488-26-000018`): operating income
    USD 3,694,000,000 (fact 114585) × (1 − effective tax rate −0.025 in `Rate`, fact
    115827) / (equity USD 62,999,000,000, fact 114709 + long-term debt USD 2,348,000,000,
    fact 114679 + current portion USD 874,000,000, fact 115446) =
    3,786,350,000 / 66,221,000,000 = 0.0571774814… OK, unit `"pure"`; NOPAT alone
    (`oi * (1 - etr)`) is OK, USD 3,786,350,000, unit `"USD"`;
  - AVGO FY2024 (10-K `0001730168-24-000139`): pretax income USD 9,916,000,000 (fact
    24844) × effective tax rate 0.378 in `Rate` (fact 26258) = USD 3,748,248,000, OK,
    unit `"USD"`, within the rate's rounding (±0.0005 × 9,916,000,000) of the filed tax
    expense USD 3,748,000,000 (fact 24847); `tax / pretax - etr` is OK, `"pure"`.

  Properties: scale invariance (every currency input × k → result × k**e, unit and
  status unchanged); swapping the currency labels of all inputs changes neither status
  nor value; any expression with two currencies is ERROR; relabelling a `pure` input as
  `Rate` (or back) changes neither status, unit nor value. Oracle: hand arithmetic on the
  filed facts (Notes); Requirement 5.
- **C3**: A compute over a non-OK step is ERROR, value None, its `error` names the step
  (Requirement 6). Point case: CEG FY2022 ProfitLoss, whose current-period facts disagree
  (−167,000,000 for 2022-01-01..2022-12-31, facts 116143/116152/116197; −318,000,000 for
  2022-02-01..2022-12-31, fact 116516; all decimals −6), resolves AMBIGUOUS through the
  real lookup logic (`_build_result` over those rows) with a value set; `pl22 / rev22`
  is ERROR naming `pl22`. Control: the same plan over NetIncomeLoss (fact 116149,
  −160,000,000) and revenue (fact 116092, USD 24,440,000,000) is OK,
  −160 / 24,440 = −0.0065466448… `"pure"`. Properties: for any non-OK status of either
  input, with or without a value, the compute is ERROR naming it; an AMBIGUOUS
  non-positive base in a percentage change is ERROR, not NOT_MEANINGFUL; a compute over
  a refused compute is ERROR. Oracle: Requirement 6; the stored facts.
- **C4**: `derived_metric_unit` follows Requirement 1. Point cases: TSM FY2025
  FreeCashFlow (TWD, TWD) → `"TWD"`; AAPL FY2025 FreeCashFlow (USD, USD) → `"USD"`; AAPL
  FY2025 FreeCashFlowPerShare (USD, USD, shares) → `"USD/shares"`; TSM FY2024
  FreeCashFlowMarginPct (TWD × 3) → `"pure"`; AVGO EffectiveTaxRate (`Rate`) → `"pure"`;
  FreeCashFlow (TWD, USD) → None; FreeCashFlowMarginPct (TWD, TWD, USD) → None;
  DilutedEpsDerived (USD) without shares → None; FreeCashFlow (USD, shares) → None;
  `[]` → None; a None unit → None; an unknown metric → None. Properties: agrees with the
  rule written out for random metrics and units; the order of units does not matter;
  relabelling the currency relabels a monetary or per-share result and leaves `"pure"`;
  replacing `pure` by `Rate` (or back) never changes the result.
  Oracle: Requirement 1; the stored component units (Notes).
- **C5**: `XbrlFactstoreScreen.screen` for a derived screen, over a fake database:
  - FY2025 `derived:FreeCashFlow`, 33 filers (Notes): TSM is not ranked; its exclusion
    `{"ticker": "TSM", "reason": "currency", "currency": "TWD"}` is in the disclosure;
    the other 32 are ranked 1..32 by OCF − capex (AAPL 98,767,000,000 first, MSFT third,
    INTC last at −4,949,000,000), each with unit `"USD"`;
  - FY2024 `derived:FreeCashFlowMarginPct`, 32 filers: all 32 are ranked in one ranking
    (31 USD filers and TSM, R1.3's ratio policy), no exclusion; TSM is ranked 6th
    (0.300648960…, between MSFT 0.302180… and QCOM 0.286458…) with unit `"pure"`;
  - a TSM row whose capex is the USD convenience fact (91587, USD 40,561,400,000) beside
    TWD operating cash flow is excluded
    `{"ticker": "TSM", "reason": "unit_mismatch", "units": ["TWD", "USD"]}`.

  Metamorphic: permuting ticker labels permutes the ranking and the excluded ticker the
  same way; relabelling TSM's currency TWD → any other non-USD code changes only the
  exclusion's `currency`; giving TSM's facts the unit USD ranks TSM first; scaling the USD
  filers' facts by k > 0 keeps the order. Oracle: Requirements 1–3 applied by hand to the
  fake store; S6.

## Interface

- `src.retriever.xbrl.xbrl_calculator_tool.expr_unit(expr: str, units: Mapping[str, str | None]) -> str`
  (raises `CalculatorError`)
- `src.retriever.xbrl.xbrl_factstore_screen.derived_metric_unit(metric_name: str, component_units: Sequence[str | None]) -> str | None`
- Changed behaviour, no new signature: `PlanExecutor.execute` (compute steps: Requirements
  5–6, `StepResult.unit` set on compute steps) and `XbrlFactstoreScreen.screen` (derived
  view: Requirements 2–3).

## Notes

- **Stored facts (xbrl_db, read-only, 2026-11-07).**
  - TSM (CIK 0001046179) FY2025 20-F `0001628280-26-025362`: operating cash flow fact
    91562 TWD 2,274,975,600,000 and 91563 USD 72,520,700,000; capex fact 91586 TWD
    1,272,410,500,000 and 91587 USD 40,561,400,000 (all at the 2026-01-01 period end).
    `mv_derived_metrics` FreeCashFlow FY2025 = 1,002,565,100,000, components 91562/91586.
  - TSM FY2024 20-F `0001193125-25-083423`: revenue fact 87103 TWD 2,894,307,700,000 and
    87104 USD 88,268,000,000 (convenience); FCF margin components 87103, 87698 (TWD
    1,826,177,100,000), 87716 (TWD 956,006,500,000).
  - AAPL: FY2024 revenue fact 13253 USD 391,035,000,000 (`0000320193-24-000123`); FY2025
    (`0000320193-25-000079`) revenue 14388 USD 416,161,000,000, net income 14424 USD
    112,010,000,000, diluted EPS 14430 `shares*USD` 7.46, diluted weighted shares 14436
    `shares` 15,004,697,000, OCF 14617 USD 111,482,000,000, capex 14629 USD 12,715,000,000.
  - CEG FY2022 10-K `0001868275-23-000014`: revenue 116092 USD 24,440,000,000; NetIncomeLoss
    116149 USD −160,000,000 (rank 1); ProfitLoss (rank 2) as in C3. The −318,000,000 fact
    covers 2022-02-01..2022-12-31 (after the separation from Exelon); the others the
    calendar year. Both end at the same period end, so a ProfitLoss lookup is AMBIGUOUS.
  - The C5 stores (33 FCF rows for FY2025, 32 FCF-margin rows for FY2024) are the
    `mv_derived_metrics` rows and their component facts, read 2026-11-07, committed in
    `test/spec/units-currency-integrity/conftest.py` with each filer's accession.
- **Store unit strings.** `mv_financial_facts.unit` renders a divide unit's two measures
  as a product: a per-share fact is `shares*USD` (5,650 facts) or `shares*TWD` (145).
  Requirement 4 reads that form as `<currency>/shares`. Fixing the rendering in the view
  is a separate change.
- **Fake database contract (C5).** So the implementation's queries are answered:
  - `to_regclass` (publish-manifest probe) → `(False,)`, so the publish gate is inert;
  - SQL mentioning `entity_master` → the roster (`cik`, `ticker`);
  - SQL mentioning `mv_derived_metrics` → for each filer, its row at the latest fiscal
    year <= `params["fy"]` whose `metric_name` equals `params["metric_name"]` (keys
    `ticker`, `cik`, `fiscal_year`, `fiscal_period`, `value`, `metric_name`, `formula`,
    `component_fact_ids` as a decoded JSON object). It carries no unit, like the view;
  - any other SQL → component fact rows (`fact_id`, `unit`, `value`, `ticker`, `cik`,
    `fiscal_year`, `concept_family`, `period_end`): those whose `fact_id` is among the
    integers (or digit strings) found in the parameters, flattening lists, tuples and
    sets; all fact rows when the parameters hold none.

  So the component units must come from a query of their own, not from a join inside the
  `mv_derived_metrics` query, and that query passes the screen's `fy` among its
  parameters even if it does not use it (as `_query_roster` does): the graduated
  `screen-exact-fiscal-year` fake filters every query by `params["fy"]`. That fake
  answers every query with the same rows (component ids 1 and 2, fact ids 1000+i): no
  component fact is found, so Requirement 2's first case keeps those rows ranked as
  today, and its derived-view tests keep passing.
- **Executor fakes (C2, C3).** The fact store is faked at `XbrlFactstoreLookup.lookup`
  (as in `derived-gross-profit`); for C3 the fake's answer is produced by the real
  `XbrlFactstoreLookup._build_result` over committed rows, so the AMBIGUOUS decision is
  the product's own.
- **Existing behaviour kept.** `XbrlCalculatorTool.evaluate` is unchanged (its callers
  outside the executor, e.g. the html10k answerer, have no units). The lookup still
  reports its best candidate's value for an AMBIGUOUS result; the executor is the guard.
  Whether `ExecutionResult.bindings` keeps a non-OK step's value is left to the
  implementation; no criterion reads it.
- **Graduated tests.** No graduated spec test asserts the old behaviour: the derived-view
  fake of `screen-exact-fiscal-year` is covered by Requirement 2's first case, and the
  executor tests of `derived-gross-profit`, `glued-hint-split` and
  `revenue-subfamily-fallback` give their OK lookups a unit (`USD`/`EUR`), so their
  computes keep a unit. Unit tests under `test/unit` that feed computes unit-less lookups
  are the implementer's to update.
- **Known edges, out of scope.** `mv_derived_metrics` picks each input with a
  unit-blind `DISTINCT ON` (today TSM's TWD fact wins by fact id); Requirement 2 catches a
  mixed row at query time, and a unit column in the view is a follow-up. The `/api/v1`
  compute route and the MCP compute tool bind facts without units (same gap, separate
  change). Restated inputs (S1) are not unit questions and are not covered here.
- **S6 and ratios (decided 2026-11-07).** S6 says a cross-filer screen ranks in USD. The
  maintainer decided that ratio screens include every filer whatever its currency: a
  ratio of same-currency inputs has no currency, so TSM is ranked in ratio screens (as
  the `screen-reporting-currency` notes already say: "Derived ratios are
  currency-neutral"). `XbrlRag_Eval_Semantics.md` S6 and the eval answer keys are being
  aligned with this policy in a separate docs/keys change; this spec does not edit them.
- **Dimensionless units in the corpus (xbrl_db, 2026-11-07).** `select unit, count(*)
  from mv_financial_facts group by unit` gives 185 distinct units. Dimensionless ones:
  `pure` (16,489 facts) and `Rate` (130: AVGO 98, AMD 19, MU 13). `Rate` carries tax-rate
  reconciliation percentages (AMD, AVGO, e.g. `EffectiveIncomeTaxRateContinuingOperations`)
  and MU's debt covenant ratios (3.25, 3.75). No unit name contains `percent`, `pct`,
  `ratio`, `bps` or `basis`. `Integer` (2 facts, AMD `NumberOfSegments`) is a count, like
  `segment` or `patent`, and is deliberately not listed. A filer can switch: AMD's FY2024
  effective tax rate 0.19 is `pure` in its FY2024 10-K (fact 20344) and `Rate` as the
  comparative in its FY2025 10-K (fact 115852).
  - AMD FY2025 10-K `0000002488-26-000018` (period 2024-12-29..2025-12-28, instants at
    2025-12-28): OperatingIncomeLoss 114585 USD 3,694,000,000 (decimals −6);
    EffectiveIncomeTaxRateContinuingOperations 115827 `Rate` −0.025 (decimals 3; the
    comparative 115852 `Rate` 0.19); statutory rate
    EffectiveIncomeTaxRateReconciliationAtFederalStatutoryIncomeTaxRate 115791 `pure`
    0.210; StockholdersEquity 114709 USD 62,999,000,000; LongTermDebtNoncurrent 114679
    USD 2,348,000,000; LongTermDebtCurrent 115446 USD 874,000,000.
  - AVGO FY2024 10-K `0001730168-24-000139` (2023-10-30..2024-11-04): pretax income
    24844 USD 9,916,000,000; IncomeTaxExpenseBenefit 24847 USD 3,748,000,000;
    EffectiveIncomeTaxRateContinuingOperations 26258 `Rate` 0.378 (decimals 3).
