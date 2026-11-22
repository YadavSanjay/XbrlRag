# last-fiscal-year-phrase — "last fiscal year" resolves like "prior fiscal year"

`EntityExtractor._resolve_one_period` (`src/retriever/xbrl/entity_extractor.py`) anchors a
relative period mention on the filer's `last_seen_fy` (the latest fiscal year with an annual
filing in the store, [last-seen-fy](last-seen-fy.md)). `RELATIVE_PERIOD_PHRASES` ("most recent",
"latest", "current", "this year") resolve to `last_seen_fy`; `PRIOR_PERIOD_PHRASES` ("prior year",
"last year", "previous year", "prior fiscal year", "year ago", "year-ago", "preceding year")
resolve to `last_seen_fy - 1`. Matching is by substring of the lowercased, stripped mention.

The list has "prior fiscal year" but not its siblings with the word "fiscal" inside them:
"last fiscal year", "previous fiscal year" and "preceding fiscal year" contain none of the
listed phrases ("last year" is not a substring of "last fiscal year"). Such a mention has no
digit, so it falls through to `could_not_resolve_year` and is ambiguous;
`BranchDispatcher` then degrades the whole query to the narrative branch, which has no fact rows.
Because the mention carries year evidence ("last", "previous", "preceding"),
[yearless-period-mention](yearless-period-mention.md) rightly does not drop it, so the
degradation stands even when another period of the query resolved. That spec named this case as
out of scope ("resolving 'last fiscal year' ... keeps degrading here").

This change is a bug fix in the existing phrase table: the three phrases mean what "prior fiscal
year" means today. It does not define "last year" anew (the v2 plan's section 6.1 parser owns
that, see last-seen-fy Notes), and it adds no interface.

## Requirement

1. **The three phrases are prior-year phrases.** A period mention whose lowercased, stripped
   text contains `last fiscal year`, `previous fiscal year` or `preceding fiscal year` resolves
   exactly as one containing `prior fiscal year` does today: with an anchored company (the first
   non-ambiguous resolved company, whose `last_seen_fy` is not None) it resolves to
   `fiscal_year = last_seen_fy - 1`, not ambiguous, through the same `_resolve_year` path (the
   detected period, `FY` by default, and the calendar check).
2. **A quarter inside the mention is kept.** "Q3 of the last fiscal year" resolves to
   `fiscal_year = last_seen_fy - 1`, `fiscal_period = "Q3"`, as "Q3 of the prior fiscal year"
   does today. The phrase wins over the digit path: the "3" of "Q3" names no year.
3. **No anchor, no guess.** With no resolved company, or one whose `last_seen_fy` is None, the
   mention stays ambiguous with no `fiscal_year`, as every prior-year phrase does today. Each of
   the three phrases carries year evidence by `period_evidence.has_year_evidence`, so this
   failure still degrades the query and is never silently dropped (yearless-period-mention,
   Requirement 2, unchanged).
4. **Only these whole phrases.** The bare stem `last fiscal` is **not** a prior-year phrase:
   "last fiscal quarter" must never resolve to a full fiscal year. Phrases that already resolve
   keep their result: "most recent fiscal year" and "latest fiscal year" stay at `last_seen_fy`.
5. **Order of the checks is unchanged.** `RELATIVE_PERIOD_PHRASES` is still checked first, then
   `PRIOR_PERIOD_PHRASES`, then digits. The implementer adds the three phrases to
   `PRIOR_PERIOD_PHRASES` (or an equivalent table entry); no new function.
6. **Unlocked, implementer:** update the period bullet of `src/retriever/xbrl/CLAUDE.md`, which
   names "last fiscal year" as an example of a mention that still degrades. The "Out of scope"
   line in yearless-period-mention's Notes is history and is not changed.

## Acceptance criteria

All year comparisons are exact integers; there are no floats.

- **C1**: for an anchored filer with `last_seen_fy = 2025`, each of "last fiscal year",
  "the last fiscal year", "previous fiscal year", "preceding fiscal year", "LAST FISCAL YEAR"
  and "Apple's last fiscal year" resolves to `(2024, "FY", ambiguous False)`. Property: for any
  anchor year in 2018-2035, any of the three phrases in any letter case and with any
  evidence-neutral words before or after it, the result is `(anchor - 1, "FY", False)` and
  equals the result for "prior fiscal year" with the same anchor. Oracle: Requirement 1, the
  existing meaning of "prior fiscal year" stated by hand.
- **C2**: with `last_seen_fy = 2027`, "Q3 of the last fiscal year" resolves to `(2026, "Q3")`
  and "fourth quarter of the previous fiscal year" to `(2026, "Q4")` (2027, so the expected
  year differs from the fixed 2024 default described in Notes). Property: for any anchor
  and any quarter Q1-Q4 (as "Qn" or its word form), "<quarter> of the <phrase>" resolves to
  `(anchor - 1, "Qn")`. Oracle: Requirement 2, by hand.
