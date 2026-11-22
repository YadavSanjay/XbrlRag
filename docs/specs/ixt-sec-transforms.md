# ixt-sec-transforms — load the SEC's inline transforms, and repair the facts stored without them

A 10-K's inline XBRL names a transform for each formatted fact, such as `ixt-sec:durwordsen` or
`ixt-sec:numwordsen`. The SEC's are not part of Arelle's core: they live in the `EDGAR/transform`
plugin, which `ArelleXBRLIngestor` has never loaded. Arelle reports
`ix11.11.1.2:invalidTransformation`, and xbrlDB stores the text `(ixTransformValueError)` with a NULL
`effective_value`.

On `xbrl_db` that is 7,006 of 268,160 facts, in all 155 filings: useful lives, vesting periods, debt
terms, exchange names, filer flags, and numbers written as words. 1,188 of them are numeric, and 199
of those carry a concept family. The values are missing, not wrong.

With the plugin loaded, Arelle gives every one of the 155 filings the same number of facts as the
store and no `(ixTransformValueError)` at all (Notes).

## Requirement

### Ingest

1. **The plugin loads.** `_arelle_argv` passes `--plugins` as
   `<patch plugin>|EDGAR/transform|xbrlDB`.
2. **The ingest image has it.** `infra/docker/Dockerfile.ingest` installs `__init__.py` and
   `text2num.py` of `transform/` from Arelle/EDGAR at commit
   `4891c4c9889c77af247428ceff4fa7cc52d534ea` (tag 26.3) under Arelle's plugin directory as
   `EDGAR/transform/`, checks each file's SHA-256 and fails the build on a mismatch. The plugin stays
   out of `src/`: it is third-party code, and it would need a lint exclusion there.
3. **A missing plugin is loud.** Arelle carries on without a plugin it cannot load, so the fault
   would return silently. GATE-A holds back any filing with one or more
   `(ixTransformValueError)` facts, with a reason that gives the count. It is hard: `gate_b_enforce`
   does not change it. `_CANDIDATE_SQL` supplies the count as `transform_error_facts`. A row with no
   such field, or with `None` or 0, passes.

### Repair

4. **Facts are repaired in place.** A re-ingest would give every fact a new `fact_id`, and citations
   and the suite's keys hold those ids. `plan_fact_updates` takes one filing's stored rows and what
   Arelle gives with the plugin, and returns an update for each stored row whose `fact_value` is the
   marker, joined on `xml_id`, which is unique within a filing.
5. **The update comes from Arelle.** It carries Arelle's `value` as `fact_value` and its
   `effective_value`, which is `None` for a fact that isn't numeric. A numeric value parses to
   `Decimal`, exactly, never through `float`.
6. **Nothing else moves, and any doubt stops the filing.** `plan_fact_updates` raises `ValueError`
   when a marker row's `xml_id` is missing from the export, when the export still holds the marker
   for it, when a row that isn't a marker has a different value in the export, or when the export has
   an `xml_id` the store does not. It returns nothing partial.
7. **Applying is guarded.** `apply_fact_updates` issues one parameterised
   `UPDATE fact SET fact_value = %s, effective_value = %s WHERE fact_id = %s AND fact_value = %s`
   per update, the last parameter being the marker, and raises `RuntimeError` naming the `fact_id`
   when a statement changes other than one row. It does not commit: the caller holds one transaction per filing.
8. **Repeating is harmless.** A filing with no marker left plans no updates.

**In scope:** requirements 1-8, the Arelle plugin that exports `xml_id`, `value` and
`effective_value`, and a command that runs one filing or all of them, dry by default. **Out of
scope:** running the repair, which is a rollout under the root `CLAUDE.md`'s checks; any view or
concept-mapping change; any new use of the repaired facts.

## Acceptance criteria

- **C1**: `_arelle_argv` gives the three plugins in the stated order, for any filing path. Oracle:
  requirement 1.
