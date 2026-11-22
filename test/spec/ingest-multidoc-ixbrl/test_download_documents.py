"""C1, C2: every inline document of a filing is saved and listed, or the download fails."""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.downloader.sec_filing_downloader import (
    FilingRequest,
    SECFilingDownloader,
    is_inline_xbrl_document,
)

REQUEST = FilingRequest(ticker="ACME", filing_type="10-K", fiscal_year=2025)
PLAIN_EXHIBIT = b"<html><body><p>Certification of the chief executive.</p></body></html>"


class Attachment:
    """What the downloader reads of an edgartools attachment."""

    def __init__(self, document: str, document_type: str, payload: bytes | str | None) -> None:
        self.document = document
        self.document_type = document_type
        self._payload = payload

    def download(self) -> bytes | str:
        if self._payload is None:
            raise OSError("connection reset")
        return self._payload


def _filing(acme: SimpleNamespace, attachments: list[Attachment]) -> SimpleNamespace:
    return SimpleNamespace(
        form="10-K",
        filing_date="2026-02-20",
        accession_no=acme.accession,
        cik=1,
        primary_document=acme.main,
        period_of_report="2025-12-31",
        attachments=attachments,
    )


def _attachments(acme: SimpleNamespace, second_type: str = "EX-13") -> list[Attachment]:
    return [
        Attachment(acme.main, "10-K", acme.main_bytes),
        Attachment(acme.second, second_type, acme.second_bytes),
        Attachment("ex311.htm", "EX-31.1", PLAIN_EXHIBIT),
        Attachment("R1.htm", "XML", b"<html><body>Cover</body></html>"),
        Attachment("logo.jpg", "GRAPHIC", b"\xff\xd8\xff"),
        Attachment("acme-20251231.xsd", "EX-101.SCH", b"<schema/>"),
        Attachment(acme.instance, "XML", acme.instance_bytes),
    ]


def _download(root: Path, filing: SimpleNamespace) -> Path:
    downloader = SECFilingDownloader(output_root=root, identity="Spec Test spec@example.com")
    return downloader._download_filing(REQUEST, filing)


def _manifest(filing_dir: Path) -> dict:
    return yaml.safe_load((filing_dir / "manifest.yaml").read_text(encoding="utf-8"))


@pytest.mark.criterion("C1")
def test_second_inline_document_typed_as_an_exhibit_is_saved(acme, tmp_path):
    # oracle: the defect — a second document whose EDGAR type is neither the form nor a linkbase
    # suffix was dropped. Hand-built filing: two inline documents, one plain exhibit
    filing_dir = _download(tmp_path, _filing(acme, _attachments(acme)))
    assert sorted(p.name for p in filing_dir.iterdir()) == sorted(
        [acme.main, acme.second, acme.instance, "acme-20251231.xsd", "manifest.yaml"]
    )
    assert (filing_dir / acme.second).read_bytes() == acme.second_bytes


@pytest.mark.criterion("C1")
def test_manifest_lists_the_documents_primary_first(acme, tmp_path):
    # oracle: requirement 1 — primary first, then the order EDGAR lists them, whatever that order
    attachments = _attachments(acme)
    attachments[0], attachments[1] = attachments[1], attachments[0]
    filing_dir = _download(tmp_path, _filing(acme, attachments))
    manifest = _manifest(filing_dir)
    assert manifest.get("ixbrl_documents") == [acme.main, acme.second]
    assert manifest["primary_document"] == acme.main
    assert acme.second in manifest["files"]


@pytest.mark.criterion("C1")
def test_single_document_filing_lists_one_document(acme, tmp_path):
    # oracle: requirement 1 — an ordinary filing lists its primary document alone
    attachments = [a for a in _attachments(acme) if a.document != acme.second]
    filing_dir = _download(tmp_path, _filing(acme, attachments))
    assert _manifest(filing_dir).get("ixbrl_documents") == [acme.main]


@pytest.mark.criterion("C1")
def test_is_inline_xbrl_document_reads_the_content(acme):
    # oracle: inline XBRL 1.1 — a fact element in the inline namespace; a document that only
    # mentions the namespace in a sentence, or binds it and tags nothing, holds no fact
    assert is_inline_xbrl_document(acme.second_bytes) is True
    assert is_inline_xbrl_document(acme.second_bytes.decode()) is True
    assert is_inline_xbrl_document(PLAIN_EXHIBIT) is False
    assert is_inline_xbrl_document(acme.page("<p>No tags at all.</p>")) is False
    mentions = b"<html><body>http://www.xbrl.org/2013/inlineXBRL has ix:nonFraction.</body></html>"
    assert is_inline_xbrl_document(mentions) is False


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(st.sampled_from(["EX-13", "EX-99.1", "EX-101.INS", "XML", "10-K", "", "OTHER"]))
def test_edgar_type_label_never_decides(acme, label):
    # oracle: metamorphic — the second document's type label changes nothing about what is kept
    with tempfile.TemporaryDirectory() as tmp:
        filing_dir = _download(Path(tmp), _filing(acme, _attachments(acme, second_type=label)))
        kept = sorted(p.name for p in filing_dir.glob("*.htm"))
        listed = _manifest(filing_dir).get("ixbrl_documents")
        assert (kept, listed) == (sorted([acme.main, acme.second]), [acme.main, acme.second])


@pytest.mark.criterion("C2")
def test_unreadable_inline_document_fails_the_download(acme, tmp_path):
    # oracle: requirement 2 — a document that cannot be read may be the one with the statements,
    # so it cannot be skipped; the old code logged and carried on
    attachments = _attachments(acme)
    attachments[1] = Attachment(acme.second, "EX-13", None)
    downloader = SECFilingDownloader(
        output_root=tmp_path, identity="Spec Test spec@example.com", max_attempts=1
    )
    with pytest.raises(RuntimeError, match="acme-20251231_d2"):
        downloader._download_filing(REQUEST, _filing(acme, attachments))
    assert not list(tmp_path.rglob("manifest.yaml"))


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(st.sampled_from(["ex311.htm", "other.html", "acme-20251231_d3.htm"]))
def test_any_unreadable_html_attachment_fails_but_a_viewer_page_does_not(acme, name):
    # oracle: metamorphic — without its bytes an HTML attachment cannot be ruled out, whatever its
    # name; a viewer page (R<n>.htm) is EDGAR's rendering, never a filed document
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        downloader = SECFilingDownloader(
            output_root=root, identity="Spec Test spec@example.com", max_attempts=1
        )
        failing = _attachments(acme) + [Attachment(name, "EX-99", None)]
        with pytest.raises(RuntimeError, match=name.split(".")[0]):
            downloader._download_filing(REQUEST, _filing(acme, failing))
        tolerated = _attachments(acme) + [Attachment("R9.htm", "XML", None)]
        filing_dir = downloader._download_filing(REQUEST, _filing(acme, tolerated))
        assert _manifest(filing_dir).get("ixbrl_documents") == [acme.main, acme.second]
