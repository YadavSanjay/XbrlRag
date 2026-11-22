# ns-expand-rank-sync — a namespace-expanded concept_mapping row always carries its base row's rank

`concept_mapping` holds two kinds of row. **Base rows** (sources `hifi_kpi`, `ifrs`,
`auto_override`, `reviewed_override`) use short-form QNames such as `us-gaap:CommonStockSharesIssued`.
**Expanded rows** (`source = 'ns_expanded'`) copy a base row once for each namespace version in the
`qname` table, as `http://fasb.org/us-gaap/2025:CommonStockSharesIssued`. `mv_financial_facts`
matches facts on the expanded rows only, so the expanded rows are what XbrlRag and the suites rank.

Expanded rows are never updated after they are written:

- `scripts/ddl/populate/concept_mapping_ns_expand.sql` inserts an expanded copy only where one is
  missing (`NOT EXISTS` on family, full QName and cik_override). It never updates an existing copy.
- `Layer0Populator._replace_source_concept_mapping` deletes and reloads a source's base rows. It
  deletes expanded copies only for reviewed-override (family, cik) pairs. The facts-count override
  pass deletes them only for the (family, cik) it rewrites.

So when the global base ranks changed (concept-rank-integrity, 2026-10-15, and concept-key-integrity,
2026-11-03), the global expanded copies kept their old ranks. Read-only queries on 2026-11-05
found the same drift on `xbrl_db` and `xbrl_heldout`. Matched on (family, taxonomy, local name,
cik_override), 4 base rows disagree with their 24 expanded copies (six us-gaap versions each):

| Family | QName | Base rank | Expanded rank |
|---|---|---|---|
| SharesOutstanding | `CommonStockSharesIssued` | 50 (component) | 2 |
| AccountsPayable | `AccountsPayableAndAccruedLiabilitiesCurrent` | 50 (component) | 2 |
| OtherNonOperatingIncome | `OtherNonoperatingIncomeExpense` | 1 | 2 |
| OtherNonOperatingIncome | `NonoperatingIncomeExpense` | 2 | 1 |

No expanded row lacks a base row, and the deprecation flags match today.