- **C2**: The Dockerfile holds the commit and both SHA-256 values of requirement 2, and a
  `sha256sum` check. Metamorphic: neither hash appears anywhere else in the file, so each names one
  file. Oracle: the hashes of those files at that commit, read from GitHub on 2026-11-01.
- **C3**: A filing with `transform_error_facts` of 1 or more fails GATE-A and isn't published, with
  or without `gate_b_enforce`, and its reason holds the count. 0, `None` or no field passes as
  before. Property: for any count, published exactly when it is 0. The candidate SQL selects the
  field. Oracle: requirement 3.
- **C4**: For stored rows of the marker on `f-1` and `f-2` and a good value on `f-3`, with an export
  of `"P5Y"` and no `effective_value` for `f-1`, and `"3"` and `"3"` for `f-2`, the plan is exactly
  those two updates, with `Decimal("3")`. Property: for any filing, one update per marker row, each
  with that row's `fact_id`, and none for another row. Oracle: requirements 4 and 5, by hand.
- **C5**: Each case of requirement 6 raises `ValueError`. Property: removing any one marker row's
  export raises, and a filing with no marker plans nothing. Oracle: requirements 6 and 8.
- **C6**: `apply_fact_updates` sends one parameterised statement per update, in order, with the
  marker as the guard, and doesn't commit; a count other than 1 raises `RuntimeError` naming the
  `fact_id`. Property: for
  any updates, the parameters are the updates'. Oracle: requirement 7. The cursor is the fake: it is
  the database boundary.

## Interface

```python
# src/ingest/sec_edgar/ingest/transform_backfill.py
def plan_fact_updates(
    stored: Sequence[Mapping[str, Any]], exported: Mapping[str, Mapping[str, Any]]
) -> list[tuple[int, str, Decimal | None]]: ...

def apply_fact_updates(
    cursor: Any, updates: Sequence[tuple[int, str, Decimal | None]]
) -> None: ...
```

`stored` rows hold `fact_id`, `xml_id` and `fact_value`. `exported` maps `xml_id` to a row with
`value` and `effective_value`, the latter a string or `None`. An update is `(fact_id, fact_value,
effective_value)`.

## Notes

- Evidence, 2026-11-01, frozen Arelle 2.41.0 with `--plugins EDGAR/transform`, to files only, over
  all 155 filings on `xbrl_db`: 155 ran, 0 markers, 0 `invalidTransformation`, and each filing's
  fact count equals the store's. KO FY2023 before and after: 46 values changed and no others.
- `NaN` in Arelle's fact-list CSV is how a numeric fact with the marker prints. The store holds the
  marker, so 7,006 is the whole count. The 316 numeric facts with an empty `fact_value` are a
  different thing and stay as they are.
- `xml_id` is set and unique within a filing on all 268,160 facts. `fact_id` comes from `seq_fact`.
  `fact_aug`, `fact_dom_position`, `section_assignment` and `chunk_cross_links` key on it.
- xbrlDB's own values: `fact.value`, and `roundValue(fact.value, fact.precision, fact.decimals)` for
  a numeric fact that isn't nil. The export plugin computes the same.
- The upstream `transform/` files equal the frozen build's apart from line endings, and did not
  change across tags 26.1, 26.1.3 and 26.3. `regex`, which they import, is a declared dependency of
  `arelle-release`. The Arelle/EDGAR project is CC0; `text2num.py` is MIT, and its notice stays in
  the file.
- **Rollout order matters.** Every stored filing holds markers, so a gate run before the repair
  holds back all of them. The order on each database is: backup; dry run; repair; rebuild the MVs;
  rebuild and diff the suite; then the gate. `xbrl_heldout` needs the same, including the
  `...\data\v2` root, and IBM's three multi-document filings have not been through this check.
- The repair adds numeric values to views, so the 199 family facts (`NumberOfSegments` 194, `Debt` 4,
  `StockRepurchases` 1) can change a view's rows. HTML chunks do not change, so no reload follows.
- The tests hold no Arelle run: Arelle is a real external tool. The export plugin is proved by the
  rollout's dry run, where requirement 6 refuses any mismatch.
