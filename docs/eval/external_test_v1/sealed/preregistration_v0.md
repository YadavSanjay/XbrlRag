# XbrlRag blog v1 — pre-registration v0: the prospective set

**Status:** sealed by the merge of this file into `main`. Two independent timestamps prove
the seal time:

- the time GitHub records for that merge;
- an OpenTimestamps proof over the SHA-256 hashes listed in [`HASHES.txt`](HASHES.txt).

**Scope of this version:** the **prospective set** only. Pre-registration **v1**, made at
tag `blog-v1-freeze`, will add the final metric definitions, system configurations, the
**hand-written set** and the **new-filer set**. v1 may add detail to this document but may
not change anything in it. Any change is recorded as a numbered amendment with its reason.

## 1. Purpose

XbrlRag's 482-case SQL-derived suite was also its development set. The prospective set measures the system
on annual reports that **did not exist when this document was sealed**. Neither the system's
code, its developers nor any language model's training data can have seen them.

## 2. The rule that defines the prospective set

The prospective set consists of every filing that meets all of these conditions:

1. **Filer:** one of the five listed in §3.
2. **Form:** an original **10-K** (not a 10-K/A) for the filer's **fiscal year 2026**, i.e.
   the fiscal year that ends in 2026.
3. **Filed after the seal:** its EDGAR filing date is later than the seal time. If a
   filing is accepted before the seal time, it is excluded and reported.
4. **Filed by the cutoff:** its EDGAR filing date is on or before **2026-11-30**.
5. **Format:** it is filed in inline XBRL.

A listed filer with no qualifying filing by the cutoff contributes nothing, and the report
says so.

## 3. Filers

| Ticker | CIK | Fiscal year-end | In the development corpus? | Usual 10-K filing window (2022–2025) |
|---|---|---|---|---|
| AAPL | 320193 | late September | yes (FY2022–25 and FY2026 10-Qs) | Oct 28 – Nov 3 |
| QCOM | 804328 | late September | no | Nov 1 – Nov 6 |
| EMR | 32604 | September 30 | no | Nov 10 – Nov 14 |
| DIS | 1744489 | about October 3 | no | Nov 13 – Nov 29 |
| SBUX | 829224 | late September | no | Nov 14 – Nov 20 |

**How these were chosen:**

- Every candidate was a large US GAAP 10-K filer whose fiscal year ends from August to
  October, so its FY2026 10-K falls inside the window.
- Candidates that had already filed by the check time were removed: MU (filed 2026-10-09),
  ACN (2026-10-16) and COST (2026-10-07).
- Candidates whose filing usually falls after the cutoff were removed: AVGO and DE, which
  file in mid-December.
- V (Visa) was removed for scope: Financials are out of scope for both this set and the
  new-filer set.
- **QCOM, EMR, DIS and SBUX are excluded from the new-filer set's draw pool,** so the two
  sets do not overlap.

**The EDGAR check:** run at **2026-10-21T01:12:38Z** against the EDGAR submissions API
(filing indexes only), by [`edgar_check.py`](edgar_check.py). It found no FY2026 10-K for
any listed filer. The raw output is in
[`edgar_check_2026-10-21.txt`](edgar_check_2026-10-21.txt). The check is re-run just before
the merge; if any listed filer has filed by then, it is removed under rule 3.

## 4. Data handling

- **Where the prospective filings go:** only into the held-out environment. That is a Postgres database
  `xbrl_heldout`, cloned from the development database at tag `blog-v1-freeze`, plus a
  separate Qdrant instance.
- **Never into the development database:** none of them, AAPL included, enters `xbrl_db`
  before the results are published.
- **Context filings:** the FY2025 10-K of each new filer (QCOM, EMR, DIS, SBUX) is loaded
  as context, so that growth questions can be built. Questions are asked about FY2026 only.
- **Ingest:** the full ingest flow at tag `blog-v1-freeze`: download, Arelle ingest,
  Layer-0, materialized views, member-alias detection, the publish gate, canonical HTML and
  chunking, then the vector load.
- **What the operators look at while ingesting:** exit codes, stage-level counts and
  publish-gate verdicts only. Not facts, values, chunks or traces.

## 5. Suite

- **Template spec:** [`prospective_spec.yaml`](prospective_spec.yaml), a copy of
  `configs/eval/sql_derived_templates.yaml` at this commit. Only the tickers, company names
  and fiscal years differ: the templates, phrasings, SQL and quotas are unchanged.
- **Build:** after the cutoff, by the suite builder at tag `blog-v1-freeze`, tier
  `standard`, run against the held-out environment.
- **Blind:** the suite file is not opened before the runs.
- **Hashed first:** its SHA-256 hash is published with an OpenTimestamps proof **before any
  system is run on it**.
- **Code is fixed:** no code may change between the build and the runs.

## 6. Systems and measurements (outline; final configurations are fixed in v1)

- **Systems:**
  - XbrlRag, the full pipeline, run three times;
  - a naive retrieval-augmented baseline filtered to the question's filer and year, run
    three times;
  - a closed-book model with no documents, run once.
- **Metrics:**
  - **Accuracy.**
  - **Refusals and hedged answers.**
  - **Confident-wrong:** a wrong number given without refusing or hedging, applied the
    same way to every system.
  - **Grounding precision and recall:** XbrlRag only.
- **Reporting:**
  - case counts with Wilson 95% intervals;
  - the new filers and AAPL reported separately as well as together;
  - cross-filer ranking cases reported separately.
- **Settings:** same model snapshots and same day for every system, at tag
  `blog-v1-freeze`.

## 7. Exclusions and failure rule

- **Ingest failures:** a filing whose ingest fails, or which the publish gate holds back, is
  excluded and listed. No fixes specific to one filer are made.
- **Generic fixes:** a generic code fix found this way is allowed only if it is disclosed.
  The prospective results are then labelled with it.
- **Minimum size:** if fewer than **3** filings qualify, the prospective set is reported as
  too small to score.

## 8. Commitment

The prospective results will be published whatever they show, alongside the main results and with
this document linked.
