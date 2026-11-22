"""C3: every Arelle stage loads the file that holds all of a filing's facts."""

from __future__ import annotations

from pathlib import PureWindowsPath

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.document_set import arelle_entry_name, inline_document_names
from src.ingest.sec_edgar.ingest.arelle_xbrl_ingestor import (
    ArelleXBRLIngestor,
    pick_accession_row,
    same_entry_path,
)
from src.ingest.util.generate_geography_normalization import _collect_geo_members_from_filings

DB = {"host": "h", "port": 5432, "user": "u", "password": "p", "dbname": "d"}
REL_KEY = "ACME/2025/10-K"


def _ingestor(root) -> ArelleXBRLIngestor:
    return ArelleXBRLIngestor(
        arelle_cmd="arelleCmdLine", sec_root=root, db_conn_params=DB, persist_audit=False
    )


def _all_files(acme) -> dict[str, bytes]:
    return {
        acme.main: acme.main_bytes,
        acme.second: acme.second_bytes,
        acme.instance: acme.instance_bytes,
    }


@pytest.mark.criterion("C3")
def test_document_names_and_entry_of_a_multi_document_filing(acme):
    # oracle: requirement 3 — the instance is the one file holding every fact of the filing
    manifest = acme.manifest()
    assert inline_document_names(manifest) == [acme.main, acme.second]
    assert arelle_entry_name(manifest) == acme.instance


@pytest.mark.criterion("C3")
def test_manifest_from_before_this_change_keeps_its_primary_document(acme):
    # oracle: requirement 3 — the 155 dev manifests have no ixbrl_documents key and stay as loaded
    manifest = acme.manifest(files=[acme.main, acme.instance])
    del manifest["ixbrl_documents"]
    assert inline_document_names(manifest) == [acme.main]
    assert arelle_entry_name(manifest) == acme.main


@pytest.mark.criterion("C3")
def test_multi_document_filing_without_an_instance_is_refused(acme):
    # oracle: requirement 3 — no file holds all the facts, and loading the primary is the defect
    with pytest.raises(ValueError, match="instance"):
        arelle_entry_name(acme.manifest(files=[acme.main, acme.second]))


@pytest.mark.criterion("C3")
def test_ingest_gives_arelle_the_instance(acme, tmp_path, no_filing_connection, arelle_runs,
                                          run_file_argument):
    # oracle: requirement 3 — the argv Arelle receives for the hand-built two-document filing
    filing_dir = acme.write_filing(tmp_path / REL_KEY, _all_files(acme), acme.manifest())
    result = _ingestor(tmp_path)._process_one(filing_dir, REL_KEY, no_filing_connection, False)
    assert result.error is None and result.success is True
    assert result.primary_document == acme.main
    assert [run_file_argument(argv) for argv in arelle_runs] == [str(filing_dir / acme.instance)]


@pytest.mark.criterion("C3")
def test_geography_stage_reads_the_instance(acme, tmp_path, arelle_runs, run_file_argument):
    # oracle: requirement 3 — this stage walks contexts, which the primary document alone may not
    # hold. The fake Arelle writes no output, so the stage reports a failure and collects nothing
    filing_dir = acme.write_filing(tmp_path / REL_KEY, _all_files(acme), acme.manifest())
    assert _collect_geo_members_from_filings(tmp_path, "arelleCmdLine") == []
    assert [run_file_argument(argv) for argv in arelle_runs] == [str(filing_dir / acme.instance)]


@pytest.mark.criterion("C3")
def test_instance_and_primary_of_one_filing_are_the_same_entry():
    # oracle: requirement 3 — entry_url is the instance while the manifest names the primary. The
    # amendment-reuses-the-file-name case (ingest-accession-identity) must still tell them apart
    ten_k = "D:/sec/AMD/2025/10-K/amd-20251227"
    ten_k_a = "D:/sec/AMD/2025/10-K_A/amd-20251227"
    assert same_entry_path(f"{ten_k}_htm.xml", f"{ten_k}.htm") is True
    assert same_entry_path(f"{ten_k_a}_htm.xml", f"{ten_k}.htm") is False
    rows = [(7, f"{ten_k_a}_htm.xml", "123"), (8, f"{ten_k}_htm.xml", "456")]
    assert pick_accession_row("0000002488-26-000018", f"{ten_k}.htm", rows) == 8


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(st.lists(st.from_regex(r"[a-z0-9-]{1,8}", fullmatch=True), min_size=2, max_size=4))
def test_entry_names_for_any_filer(acme, parts):
    # oracle: metamorphic — the rule depends on how many documents there are, not on their names;
    # the instance of a filing resolves to the same entry as its primary document
    stem = "-".join(parts)
    primary, instance = f"{stem}.htm", f"{stem}_htm.xml"
    single = acme.manifest(
        primary_document=primary, ixbrl_documents=[primary], files=[primary, instance]
    )
    double = acme.manifest(
        primary_document=primary,
        ixbrl_documents=[primary, f"{stem}_d2.htm"],
        files=[f"{stem}_d2.htm", primary, instance],
    )
    assert arelle_entry_name(single) == primary
    assert arelle_entry_name(double) == instance
    root = PureWindowsPath("D:/sec/ACME/2025/10-K")
    assert same_entry_path(str(root / instance), str(root / primary)) is True
