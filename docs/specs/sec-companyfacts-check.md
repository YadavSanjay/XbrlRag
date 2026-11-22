# sec-companyfacts-check — the suite's expected values against the SEC's own XBRL data

External test v1. The obvious objection to the main suite is that "the expected
answers agree with your own database". This change checks them against the SEC's
**companyfacts** API (`https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`). That
API is the SEC's own extraction of every non-dimensional fact in every filing.

**What it can claim:** "the expected values match the SEC's published XBRL figures". That
confirms the ingest, concept, unit and period choices. It does **not** show independence
from the filings, since companyfacts is built from the same XBRL.

**One change from the plan.** The plan sampled about 100 cases. Fetching companyfacts costs
one request per filer, so this change checks **every** case that can be checked
automatically: 199 lookups, 108 computes and 22 screens. That leaves no sampling error,
and the effort scales only with the mismatches. The sample survives only for the hand
check of dimensional and breakdown cases, which companyfacts can't cover.

## Requirement

### Inputs

1. **A fact record** (`FactRecord`, a `TypedDict`) describes one of our facts. Its keys:
   - `fact_id: int`, `ticker: str`, `cik: str` (10 digits), `accession_no: str`;
   - `concept: str`: the namespace URI, then `:`, then the local name, as in
     `mv_financial_facts.concept`, e.g. `http://fasb.org/us-gaap/2024:Revenues`;
   - `unit: str`, e.g. `USD`, `TWD`, `pure`, `Rate`;
   - `period_start: str | None` and `period_end: str`: ISO dates. `period_start` is
     `None` for an instant;
   - `value: float`;
   - `dimensional: bool`: the fact has any dimension (axis/member).
2. **Two I/O seams** in `src/eval/sec_check/base.py`:
   - `CompanyFactsSource.company_facts(cik)` returns the companyfacts JSON for one filer,
     as a `dict`;
   - `FactStore.facts(fact_ids)` returns `{fact_id: FactRecord}`;
   - `FactStore.metric_components(ticker, fiscal_year, metric)` returns the FY row's
     `component_fact_ids` from `mv_derived_metrics` (role → fact id, or list of fact ids),
     or `None`.

### Matching one fact (`check_fact`)

3. **Taxonomy.** `taxonomy_key(namespace)` maps a namespace URI to its companyfacts key:
   - a URI containing `fasb.org/us-gaap` gives `"us-gaap"`;
   - one containing `xbrl.ifrs.org` and `ifrs-full` gives `"ifrs-full"`;
   - one containing `fasb.org/srt` gives `"srt"`;
   - one containing `xbrl.sec.gov/dei` gives `"dei"`;
   - anything else is a filer extension and gives `None`.

   The namespace is everything before the **last** `:` of `concept`.
4. **The match rule:** same taxonomy concept, same unit, same period and the **same
   accession**.
   - **Concept:** `facts[taxonomy_key][local_name]`.
   - **Unit:** the key under `units` equal to `unit`. `pure` and `Rate` are one alias
     group: a fact in either matches an entry under either.
   - **Period:** an entry with `start == period_start` and `end == period_end` for a
     duration. For an instant, an entry with no `start` and `end == period_end`.
   - **Accession:** an entry whose `accn` equals `accession_no`.
   - **Value:** values agree when `abs(a - b) <= 1e-9 * max(abs(a), abs(b), 1)`. This
     is called "agree" below.
5. **`check_fact(fact, company_facts)` returns a `FactCheck`** (a `TypedDict`) with
   `fact_id`, `status`, `sec_value: float | None`, `sign_flip: bool` and
   `restatements: list[{"accession_no": str, "value": float}]`. `status` is decided in
   this order:
   1. `"dimensional"`: `fact["dimensional"]`. Nothing else is checked.
   2. `"extension_concept"`: `taxonomy_key` is `None`.
   3. `"concept_absent"`: no entry matches concept, unit and period in any accession.
   4. `"match"`: an entry for our accession agrees with `value`. `sec_value` is that
      entry's `val`.
   5. `"value_mismatch"`: entries exist for our accession, and none agrees.
      - `sec_value` is the first such entry's `val`.
      - `sign_flip` is true when that `val` agrees with `-value`.
   6. `"match_other_accession"`: no entry for our accession, but another accession's
      entry agrees. `sec_value` is the first such `val`.
   7. `"absent_for_accession"`: entries exist only for other accessions, and none agrees.

   `restatements` lists every entry for the same concept, unit and period whose
   accession differs from ours and whose value does not agree with ours, in input
   order. It is filled for statuses 4–7 and empty otherwise. `sign_flip` is false
   except as in step 5.

### Recomputing a derived value (`recompute_metric`)

