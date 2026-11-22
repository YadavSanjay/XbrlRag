# unit-aware-lookup — resolve a lookup within one unit, defaulting to the reporting currency

Phase 1.5 of the accuracy plan (follows `company-aliases`).

## Requirement

A filing can tag the same concept and period in more than one unit. Foreign private issuers'
20-Fs do this routinely: TSMC reports revenue in TWD (the reporting currency) and adds a USD
**convenience translation** for the latest year only — FY2024 revenue is NT$2,894,307,700,000
*and* US$88,268,000,000, both tagged `ifrs-full:Revenue` for the same period. Today the lookup
judges ambiguity across all units, sees two different values, and returns `AMBIGUOUS`, so every
TSMC lookup is refused (14 of 14 cases in run `eval-ab63dd172611`, after `company-aliases` made
"TSMC" resolve). Comparing values across units also violates the house rule: check `unit`
before comparing values.

After this change a lookup resolves within a single unit:

1. If the step names a unit, only facts in that unit count.
2. Otherwise, when the latest period carries more than one unit, the **reporting currency**
   wins: the unit that also appears in an earlier period of the same concept (the filing's
   comparatives). A convenience translation appears only for the latest period, so it is never
   picked by default. If that does not single out one unit, the lookup stays `AMBIGUOUS`.
3. Ambiguity (distinct values at the latest period) is then judged within the chosen unit only.

A deterministic currency extractor lets the router's fast path honour "in New Taiwan dollars"
/ "in US dollars": it strips the currency phrase from the concept phrase and sets the step's
unit.

## Acceptance criteria

- **C1**: `choose_lookup_unit(observations, requested_unit)` — `observations` are
  `(unit, period_end)` pairs for one concept, period ends as ISO dates — returns
  `requested_unit` when any observation at the **latest** period end has that unit, and `None`
  when none does (the caller reports `NOT_FOUND`). Oracle: requirement 1, hand-built
  observations.
- **C2**: With no requested unit, it returns the single unit at the latest period end when
  there is only one; when there are several, the one that also appears at an earlier period
  end; and `None` when zero or several latest-period units also appear earlier (genuinely
  ambiguous). TSMC FY2024 revenue — TWD at 2025-01-01, 2024-01-01, 2023-01-01 and USD only at
  2025-01-01 — returns `"TWD"`. Oracle: the 20-F convenience-translation pattern (TSMC's FY2024
  20-F, verified in `mv_financial_facts`).
- **C3**: The result does not depend on the order of `observations`, and adding observations
  for periods earlier than the latest in the chosen unit never changes the choice. Oracle:
  metamorphic (a set of facts has no order; more comparatives in the reporting currency only
  confirm it).
- **C4**: `extract_currency_unit(phrase)` returns `(phrase_without_currency, iso_code)` for a
  trailing currency mention — "in New Taiwan dollars", "in NT dollars", "in NT$", "in TWD",
  "in US dollars", "in U.S. dollars", "in USD" — case-insensitive and tolerant of surrounding
  punctuation, e.g. "revenue in New Taiwan dollars" → `("revenue", "TWD")`; a phrase with no
  currency mention is returned unchanged with `None`. Oracle: ISO 4217 codes (TWD, USD).
- **C5**: Extraction is idempotent and composes with the fast path: extracting from the
  stripped phrase yields `None`, and for every C4 currency form, `IntentRouter
  ._match_concept_family` of the stripped phrase equals that of the bare concept ("revenue" →
  `Revenue`). Oracle: metamorphic (removing the currency mention leaves the concept).

## Interface

New functions (stubbed in the spec PR):

- `src.retriever.xbrl.xbrl_factstore_lookup.choose_lookup_unit(observations: Sequence[tuple[str | None, str | None]], requested_unit: str | None = None) -> str | None`
- `src.retriever.xbrl.intent_router.extract_currency_unit(phrase: str) -> tuple[str, str | None]`

## Notes

- Integration (implementer):
  - `XbrlFactstoreLookup` filters candidates to `choose_lookup_unit(...)` before scoring
    confidence and judging ambiguity; `None` with a requested unit → `NOT_FOUND`, `None`
    without one → `AMBIGUOUS` (as today). Resolution notes record the chosen unit and any
    unit set aside ("USD convenience translation ignored").
  - The lookup step accepts an optional `unit` (ISO 4217, `^[A-Z]{3}$`); `PlanValidator`
    accepts it on `lookup` steps and rejects any other shape.
  - The router's fast path runs `extract_currency_unit` on the concept phrase before
    `_match_concept_family` and puts the code on the 1-step plan's `unit`.
  - Teaching `plan_generator` to emit `unit` is a prompt change (version bump) and is out of
    scope; the reporting-currency default already gives the right answer for "in New Taiwan
    dollars" questions that reach the planner.
  - Verified live by the TSMC slice (`--ticker TSM`) and a full-suite check that no non-TSMC
    case changes (every other filer reports a single unit per concept).
- **Ground truth (decision for review):** `aq-1a-revenue-lookup:fy=2024-ticker=TSM` ("What
  was TSMC's revenue in fiscal 2024?", no currency named) expects the **USD convenience**
  value 88,268,000,000. Under this spec the answer is the reporting-currency value,
  NT$2,894,307,700,000. Proposed: expected value TWD, with the USD convenience figure as an
  `alt_values` entry — both are filed facts, so neither is wrong, and the default the system
  gives is the audited reporting-currency figure. Changes one case in the committed suite.
- Out of scope: share-count and per-share units, and scale (`decimals`) — units here are
  currencies.
