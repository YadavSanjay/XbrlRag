# H-X filing locator

A local, read-only browser for writing the external set H-X
([guide 12.4](../../docs/guides/xbrl-flow/12-heldout-set-b.md#124-external-set-h-x-v2)). It
finds values in the filings themselves, shows where each one sits, and tells you before you
write a case what `hx.py validate` and `hx.py check-keys` will say about it.

```powershell
conda run -n py312 --no-capture-output python -m tools.hx_locator          # http://127.0.0.1:8765
conda run -n py312 --no-capture-output python -m tools.hx_locator --port 8770
```

Run it from the repository root. Postgres (`xbrl_db`) is optional: without it the tool still
browses the filings, labels years from each filing's cover page, and skips the key checks.

## What it reads, and why it is independent

| Source | Used for |
|---|---|
| The raw iXBRL filings under `SEC_EDGAR_ROOT` (`ixbrl.py`) | Every value it shows: the filer's own tagged fact, with `scale`, `sign` and `format` applied, and the element `id` |
| The canonical HTML under `XBRLRAG_NORMALIZED_ROOT` | Section paths (Item 1A, Item 7 ...) for text search |
| The committed concept mapping (`committed_mapping_rows`, the view's `fact_family` rule) | Grouping raw facts into families and sub-families, exactly as `mv_financial_facts` groups them |
| `xbrl_db`, read-only (`factstore.py`) | The fiscal-year labels (`filing_registry`), the candidates `check-keys` sees, and the SQL tab |

Values never come from the fact store, so a key you take from this tool is the filing's
figure, not ours; `check-keys` then confirms the fact store carries it. The **S1** rule
(docs/design/XbrlRag_Eval_Semantics.md) is applied to the raw filings: *as originally
reported* is the year's own annual report; *latest* is the most recently filed later annual
report that states the same concept, unit, dimensions and period. A later value that differs
only by rounding is marked `precision`, not `restated`.

**Fiscal years** are the fact store's labels, the ones the keys use. Download folders and
cover pages can disagree (HD's year ending January 2024 is its FY2023; CRM's cover page tags
the year ending January 2026 as 2025); the sidebar lists every such filing.

## Tabs

| Tab | For |
|---|---|
| Cross-filer | One family across a group or picked filers for one year: latest values, rank, sum, median, exclusions (`not_filed`, `not_tagged`, `currency`), and a callout when filers tag the family with different concepts. Drafts `screen_top/bottom`, `count`, `sum`, `median`, `peer_relative`. |
| Cross-period | One filer over years: original vs latest per year with both accessions, CAGR. Drafts `trend`, `cagr`, `point_in_time`. |
| Compound | A ranking, then a dependent part (cross-period, cross-filer or text) about the ranked filers. |
| Honesty | Coverage per filer-year (filed? family tagged?) for `decline`; side-by-side readings of a vague metric for `ambiguous`; the true value for a `false_premise` correction. |
| Text | Word or phrase search in a year's annual report by section; quote a paragraph or a selection as a passage with its accession. |
| Explore filing | A filing's family facts (all concepts, members), facts by axis, and a concept/label search that bypasses our mapping. |
| Fact store & SQL | What `check-keys` sees for a filer-year, a check of any amount, and editable SQL over the views. |

Every value links to **in filing** (the local copy, scrolled to the fact and outlined) and
**sec.gov** (the SEC inline viewer). Each draft is validated with `validate_hx` and, for the
numeric kinds, resolved with `resolve_hx_case`: the same code as the command line.

## Privacy and the held-out rules

- It binds to `127.0.0.1` and stores nothing. It writes no log file (it closes the
  repository's per-process log and deletes it at start), has no access log, and prints only
  warnings to its console. Your questions stay in the browser.
- Postgres keeps no record of its queries. The server logs no successful statement here
  (`log_statement = none`, no `pg_stat_statements`), and the session sets
  `log_min_error_statement` and `log_min_messages` to `panic`, so a failing or timed-out
  statement and its error are not logged either. That needs a superuser; without one, a
  "failing SQL is logged by Postgres" pill says so.
- Every response is sent `Cache-Control: no-store` (no copy in the browser's disk cache)
  and `Referrer-Policy: no-referrer` (a sec.gov link does not name the local page).
- What remains is the browser's own history: the pages you open (`/doc/...` names the
  filing and the fact). Use a private window to leave none. Following a **sec.gov** link
  is a request to the SEC, like any visit to EDGAR.
- Every ticker of the held-out pool (`docs/eval/heldout_v2/heldout_v2_pool.json`) is refused
  in every route, and only `xbrl_db` is opened, whatever `XBRL_DB_NAME` says.
- SQL runs in a read-only session with a 30 s statement timeout and at most 500 rows.
- Keep H-X's YAML in your vault, as guide 12 says; the tool only prints a draft to copy.
