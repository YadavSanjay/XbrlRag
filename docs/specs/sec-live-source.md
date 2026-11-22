# sec-live-source — a live, cached `SecSource` for the SEC companyfacts and frames APIs

v2 evaluation ([v2 plan](../design/XbrlRag_v2_Plan.md) §3.3). [sec-keys](sec-keys.md) builds
answer keys from SEC payloads behind the `SecSource` seam and left the HTTP client to a
later change. This is that change: a `SecSource` that fetches `data.sec.gov` companyfacts and
frames politely, stores every payload content-addressed through the storage seam, and exposes
each payload's SHA-256 so a key can name the exact bytes it was built from. A suite build is
then reproducible offline from the cache.

It does not build suites, change the key builders or their locked signatures, or read our
fact store.

## Requirement

### The transport seam

1. **`HttpTransport`** (an `ABC` in `src/eval/keys/base.py`) has one method
   `get(self, url: str, headers: dict) -> tuple[int, bytes]`: one HTTP GET, returning the
   status code and the raw body. It does not retry or pace. The production transport (httpx,
   following redirects, a timeout) is the implementer's private default when no transport is
   passed; tests always pass a fake.

### The source

2. **`LiveSecSource(SecSource)`** in `src/eval/keys/sec_live.py`, built as
   `LiveSecSource(store, transport=None, identity=None, max_rps=10.0, max_retries=4,
   backoff_s=1.0, refresh=False, clock=None, sleep=None, wall_clock=None)`:
   - `store: FilingStore` (`src/ingest/sec_edgar/storage.py`): the cache's byte store. Every
     cache read and write goes through it (`read_bytes` / `write_bytes` / `exists`), never
     through a path of its own, so the cache lives on local disk or S3 as the deploy mode
     selects (`FilingStore.from_env(...)` at the call site).
   - `clock` (monotonic seconds, `time.monotonic` when `None`), `sleep` (`time.sleep` when
     `None`) and `wall_clock` (returns an aware UTC `datetime`, now when `None`) are
     injectable. The constructor makes no request.
3. **Identity.** The `User-Agent` is `identity` when given, else the `SEC_EDGAR_IDENTITY`
   environment variable, the downloader's rule; it is stripped of surrounding whitespace.
   Missing or blank raises `ValueError` **at construction**. Every request carries
   `headers["User-Agent"]` equal to it.
4. **URLs.** `company_facts(cik)` requests
   `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json`, where `cik10` is
   `str(int(cik))` zero-padded to 10 (`"320193"` and `"0000320193"` are one URL). A CIK that
   is not 1 to 10 decimal digits (after stripping) raises `ValueError` with no request.
   `frames(taxonomy, tag, unit, period)` requests
   `https://data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json`. Both return
   the parsed JSON of the body (`json.loads`), as `SecSource` requires.
5. **Rate limit.** `max_rps` must satisfy `0 < max_rps <= 10` (the SEC's fair-access limit),
   else `ValueError` at construction. Before each request (every attempt, retries included)
   the source sleeps just long enough that the request starts at least `1 / max_rps` seconds
   (by `clock`) after the previous request started. The first request does not sleep, and no
   request sleeps when that interval has already passed.
6. **Retries.** Status `429` and every `5xx` are retried: at most `max_retries` retries
   (`max_retries + 1` attempts in all; `max_retries >= 0`, else `ValueError` at construction).
   Retry `k` (1-based) starts at least `backoff_s * 2 ** (k - 1)` seconds after attempt `k`
   started. When the attempts are used up, `SecFetchError` (a `RuntimeError`) is raised,
   naming the URL and the last status.
   - `404` raises `SecNotFoundError`, a subclass of sec-keys' `KeyNotFoundError` (so of
     `LookupError`): a typed not-found, never an empty payload. No retry.
   - Any other non-200 status raises `SecFetchError` with no retry.
   - A `200` whose body is not valid JSON raises `SecFetchError` with no retry.
   - Nothing but a `200` with a JSON body is ever written to the cache.

### The cache

