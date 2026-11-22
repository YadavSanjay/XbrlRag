# XBRL Flow — 3. Data Download (SEC EDGAR)

How to fetch 10-K, 10-Q and 20-F filings from SEC EDGAR onto local disk. The downloader
(built on `edgartools`) saves each filing's inline XBRL documents, instance, schema and
linkbases, and writes a `manifest.yaml` that every later stage reads. Read this before adding
filers or years to the corpus; loading them is [guide 4](./04-ingest-and-populate.md).

- Module: [`src/ingest/sec_edgar/downloader/__main__.py`](../../../src/ingest/sec_edgar/downloader/__main__.py)
  (class [`SECFilingDownloader`](../../../src/ingest/sec_edgar/downloader/sec_filing_downloader.py))
- Config: [`configs/pipelines/sec_edgar_downloads.yaml`](../../../configs/pipelines/sec_edgar_downloads.yaml)

## 3.1 Run it

```powershell
# Batch: every entry in the config's downloads: list
conda run -n py312 python -m src.ingest.sec_edgar.downloader
conda run -n py312 python -m src.ingest.sec_edgar.downloader --config configs/pipelines/sec_edgar_downloads.yaml

# One filing
conda run -n py312 python -m src.ingest.sec_edgar.downloader --ticker XOM --filing-type 10-K --fiscal-year 2024
conda run -n py312 python -m src.ingest.sec_edgar.downloader --ticker AMZN --filing-type 10-Q --fiscal-year 2024 --quarter 2
conda run -n py312 python -m src.ingest.sec_edgar.downloader --ticker TSM --filing-type 20-F --fiscal-year 2024
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--config` | `configs/pipelines/sec_edgar_downloads.yaml` | batch mode: the YAML to read |
| `--ticker` | — | single-filing mode; then `--filing-type` and `--fiscal-year` are required |
| `--filing-type` | — | `10-K`, `10-Q` or `20-F` |
| `--fiscal-year` | — | the calendar year the filer's fiscal year ends in |
| `--quarter` | — | `1`, `2` or `3`: the **fiscal** quarter, 10-Q only ([§3.4](#34-the-config--sec_edgar_downloadsyaml)) |
| `--output-root` | `SEC_EDGAR_ROOT`, else the local-dev default | single-filing mode only. Batch mode writes to `SEC_EDGAR_ROOT`, else the YAML `output_root:` (blank), else the local-dev default |
| `--identity` | `SEC_EDGAR_IDENTITY` | EDGAR User-Agent `Name email@domain`. Precedence: `--identity` > `SEC_EDGAR_IDENTITY` > YAML `identity:` (blank). With none set the downloader fails at startup |

> ⚠️ **SEC requires the identity**, and EDGAR rate-limits and blocks per identity. Put your
> own contact in `SEC_EDGAR_IDENTITY`.

## 3.2 Full vs incremental

The downloader is **declarative**: it downloads exactly the `downloads:` list. There is no
`--since` or date-range flag and no dedup at download time.

- **Full download:** run the batch. Every entry is fetched and written (idempotent on disk).
- **Incremental:** append the new rows (a new year, a new quarter) to `downloads:` and re-run,
  or use single-filing mode. The later Arelle ingest skips every filing already in Postgres,
  so re-fetching old rows is harmless.
- **Originals and amendments:** for each request the downloader queries the base form and its
  amendment (`10-K` and `10-K/A`), selects by the index's report date, and keeps the latest
  original plus the latest amendment filed after it. The original goes to `10-K/`, the
  amendment to `10-K_A/` with `is_amendment: true`. Which one counts is decided later, from
  the ingested facts: an amendment becomes canonical only if it carries the primary statements,
  so a Part III-only 10-K/A never replaces the 10-K
  ([spec](../../specs/annual-amendment-handling.md)).
- **Every inline document is kept.** A filing can split its inline XBRL across several HTML
  documents, and EDGAR may type the later ones as exhibits, so every `.htm`/`.html` attachment
  is read and kept if it holds inline facts. An attachment that cannot be read fails the
  download (no manifest is written); EDGAR viewer pages (`R<n>.htm`) are exempt
  ([spec](../../specs/ingest-multidoc-ixbrl.md)).
- **Retries:** transient errors retry with bounded exponential backoff (3 attempts, base 2 s);
  `LookupError`, `ValueError`, `FileNotFoundError` and `KeyError` are permanent. The read
  timeout is `XBRLRAG_EDGAR_READ_TIMEOUT_S` (default 120 s).

## 3.3 Output layout

```
<root>/<TICKER>/<FY>/<FORM_DIR>[/Q<n>]/      FORM_DIR: 10-K, 10-K_A, 20-F, 20-F_A, 10-Q, 10-Q_A
  <primary>.htm             the inline XBRL document (the canonical source later)
  <other>.htm               any further inline document (most filings have none)
  *_htm.xml  *.xsd          instance + schema
  *_lab.xml *_pre.xml *_def.xml *_cal.xml   linkbases
  manifest.yaml             ticker, form, is_amendment, fiscal_year, quarter, period dates,
                            fye, accession_no, cik, filing_date, primary_document,
                            ixbrl_documents, files
```

`manifest.yaml` is the contract every later stage reads; don't delete it. `ixbrl_documents`
lists the inline documents, primary first (a manifest without the key counts as the primary
alone). With more than one, Arelle loads the extracted instance and `canonical.htm` is the
merge of the documents in filing order.

## 3.4 The config — `sec_edgar_downloads.yaml`

```yaml
identity:        # blank on purpose: set SEC_EDGAR_IDENTITY
output_root:     # blank on purpose: set SEC_EDGAR_ROOT
downloads:
  - {ticker: "NVDA", filing_type: "10-K", fiscal_year: 2025}
  - {ticker: "TSM",  filing_type: "20-F", fiscal_year: 2025}
  - {ticker: "NVDA", filing_type: "10-Q", fiscal_year: 2027, quarter: 1}   # quarter ended April 2026
```

The file is the authoritative list of the corpus. Each entry needs `ticker`, `filing_type` and
`fiscal_year`; a 10-Q also needs `quarter`.

**10-Q years and quarters are fiscal.** `fiscal_year` is the calendar year the fiscal year ends
in (as for a 10-K) and `quarter` is the fiscal quarter 1–3; the fourth quarter is the 10-K. The
downloader places each 10-Q on the filer's own fiscal calendar, whose year ends are the report
dates of its 10-K filings, so a quarter ending in any month can be requested (NVDA's quarter
ended April 2026 is `(2027, 1)`; Apple's ended December 2025 is `(2026, 1)`). With no 10-K near
the quarter it falls back to the calendar year and quarter of the period end
([spec](../../specs/tenq-fiscal-labels.md)). Folders from older downloads named by calendar
quarter keep their names; the registries step relabels every 10-Q from the filer's calendar
regardless of folder or manifest.

> ⚠️ **20-F / IFRS:** the IFRS `concept_mapping` rows (`source='ifrs'`) must be loaded for the
> fact store to resolve `ifrs-full:*` concepts. They ship in the generated concept-mapping CSV
> ([guide 4 §4.6](./04-ingest-and-populate.md#46-concept-mapping-the-semantic-layer)).

Next: [4. Ingest & populate](./04-ingest-and-populate.md).
