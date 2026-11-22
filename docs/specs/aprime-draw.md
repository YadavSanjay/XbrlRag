# aprime-draw — the pool and random draw for the new-filer held-out set A′

External test v1, the new-filer set A′. A′ measures how XbrlRag does on **companies it
has never seen**. Its filers have to be chosen in a way nobody can steer. So the rule is
fixed in pre-registration v1 before the draw, and the randomness comes from a public
source nobody controls: a **NIST Randomness Beacon** pulse at a time stated in v1,
which has to be later than v1's seal.

This change gives the operator two steps:
1. **`pool`**, run before v1. It builds the candidate pool from a saved S&P 100
   constituents list. The pool file is committed and its hash goes into v1.
2. **`draw`**, run after the stated pulse time. It ranks each stratum by a hash of the
   pulse, checks each filer's eligibility from EDGAR filing indexes only, and records the
   drawn filers and alternates. Its output goes into pre-registration v2.

Decisions (2026-10-27):
- **Pool:** the S&P 100 constituents on the seal date, minus the corpus filers, the
  Financials and Real Estate sectors, and the P filers. 20-F filers drop out at the
  eligibility check, because they file no 10-K.
- **Strata:** S (Information Technology and Communication Services) and D (every other
  sector left in the pool).
- **Draw:** 5 filers per stratum, plus 2 ordered alternates per stratum.
- **Eligibility:** an original inline-XBRL 10-K for each of fiscal years 2023, 2024 and
  2025. FY2023 is needed only as YoY context.

## Requirement

### The constituents list and CIKs

1. **`load_constituents(text)`** parses a CSV whose header includes `symbol`, `name` and
   `sector`. Other columns are ignored.
   - It returns one dict `{"symbol", "name", "sector"}` per data row, in file order, with
     each value stripped of surrounding whitespace.
   - It raises `ValueError` for:
     - a missing column;
     - an empty `symbol`;
     - two rows whose symbols are equal after `normalize_symbol`;
     - no data rows.
2. **`normalize_symbol(symbol)`** strips the symbol, upper-cases it, and replaces each
   `.` and `/` with `-`. This is how SEC writes share classes: `brk.b` → `BRK-B`.
3. **`cik_map(company_tickers)`** reads SEC's `company_tickers.json` document: an object
   whose values are `{"cik_str": int, "ticker": str, "title": str}`.
   - It returns `{normalize_symbol(ticker): cik10}`, where `cik10` is the CIK as a
     10-digit zero-padded string.
   - The same normalized ticker with two different CIKs raises `ValueError`. The same
     ticker repeated with the same CIK is accepted.

### The pool

4. **`build_pool(constituents, ciks, corpus_ciks)`** returns `{"candidates": [...],
   "excluded": [...], "counts": {...}}`.
   - **Sectors.** Each constituent's `sector` must be one of the 11 GICS sector names,
     spelled exactly: `Communication Services`, `Consumer Discretionary`, `Consumer
     Staples`, `Energy`, `Financials`, `Health Care`, `Industrials`, `Information
     Technology`, `Materials`, `Real Estate`, `Utilities`. Anything else raises
     `ValueError` naming the symbol.
   - **CIKs.** Each constituent's CIK is `ciks[normalize_symbol(symbol)]`. If any symbol
     is missing from `ciks`, it raises one `ValueError` that names every missing symbol.
     `corpus_ciks` entries are compared as 10-digit zero-padded strings.
   - **Exclusions.** Each constituent is excluded for the **first** reason that applies,
     in this order:
     1. `sector`: its sector is `Financials` or `Real Estate`;
     2. `corpus`: its CIK is in `corpus_ciks`;
     3. `prospective`: its CIK is one of the five P filers in pre-registration v0 §3:
        AAPL `0000320193`, QCOM `0000804328`, EMR `0000032604`, DIS `0001744489`, SBUX
        `0000829224`;
     4. `duplicate_cik`: another constituent that wasn't excluded for reasons 1–3 has
        the same CIK (share classes such as GOOG and GOOGL). The one whose normalized
        symbol sorts first is kept, and the others are excluded.
   - **`candidates`:** one dict per remaining constituent, `{"symbol": <normalized>,
     "name", "sector", "cik": <cik10>, "stratum"}`, sorted by `cik`. `stratum` is `"S"`
     for Information Technology and Communication Services, and `"D"` otherwise.
   - **`excluded`:** `{"symbol": <normalized>, "cik", "reason"}` per excluded
     constituent, sorted by `symbol`.
   - **`counts`:** `{"constituents": n, "S": n_S, "D": n_D, "excluded": n_excluded}`.
   - The result doesn't depend on the order of `constituents`.