6. **`recompute_metric(metric, values)`** takes role names mapped to floats and returns
   the value from this table, or `None` when the metric is unknown, a role is missing
   or a denominator is 0:

   | `metric` | Formula over roles |
   |---|---|
   | `GrossMarginPct` | `(revenue - cost_of_revenue) / revenue` |
   | `OperatingMarginPct` | `operating_income / revenue` |
   | `NetMarginPct` | `net_income / revenue` |
   | `RnDIntensityPct` | `rnd / revenue` |
   | `EffectiveTaxRate` | `effective_tax_rate` when present, else `tax / pretax_income` |
   | `CurrentRatio` | `current_assets / current_liabilities` |
   | `FreeCashFlow` | `operating_cash_flow - capex` |
   | `EBITDA` | `operating_income + dep_amort` |
   | `yoy` | `(current - prior) / abs(prior)` |

   This is the formula `scripts/ddl/mv/34_mv_derived_metrics.sql` uses. YoY follows
   `mv_yoy_comparison`'s `|base|` convention.
   - When a component's value in `metric_components` is a list, the role's fact is its
     **first** element. `cost_of_revenue` is `[cost_of_revenue, gross_profit]` when
     gross profit is also tagged.

### Checking one case (`check_case`)

7. **`check_case(case, store, source)` returns a `CaseCheck`** (a `TypedDict`) with
   `case_id`, `kind`, `status`, `reason: str | None`, `facts: list[FactCheck]` and
   `items: list[{"label": str, "expected": float, "sec_value": float | None,
   "agree": bool}]`.
   - The case is a suite case dict (`test/eval/suites/sql_derived_v1.json`). Its
     parameters are the `key=value` pairs after the `:` in `case["id"]`, separated by
     `-`.
   - `source.company_facts` is called **at most once per CIK** within one `check_case`.

   How each kind is checked:
   - **`dimensional` and `breakdown`** give `status: "hand_check"`, `reason:
     "dimensional"`, no facts and no items. The store and the source are not called.
   - **`lookup`** has one item. Its fact is `expected_fact_ids[0]`, its `expected` is
     `case["expected_value"]`, its `sec_value` is the fact's `sec_value`, and its label is
     `case["ticker"]`.
   - **`compute`** has one item, labelled `case["ticker"]`:
     - when `case["id"]` has a `metric` parameter, the roles come from
       `store.metric_components(case["ticker"], case["fy"], metric)`, and the metric is
       that parameter;
     - otherwise, when the case has exactly 2 `expected_fact_ids`, the metric is `yoy`.
       `current` is the fact with the later `period_end`, and `prior` the other;
     - otherwise `status` is `"template_unknown"`.

     `sec_value` is `recompute_metric` over the facts' `sec_value`s. It is `None` when
     any of them is `None`.
   - **`screen`** has one item per `expected_rows` row, in order, labelled
     `row["entity"]`, with `expected = row["value"]`. A row's facts are the cited facts
     whose `ticker` is the row's entity. Then, in this order:
     - a `metric` parameter means a metric with roles from
       `store.metric_components(entity, case["fy"], metric)`;
     - otherwise 2 facts mean `yoy` (as for compute);
     - otherwise 1 fact means the fact's `sec_value`;
     - otherwise the row's `sec_value` is `None`.

     A screen with any dimensional fact gives `status: "hand_check"`, with its fact
     checks kept.

   An item `agree`s when its `sec_value` is not `None` and agrees with `expected` (the
   rule in requirement 4). `facts` holds a `check_fact` for every distinct fact the case
   used, in first-use order.

   `status`, for a case that isn't `hand_check` or `template_unknown`, is decided in this
   order:
   1. `"mismatch"`: any fact is `value_mismatch`, or any item has a `sec_value` that
      doesn't agree.
   2. `"unverifiable"`: any fact is `extension_concept`, `concept_absent` or
      `absent_for_accession`, or any item's `sec_value` is `None`. `reason` names the
      first such fact's status, or `"no_value"`.
   3. `"match"`: otherwise.

### Hand check and summary

8. **`select_hand_checks(cases, seed)`** returns the case dicts to check by hand.
   - The pool is the cases of kind `dimensional` or `breakdown`, grouped by origin.
   - A `dimensional` origin gives `min(6, n)` cases, and a `breakdown` origin
     `min(2, n)`.
   - Within an origin, the selection is `random.Random(f"{seed}:{origin}").sample(ids,
     k)`, over the origin's case ids sorted.
   - The result is sorted by case id. The same inputs always give the same list.
9. **`summarize(case_checks)`** returns, per kind and for `"all"`, the counts of each
   status and:
   - `checked = match + mismatch`;
   - `match_rate = match / checked`, or `None` when `checked == 0`.

   It also returns `fact_status_counts` over every distinct `fact_id` across the cases.

   The shape: top-level keys are each kind present, `"all"` and `"fact_status_counts"`.
   - A kind's value (and `"all"`'s) is a flat dict of status → count, plus `checked`
     and `match_rate`. A status with no cases may be absent.
   - `fact_status_counts` maps status → count.
