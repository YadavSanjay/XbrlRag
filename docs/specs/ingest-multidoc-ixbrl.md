# ingest-multidoc-ixbrl — filings whose inline XBRL spans several documents

Some 10-Ks split inline XBRL across two or more HTML documents: a small main document and a far
larger one (`<stem>_d2.htm`) holding the financial statements. The extracted instance
`<stem>_htm.xml` is complete and the download saves it, but `_save_xbrl_attachments` drops the
second document and ingest reads the main document alone. Such a filing arrives with about 62
facts of about 1,300, no primary statements, and fails GATE-A. Any filer structured this way
silently loses its statements.

A second defect is fixed here because it stops the same download. `_select_filings` reads
`period_of_report` on every filing a company ever made, and each read fetches that filing's full
text, so one stalled request anywhere in a filer's history fails the filer.

Out of scope: see Notes.

## Why the file was skipped

`_is_xbrl_artifact` keeps a file in exactly two cases: its EDGAR `document_type` equals the form,
or its name ends in a linkbase suffix or `.xsd`. A document typed as the form would have been
saved, so the second document's type must be something else, most likely an exhibit type. This
is inferred: the reported filing's filer is outside the dev set and its type was not read.
edgartools cannot help: attachments built from the submission text are always `ixbrl=False`
(`edgar/sgml/sgml_common.py`). Ingest then picks its entry from `manifest["primary_document"]`,
the only document the manifest names.

## Design

**Facts come from the extracted instance; the DOM comes from a merge of the documents.**

1. *Fact store.* A multi-document filing gives Arelle `<stem>_htm.xml` as its entry. Nothing else
   about the run changes, and a single-document filing keeps its primary document.
2. *Canonical HTML.* `canonical.htm` stays one file: the canonical bytes of each document, merged
   in filing order into one body. `fact_dom_position`, `section_assignment`, the chunker, Qdrant
   and the long-context arm all key on one file and one offset space, so none of them changes.
3. *The join between them* is the fact id. `fact.xml_id` comes from the instance, the position
   from the merged DOM, and criterion C4 proves before Arelle runs that every instance id is in
   the saved documents.

**Why the instance and not Arelle's inline document set.** Measured on this repo's Arelle:

| | Inline document set | Extracted instance |
|---|---|---|
| Load | Needs the `inlineXbrlDocumentSet` plugin and the JSON form of `-f`. The `a.htm\|b.htm` form the plugin's docstring shows loaded **two separate models** here (run on a synthetic pair), one with facts that have no context, which xbrlDB would store as two accessions. | A plain `-f` path, as today. |
| `entry_url` | Becomes `<dir>/_IXDS` (measured). The duplicate check and `patch_accession_numbers` match on the primary document's stem, so they miss it; the file would need a plugin hook or a DB update. | `<dir>/<stem>_htm.xml`: the stem still matches and the parent directory still resolves `canonical.htm`. `same_entry_path` needs one rule (C3). |
| Other Arelle stages | The concept-label, dimension-member and geography stages load `entry_url` or the primary document, and the last two read contexts, so each needs the JSON entry and the plugin. | They get the whole filing's contexts by loading the same file. Only the geography stage, which reads the manifest, changes. |
| xbrlDB | The surrogate `_IXDS` document has never gone through `XbrlPublicPostgresDB` here, and proving it needs a write to a database. | One file, no surrogate. |
| Fact fidelity | Source of truth. | On KO FY2025 (2,804 facts; 660 contexts), loaded both ways with Arelle 2.41.0: the same ids, concepts, contexts, units and nil flags, and every value equal except 49 facts where the inline load has `(ixTransformValueError)` and the instance has the real value. VST FY2025 (3,298 facts; 1,121 contexts): the same ids, concepts, contexts and units; 55 values differ, and those sampled are that same error. |
| Citation back to the DOM | Ids are the DOM's by construction. | In all 155 dev filings the instance's fact-id set equals the primary document's, and C4 enforces it per filing. |

The instance's one real cost is that it is a derivative: store and DOM now come from different
files. C4 closes that gap by refusing any filing where they disagree.

`mv_anchor_coverage`, `promotion.py` and `XbrlRag_Materialized_Views.md` say that a load from
`*_htm.xml` leaves `fact.xml_id` NULL. Neither the code nor the data supports that for the
instances EDGAR writes today: `elementFragmentIdentifier` returns the element's `id`, and those
ids are in the file. GATE-A stays as the net, and the rollout's first real load must confirm it.
The implementer corrects those three texts.

**Why a merge and not one canonical file per document.** Per-document files need a document column
on `fact_dom_position` and `chunk_metadata`, and `chunk_id` hashes only accession and byte range,
so two documents would collide. A merge changes no schema and no consumer.

## Requirement