### Ranking

5. **`rank_key(output_value, cik)`** is the lowercase SHA-256 hex digest of the ASCII
   string `"xbrlrag-aprime-v1|" + output_value.upper() + "|" + cik10`, where `cik10` is
   `cik` zero-padded to 10 digits. Anyone can recompute it in one line from the public
   pulse and the pool.
6. **`rank(candidates, output_value)`** returns a new list: each candidate dict copied,
   with `"key": rank_key(output_value, cik)` and `"rank"` (1-based) added, sorted by
   `(key, cik)`. The input list and its dicts aren't modified.

### Eligibility (filing indexes only)

7. **`fiscal_year_of(report_date)`** maps a 10-K's period-of-report date (`YYYY-MM-DD`)
   to a fiscal year: the date's year, except that a date from January 1 to January 7
   belongs to the previous year. That's the 52/53-week year whose end slips into the next
   calendar year. A malformed date raises `ValueError`.
8. **`check_eligibility(filings, years=(2023, 2024, 2025))`** takes filing-index records
   `{"form", "filing_date", "report_date", "accession_no", "is_inline_xbrl"}`. It returns
   `{"eligible": bool, "missing_years": [...], "not_inline_years": [...]}`, both lists
   sorted ascending.
   - Only `form == "10-K"` exactly counts. `10-K/A`, `10-KT`, `20-F` and every other form
     are ignored, as are records with an empty or missing `report_date`.
   - A year in `years` is **not_inline** when at least one counted 10-K maps to it and
     none of them has `is_inline_xbrl` true. It is **missing** when no counted 10-K maps
     to it.
   - `eligible` is true exactly when both lists are empty.

### The pulse

9. **`check_pulse(pulse, requested_utc)`** checks a NIST Beacon 2.0 pulse object (the
   `pulse` member of the API's JSON). It returns None, or raises `ValueError` when:
   - `pulse` lacks a string `timeStamp` or `outputValue`;
   - `outputValue` isn't exactly 128 hexadecimal characters (either case);
   - `requested_utc` isn't an ISO 8601 UTC time (`Z` or `+00:00`) on a whole minute
     (seconds and fractions zero);
   - `timeStamp` (e.g. `2026-11-02T15:00:00.000Z`) isn't the same instant as
     `requested_utc`.

### The draw

10. **`draw(pool, pulse, source, per_stratum=5, alternates=2)`** returns
    `{"output_value": <outputValue upper-cased>, "pulse_timestamp": <timeStamp>,
    "strata": {"S": {...}, "D": {...}}}`.
    - A malformed `outputValue` (requirement 9) raises `ValueError`. `draw` doesn't
      check the time; the CLI calls `check_pulse` first.
    - **For each stratum,** the pool's candidates in that stratum are ranked
      (requirement 6) and walked in rank order. For each one, `source.filings(cik)` is
      called once and `check_eligibility` applied. Eligible candidates fill `drawn` up to
      `per_stratum`, then `alternates` up to `alternates`.
    - **The walk stops** as soon as both lists are full. `source.filings` is never called
      for a later candidate.
    - **Each stratum's entry is** `{"drawn": [...], "alternates": [...], "checked":
      [...], "shortfall": n}`:
      - `drawn` and `alternates` entries are `{"symbol", "name", "sector", "cik",
        "rank", "key"}`;
      - `checked` lists every candidate walked, in rank order, as `{"rank", "symbol",
        "cik", "key", "eligible", "missing_years", "not_inline_years"}`;
      - `shortfall` is `per_stratum + alternates - len(drawn) - len(alternates)`. It's 0
        unless the stratum runs out of eligible candidates, which isn't an error.
    - The result doesn't depend on the order of `pool["candidates"]`.

### The I/O seams and the CLI

11. **Seams** (`src/eval/aprime/base.py`):
    - `FilingIndexSource.filings(cik) -> list[dict]`: every filing-index record of a
      filer (requirement 8's keys). The real implementation reads EDGAR's submissions
      API (`data.sec.gov/submissions/CIK##########.json`). It merges `filings.recent`
      with every older page listed in `filings.files`, sends SEC's required identity
      `User-Agent`, stays under 10 requests per second, and caches each document on
      disk.
    - `BeaconSource.pulse(timestamp_utc) -> dict`: the `pulse` object for one minute.
      The real implementation reads
      `https://beacon.nist.gov/beacon/2.0/pulse/time/<epoch milliseconds>`.