- **C3**: each phrase with no company, or with a company whose `last_seen_fy` is None, is
  ambiguous with `fiscal_year` None, and `has_year_evidence` is True for it; the same mention
  with an anchor resolves to `anchor - 1`. Property (metamorphic): for any anchor and phrase,
  removing the anchor turns the resolved `anchor - 1` into an ambiguous period and never into a
  different year. Oracle: Requirement 3, by hand.
- **C4**: with `last_seen_fy = 2025`, "last fiscal year" resolves to `(2024, "FY")` while
  "last fiscal quarter" yields no unambiguous `FY` period, and "most recent fiscal year" and
  "latest fiscal year" still resolve to 2025. Property (metamorphic): for any anchor, replacing
  "year" by "quarter" in a resolving phrase never yields the full year `anchor - 1`. Oracle:
  Requirement 4, by hand.

## Interface

No new or changed signature and no stub (bug-fix mode). The tests call the existing
`src.retriever.xbrl.entity_extractor.EntityExtractor.resolve_periods(mentions, companies)` and
`src.retriever.xbrl.period_evidence.has_year_evidence(mention)`.

## Notes

- **Oracle.** The meaning of the existing "prior fiscal year" entry, applied by hand to
  synthetic mention text and synthetic companies. No data file, database, LLM or clock is read.
  The database is faked at `psycopg2.connect` (the I/O boundary); the fake `fiscal_calendar`
  read returns no row, so dates stay None, which the tests do not check.
- **Evidence (read-only, local `xbrl_db`, `audit.query_audit` + `audit.query_event`,
  2026-11-11, $0).** 1,045 stored queries (66 distinct texts) match
  `(last|previous|prior|preceding|past) (fiscal )?year|last fiscal`. Every one uses "prior
  year" relative to a named year ("fiscal 2024 versus the prior year"); the IntentRouter
  rewrote each into explicit years (0 ambiguous periods, all `PLAN_GENERATOR`). **No stored
  query contains "last fiscal year", "previous fiscal year", "preceding fiscal year", "last
  year" or "previous year".** The gap is latent: the eval paraphrase checker lists "last fiscal
  year" as a drift term (`src/eval/suites/paraphrases.py`), so generated suites avoid it, and it
  reaches the extractor only from user-typed questions where the router passes the phrase
  through.
- **Expected movement.** None on stored eval runs (no case uses the phrases). On user questions
  that use them and whose router passes the phrase through, a narrative degradation becomes a
  planned answer for `last_seen_fy - 1`.
- **Decision for the reviewer: the meaning of "last fiscal year".** This spec makes it equal to
  "prior fiscal year" and "last year" (`last_seen_fy - 1`), the table's existing convention, in
  which `last_seen_fy` is "this/current year". In analyst usage "last fiscal year" often means
  the most recently *completed* fiscal year, which is `last_seen_fy` itself (the latest 10-K).
  If that reading is wanted, the phrase belongs in `RELATIVE_PERIOD_PHRASES` instead and C1-C4
  change to `anchor`; the same question then applies to "last year", which the 6.1 parser is to
  define once. Locked here: the table's convention.
- **Found while writing this spec, out of scope: a yearless digit mention resolves to 2024.**
  `src/retriever/extract_year_util.py::extract_years_in_range` ends with
  `if not years: years.add(2024)  # Latest year default`. A mention with a digit but no year
  ("Q3", "Q3 of the last fiscal year" today, "H1", "1st quarter") therefore resolves, not
  ambiguous, to fiscal 2024 for every filer, whatever its `last_seen_fy`. Today "Q3 of the last
  fiscal year" happens to give 2024 for a filer anchored on 2025 (by the default, not by the
  phrase); C2 uses anchor 2027 so it cannot pass by that accident. yearless-period-mention's
  premise that "'Q3' with no year" fails and degrades does not hold on this path; its spec tests
  use synthetic periods, so they are unaffected. This is a silent-wrong risk worth its own
  change (locked route).
- **Seen but out of scope.** A "prior year" stated relative to a named year ("fiscal 2024 versus
  the prior year") would resolve to `last_seen_fy - 1`, not 2023, if the router ever passed it
  through verbatim; today it rewrites it. That is the 6.1 parser's relative-to-a-named-year rule.
- **Validation plan.** Implementer: `pytest test/spec/last-fiscal-year-phrase`,
  `pytest test/spec -n auto` (yearless-period-mention and last-seen-fy must stay green), unit
  tests. No paid run: no stored case moves.
