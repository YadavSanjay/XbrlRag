# reporting-currency-coverage — the reporting currency is the unit with the most periods

`choose_lookup_unit` ([`unit-aware-lookup`](unit-aware-lookup.md)) resolves a lookup in a
single unit. When the latest period carries several units, it takes the one that also appears
in an earlier period. The assumption was that a 20-F's convenience translation is tagged for
the latest period only.

TSMC's balance sheet breaks that assumption: it tags the **prior** year-end in USD as well.
Cash and cash equivalents FY2024 (filing `0001193125-25-083423`):

- **TWD** at 2025-01-01, 2024-01-01, 2023-01-01 and 2022-01-01;
- **USD** at 2025-01-01 and 2024-01-01.

Both units therefore have a comparative, so the rule finds no single answer. The lookup is
AMBIGUOUS, and the `aq-5a` cash cases for TSMC FY2024 and FY2025 are refused in every run.

## Requirement

Among the units at the latest period, the reporting currency is the one tagged at the **most
distinct period ends**. A tie means no single unit, and the lookup stays AMBIGUOUS as before.
A requested unit and a single latest-period unit are handled exactly as before.

This is a refinement, not a reversal. Wherever the old rule picked a unit, that unit has at
least two period ends and every other latest-period unit has exactly one, so the new rule picks
the same one.

## Acceptance criteria

- **C1**: `choose_lookup_unit(observations, requested_unit)` follows the requirement.

  Point cases:
  - TSMC cash FY2024 (TWD at 4 period ends, USD at 2) gives `TWD`;
  - TSMC revenue (TWD at 3, USD at 1) gives `TWD`, unchanged;
  - two currencies at the latest period with equal coverage give `None`, unchanged;
  - a requested `USD` for TSMC cash gives `USD`.

  Oracle: the definition, on the stored facts.
- **C2**: It agrees with a brute-force statement of the rule for any observations. Repeating
  an observation (the same unit and period end) changes nothing; only distinct period ends
  count.

  Oracle: the rule written out.
- **C3**: Metamorphic properties:
  - the order of the observations does not matter;
  - adding an earlier period end for the chosen unit never changes the choice;
  - wherever the earlier "has a comparative" rule returned a unit, this rule returns the same
    unit.

  Oracle: the definitions.

## Interface

No new functions. This changes the behaviour of the existing
`src.retriever.xbrl.xbrl_factstore_lookup.choose_lookup_unit`.

## Notes

- **Expected effect:** the 2 TSMC `aq-5a` cash cases resolve in TWD (2,127,627,000,000 for
  FY2024).
- **Out of scope:** TSMC's effective tax rate, which needs a compute fallback (maintainer
  decision: dropped).
