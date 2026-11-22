# hx-kind-targets — per-kind H-X warning targets from pre-registration Amendment 1.1

[hx-authoring](hx-authoring.md) (implemented, retired) requirement 8 makes `validate_hx`
warn for every kind whose count is not 15. The held-out pre-registration's **Amendment 1.1**
(2026-11-11, `docs/eval/heldout_v2/preregistration.md`) keeps H-X at 75 questions but changes
the mix, before any H-X question was written:

| Kind | Was | Now |
|---|---|---|
| `cross_filer` | 15 | **24** |
| `cross_period` | 15 | **15** |
| `compound` | 15 | **18** |
| `honesty` | 15 | **9** |
| `text` | 15 | **9** |
| total | 75 | **75** |

Until this change lands, the amendment says the warnings at the new counts are expected. This
change makes the authoring tool's warnings follow the amendment, so a warning again means "off
the pre-registered mix".

## Requirement

1. **Per-kind targets.** `validate_hx` compares each kind's count with that kind's own target
   from the table above (`cross_filer` 24, `cross_period` 15, `compound` 18, `honesty` 9,
   `text` 9; 75 in all), not with one target for every kind.
2. **Warnings.** One warning per kind whose count differs from its target (above or below),
   and none for a kind at its target. Each warning names the kind, its count and its target,
   as whole numbers (for example `"text: 8 cases, target 9"`; the exact wording is free, but
   the only integers it contains are the count and the target), and names no other kind. A
   kind with no cases counts 0 and warns. Warnings stay whole-set (no id prefix).
3. **No values in messages** (hx-authoring requirement 8, unchanged): a warning never contains
   any value from a case: no id, question, ticker, amount or other case text.
4. **Nothing else changes.** Errors, `mix` (kinds, subtypes, filers, total) and the
   `ValueError` on overlapping filer sets are exactly as hx-authoring requires. Sub-type spread
   (Amendment 1.1's "4 each", "5 each", "3 each") is guidance for the author, not a warning.

## Acceptance criteria

Cases are hx-authoring's hand-built valid cases (`kit.make`, fictional tickers `DEVA`...);
the targets in every test are written from Amendment 1.1's table, never read from `src`.

- **C1**: per-kind targets and warnings (requirements 1–3).
  - The Amendment 1.1 mix (24, 15, 18, 9, 9) gives no errors and no warnings; total 75.
  - One kind one below its target, or one above, with the others at target, gives exactly one
    warning, naming that kind, its count and its target (all five kinds, both directions).
  - The old mix (15 of each) gives exactly four warnings: `cross_filer` (15, target 24),
    `compound` (15, target 18), `honesty` (15, target 9) and `text` (15, target 9); none for
    `cross_period`.
  - A set with only one `text` case warns for all five kinds, each with count and target.
  - No warning contains a case id, ticker or question text.

  Property: for random per-kind counts (0–30 each), the kinds warned are exactly those whose
  count differs from its target, and each warning's integers are exactly its count and target.
  Oracle: Amendment 1.1's table (pre-registration) and requirement 2.

## Interface

No new function or name. Changed (implementer): the warning targets of
`src.eval.set_b.hx.validate_hx` (`TARGET_PER_KIND`, `src/eval/set_b/hx.py`). How the targets
are held (for example a per-kind mapping) is the implementer's choice; the tests read only
`validate_hx`'s output.

## Notes

- **Oracle.** The targets come from the pre-registration Amendment 1.1 table, a
  maintainer decision made on the shape of the set alone, independent of this code.
- **Retired tests.** hx-authoring C1 pinned "15 of each gives no warning" and "14 of one kind
  gives exactly one warning". This spec PR rewrites those two tests so they hold under both
  the old and the new targets (15 of each never warns for `cross_period`, and every warning
  then says 15; 14 `text` cases always give a warning naming `text` and 14, with no case
  value in any warning). The exact targets are pinned here only. All must keep passing
  (`pytest test/spec`).
- **Text the implementer updates with the code** (not tests): the `hx.py` module docstring
  ("15 each"), `configs/eval/hx_template.yaml` ("15 per kind"), and the H-X wording in
  `docs/guides/xbrl-flow/12-heldout-set-b.md`. hx-authoring.md requirement 8 and the v2 plan
  say 15 per kind; the maintainer decides whether to annotate them (an implemented spec is not
  re-locked).