12. **The CLI, `scripts/eval/aprime_draw.py`:**
    - `pool --constituents CSV --source TEXT --out POOL.json [--tickers-json PATH]`.
      - It reads the corpus CIKs from `xbrl_db`: the union of `entity_master.cik` and
        `publish_manifest.cik`.
      - `company_tickers.json` comes from SEC, or from `--tickers-json`.
      - It writes the pool plus `{"source": TEXT, "constituents_sha256", "tickers_sha256",
        "corpus_ciks": [...]}`, and prints the file's SHA-256.
    - `draw --pool POOL.json --pulse-time ISO --out DRAW.json [--cache-dir DIR]`.
      - It fetches the pulse and runs `check_pulse`, then `draw` with the EDGAR source.
      - It writes the result plus the full pulse object and the pool file's SHA-256, and
        prints the file's SHA-256 and the drawn symbols.
      - It refuses a pulse time later than the current time.
    - Both write with `src.eval.set_b.core.canonical_bytes`, so the files hash
      deterministically.
    - Neither downloads a filing or reads any financial data.

    The real sources, the database read and the CLI aren't covered by spec tests.

## Acceptance criteria

Fakes sit only at the I/O seam: a fake `FilingIndexSource` that returns synthetic
records and records its calls. Constituents CSV text, `company_tickers` documents, pools
and pulses are built in the tests. All values are synthetic and chosen by hand, except
the five P CIKs, which come from pre-registration v0 §3.

- **C1**: `load_constituents`, `normalize_symbol` and `cik_map`.
  - A three-row CSV with an extra column and padded values parses to the stripped dicts,
    in file order.
  - Each error in requirement 1.
  - `normalize_symbol` on `" brk.b "`, `"BF/B"` and `"msft"`.
  - `cik_map` pads `320193` to `"0000320193"` and normalizes `"BRK.B"`. It raises for a
    ticker with two CIKs, and accepts an exact repeat.

  Property: for random valid symbols, `normalize_symbol` is idempotent. Oracle:
  requirements 1–3.
- **C2**: `build_pool`.
  - One constituent per exclusion reason, each with the expected reason.
  - Precedence: a Financials filer whose CIK is also in `corpus_ciks` is excluded as
    `sector`, and a corpus filer that is also a P filer as `corpus`.
  - The five P CIKs are each excluded as `prospective`.
  - A GOOG/GOOGL-style pair keeps the symbol that sorts first.
  - Strata are assigned per sector.
  - Candidates are sorted by CIK, exclusions by symbol, and the counts add up.
  - Errors: an unknown sector, and two missing CIKs (both named in one error).
  - `corpus_ciks` given unpadded still matches.

  Metamorphic: shuffling the constituents gives an identical result. Oracle:
  requirement 4 and pre-registration v0 §3.
- **C3**: `rank_key` and `rank`.
  - `rank_key` equals `hashlib.sha256` over the requirement-5 string, computed in the
    test, for a lower-case `outputValue` and an unpadded CIK.
  - `rank` orders by key, numbers ranks from 1, and doesn't modify its input.

  Metamorphic: adding a candidate never changes the relative order of the others, and
  shuffling the input gives the same output. Oracle: requirements 5–6.
