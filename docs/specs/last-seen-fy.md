# last-seen-fy — `entity_master.last_seen_fy` follows the corrected annual labels

`scripts/ddl/populate/entity_master.sql` sets `last_seen_fy` to the highest DEI
`DocumentFiscalYearFocus` of any form, read from the raw fact tables, so it bypasses the labels
[fiscal-label-alignment](fiscal-label-alignment.md) put in `filing_registry`. On live `xbrl_db`
(2026-10-30) it differs from the latest annual label on 12 of 33 filers:

- **Ten filers read one year ahead**, because they filed FY2026 10-Qs: AAPL, AMD, AMZN, AVGO,
  CEG, GOOGL, INTC, META, TSLA and VST read 2026 against 2025.
- **NVDA reads 2027** against 2026, the same way.
- **CRM reads 2025** against 2026, because its January-2026 10-K mistags DEI 2025.

The extractor anchors relative periods on this column, so "latest revenue" asks for an annual
year that does not exist, or, for CRM, reads last year's filing and says nothing.

This is a data correction. It adds no period logic. The deterministic period parser belongs to
section 6.1 of the [v2 plan](../design/XbrlRag_v2_Plan.md), in phase 5.

## Requirement

1. **`last_seen_fy` is the latest fiscal year for which the filer has an annual filing in the
   store:** the greatest `filing_registry.fiscal_year` over the filer's rows whose `form_type` is
   in `fiscal_labels.ANNUAL_FORMS` (`10-K`, `10-K/A`, `20-F`, `20-F/A`, `40-F`, `40-F/A`). With no
   such filing it is NULL. No other form counts, including `10-KT`. Since labels strictly increase
   with period end, this is the label of the latest annual period.
2. **`entity_master.sql` becomes two statements, the INSERT and then one UPDATE.**
   - The INSERT no longer writes `last_seen_fy`: not in its column list and not in `ON CONFLICT`.
     `GREATEST` goes, because a floor would hold NVDA at 2027 for ever.
   - The UPDATE is the last statement in the file and sets `last_seen_fy` on every row from
     `filing_registry` alone. It runs on every execution, so the column can fall as well as rise.
   - The file keeps its rule that a semicolon appears only as a statement terminator.
3. **Nothing else in the row moves.** `exchange`, `sic_code`, `filer_category`, `fye_month`,
   `fye_day` and `last_seen_accession` still come from the accession with the highest DEI
   (`dei_latest`, whose DEI column may be renamed), so the meaning of `last_seen_accession` and the
   identity that `entity_identity` reads from it do not change. Its `CASE` compared DEI with the
   stored year; it becomes `last_seen_accession = EXCLUDED.last_seen_accession`, because
   `dei_latest` already spans the whole store. The file still sets no `company_name` or
   `trading_symbol`.
4. **The labels exist first.** `bootstrap_db.py` runs `registries` before `layer0-populate`. That
   order is kept.
5. **No consumer's code changes.** `entity_extractor.py`, `plan_generator.py`, `long_context_arms.py`
   and `src/api/v1` read the new value as they read the old one, and the column keeps its name, so
   no API or MCP field is renamed.

**In scope:** requirements 1-5. **Out of scope:**

- A period parser, a "latest quarter" anchor, or any change to how the extractor treats "last
  year" or "the most recent quarter". See "What phase 3b and phase 5 must revisit" in Notes.
- `last_seen_accession`, which today can name a 10-Q or an older filing. See "Decision for the
  reviewer" in Notes.
- `dimension_catalog.last_seen_fy`, a different column of the same name, per dimension member.
- A re-measurement of accuracy.

## Acceptance criteria

All integer comparisons are exact.

- **C1**: on the committed rows, AAPL 2025, NVDA 2026, CRM 2026, TSLA 2025, HD 2025, TSM 2025 and
  MSFT 2026. Property: the value equals the greatest annual label computed in the test, and equals
  `assign_fiscal_labels`' label of the latest period for any DEI run. Oracle: each company's own
  name for its latest annual report; requirement 1; fiscal-label-alignment requirement 2.
- **C2**: every form in `ANNUAL_FORMS` counts; `10-Q`, `10-Q/A`, `10-KT`, `10-KT/A`, `8-K`, `20-FR`
  and `NT 10-K` do not. Property: adding non-annual filings at any label changes nothing. Oracle:
  `fiscal_labels.ANNUAL_FORMS`; requirement 1.
- **C3**: a stored 2027 falls to 2026, a stored 2025 rises to 2026, a stored NULL fills. Property:
  the result does not depend on the stored value, and a second run changes nothing. Oracle:
  NVDA's and CRM's live values; requirement 2.
