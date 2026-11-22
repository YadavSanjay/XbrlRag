# fastpath-concepts — the router's fast path must not map a metric to a near-miss family

Phase 1 of the accuracy plan.

## Requirement

The intent router's fast path turns a single concept phrase into a 1-step `lookup` plan
via `IntentRouter._match_concept_family`. Today it character-matches
(`difflib.SequenceMatcher` ≥ 0.72) against a condensed glossary, so a phrase lands on a
*less specific* family that looks similar: "total current assets" → `TotalAssets`,
"gross margin" → `GrossProfit`, "EBITDA" → `OperatingIncome` (via "ebit"). The answer then
cites a real but different fact, and the case fails.

After this change, the fast path maps a phrase only when it names the family; anything
that is a ratio, margin, derived metric, or carries a qualifier the family lacks returns
`None`, so `plan_generator` (full taxonomy, compute steps) handles it instead.

## Acceptance criteria

Behavior on 2026-10-13 `main`, for reference: "total current assets" → `TotalAssets`,
"current assets" → `None`, "current liabilities" → `TotalLiabilities`, "gross margin" →
`GrossProfit`, "net margin" → `NetIncome`, "EBITDA" → `OperatingIncome`, "operating income
growth" → `OperatingIncome`, "revenue per share" → `DividendsPerShare`, "free cash flow" →
`FreeCashFlow`.

- **C1**: "total current assets" and "current assets" → `CurrentAssets`;
  "total current liabilities" and "current liabilities" → `CurrentLiabilities`; and
  prefixing "total " to "current assets"/"current liabilities" does not change the family.
  Oracle: US-GAAP taxonomy — `us-gaap:AssetsCurrent` / `us-gaap:LiabilitiesCurrent` are
  distinct elements from `us-gaap:Assets` / `us-gaap:Liabilities`.
- **C2**: Ratio, margin and derived-metric phrases return `None`: "gross margin",
  "operating margin", "net margin", "net profit margin", "EBITDA", "EBITDA margin",
  "free cash flow", "return on equity", "debt to equity", "current ratio",
  "revenue growth". Oracle: these are not single tagged US-GAAP facts (they are ratios
  or non-GAAP measures computed from several facts; `mv_derived_metrics` derives them).
- **C3**: Direct names keep their family *while* their derived or qualified sibling does
  not take it — contrast pairs: "gross profit" → `GrossProfit` / "gross margin" → `None`;
  "operating income" → `OperatingIncome` / "operating margin" → `None` and "EBITDA" →
  `None`; "net income" → `NetIncome` / "net margin" → `None`; "revenue" and "net sales" →
  `Revenue` / "revenue growth" → `None`; "total assets" → `TotalAssets` / "total current
  assets" → `CurrentAssets`; "total liabilities" → `TotalLiabilities` / "current
  liabilities" → `CurrentLiabilities`; "capital expenditures" → `Capex` / "free cash
  flow" → `None`; "effective tax rate" → `EffectiveTaxRate` (a tagged fact,
  `us-gaap:EffectiveIncomeTaxRateContinuingOperations`) / "return on equity" → `None`.
  Oracle: US-GAAP element names. (Stated as pairs because a spec with no new functions is
  in bug-fix mode: every test must fail on current code, so no-regression checks ride
  alongside the behavior they guard.)
- **C4**: A ratio-forming qualifier yields `None`: for each base in {"revenue",
  "net income", "operating income", "gross profit"} and qualifier in {"margin", "growth",
  "per share"}, "<base> <qualifier>" → `None`. Oracle: metamorphic — the qualifier turns a
  tagged value into a ratio, rate or per-share figure, which is not that value.
- **C5**: Matching is invariant to case, surrounding whitespace, inner whitespace runs and a
  trailing period, and the invariant holds for the corrected mappings: e.g.
  "  Total   Current Assets. " → `CurrentAssets`, "Total Assets." → `TotalAssets`,
  " GROSS  MARGIN " → `None`. Oracle: property of the phrase (formatting does not change
  meaning), with C1–C3 as the expected values.

## Interface

- Existing: `src.retriever.xbrl.intent_router.IntentRouter._match_concept_family(phrase: str) -> str | None`
  (staticmethod; behavior changes, signature does not).

## Notes

- Implementation is free to replace `SequenceMatcher` (e.g. normalized exact/token match
  against the synonym list); the spec fixes behavior, not the algorithm.
- `FreeCashFlow` leaves the fast-path glossary: it is derived, and `lookup` cannot read
  `mv_derived_metrics` until the Phase 2 spec (`derived-lookup`) lands.
- `CONDENSED_CONCEPT_FAMILIES` must only name families present in `concept_mapping`
  (verify `CurrentAssets` / `CurrentLiabilities` exist — they do as of 2026-10-12).
- Expected live effect: the 7 `aq-5a-balance-sheet-lookup` current-assets/liabilities
  failures and the fast-path-misrouted `aq-7a-derived-metric-lookup` cases reach the
  plan generator (the latter only become *correct* after Phase 2).