- **C4**: `fiscal_year_of` and `check_eligibility`.
  - `2025-01-03` → 2024, `2025-01-07` → 2024, `2025-01-08` → 2025, `2025-09-27` → 2025.
  - A malformed date raises.
  - All three years inline → eligible.
  - A year covered only by a `10-K/A` → missing.
  - A year whose only 10-K isn't inline → `not_inline`.
  - A year with one inline and one non-inline 10-K → fine.
  - A record without `report_date` is ignored.

  Property: adding any number of non-`10-K` records never changes the result. Oracle:
  requirements 7–8.
- **C5**: `check_pulse`.
  - A matching pulse passes, with `requested_utc` given as `Z` and as `+00:00`.
  - Each error in requirement 9 raises: a one-minute mismatch, a 127-character
    `outputValue`, a non-hex character, a missing field, and a request with seconds.

  Property: for random whole-minute instants and random 128-hex output values, a pulse
  stamped in NIST's format passes against the same instant. Oracle: requirement 9.
- **C6**: `draw`.
  - A hand-built pool, with eligibility chosen per CIK in the fake, gives the expected
    `drawn`, `alternates`, `checked` and `shortfall` per stratum. The expected order
    is computed in the test with `hashlib`.
  - Ineligible candidates are skipped but listed in `checked`.
  - `source.filings` isn't called after both lists are full.
  - A stratum with too few eligible candidates reports its `shortfall`.
  - A malformed `outputValue` raises.

  Metamorphic: shuffling `pool["candidates"]` gives an identical result. Property: for
  random pools and random eligibility, `drawn` followed by `alternates` equals the first
  `per_stratum + alternates` eligible candidates of the stratum in rank order. Oracle:
  requirement 10.

## Interface

New, stubbed in the spec PR. New files carry no module docstring, because the stub lint
allows none.

`src/eval/aprime/__init__.py`: comments only.

`src/eval/aprime/base.py`:
- `class FilingIndexSource(ABC)`, with
  `@abstractmethod filings(self, cik: str) -> list[dict]`.
- `class BeaconSource(ABC)`, with
  `@abstractmethod pulse(self, timestamp_utc: str) -> dict`.

`src/eval/aprime/core.py`:
- `load_constituents(text: str) -> list[dict]`
- `normalize_symbol(symbol: str) -> str`
- `cik_map(company_tickers: dict) -> dict[str, str]`
- `build_pool(constituents: list[dict], ciks: dict[str, str], corpus_ciks: set[str]) -> dict`
- `rank_key(output_value: str, cik: str) -> str`
- `rank(candidates: list[dict], output_value: str) -> list[dict]`
- `fiscal_year_of(report_date: str) -> int`
- `check_eligibility(filings: list[dict], years: tuple[int, ...] = (2023, 2024, 2025)) -> dict`
- `check_pulse(pulse: dict, requested_utc: str) -> None`
- `draw(pool: dict, pulse: dict, source: FilingIndexSource, per_stratum: int = 5, alternates: int = 2) -> dict`

Not stubbed (implementer, not spec-tested):
- `src/eval/aprime/edgar_source.py`, the EDGAR submissions source. It can follow
  `src/eval/sec_check/sec_client.py` for identity, pacing and caching.
- `src/eval/aprime/beacon_source.py`, the NIST beacon source.
- `scripts/eval/aprime_draw.py`, the CLI.
- A short section on the operator steps in `docs/guides/xbrl-flow/11-evaluation.md`.

## Notes

- **Why a hash ranking and not a seeded PRNG.** `sha256(prefix | outputValue | cik)`
  doesn't depend on any library or Python version. A reader can check the published
  draw with one line in any language. The fixed prefix ties the ranking to this
  benchmark.
- **Why the pulse time must be later than v1's seal.** Otherwise the outputValue would
  already be known when the rule was fixed. v1 states the exact minute (UTC).
- **The constituents list** is saved as a CSV with its source (for example a dated
  permalink of a public S&P 100 list) and committed with the pool. Their hashes go into
  v1. The CSV and the pool contain public index membership only.
- **Fiscal-year labels.** The January 1–7 rule follows the fiscal year's end date. Some
  retailers label their year by its start (a year ending February 1, 2025 is their
  "fiscal 2024"). Eligibility only needs three consecutive years, so the label
  convention doesn't change who is eligible.
- **Corpus filers today:** the 12 suite filers plus CEG, INTC and VST (15 CIKs in
  `entity_master` and `publish_manifest`).