- **C4**: a filer with no annual filing gets NULL and keeps its row. Property: this holds for any
  mix of other forms and any stored value. Oracle: requirement 1.
- **C5**: only `last_seen_fy` changes, `filing_registry` does not, and each filer reads only its
  own filings. Property: another filer's filings change nothing in this row. Oracle: requirement
  3, by hand.
- **C6**: the file is two statements, INSERT then UPDATE, with no semicolon in a comment; the
  INSERT leaves `last_seen_fy` alone and has no `GREATEST`; the other columns keep their source;
  the UPDATE reads `filing_registry` and no DEI or fact table; `registries` precedes
  `layer0-populate`. Property: removing the comments leaves the same statements. Oracle:
  requirements 2-4, read from source.
- **C7**: with the extractor's and the arm's code unchanged, "most recent fiscal year" resolves to
  AAPL 2025, NVDA 2026 and CRM 2026, `filing_targets` with no period names the same years, and a
  filer with no annual filing stays ambiguous or gets a reason. Property: a later 10-Q never moves
  the anchor. Oracle: as C1; requirement 5.

## Interface

No Python signature changes and no stub. The interface is one statement in
`scripts/ddl/populate/entity_master.sql`, which the tests read from the file and run as written,
so it must stay plain SQL that both Postgres and SQLite accept:

```sql
UPDATE entity_master AS em
   SET last_seen_fy = (SELECT MAX(fr.fiscal_year)
                         FROM filing_registry AS fr
                        WHERE fr.cik = em.cik
                          AND fr.form_type IN ('10-K', '10-K/A', '20-F', '20-F/A', '40-F', '40-F/A'))
```

No `= ANY(ARRAY[...])`, no `UPDATE ... FROM` (it would leave a filer with no annual filing
untouched) and no `::` cast.

## Notes

**The check is SQLite, on purpose.** Spec tests have no Postgres, and C1-C5 and C7 would otherwise
only read source text, which a wrong statement can pass. The UPDATE above is portable, so
`conftest.py` runs the file's own statement on an in-memory SQLite database over committed rows.
SQLite stands in at the database boundary, and it is the best check available here. The INSERT is
Postgres-only over the Arelle tables, so C6 reads its source; the operator's SQL below is the real
check. The statement above has not been run on Postgres: it was not tried on `xbrl_db`, where this
change may only SELECT. The same subquery ran there as a SELECT (the 12 of 33 above). In a scratch
copy of the file on SQLite, this statement passed all 47 tests, and seven wrong ones each failed
some: `LIKE '10-K%'`, no form filter, no 20-F, keeping the stored value on NULL, a floor at the
stored value, no `cik` match, and `fiscal_period = 'FY'` in place of the forms.

**Oracles and data.** `FILERS` and `REGISTRY` in `conftest.py` were read from live `xbrl_db`,
read-only, on 2026-10-30. They are public SEC filing metadata. Expected years are the companies'
own names for their annual reports; no value comes from running this change's code. The other
filers are synthetic. No held-out filer appears in the tests or fixtures.

**Consumers surveyed.** `last_seen_fy` has these readers, by grep of the repo; nothing else reads
it, and the other `entity_master` readers select other columns.

| Reader | Use | Relies on the 10-Q-inclusive meaning? |
|---|---|---|
| `entity_extractor.py` `_resolve_one_period` | "most recent" / "latest" / "current" / "this year" → the value; "last year" / "prior year" / "year ago" → value − 1 | **Yes, for a quarter named with a relative word, and in effect for "last year".** See below |
| `plan_generator.py` `_format_resolved_entities` | Printed to the planner as `last_seen_fy=` beside each company | No. The active prompts never mention the name; their rule is a hard-coded "most recent → 2026" |
| `long_context_arms.py` `filing_targets` | The year of a question with no period | No: `_RESOLVE_SQL` filters `fiscal_period = 'FY'`, so the old value found no filing for 11 filers and the wrong year for CRM. The arm is fixed by this |
| `src/retriever/html10k/scopes.py` `search_scopes` (added after this spec was first written) | The fiscal-year filter of a filer's search when the question names no period | No: `html10k_text` holds 10-Ks only, so the old value filtered the 11 filers to a year with no chunks (a refusal) and CRM to last year's 10-K. The `html_rag` arm is fixed by this |
| `src/api/v1` `_company_model`, `/api/v1/companies/{company}`, and every response embedding `ResolvedCompanyModel`; MCP `resolve_company` | Passed through | No code does, but an external reader sees the meaning change. The field has no documented meaning today |
| `ui/src/lib/types.ts` | A type, never rendered | No |
| `mv_entity_registry.latest_10k_fy` | An alias, read by nothing in `src/` or `scripts/` | No. The new value is what its name always said. Needs a refresh |
| `entity_identity.py` | Reads `last_seen_accession`, not this column | No: requirement 3 keeps that column |