1. **Every inline document is downloaded.** An HTML attachment is an inline document when its
   content binds the inline XBRL namespace and holds a fact element, whatever its EDGAR type. It
   is saved, and `manifest.yaml` gains `ixbrl_documents`: the primary document first, then the
   others in attachment order. A single-document filing lists one name.

2. **A document that cannot be read fails the download.** Today a read failure logs and skips. Any
   `.htm`/`.html` attachment that cannot be read raises `RuntimeError` naming the file, and no
   manifest is written, because without its bytes it cannot be ruled out. EDGAR's viewer pages
   (`R<n>.htm`) are exempt. Other attachments keep today's behaviour.

3. **Arelle loads the file that holds every fact.** `arelle_entry_name` returns the primary
   document for a filing with one inline document, including a manifest without
   `ixbrl_documents`, and the `*_htm.xml` listed in `files` for more than one. More than one and no
   instance raises `ValueError`. The fact ingest and the geography stage use it. The concept-label
   and dimension-member stages already load `accession.entry_url`, so they follow. `IngestResult.
   primary_document` stays the primary document. `same_entry_path` treats `<stem>_htm.xml` and
   `<stem>.htm` in one directory as the same entry, so `pick_accession_row` still separates an
   amendment that reuses its original's file name.

4. **Documents and instance must agree, or the filing fails.** `assert_document_set_complete`
   raises `DocumentSetMismatchError`, stating both counts, when the documents hold fewer fact elements
   (`ix:nonFraction`, `ix:nonNumeric`, `ix:fraction`) than the instance has facts, or when an
   instance fact id is in none of them. Instance facts are the root's element children other than
   `schemaRef`, `context`, `unit`, `footnoteLink`, `roleRef`, `arcroleRef` and `linkbaseRef`. More
   inline facts than instance facts is not a mismatch here: no fact is lost. `_process_one`
   runs the check when the filing has an instance on disk, after the duplicate check and before
   the dry-run branch and Arelle, and returns an `IngestResult` with that error, so the run
   reports a failure. A document the manifest lists that is not on disk fails the same way. A
   single-document filing with no instance on disk loads as today.

5. **The canonical document covers every document.** `merge_canonical_documents` returns one
   document with one `<html>` and one `<body>`, the documents in the given order, each document's
   head dropped after the first. Links of the form `<file>#<id>` to a document in the set become
   `#<id>`. A non-fact id that an earlier document already uses is renamed in the later document,
   and links to it from any document follow. Fact ids are never renamed: the same fact id in two
   documents raises `ValueError`. Output is lxml's serialization, so
   `_element_byte_starts` keeps its one-to-one scan. The result is deterministic.

6. **The canonicalizer merges.** `canonicalize_filing_dir` canonicalizes each document of
   `inline_document_names` with `_canonicalize_html`, merges, and writes `canonical.htm`.
   `canonicalization_meta.json` gains `source_documents`. A listed document that is missing, or
   that fails to canonicalize, sets `CanonResult.error` (never a skip) and writes nothing.
   `--force` rebuilds as today.

7. **Single-document filings do not change.** `canonical.htm`, every offset and every `chunk_id`
   are byte-identical, and the argv Arelle gets is the same. No existing `src.chunking`, DOM parser
   or `_canonicalize_html` code changes.

8. **Selection reads the index only.** `select_filings` matches `report_date` on the existing
   rules (annual: the year of the period end is the fiscal year; 10-Q: also
   `(month - 1) // 3 + 1` is the quarter), for filings filed on or after `filing_date_floor`,
   then applies `choose_filings`. Rows with an empty or missing `report_date` do not match. No
   filing text is fetched: no `period_of_report`, `attachments` or `sgml()`. No match raises
   `LookupError`, as today. `SECFilingDownloader._select_filings` calls it.

9. **The EDGAR read timeout is configured.** `edgar_read_timeout_s` reads
   `XBRLRAG_EDGAR_READ_TIMEOUT_S`, default 120 seconds. Anything but a positive finite number
   raises `ValueError`. `SECFilingDownloader.__init__` applies it with `edgar.configure_http`.

10. **The HTML flow can use the same merge.** `merge_canonical_documents` takes `ix_strip_html`
    output too, unchanged for one document. `detect_sections` then finds a section linked into a
    later document. Wiring its callers is a follow-up (see Notes).

## Acceptance criteria

- **C1**: A hand-built filing with an inline document typed `EX-13` saves that document and no
  plain exhibit, viewer page or graphic; `ixbrl_documents` is primary first in any attachment
  order and has one entry for a single-document filing; for any type label the result is the
  same; `is_inline_xbrl_document` is True on a fact-bearing document as bytes or text, False on a
  document that only mentions the namespace or binds it and tags nothing. Oracle: hand-built
  attachments.
