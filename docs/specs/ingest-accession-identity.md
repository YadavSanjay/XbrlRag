# ingest-accession-identity — identify an ingested filing by its accession number, not its file name

Found while re-ingesting the originals that `annual-amendment-handling` made downloadable.

## Requirement

Two ingest steps identify a filing's row in Arelle's `accession` table by the **file name stem**
of its primary document, matched with `entry_url ILIKE '%<stem>%'`:

1. `ArelleXBRLIngestor._is_already_ingested` skips a filing whose stem matches any row. Its
   docstring assumes the stem "is unique across all filings".
2. `Layer0Populator._patch_one_accession` writes the manifest's accession number into the one
   row matching the stem. It skips the filing when more than one row matches.

An amendment can reuse its original's file name. AMD's FY2025 10-K and its Part III 10-K/A are
both `amd-20251227.htm`, in `AMD/2025/10-K/` and `AMD/2025/10-K_A/`. The 10-K/A was ingested
first, so on 2026-10-14 the 10-K was skipped as already ingested: 0 facts, while it is now the
canonical filing. Had it been ingested, the patch step would still have found two rows and
patched neither.

After this change, a stem match identifies the filing only through the accession number:

- A row already patched to a **different** accession number belongs to another filing and is
  ignored by both steps.
- A row patched to **this** filing's accession number means the filing is already ingested.
- A row not yet patched (Arelle's placeholder, e.g. `'1779318991'`) is still treated as a
  possible match, as today. When several unpatched rows remain, the exact `entry_url` path
  decides.

## Acceptance criteria

- **C1**: `is_accession_number(value)` is `True` iff `value` is a string of the EDGAR form
  `\d{10}-\d{2}-\d{6}`, after stripping surrounding whitespace. Any other value is `False`:
  `None`, `''`, Arelle's integer/timestamp placeholders, a dashless 18-digit string.
  Oracle: the EDGAR accession-number format.
- **C2**: `already_ingested(accession_no, rows)` takes the manifest's accession number (or
  `None`) and the `(entry_url, filing_accession_number)` rows whose `entry_url` matched the
  filing's stem. It is `True` iff some row's `filing_accession_number` equals `accession_no`
  (both stripped), or some row is unpatched (`is_accession_number` false). With
  `accession_no` `None`, any matching row counts, as today.

  Oracle: requirement. Point case: AMD FY2025. The 10-K (`0000002488-26-000018`) against the
  10-K/A's patched row (`0000002488-26-000021`) is not ingested; against a row patched to
  `0000002488-26-000018` it is.
- **C3**: `same_entry_path(entry_url, path)` is `True` iff the two name the same file after
  normalising:
  - remove a leading `file:` scheme and any slashes before a drive letter;
  - `\` becomes `/`;
  - compare case-insensitively;
  - ignore trailing whitespace.

  `None` or empty on either side is `False`. Oracle: Windows path semantics, the forms seen in
  `accession.entry_url` (raw `D:\...` paths and `file:///D:/...` URIs).
- **C4**: `pick_accession_row(accession_no, path, rows)` takes the manifest's accession
  number, the local primary-document path (or `None`) and the stem-matched
  `(accession_id, entry_url, filing_accession_number)` rows. It returns the `accession_id` to
  patch, or `None`:
  - A row already carrying `accession_no`: that row's id (nothing to change; first such row by
    `accession_id`).
  - Otherwise, among the unpatched rows (rows patched to another accession are ignored):
    - exactly one: its id;
    - several: the one whose `entry_url` is `same_entry_path` as `path`, if exactly one is;
    - else `None`.

  Oracle: requirement; AMD point case. Rows: the 10-K/A patched to `…-000021`, plus one
  unpatched row; the 10-K's patch picks the unpatched row.

## Interface

New functions (stubbed in the spec PR):

- `src.ingest.sec_edgar.ingest.arelle_xbrl_ingestor.is_accession_number(value: str | None) -> bool`
- `src.ingest.sec_edgar.ingest.arelle_xbrl_ingestor.already_ingested(accession_no: str | None, rows: Sequence[tuple[str | None, str | None]]) -> bool`
- `src.ingest.sec_edgar.ingest.arelle_xbrl_ingestor.same_entry_path(entry_url: str | None, path: str | None) -> bool`
- `src.ingest.sec_edgar.ingest.arelle_xbrl_ingestor.pick_accession_row(accession_no: str, path: str | None, rows: Sequence[tuple[int, str | None, str | None]]) -> int | None`

Behavior on existing code (added by the implementer; a spec PR may only add stubs):

- `_is_already_ingested` fetches the stem-matched `(entry_url, filing_accession_number)` rows
  and returns `already_ingested(<manifest accession_no>, rows)`.
- `_patch_one_accession` uses `pick_accession_row`, passing the manifest's primary-document
  path in local mode (`None` in S3 mode). The "multiple matches" warning remains for the
  `None` outcome.

## Notes

- Evidence (2026-10-14, local `xbrl_db`): `accession` has one row with stem `amd-20251227`
  (`entry_url` …`\AMD\2025\10-K_A\amd-20251227.htm`, patched to `0000002488-26-000021`).
  `filing_registry` has the 10-K `0000002488-26-000018` as canonical, with 0 facts in
  `mv_financial_facts` and 0 chunks.
- Operator follow-up after the impl merges: re-run the AMD part of the re-ingest — the Arelle
  ingest for `AMD 2025 10-K`, patch accession numbers, registries, and the later steps.
- Out of scope: stamping the accession number at ingest time (right after Arelle succeeds).
  That would remove the placeholder window entirely, but it needs the exact `entry_url` Arelle
  writes in S3 mode, which is a temp-dir path.