The effect is that two component-band concepts answer as primaries, both in the product and in the v2
suite keys (`drop_component_rows` in `src/eval/suites/v2_sources.py` reads the MV's ranks). For
example, Procter & Gamble "shares outstanding" is keyed at 4,009.2M issued shares instead of about
2.35B outstanding, the case concept-key-integrity set out to fix. `OtherNonOperatingIncome` still
answers with total non-operating income first.

## Requirement

1. **Expanded rows are derived from base rows, and from nothing else.** For each base row whose QName
   is `<prefix>:<local>`, with `<prefix>` one of the four taxonomies below, and for each
   `(namespace, local_name)` in the `qname` table with `local_name = <local>` and `namespace`
   starting with one of the prefix's namespace bases, there is exactly one expanded row. It has:
   - QName `namespace + ":" + local`;
   - the base row's `concept_family`, `preference_rank`, `cik_override` and `is_deprecated`;
   - `source = 'ns_expanded'`.

   There are no other expanded rows. An expanded row whose base row is gone is removed, and one
   whose rank or deprecation flag differs is replaced. This holds for every source, including the
   global (`cik_override` NULL) rows. Deriving again from the same base rows gives the same rows
   (idempotent).

   | Prefix | Namespace bases (the same as `concept_mapping_ns_expand.sql`) |
   |---|---|
   | `us-gaap` | `http://fasb.org/us-gaap/` |
   | `srt` | `http://fasb.org/srt/` (so not `http://fasb.org/srt-types/`) |
   | `dei` | `http://xbrl.sec.gov/dei/` |
   | `ifrs-full` | `http://xbrl.ifrs.org/taxonomy/`, `https://xbrl.ifrs.org/taxonomy/` |

   A base row with any other prefix, or one already in full-namespace form (`http://…` or
   `https://…`), has no expanded row.
2. **A base row is matched on family, taxonomy, local name and cik_override**, never on the local
   name alone. The taxonomy of an expanded row is the prefix whose namespace base its namespace starts
   with, and its local name is the text after the QName's last `:`. For example, `NetIncome` has
   global `us-gaap:ProfitLoss` at 2 and `ifrs-full:ProfitLoss` at 100, while TSMC (`0001046179`) ranks
   `ifrs-full:ProfitLoss` 1 and `us-gaap:ProfitLoss` 3. Each copy takes its own base row's rank.
3. **Drift is detected.** Given every concept_mapping row, the drift check returns each expanded row
   whose base row differs in `preference_rank` or `is_deprecated`, paired with that base row, and
   each expanded row with no base row (an orphan, including one whose namespace matches no prefix),
   paired with nothing. Rows in sync, and base rows, are never returned.
4. **Drift fails loud on every mapping write.** After every write to `concept_mapping`, the
   expanded rows are re-derived and the check runs in the same transaction. Drift raises
   `NsExpandedDriftError`, so the transaction rolls back and the step fails. The writes are
   `populate_concept_mapping` (every mode), `populate_concept_mapping_overrides` and the
   `layer0-populate` step of `bootstrap_db.py`.

   **Why the populate step and not the publish gate.** `concept_mapping` is global. It changes only
   through these writes, and a drifted row is wrong for every filer the moment it is written. The
   publish gate (`promotion.py`) runs per filing, from its own CLI outside `bootstrap_db.py`. It does
   not run when the mapping is reloaded, and it would block one filing for a defect in all of them.
   The 2026-10-15 and 2026-11-03 reloads touched no filing, so a check in the gate would not have run.

## Acceptance criteria

All rows are `ConceptMappingRecord`s. A *base row* is a row whose source is not `ns_expanded`. A
row's *key* is `(concept_family, qname, cik_override or "")`.

- **C1**: `expand_concept_mapping(rows, known_qnames)` returns exactly the rows of requirement 1,
  sorted by key, with the values of each one's base row. Point cases:
  - The four drifted base rows above, with the six us-gaap versions `2021-01-31`, `2022`–`2026`, give
    24 rows. `CommonStockSharesIssued` and `AccountsPayableAndAccruedLiabilitiesCurrent` are at 50,
    `OtherNonoperatingIncomeExpense` at 1 and `NonoperatingIncomeExpense` at 2, whatever stale
    `ns_expanded` rows the input also holds.
  - `NetIncome` `ProfitLoss` (requirement 2), with the six us-gaap and four IFRS versions, gives the
    global us-gaap copies at 2, the global IFRS copies at 100, and TSMC's copies at 3 (us-gaap) and
    1 (IFRS).
  - There is no row for an `srt:` base row in `http://fasb.org/srt-types/2025`, for a local name
    absent from `known_qnames`, for a us-gaap base row in an IFRS namespace, for a full-form or
    unknown-prefix base row, or from an input `ns_expanded` row.
  - A deprecated base row gives deprecated copies.
  - A pair repeated in `known_qnames` gives one row.

  Oracle: the base rows, written by hand, and the expansion rule of `concept_mapping_ns_expand.sql`.
- **C2**: Properties of `expand_concept_mapping` over generated base rows and known QNames:
  - every row is `ns_expanded` and has a base row with the same family, taxonomy, local name and
    cik_override, whose rank and deprecation flag it carries;
  - the number of rows is the number of (base row, matching known QName) pairs, and keys are
    distinct;
  - the output is sorted by key;
  - adding the output, or any stale `ns_expanded` rows, to the input changes nothing (idempotent);
  - the result does not depend on the order of either input.

  Oracle: requirement 1.
- **C3**: `ns_expanded_drift(rows)` on the 2026-11-05 state of the four drifted families and
  `NetIncome` (base rows and expanded rows as found, written by hand) returns exactly the 24 drifted
  copies, sorted by key, each with its base row. It does not return the IFRS copies at 100 (global)
  or 1 (TSMC), nor the us-gaap `ProfitLoss` copies at 2 (global) and 3 (TSMC). It does flag:
  - a TSMC IFRS copy at the global rank 100;
  - a global copy at TSMC's rank 1;
  - a copy whose deprecation flag differs from its base row's;
  - an orphan, returned with `None`. Orphans are a copy whose family has no base row, and a copy in
    an unknown namespace.

  Oracle: the base rows, written by hand.
- **C4**: Properties of `ns_expanded_drift`:
  - base rows plus `expand_concept_mapping` of them have no drift;
  - changing the rank of any subset of those copies flags exactly that subset;
  - removing a base row makes exactly its copies orphans;
  - adding or reordering base rows that have no copies changes nothing.

  Oracle: requirements 1–3.
- **C5**: `assert_ns_expanded_in_sync(rows)` returns the number of `ns_expanded` rows when
  `ns_expanded_drift(rows)` is empty. Otherwise it raises `NsExpandedDriftError`, a `ValueError`,
  whose message contains each drifted row's family and full QName. On the C3 point state it raises.
  On base rows plus their derived copies it returns the copies' count. Oracle: requirement 4.

## Interface

New, in `src/ingest/sec_edgar/ingest/layer0_populator.py` (stubbed in the spec PR):

- `class NsExpandedDriftError(ValueError)`
- `expand_concept_mapping(rows: Iterable[ConceptMappingRecord], known_qnames: Iterable[tuple[str, str]]) -> list[ConceptMappingRecord]`
- `ns_expanded_drift(rows: Iterable[ConceptMappingRecord]) -> list[tuple[ConceptMappingRecord, ConceptMappingRecord | None]]`
- `assert_ns_expanded_in_sync(rows: Iterable[ConceptMappingRecord]) -> int`

Behavior on existing code (added by the implementer, with unit tests against a fake connection;
not in the spec tests):

- A `Layer0Populator` method that, on an open connection:
  1. reads every concept_mapping row and the `(namespace, local_name)` pairs of `qname`;
  2. deletes every `ns_expanded` row and inserts `expand_concept_mapping` of the rest;
  3. re-reads the rows and calls `assert_ns_expanded_in_sync`.

  It runs inside the caller's transaction and is called at the end of `populate_concept_mapping`
  (every mode, not on a dry run) and `populate_concept_mapping_overrides` (not on a dry run). The
  per-(family, cik) `ns_expanded` deletes in `_replace_source_concept_mapping` and
  `_replace_overrides` may stay or go; the re-derivation supersedes them.
- `bootstrap_db.py` `layer0-populate` runs the same method after the populate SQL files.
  `concept_mapping_ns_expand.sql` keeps fix 2 (the ProductRevenue/ServiceRevenue demotion). Its
  fix-1 `INSERT` is either dropped in favour of the method or kept, as long as the method runs after
  it. The demotion updates base and expanded rows alike, and re-derivation copies the demoted base
  ranks, so the two fixes agree in either order. The check runs last.
- The docstrings that tell the operator to re-run `concept_mapping_ns_expand.sql` after an override
  pass are updated, as is the `concept_mapping` note in `src/ingest/sec_edgar/CLAUDE.md`.

## Notes

- **Oracle.** The base rows, written by hand from the 2026-11-05 read-only queries, and the
  expansion rule of `concept_mapping_ns_expand.sql` (prefix → namespace bases, joined on local name).
  The expected expanded rows are the base rows' values. No test reads a database.
- **Measured state (2026-11-05, both databases).**
  - `xbrl_db`: 5,375 `ns_expanded` rows, 24 of which drift from 4 base rows. No orphans, no missing
    copies, and no deprecated rows.
  - `xbrl_heldout`: 6,477 `ns_expanded` rows, with the same 24 drifted copies.
  - `NetIncome`: `ifrs-full:ProfitLoss` is global 100 and TSMC 1; `us-gaap:ProfitLoss` is global 2
    and TSMC 3. All of their copies are in sync.
- **Rollout, run by the maintainer after the implementation merges (not in tests).** The
  implementation PR carries the exact PowerShell commands:
  1. re-derive on `xbrl_db`, then check that the drift is empty;
  2. on `xbrl_heldout`, with the maintainer's OK, do the same;
  3. rebuild the MVs on each (`scripts/bootstrap_db.py --phase mv`);
  4. rebuild and diff the v1 `sql_derived` suite;
  5. rebuild the frozen v2 dev and validation suites, and diff them against their frozen versions.

  Expected suite changes: `SharesOutstanding` keys move from issued to outstanding shares, or the
  filer-year leaves the suite where only issued shares are reported. `AccountsPayable` keys stop
  using payables plus accrued liabilities. `OtherNonOperatingIncome` keys prefer
  `OtherNonoperatingIncomeExpense`. The new suite hashes go into the pre-registration amendment.
- concept-rank-integrity said `concept_mapping_ns_expand.sql` "is unchanged" and relied on the
  reload deleting `ns_expanded` rows first. Its reload steps did that for `auto_override`, but
  nothing deleted the global copies of reloaded `hifi_kpi` rows, which is how this drift arose.
  Re-deriving on every write removes the need for an ordered manual step.