**What the defect does, traced read-only.** The real `EntityExtractor.resolve_periods` and
`XbrlFactstoreLookup.lookup` were run for Revenue on all 33 filers with the stored value and with
the corrected one, with no LLM call. The router and planner were not run, so this is what a
one-company fast-path lookup gets, the path these phrases take. Figures are in USD.

| Phrase | The 21 unchanged filers | 11 filers with a 10-Q year ahead (10 + NVDA) | CRM |
|---|---|---|---|
| "most recent fiscal year", "latest" | Same | **NOT_FOUND → the latest annual figure.** The defect | **Fixed: 37,895M from last year's filing, cited and stale → 41,525M.** Silent-wrong before |
| "last year", "prior year" | Same | **FY2025 → FY2024** (NVDA FY2026 → FY2025). Both OK; see below | FY2024 → FY2025 |
| "latest second quarter" and other "latest Qn" | Same | **OK → NOT_FOUND**: 10 filers for Q1, 11 for Q2, 2 for Q3. A regression | NOT_FOUND both ways, no 10-Qs |
| "most recent quarter" | Same: the annual figure | **NOT_FOUND → the annual figure.** See below | Annual figure both ways, one year newer |

In the "most recent" and "most recent quarter" rows one unchanged filer, TSM, gets NOT_FOUND, as it
has no undimensioned Revenue row at its latest year. That is unrelated to this change.

- **Harmless, untouched:** a question naming a year. The extractor reads `last_seen_fy` only on a
  relative phrase. A question with no period mention at all reaches XbrlRag's plan only as the
  printed `last_seen_fy=`, and the fast path needs a period; only the long-context arms read it.
- **Narrative.** A narrative step filters on the resolved year. For the 11 filers the old value
  selected 10-Q chunks only (AAPL 2026: 219 chunks, NVDA 2027: 361, all 10-Q), so "latest risk
  factors" answered from a 10-Q and now answers from the 10-K. Neither is an error, but it is a
  visible change: the 10-Q was the newer document, and the 10-K is the fuller one.
- **"last year" is a real trade.** The old value gave the 11 filers the latest complete year by
  accident, while every other filer already got the year before it. After this change all 33 use
  one rule, label − 1. Where "last year" means the latest fiscal year, as the assumption in
  `configs/eval/hx_template.yaml` has it, the extractor is wrong for all 33 and stays so until
  phase 5. Both answers are OK, cited and confident, so a reader cannot see the difference.
- **"most recent quarter" gets worse before it gets better.** `_detect_period` finds no ordinal, so
  the mention stays `FY`. The 21 unchanged filers already answer it with an annual figure; the 11
  now do too, instead of NOT_FOUND. That is what the corrected value implies, and this change must
  not touch it.
- **The eval suites.** Of 9,890 stored router responses, none has a "most recent", "latest",
  "current" or "this year" mention and 438 have "prior year", all from questions whose text says
  "prior year" (65 distinct texts, of the `sql_derived` form "versus the prior year").
  `configs/eval/v2/phrasebook.yaml` forbids relative wording, so its templates do not reach this
  path; the held-out sets were not read. In `sql_derived_v1.json`, 56
  cases say "prior year", 34 on changed filers. Their second mention resolves to the value − 1, not
  to the year before the one asked, and joins `FISCAL_YEARS_IN_SCOPE`. On 16 of the 34 the fix puts
  the right year there, where the old value did not; on the other 18 it was not there before and
  is not now. For a filer-year where the mention lands above the asked year, as AAPL fiscal 2024
  did at 2025, `filing_targets` would read a later filing; after the fix it does not. The effect
  on graded results was not measured, as that needs paid runs. No suite or key reads the column.

**What phase 3b and phase 5 must revisit.**

- **"latest Qn" needs its own anchor.** After this change it asks for the latest annual year's
  quarter. Today that is NOT_FOUND. Once 3b ingests the 10-Qs of earlier years it becomes OK,
  confident and a year stale: a silent-wrong case. 3b must add a latest-filed-quarter anchor before
  or with those 10-Qs. 3b's quarter keys use explicit periods, so its exit criteria will not show
  this.
