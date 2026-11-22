# SEC companyfacts check

- Suite: `test/eval/suites/sql_derived_v1.json` (corpus fingerprint `pm:3bf0c14a2f4afad3`)
- Run: 2026-10-22T01:40:13+00:00, git `bcb83ee`, seed 20261021

## Cases by kind

| kind | total | match | mismatch | unverifiable | hand_check | template_unknown | checked | match rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| breakdown | 53 | 0 | 0 | 0 | 53 | 0 | 0 | — |
| compute | 108 | 104 | 0 | 4 | 0 | 0 | 104 | 100.0% |
| dimensional | 100 | 0 | 0 | 0 | 100 | 0 | 0 | — |
| lookup | 199 | 188 | 0 | 11 | 0 | 0 | 188 | 100.0% |
| screen | 22 | 19 | 0 | 1 | 2 | 0 | 19 | 100.0% |
| all | 482 | 311 | 0 | 16 | 155 | 0 | 311 | 100.0% |

## Facts by status

| fact status | facts |
|---|---:|
| concept_absent | 14 |
| dimensional | 9 |
| match | 355 |

## Non-match cases (16)

### `aq-1c-pnl-line-lookup:concept=NetIncome-fy=2025-ticker=TSM` — unverifiable (concept_absent)

- item TSM: expected 1,695,124,900,000, SEC — — differs
- fact 91334: concept_absent, SEC —

### `aq-1c-pnl-line-lookup:concept=TaxExpense-fy=2025-ticker=TSM` — unverifiable (concept_absent)

- item TSM: expected 346,529,800,000, SEC — — differs
- fact 93425: concept_absent, SEC —

### `aq-2c-yoy-growth:concept=OperatingIncome-fy=2025-ticker=TSM` — unverifiable (concept_absent)

- item TSM: expected 0.464458, SEC — — differs
- fact 90979: concept_absent, SEC —
- fact 87135: match, SEC 1,322,053,000,000

### `aq-2c-yoy-growth:concept=NetIncome-fy=2025-ticker=TSM` — unverifiable (concept_absent)

- item TSM: expected 0.464441, SEC — — differs
- fact 91334: concept_absent, SEC —
- fact 87469: match, SEC 1,157,523,900,000

### `aq-5a-balance-sheet-lookup:concept=TotalAssets-fy=2025-ticker=TSM` — unverifiable (concept_absent)

- item TSM: expected 7,932,842,500,000, SEC — — differs
- fact 90847: concept_absent, SEC —

### `aq-5a-balance-sheet-lookup:concept=CashAndEquivalents-fy=2025-ticker=TSM` — unverifiable (concept_absent)

- item TSM: expected 2,767,856,400,000, SEC — — differs
- fact 91832: concept_absent, SEC —

### `aq-6a-cash-flow-lookup:concept=CashFromFinancing-fy=2025-ticker=TSM` — unverifiable (concept_absent)

- item TSM: expected -440,344,600,000, SEC — — differs
- fact 91718: concept_absent, SEC —

### `aq-6a-cash-flow-lookup:concept=Capex-fy=2025-ticker=TSM` — unverifiable (concept_absent)

- item TSM: expected 1,272,410,500,000, SEC — — differs
- fact 93787: concept_absent, SEC —

### `aq-7a-derived-metric-lookup:fy=2025-metric=EffectiveTaxRate-ticker=TSM` — unverifiable (concept_absent)

- item TSM: expected 0.16973, SEC — — differs
- fact 91015: concept_absent, SEC —
- fact 91011: concept_absent, SEC —

### `aq-7a-derived-metric-lookup:fy=2025-metric=CurrentRatio-ticker=TSM` — unverifiable (concept_absent)

- item TSM: expected 2.50729, SEC — — differs
- fact 90811: concept_absent, SEC —
- fact 90883: concept_absent, SEC —

### `aq-7b-margin-screen:fy=2025-metric=FreeCashFlow` — unverifiable (concept_absent)

- item TSM: expected 1,002,565,100,000, SEC — — differs
- item AAPL: expected 98,767,000,000, SEC 98,767,000,000 — agrees
- item GOOGL: expected 73,266,000,000, SEC 73,266,000,000 — agrees
- item MSFT: expected 71,611,000,000, SEC 71,611,000,000 — agrees
- item NVDA: expected 60,853,000,000, SEC 60,853,000,000 — agrees
- fact 91586: concept_absent, SEC —
- fact 91562: concept_absent, SEC —
- fact 14629: match, SEC 12,715,000,000
- fact 14617: match, SEC 111,482,000,000
- fact 34569: match, SEC 91,447,000,000
- fact 34566: match, SEC 164,713,000,000
- fact 49046: match, SEC 64,551,000,000
- fact 49019: match, SEC 136,162,000,000
- fact 64635: match, SEC 3,236,000,000
- fact 64620: match, SEC 64,089,000,000

### `aq-10a-tsm-twd-lookup:concept=GrossProfit-fy=2025` — unverifiable (concept_absent)

- item TSM: expected 2,281,294,000,000, SEC — — differs
- fact 90955: concept_absent, SEC —

### `aq-10a-tsm-twd-lookup:concept=OperatingIncome-fy=2025` — unverifiable (concept_absent)

- item TSM: expected 1,936,091,700,000, SEC — — differs
- fact 90979: concept_absent, SEC —

### `aq-10a-tsm-twd-lookup:concept=NetIncome-fy=2025` — unverifiable (concept_absent)

- item TSM: expected 1,695,124,900,000, SEC — — differs
- fact 91334: concept_absent, SEC —

### `aq-10a-tsm-twd-lookup:concept=CashFromOperations-fy=2025` — unverifiable (concept_absent)

- item TSM: expected 2,274,975,600,000, SEC — — differs
- fact 91562: concept_absent, SEC —

### `aq-10a-tsm-twd-lookup:concept=Capex-fy=2025` — unverifiable (concept_absent)

- item TSM: expected 1,272,410,500,000, SEC — — differs
- fact 93787: concept_absent, SEC —