- **C2**: An unreadable inline document raises `RuntimeError` naming it, and leaves no manifest; any
  unreadable HTML attachment does, an unreadable `R9.htm` does not. Oracle: requirement 2.
- **C3**: The entry is the instance for two documents, the primary for one and for a pre-change
  manifest; two documents and no instance raises; `_process_one` and the geography stage hand
  Arelle exactly that path (fake subprocess); `same_entry_path` matches instance to primary in one
  directory and not across `10-K` and `10-K_A`, and `pick_accession_row` picks the right row of
  two unpatched rows with instance entries; the same for any stem. Oracle: hand-built manifests.
- **C4**: The check returns 3 for the fixture; one document of two raises and states 1 and 3; equal
  counts with a foreign id raises naming it; the reported shape (old manifest, main document,
  complete instance) makes `_process_one` return an error with both counts, with no Arelle run,
  in a real and a dry run, in words `_ingest_from_keys` counts as a failure; a listed document
  absent from disk is refused; an ordinary filing passes and loads its primary; any split of ids
  between two documents is complete as a pair and incomplete alone. Oracle: the fixture by eye.
- **C5**: On the merged fixture f-1, f-2 and f-3 each slice to their own element and value, in
  filing order; one `html`, one `body`; the ToC link into the second document makes a section
  holding f-2 and f-3, not f-1; chunks are gap-free from 0 to the end, include the second
  document's prose, and put f-2 in a table chunk under Item 8; a colliding anchor keeps its id in
  the first document and is renamed with every link to it in the second; the same fact id twice
  and an empty list raise; for any split of facts across documents every fact is found once, in
  order, and merging twice gives the same bytes. Oracle: the fixture by eye.
- **C6**: `canonical.htm` equals the merge and the meta names the sources in order; a missing listed
  document is an `error`, not a skip, and writes nothing; a canonical built from the main document
  alone stays until `--force`, which then equals a first build. Oracle: requirement 6.
- **C7**: For a hand-written source and its hand-derived canonical bytes: the bytes match exactly
  through `_canonicalize_html`, the merge and the whole canonicalizer, with old and new manifests;
  chunk ranges come from `bytes.find` on those bytes and chunk ids are
  `uuid5(namespace, "<accession>:<start>:<end>")`; the fact's offsets are the same; for any body,
  one document merges to itself and chunks to the same ids. Oracle: closed form on hand-written
  bytes.
- **C8**: Latest original plus latest later amendment, with no text fetched; a January year end is
  the year it ends in; the quarter comes from the period month; rows with no `report_date` do not
  match, and no match raises; the floor is January 1 of the fiscal year; for any index the result
  equals the rule restated in the test. Oracle: annual-amendment-handling, restated by hand.
- **C9**: Default 120, override 45.5; constructing the downloader sets `read=45, connect=10` on
  edgartools' client settings; `0`, `-1`, `abc`, `nan`, `inf` raise; any positive value reads back.
  Oracle: requirement 9 and edgartools' `configure_http`.
- **C10**: Stripped and merged, the fixture has no XBRL left and `item_8` starts at the second
  document's `<div id="i8">` from the ToC; one stripped document merges to itself. Oracle: the
  fixture by eye.

## Interface

```python
# src/ingest/sec_edgar/document_set.py (new)
class DocumentSetMismatchError(ValueError): ...
inline_document_names(manifest: Mapping[str, Any]) -> list[str]
arelle_entry_name(manifest: Mapping[str, Any]) -> str
assert_document_set_complete(inline_documents: Sequence[bytes], instance: bytes) -> int

# src/ingest/sec_edgar/xbrl/html_canonicalizer.py
merge_canonical_documents(documents: Sequence[tuple[str, bytes]]) -> bytes

# src/ingest/sec_edgar/downloader/sec_filing_downloader.py
is_inline_xbrl_document(payload: bytes | str) -> bool
filing_date_floor(request: FilingRequest) -> date
select_filings(company: Any, request: FilingRequest) -> list[Any]
edgar_read_timeout_s() -> float
```

`manifest.yaml` gains `ixbrl_documents: list[str]`; `canonicalization_meta.json` gains
`source_documents: list[str]`. `XBRLRAG_EDGAR_READ_TIMEOUT_S` goes in `.env.example`. Existing
signatures do not change.

## Notes

**Evidence (2026-10-30, dev corpus: the 155 filings under `data/storage/sec_edgar`).**

Filenames, from each filing's EDGAR `index.json` (155 of 155 fetched, 33 filers): no filing has a
`<stem>_d<n>.htm` document. 23 filings of 5 filers (ABT, AMZN, CEG, LLY, VST) have other HTML files
sharing the primary's stem, 270 in all, and every one is named as an exhibit
(`...x10kxexh311.htm`, `...xex211.htm`). The other 1,676 non-primary HTML names say nothing either
way, so filenames alone would not settle this.