10. **The SEC client.**
    `SecCompanyFactsClient(identity, cache_dir, http_get=None, min_interval_s=0.1,
    clock=None, sleep=None)` implements `CompanyFactsSource`.
    - It fetches `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:0>10}.json`, sending a
      `User-Agent` header equal to `identity`.
    - It caches the body as `cache_dir / f"CIK{cik:0>10}.json"`, and reads that file
      instead of fetching when it exists.
    - It leaves at least `min_interval_s` between the starts of two fetches, using
      `clock` and `sleep` (default `time.monotonic` and `time.sleep`).
    - A non-200 status raises `RuntimeError` naming the URL and the status, and writes no
      cache file.
    - `http_get(url, headers) -> (status, body_bytes)` defaults to `httpx`.
    - The CLI passes `resolve_sec_identity()` as `identity`, and `data/sec_companyfacts/`
      as `cache_dir`.
11. **The CLI** `scripts/eval/check_against_sec.py` runs every suite case through
    `check_case`, using a Postgres `FactStore` and the SEC client. It writes:
    - a report JSON: every `CaseCheck`, the summary and `select_hand_checks`;
    - a Markdown table per kind;
    - a hand-check list with each selected case's question, expected value(s), ticker,
      accession and EDGAR filing-index URL.

    It takes `--suite`, `--out-dir` and `--seed` (default `20261021`). The Postgres store
    and the CLI aren't covered by spec tests, because they need the database. Their
    output is checked by reading the first run's mismatches.

## Acceptance criteria

Fakes sit only at the I/O seams:
- a fake `CompanyFactsSource` returning synthetic companyfacts dicts, in the API's shape;
- a fake `FactStore` returning synthetic `FactRecord`s and components;
- a fake `http_get`, `clock` and `sleep`, and a pytest `tmp_path` cache directory.

`taxonomy_key`, `check_fact`, `recompute_metric`, `check_case`, `select_hand_checks`,
`summarize` and the client's logic are real. All data is synthetic, with values chosen by
hand.

- **C1**: `taxonomy_key`.
  - It maps `http://fasb.org/us-gaap/2024`, `https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full`,
    `http://fasb.org/srt/2024` and `http://xbrl.sec.gov/dei/2024` to their keys.
  - It maps `http://www.apple.com/20240928` to `None`.

  Property: any year suffix on a us-gaap URI still maps to `"us-gaap"`. Oracle:
  requirement 3.
- **C2**: `check_fact`. One hand-built companyfacts dict yields each of the seven
  statuses:
  - a match, including a `Rate` fact matched under `pure`;
  - a value mismatch with `sign_flip` true, and one with it false;
  - a match in another accession only;
  - absent for our accession;
  - concept absent, where the concept is present but the period is not;
  - an extension concept;
  - a dimensional fact.

  It also lists restatements: our accession matches, and a later accession reports a
  different value. An instant fact matches an entry with no `start`, and does not match a
  duration entry with the same `end`.

  Property: scaling every value in the dict and the fact by the same non-zero factor
  leaves `status` unchanged. Oracle: requirements 4 and 5.
- **C3**: `recompute_metric`.
  - Every row of the table, with hand-chosen numbers and the result computed in the test.
  - `EffectiveTaxRate` prefers `effective_tax_rate` over `tax / pretax_income`.
  - A zero denominator, a missing role and an unknown metric each give `None`.
  - `yoy` with a negative prior uses `abs(prior)`.

  Property: for the ratio metrics, scaling every input by `k > 0` leaves the result
  unchanged; for `FreeCashFlow` and `EBITDA`, it scales the result by `k`. Oracle:
  requirement 6 and `34_mv_derived_metrics.sql`.
- **C4**: `check_case` for lookups and computes.
  - A lookup whose fact matches is a `match`.
  - A lookup whose expected value differs from the SEC value, with the fact itself
    matching our database value, is a `mismatch`.
  - A `yoy` compute picks current and prior by `period_end`.
  - A metric compute uses `metric_components`, and the first element of a list role.
  - An unknown compute template gives `template_unknown`.
  - A `concept_absent` fact gives `unverifiable`.
  - The source is called once per CIK.

  Property: when every fact matches and each expected value is the recomputed SEC value,
  the status is `match`. Oracle: requirement 7.
