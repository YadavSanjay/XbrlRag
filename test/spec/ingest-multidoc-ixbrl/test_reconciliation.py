"""C4: a filing whose saved documents and instance disagree fails loudly, before Arelle runs."""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.document_set import DocumentSetMismatchError, assert_document_set_complete
from src.ingest.sec_edgar.ingest.arelle_xbrl_ingestor import ArelleXBRLIngestor

DB = {"host": "h", "port": 5432, "user": "u", "password": "p", "dbname": "d"}
REL_KEY = "ACME/2025/10-K"


def _ingestor(root) -> ArelleXBRLIngestor:
    return ArelleXBRLIngestor(
        arelle_cmd="arelleCmdLine", sec_root=root, db_conn_params=DB, persist_audit=False
    )


@pytest.mark.criterion("C4")
def test_complete_set_returns_the_instance_fact_count(acme):
    # oracle: the fixture by eye — f-1 in the main document, f-2 and f-3 in the second, and those
    # three as the only facts of the instance (schemaRef, context and unit are not facts)
    documents = [acme.main_bytes, acme.second_bytes]
    assert assert_document_set_complete(documents, acme.instance_bytes) == 3


@pytest.mark.criterion("C4")
def test_missing_document_is_a_mismatch_that_states_the_counts(acme):
    # oracle: the defect in miniature — 1 fact saved of the 3 the instance holds
    with pytest.raises(DocumentSetMismatchError) as caught:
        assert_document_set_complete([acme.main_bytes], acme.instance_bytes)
    message = str(caught.value)
    assert re.search(r"\b1\b", message) and re.search(r"\b3\b", message)


@pytest.mark.criterion("C4")
def test_equal_counts_with_a_foreign_id_is_a_mismatch(acme):
    # oracle: requirement 4 — citations join on ids, so one id the DOM lacks is a broken citation
    with pytest.raises(DocumentSetMismatchError, match="f-9"):
        assert_document_set_complete(
            [acme.document_with(["f-1", "f-2"])], acme.instance_with(["f-1", "f-9"])
        )


@pytest.mark.criterion("C4")
def test_ingest_refuses_the_reported_filing_before_arelle_runs(
    acme, tmp_path, no_filing_connection, arelle_runs
):
    # oracle: the defect — a manifest from before this change, the main document alone on disk, a
    # complete instance beside it. The run must fail, not load the main document's facts
    manifest = acme.manifest(files=[acme.main, acme.instance])
    del manifest["ixbrl_documents"]
    filing_dir = acme.write_filing(
        tmp_path / REL_KEY,
        {acme.main: acme.main_bytes, acme.instance: acme.instance_bytes},
        manifest,
    )
    result = _ingestor(tmp_path)._process_one(filing_dir, REL_KEY, no_filing_connection, False)
    assert arelle_runs == []
    assert result.success is False
    assert re.search(r"\b1\b", result.error) and re.search(r"\b3\b", result.error)
    # _ingest_from_keys counts an error with these words as a skip, not as a failure
    assert "no manifest" not in result.error.lower()
    assert "unsupported" not in result.error.lower()
    # the same refusal shows in a dry run
    dry = _ingestor(tmp_path)._process_one(filing_dir, REL_KEY, no_filing_connection, True)
    assert dry.error == result.error
    assert dry.dry_run is False


@pytest.mark.criterion("C4")
def test_listed_document_missing_from_disk_is_refused(
    acme, tmp_path, no_filing_connection, arelle_runs
):
    # oracle: requirement 4 — a manifest that names a document the directory lacks is incomplete
    filing_dir = acme.write_filing(
        tmp_path / REL_KEY,
        {acme.main: acme.main_bytes, acme.instance: acme.instance_bytes},
        acme.manifest(),
    )
    result = _ingestor(tmp_path)._process_one(filing_dir, REL_KEY, no_filing_connection, False)
    assert arelle_runs == []
    assert result.success is False
    assert acme.second in result.error


@pytest.mark.criterion("C4")
def test_ordinary_filing_passes_and_loads_as_before(
    acme, tmp_path, no_filing_connection, arelle_runs, run_file_argument
):
    # oracle: requirement 4 and 7 — inline and instance agree on one document, as on all 155 dev
    # filings, so the argv is the primary document's
    document = acme.document_with(["f-1", "f-2"])
    assert assert_document_set_complete([document], acme.instance_with(["f-1", "f-2"])) == 2
    filing_dir = acme.write_filing(
        tmp_path / REL_KEY,
        {acme.main: document, acme.instance: acme.instance_with(["f-1", "f-2"])},
        acme.manifest(ixbrl_documents=[acme.main], files=[acme.main, acme.instance]),
    )
    result = _ingestor(tmp_path)._process_one(filing_dir, REL_KEY, no_filing_connection, False)
    assert result.error is None and result.success is True
    assert [run_file_argument(argv) for argv in arelle_runs] == [str(filing_dir / acme.main)]


ids = st.lists(st.from_regex(r"f-[0-9]{1,4}", fullmatch=True), min_size=2, max_size=8, unique=True)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(ids, st.integers(min_value=1, max_value=7))
def test_dropping_a_document_is_always_caught(acme, fact_ids, cut):
    # oracle: metamorphic — however the facts split between two documents, the pair is complete
    # and either document alone is not
    cut = min(cut, len(fact_ids) - 1)
    first, second = acme.document_with(fact_ids[:cut]), acme.document_with(fact_ids[cut:])
    instance = acme.instance_with(fact_ids)
    assert assert_document_set_complete([first, second], instance) == len(fact_ids)
    assert assert_document_set_complete([second, first], instance) == len(fact_ids)
    for alone in (first, second):
        with pytest.raises(DocumentSetMismatchError):
            assert_document_set_complete([alone], instance)