Local reconciliation, no network, settles it: the primary document's fact elements equal the
instance's fact count on 155 of 155 filings, and the id sets are equal. **No dev filing has more
than one inline document, and none is missing facts.** That is also why C4 can be exact rather
than a ratio. TSM's FY2022 20-F has 3,666 facts and only 100 ids, and agrees on both counts.

**A separate defect, not fixed here.** Inline loads record `(ixTransformValueError)` as the value
of facts that use SEC transforms (exchange names, durations, booleans, numbers in words): 7,006 of
268,160 facts in `xbrl_db`. On KO FY2025 an instance load has the real value for all 49. The likely
cause is that Arelle's `transforms/SEC` plugin is not loaded; that is not tested here. Fixing it
changes stored values for every filer and needs its own spec. Until then a multi-document filing
has the right values and its neighbours do not.

**The read timeout.** `period_of_report` calls `Filing.sgml()`, which streams
`/Archives/edgar/data/<cik>/<accession>.txt` through `stream_with_retry`, and `sgml()` re-raises
`httpx.ReadTimeout` rather than falling back to the filing homepage. The 30 seconds is edgartools'
stated default; its `httpx_params` carry no timeout until `configure_http` sets one. From code and
docs alone I could not establish why those two filers stall, and did not fetch their filings. One
observation points away from file size: this change's survey fetched small `index.json` files at
2 requests a second, and `www.sec.gov` stalled 19 times while serving the last 121 of them
(30-second read and TLS-handshake timeouts, one dropped connection), each file arriving within
three attempts. Stalls look intermittent, so removing the per-filing fetches removes most of the
exposure and the longer timeout covers the fetch that remains. Whether a longer timeout outlasts
such a stall or only a retry does is not known.

**The window has no upper bound.** "Latest amendment after the original" has no time limit, so
narrowing by filing date can only cut below: a period ending in year Y cannot be filed before
January 1 of Y. With `report_date` in the index, this is a cheap filter, not a fetch saver. Passing
`filing_date="<floor>:"` to `get_filings` or filtering in Python are both fine.

**`report_date` versus `period_of_report`.** One comes from EDGAR's submissions index and the other
from the filing's text header. On all 155 dev filings the index's `reportDate` equals the
`period_of_report` the manifest recorded (checked against `data.sec.gov/submissions`), so selection
picks the same period on dev. `manifest["period_of_report"]` still reads the property for the
chosen filing, whose text is being fetched anyway.

**Limits of the merge.** Text in a later document that no ToC link reaches falls into the last
section of the document before it, because `build_section_tree` only knows linked anchors.
Facts keep their citations either way. A fact with no `id` in a multi-document filing has no
position, as in a single-document filing today. If such a filing appears, check section paths
after the load.

**HTML flow: follow-up.** `scripts/html10k/build_table_oracle.py` and
`measure_table_accuracy.py` read `manifest["primary_document"]` and pass it to `ix_strip_html`;
nothing else calls it yet, and no dev filing is multi-document. What it needs: for each name in
`inline_document_names`, `ix_strip_html`, then `merge_canonical_documents`. C10 proves that
composition, so the follow-up is a call-site change at the point the flow gets a real ingest
path. Until then a multi-document filing would give the baseline no statements; the oracle
builders' filers should be checked against the document list first.

**Out of scope.** The SEC transforms defect above. A purge for an accession already loaded: the
duplicate check still skips it, so a partial load must be removed first (rollout below). Inline
facts in a filing with no instance. S3 mode beyond what `localize_dir` and the store already do;
every read here goes through them.

**Rollout.**

1. Merge the spec, then the implementation; run `pytest test/spec` (all spec dirs), since this
   touches the download and ingest paths retired specs pin.
2. No dev re-ingest: no dev filing is multi-document (Notes). Old manifests keep working, and C4
   holds on all 155 by the count above.
3. Before relying on it, load one real multi-document filing into a scratch database and confirm
   `facts_with_xmlid = fact_count` in `mv_anchor_coverage`, GATE-A passed, and `fact_dom_position`
   covers the facts. No row of `accession` in `xbrl_db` has an instance as its entry today, and the
   claim under Design rests on Arelle's code and a load without a database.
4. For each filing outside dev that came in partial: remove its rows, or rebuild that database,
   then re-download, `html_canonicalizer --force`, and run the ingest steps of guide 4. A filing
   already in `accession` is skipped, so a re-download alone changes nothing.
5. Going forward C4 refuses any such filing at ingest.
6. Correct the three texts named under Design, and add `ixbrl_documents` to guide 3 and the
   module `CLAUDE.md`.