- **C5**: `check_case` for screens and hand-check kinds.
  - A three-row screen without a `metric` parameter, mixing direct and `yoy` rows, has
    one item per row, in order. So does a three-row metric screen. A `metric` parameter
    applies to every row, so one case can't mix the three.
  - A screen with a dimensional fact gives `hand_check`.
  - `dimensional` and `breakdown` cases give `hand_check` without calling the store or
    the source.
  - A row with no cited fact gets `sec_value None`, so the case is `unverifiable`.

  Metamorphic: reversing the order of the cited fact ids doesn't change the items. Oracle:
  requirement 7.
- **C6**: `select_hand_checks` and `summarize`.
  - Over a synthetic pool with origins of 10, 3, 1 and 0 cases, the per-origin counts
    are `min(6, n)` or `min(2, n)` by kind.
  - The selection equals `random.Random(f"{seed}:{origin}").sample`, computed in the test.
  - The output is sorted, and is deterministic for one seed.
  - `summarize` counts statuses per kind, gives `match_rate = match / (match + mismatch)`
    and `None` when that is 0, and counts each distinct fact once.

  Property: the selection is a subset of the pool, with no duplicates. Oracle:
  requirements 8 and 9.
- **C7**: the SEC client.
  - With a fake `http_get` returning `(200, b'{"cik": 320193}')`:
    - the URL is `.../CIK0000320193.json`;
    - the `User-Agent` header equals the identity;
    - the result is the parsed dict;
    - the cache file exists.
  - A second call for the same CIK makes no HTTP call.
  - Two fetches for different CIKs at fake times 0.00 and 0.03 sleep 0.07.
  - A 404 raises `RuntimeError` and writes no cache file.

  Property: over random sequences of distinct CIKs and fetch times, consecutive fetch
  starts are at least `min_interval_s` apart. Oracle: requirement 10 and the SEC's fair
  access policy (10 requests per second at most).

## Interface

New, stubbed in the spec PR:

`src/eval/sec_check/__init__.py`: empty apart from comments. The stub lint allows no
module docstrings in new files.

The stubbed `TypedDict`s list their keys in their docstrings. The implementer adds the
annotations.

`src/eval/sec_check/base.py`:
- `class FactRecord(TypedDict)`, with the keys in requirement 1;
- `class CompanyFactsSource(ABC)`, with
  `@abstractmethod company_facts(self, cik: str) -> dict`;
- `class FactStore(ABC)`, with
  `@abstractmethod facts(self, fact_ids: list[int]) -> dict[int, FactRecord]` and
  `@abstractmethod metric_components(self, ticker: str, fiscal_year: int, metric: str) -> dict | None`.

`src/eval/sec_check/core.py`:
- `class FactCheck(TypedDict)`, `class CaseCheck(TypedDict)`;
- `taxonomy_key(namespace: str) -> str | None`;
- `check_fact(fact: FactRecord, company_facts: dict) -> FactCheck`;
- `recompute_metric(metric: str, values: dict[str, float]) -> float | None`;
- `check_case(case: dict, store: FactStore, source: CompanyFactsSource) -> CaseCheck`;
- `select_hand_checks(cases: list[dict], seed: int) -> list[dict]`;
- `summarize(case_checks: list[CaseCheck]) -> dict`.

`src/eval/sec_check/sec_client.py`:
- `class SecCompanyFactsClient(CompanyFactsSource)`, with
  `__init__(self, identity: str, cache_dir: Path, http_get: Callable | None = None, min_interval_s: float = 0.1, clock: Callable | None = None, sleep: Callable | None = None) -> None`
  and `company_facts(self, cik: str) -> dict`.

Not stubbed (implementer, not spec-tested): `src/eval/sec_check/pg_store.py`
(`PostgresFactStore`, reading `mv_financial_facts` and `mv_derived_metrics`) and
`scripts/eval/check_against_sec.py`.

## Notes

- **companyfacts shape** (SEC "EDGAR Application Programming Interfaces", 2026):
  - the top level is `{"cik": int, "entityName": str, "facts": {taxonomy: {concept:
    {"label", "description", "units": {unit: [entry, ...]}}}}}`;
  - an entry is `{"start"?, "end", "val", "accn", "fy", "fp", "form", "filed",
    "frame"?}`;
  - an instant has no `start`;
  - dimensional facts and filer extensions aren't included.
- **The data, as seen on 2026-10-21:**
  - every non-dimensional fact the suite cites (387) is in `us-gaap` (5 taxonomy years)
    or `ifrs-full` (TSMC);
  - their units are `USD`, `TWD`, `pure` and `Rate` (Broadcom's tax rate).
- **The second human check.** The 20 hand-check cases (12 dimensional and 8
  breakdown) are checked against the filing by the user or a second person, never by
  the system's author.
- **Every mismatch in the first run is classified in the SEC-check report** as our error,
  restatement or rule edge. The classification is a reading, not code.
- Cost: $0, about 12 HTTP requests, one per filer.