- **"the most recent quarter"** needs a meaning in `_detect_period`, so it stops falling to `FY`.
- **"last year"** needs one definition, in the 6.1 parser. The tests here do not lock "last year"
  or a quarter phrase, so that work does not have to re-lock this spec.
- **The planner's hard-coded "most recent → 2026"** is in the plan generator prompts and disagrees
  with the corrected column on the 24 of 33 filers whose latest label is not 2026. It belongs to 6.1
  or 6.2.

**Decision for the reviewer: `last_seen_accession` stays as it is.** It can name a 10-Q (MSFT and
MU, where a 10-Q and a 10-K tie on DEI and the tie is arbitrary, and the 11 filers above) or an
older filing (CRM). The row therefore pairs an annual year with a filing of another period. It is
kept because `entity_identity` reads name and symbol from it, and moving it would be a behaviour
change this data correction does not need. The header comment must say so. If it should follow the
annual filing instead, that is a separate change, checked with `entity_identity --dry-run`.

**The single-accession recipe goes.** The header's "to process a single accession" note relied on
the `CASE`. No code uses it. With `EXCLUDED` always winning, it would set the accession to the
filtered one, so the implementer deletes the note.

**Rollout, for the operator; nothing here is done by the spec.** No stop is needed: the API reads
`entity_master` per query. Run the steps on `xbrl_db` first, then with
`$env:XBRL_DB_NAME = 'xbrl_heldout'` on `xbrl_heldout`. Agents never connect to `xbrl_heldout`.

1. On `xbrl_heldout` only: its `filing_registry` must already carry the fiscal-label-alignment
   labels. If its registries have not run since that merged, run the `registries` step first.
2. `conda run -n py312 python scripts/dbq.py "SELECT current_database()"` to see the right database.
3. `conda run -n py312 python scripts/dbq.py -f scripts/ddl/populate/entity_master.sql --commit`
4. `conda run -n py312 python -m src.ingest.sec_edgar.ingest.entity_identity --dry-run`, which should
   report 0 changes, since the accession rule did not change. A tie on DEI can move a row, so
   read any change before running it for real.
5. `conda run -n py312 python scripts/dbq.py "REFRESH MATERIALIZED VIEW mv_entity_registry" --commit`
   (a full `rebuild_mvs.py` is not needed).
6. If `SemanticQueryCache` is enabled, restart the API, so "latest" answers cached under the old
   value go.
7. Run the check below. On `xbrl_db`, expect no rows from the first query, 33 and 33 from the second,
   and AAPL 2025, CRM 2026, NVDA 2026 from the third. On `xbrl_heldout`, share only the counts.

```sql
SELECT em.trading_symbol, em.last_seen_fy, x.label
  FROM entity_master em
  LEFT JOIN (SELECT cik, MAX(fiscal_year) AS label FROM filing_registry
              WHERE form_type IN ('10-K','10-K/A','20-F','20-F/A','40-F','40-F/A')
              GROUP BY cik) x ON x.cik = em.cik
 WHERE em.last_seen_fy IS DISTINCT FROM x.label;  -- expect no rows
SELECT count(*), count(last_seen_fy) FROM entity_master;  -- any gap is a filer with no annual filing
SELECT trading_symbol, last_seen_fy FROM entity_master
 WHERE trading_symbol IN ('AAPL','CRM','NVDA') ORDER BY 1;
```

A NULL on `xbrl_heldout` means a filer with 10-Qs and no annual filing. That is correct under
requirement 1, but look before a run: the relative phrases then come back ambiguous.

No suite rebuild, re-chunk or Qdrant reload is needed: chunks, keys and suites do not read this
column. Apply it before the phase 4 baseline, so that every arm sees one value.
The frozen v2 dev and validation suites (2026-11-02) name a year in every question and use no
relative wording, so their results do not move; the hand-written sets (H-X, set B) and real
users are where it matters. Land it before phase 3b ingests earlier 10-Qs (see above). Optionally, a
`sql_derived` slice over the 34 prior-year cases, with a same-day control, shows the effect in
the note above (about $0.30-0.50).

**Implementer, also do:** rewrite the header of `entity_master.sql`, whose lines on `last_seen_fy` /
`last_seen_accession` "reflect the latest filing" go stale, and say what each now means. Fix the
docstring in `entity_extractor.py` only if a comment is wrong; change no code there. Run all of
`test/spec`: `long-context-arms` C4 reads HD, whose value does not change.

**A stale note elsewhere.** The "`entity_master.last_seen_fy` is not corrected" note in
[long-context-arms](long-context-arms.md) goes out of date. A spec branch may edit only its own
spec, so it needs a spec-only PR of its own if it is to be fixed.