7. **Content-addressed payloads.** A fetched body is stored byte for byte at the store key
   `_sec_api/payloads/{sha256}.json`, `sha256` being the lowercase hex SHA-256 of the body.
   Two URLs whose bodies are equal share that one payload. Alongside, the source records,
   per URL (the index layout is the implementer's, under `_sec_api/`), a **`FetchRecord`**
   (`TypedDict`): `url: str`, `sha256: str`, `fetched_at: str` (the `wall_clock` time of the
   fetch, `YYYY-MM-DDTHH:MM:SSZ` in UTC).
8. **Cache hits.** When a URL has a record and `refresh` is `False`, the source makes no
   request (and no pacing sleep) and returns the recorded payload, with the original record.
   This holds for a new `LiveSecSource` over the same store, so a build reruns offline. A
   stored payload whose bytes no longer hash to its recorded `sha256` raises `ValueError`
   (fail loud; never a silent refetch).
9. **Refresh.** With `refresh=True`, every call requests its URL, and on success stores the
   payload (as in 7) and replaces that URL's record. Earlier payloads are never deleted, so an
   older record's hash still resolves.
10. **`payload(sha256: str) -> bytes`**: the stored payload with that hash. Unknown hash:
    `KeyError`. Bytes that do not hash to it: `ValueError`.

### Provenance

11. **`fetch_company_facts(cik) -> Fetched`** and
    **`fetch_frames(taxonomy, tag, unit, period) -> Fetched`**, where `Fetched` is a
    `TypedDict` `{"document": dict, "record": FetchRecord}`, do what `company_facts` /
    `frames` do and also return the record. `company_facts(cik)` equals
    `fetch_company_facts(cik)["document"]` (and likewise for frames).
12. **`SourcedKey`** (`TypedDict`): `{"key": AnswerKey, "payload": FetchRecord}`.
    sec-keys' `AnswerKey` has exactly its locked fields, so the payload hash travels beside
    the key, not inside it.
    - **`fiscal_key_with_payload(source, cik, concept, unit, fiscal_year) -> SourcedKey`**:
      one `source.fetch_company_facts(cik)`, then `fiscal_year_key` over its document; the
      record is that fetch's.
    - **`calendar_key_with_payload(source, cik, concept, unit, calendar_year, instant=False)
      -> SourcedKey`**: one `source.fetch_frames(taxonomy, tag, unit, period)` with
      `period` as in sec-keys requirement 10, then `calendar_year_key(document, cik, unit)`.
    - The builders' errors (`KeyNotFoundError`, `UnitMismatchError`, ...) propagate.

## Acceptance criteria

Fakes sit only at the I/O boundaries: a `FakeTransport` (scripted `(status, body)`
responses; it records each URL, headers and its start time on the fake clock), a fake
monotonic clock whose `sleep` advances it, a fixed wall clock, and an in-memory `FilingStore`
(the real `LocalFilingStore` over `tmp_path` where noted). Payloads are synthetic bytes, in
the SEC API shape, with values chosen by hand. Times are compared with `abs=1e-9` (the fake
clock adds floats).

- **C1**: identity. An explicit `identity` wins over `SEC_EDGAR_IDENTITY`; with no argument
  the environment variable is used; neither, or a blank value, raises `ValueError` before any
  request. Every request's `User-Agent` is the stripped identity. Property: for any non-blank
  identity text, every request's header equals it stripped. Oracle: requirement 3.
- **C2**: URLs and parsing. `company_facts("320193")` requests
  `https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json` and returns the parsed body;
  `frames("us-gaap", "Revenues", "USD", "CY2024")` requests
  `https://data.sec.gov/api/xbrl/frames/us-gaap/Revenues/USD/CY2024.json`. A non-digit or
  11-digit CIK raises `ValueError` with no request. Metamorphic: any CIK in `1..9_999_999_999`,
  padded or not, requests the same URL. Oracle: requirement 4, the SEC API docs.
- **C3**: rate limit. Three uncached requests on an instant transport start at `0.0`, `0.1`,
  `0.2`; with `max_rps=2`, at `0.0`, `0.5`, `1.0`; a transport that takes `0.3 s` causes no
  sleep at all. `max_rps` `0`, negative or `> 10` raises `ValueError`. Property: for any
  `max_rps` in `(0, 10]` and any per-request latencies, consecutive request starts are at least
  `1 / max_rps` apart, and no sleep is longer than needed (the next start is exactly
  `max(previous start + 1/max_rps, the time the source was called)`). Oracle: requirement 5.
- **C4**: retries and errors. `503, 503, 200` returns the document after 3 attempts, the
  retries starting at least `1.0` and `2.0 s` after the attempts before them; `429` is
  retried; a persistent `500` raises `SecFetchError` after exactly `max_retries + 1 = 5`
  attempts; `403` and a non-JSON `200` raise `SecFetchError` after one attempt; `404` raises
  `SecNotFoundError` (a `KeyNotFoundError` and `LookupError`) after one attempt, and is not
  cached (a second call requests again). `max_retries=-1` raises `ValueError`. Property: for
  any `max_retries` in `0..5`, any retryable status and `k` failures, the call succeeds with
  `k + 1` attempts when `k <= max_retries`, else raises `SecFetchError` after `max_retries + 1`.
  Oracle: requirement 6.
- **C5**: the cache. After one fetch, the record is `{"url", "sha256", "fetched_at"}` with
  `sha256 == hashlib.sha256(body).hexdigest()` and `fetched_at "2026-01-02T03:04:05Z"` from
  the fixed wall clock; the store holds the body byte for byte at
  `_sec_api/payloads/{sha256}.json`; `payload(sha256)` returns it. A second call, and a new
  source over the same store (a real `LocalFilingStore`) whose transport fails on any request,
  make no request and return the same document and record. A tampered payload raises
  `ValueError` on a cache hit and in `payload`; an unknown hash raises `KeyError`. Metamorphic:
  two URLs with equal bodies share one payload key and one `sha256`. Property: for any JSON
  body, the record's hash is the body's SHA-256 and `payload` returns the exact bytes.
  Oracle: requirements 7, 8, 10; `hashlib` (FIPS 180-4).
- **C6**: refresh. With `refresh=True` a cached URL is requested again; a changed body gives a
  new `sha256` and `fetched_at`, the old hash still resolves through `payload`, and a later
  non-refresh source returns the new document. Metamorphic: a refresh that returns the same
  bytes keeps the same `sha256`. Oracle: requirement 9.
- **C7**: provenance. `fiscal_key_with_payload` on the fixture filer returns the FY2024 key
  (`391_035_000_000`, `2023-10-01` → `2024-09-28`, its accession) and a record whose `sha256`
  is the SHA-256 of the served companyfacts bytes, after one request;
  `calendar_key_with_payload` requests the `CY2024` (or `CY2024Q4I` with `instant`) frame
  once and returns that frame's row and hash. A `404` frame raises `KeyNotFoundError`.
  `company_facts` equals `fetch_company_facts(...)["document"]`. Metamorphic: changing the
  served payload's bytes without changing its facts (whitespace) changes `sha256` but not the
  key. Oracle: requirements 11–12, the fixture.

## Interface

New, stubbed in the spec PR (stub `TypedDict`s list their keys in their docstrings; the
implementer adds the annotations):

`src/eval/keys/base.py`:
- `class HttpTransport(ABC)` with `@abstractmethod get(self, url: str, headers: dict) -> tuple[int, bytes]`.

`src/eval/keys/sec_live.py`:
- `class SecFetchError(RuntimeError)`; `class SecNotFoundError(KeyNotFoundError)`;
- `class FetchRecord(TypedDict)`, `class Fetched(TypedDict)`, `class SourcedKey(TypedDict)`;
- `class LiveSecSource(SecSource)`:
  - `__init__(self, store: FilingStore, transport: HttpTransport | None = None, identity: str | None = None, max_rps: float = 10.0, max_retries: int = 4, backoff_s: float = 1.0, refresh: bool = False, clock: Callable[[], float] | None = None, sleep: Callable[[float], None] | None = None, wall_clock: Callable[[], datetime] | None = None) -> None`;
  - `company_facts(self, cik: str) -> dict`; `frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict`;
  - `fetch_company_facts(self, cik: str) -> Fetched`; `fetch_frames(self, taxonomy: str, tag: str, unit: str, period: str) -> Fetched`;
  - `payload(self, sha256: str) -> bytes`;
- `fiscal_key_with_payload(source: LiveSecSource, cik: str, concept: str, unit: str, fiscal_year: int) -> SourcedKey`;
- `calendar_key_with_payload(source: LiveSecSource, cik: str, concept: str, unit: str, calendar_year: int, instant: bool = False) -> SourcedKey`.

## Notes

- **Endpoints**: SEC "EDGAR Application Programming Interfaces"
  (`data.sec.gov/api/xbrl/companyfacts/CIK##########.json`,
  `data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json`); fair-access policy:
  at most 10 requests per second with a declared `User-Agent`. Identity rule as
  `src/ingest/sec_edgar/downloader/sec_filing_downloader.py` (`SEC_EDGAR_IDENTITY` or an
  explicit argument; missing fails).
- **Why content addressing.** A key's `SourcedKey.payload.sha256` names the exact bytes it was
  built from; a refresh never deletes those bytes, so an old suite's keys can be rebuilt and
  audited after the SEC data moves on (recasts, new filings).
- **Cache location.** Under `_sec_api/` in whichever `FilingStore` the caller passes, e.g.
  `FilingStore.from_env(role="sec_edgar")`; the leading underscore keeps it apart from the
  `{TICKER}/{YEAR}/{FORM}` tree, and manifest discovery ignores it. No new storage role and no
  change to `storage.py`.
- **Not here**: honouring `Retry-After`, retrying transport exceptions (the httpx transport may
  map them to a `5xx`-like status), negative caching of 404s, and migrating
  `SecCompanyFactsClient` / `aprime.edgar_source` onto this client.
- **Fixtures** (synthetic; CIK `0000900001` made up): one companyfacts document with an
  FY2024 10-K revenue entry and its FY2023 comparative, and a CY2024 revenue frame and a
  CY2024Q4I assets frame naming that CIK.
- Cost: $0; no network.
