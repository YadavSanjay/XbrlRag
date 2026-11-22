"""Fixtures for the ingest-multidoc-ixbrl spec tests.

Spec: docs/specs/ingest-multidoc-ixbrl.md. Every document here is synthetic, written by hand for
a made-up filer, Acme. The shape follows the defect: a small main document that carries the header
and links to Item 8, and a second document that holds the statements. Session-scoped where
hypothesis tests use them.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

MAIN = "acme-20251231.htm"
SECOND = "acme-20251231_d2.htm"
INSTANCE = "acme-20251231_htm.xml"
ACCESSION = "0000000001-26-000001"

_NAMESPACES = (
    'xmlns="http://www.w3.org/1999/xhtml" '
    'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
    'xmlns:xbrli="http://www.xbrl.org/2003/instance" '
    'xmlns:link="http://www.xbrl.org/2003/linkbase" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" '
    'xmlns:iso4217="http://www.xbrl.org/2003/iso4217" '
    'xmlns:dei="http://xbrl.sec.gov/dei/2025" '
    'xmlns:us-gaap="http://fasb.org/us-gaap/2025"'
)

_HEADER = (
    '<div style="display:none"><ix:header><ix:references>'
    '<link:schemaRef xlink:type="simple" xlink:href="acme-20251231.xsd"/>'
    "</ix:references><ix:resources>"
    '<xbrli:context id="c-1"><xbrli:entity>'
    '<xbrli:identifier scheme="http://www.sec.gov/CIK">0000000001</xbrli:identifier>'
    "</xbrli:entity><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period>"
    "</xbrli:context>"
    '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
    "</ix:resources></ix:header></div>"
)


def _page(body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<html {_NAMESPACES}><head><title>Acme</title></head><body>{body}</body></html>"
    ).encode()


def _number(fact_id: str, value: str = "1,234", concept: str = "us-gaap:Assets") -> str:
    return (
        f'<ix:nonFraction id="{fact_id}" name="{concept}" contextRef="c-1" unitRef="usd" '
        f'decimals="0">{value}</ix:nonFraction>'
    )


MAIN_BYTES = _page(
    _HEADER + "<table>"
    '<tr><td><a href="#item1">Item 1. Business</a></td></tr>'
    f'<tr><td><a href="{SECOND}#i8">Item 8. Financial Statements</a></td></tr>'
    "</table>"
    '<div id="item1"><div>Item 1. Business</div>'
    "<p>Acme makes anvils under the name "
    '<ix:nonNumeric id="f-1" name="dei:EntityRegistrantName" contextRef="c-1">Acme Corp'
    "</ix:nonNumeric>.</p></div>"
)

SECOND_BYTES = _page(
    '<div id="i8"><div>Item 8. Financial Statements</div></div>'
    "<p>Inventories are stated at the lower of cost or market.</p>"
    f"<table><tr><td>Total assets</td><td>{_number('f-2')}</td></tr></table>"
    f"<p>Liabilities were {_number('f-3', '567', 'us-gaap:Liabilities')} at year end.</p>"
)

INSTANCE_BYTES = (
    b'<?xml version="1.0" encoding="utf-8"?>\n'
    b'<xbrl xmlns="http://www.xbrl.org/2003/instance" '
    b'xmlns:dei="http://xbrl.sec.gov/dei/2025" '
    b'xmlns:iso4217="http://www.xbrl.org/2003/iso4217" '
    b'xmlns:link="http://www.xbrl.org/2003/linkbase" '
    b'xmlns:us-gaap="http://fasb.org/us-gaap/2025" '
    b'xmlns:xlink="http://www.w3.org/1999/xlink">'
    b'<link:schemaRef xlink:href="acme-20251231.xsd" xlink:type="simple"/>'
    b'<context id="c-1"><entity><identifier scheme="http://www.sec.gov/CIK">0000000001'
    b"</identifier></entity><period><instant>2025-12-31</instant></period></context>"
    b'<unit id="usd"><measure>iso4217:USD</measure></unit>'
    b'<dei:EntityRegistrantName contextRef="c-1" id="f-1">Acme Corp</dei:EntityRegistrantName>'
    b'<us-gaap:Assets contextRef="c-1" decimals="0" id="f-2" unitRef="usd">1234</us-gaap:Assets>'
    b'<us-gaap:Liabilities contextRef="c-1" decimals="0" id="f-3" unitRef="usd">567'
    b"</us-gaap:Liabilities>"
    b"</xbrl>"
)


def _instance_with(fact_ids: Sequence[str]) -> bytes:
    facts = "".join(
        f'<us-gaap:Assets contextRef="c-1" decimals="0" id="{i}" unitRef="usd">1</us-gaap:Assets>'
        for i in fact_ids
    )
    return (
        '<xbrl xmlns="http://www.xbrl.org/2003/instance" '
        'xmlns:us-gaap="http://fasb.org/us-gaap/2025">'
        '<context id="c-1"/><unit id="usd"/>'
        f"{facts}</xbrl>"
    ).encode()


def _document_with(fact_ids: Sequence[str]) -> bytes:
    cells = "".join(f"<p>{_number(i)}</p>" for i in fact_ids)
    return _page(f"<div>Words before the facts.</div>{cells}")


def _manifest(**overrides: Any) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "ticker": "ACME",
        "filing_type": "10-K",
        "form": "10-K",
        "is_amendment": False,
        "fiscal_year": 2025,
        "quarter": None,
        "accession_no": ACCESSION,
        "cik": 1,
        "filing_date": "2026-02-20",
        "primary_document": MAIN,
        "ixbrl_documents": [MAIN, SECOND],
        "files": sorted([INSTANCE, MAIN, SECOND, "acme-20251231.xsd"]),
    }
    manifest.update(overrides)
    return manifest


def _write_filing(
    filing_dir: Path, files: Mapping[str, bytes], manifest: Mapping[str, Any]
) -> Path:
    filing_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (filing_dir / name).write_bytes(data)
    (filing_dir / "manifest.yaml").write_text(yaml.safe_dump(dict(manifest)), encoding="utf-8")
    return filing_dir


class _FakeCursor:
    """A database that holds no filing: every query returns no rows."""

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, *_: object) -> None:
        return None

    def fetchall(self) -> list[Any]:
        return []


class _FakeConnection:
    def cursor(self) -> _FakeCursor:
        return _FakeCursor()

    def rollback(self) -> None:
        return None


@pytest.fixture(scope="session")
def acme() -> SimpleNamespace:
    """The synthetic filing and the helpers that build variations of it."""
    return SimpleNamespace(
        main=MAIN,
        second=SECOND,
        instance=INSTANCE,
        accession=ACCESSION,
        main_bytes=MAIN_BYTES,
        second_bytes=SECOND_BYTES,
        instance_bytes=INSTANCE_BYTES,
        page=_page,
        number=_number,
        instance_with=_instance_with,
        document_with=_document_with,
        manifest=_manifest,
        write_filing=_write_filing,
    )


@pytest.fixture
def no_filing_connection() -> _FakeConnection:
    """A connection to a database that has none of these filings."""
    return _FakeConnection()


@pytest.fixture
def arelle_runs(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Fakes the Arelle subprocess, the I/O boundary, and records each argv."""
    runs: list[list[str]] = []

    class FakePopen:
        def __init__(self, args: Sequence[str], **_: object) -> None:
            runs.append(list(args))
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")
            self.returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def kill(self) -> None:
            return None

    monkeypatch.setattr("subprocess.Popen", FakePopen)
    return runs


@pytest.fixture
def run_file_argument() -> Callable[[list[str]], str]:
    """Reads the ``-f`` value of a recorded Arelle argv."""
    return lambda argv: argv[argv.index("-f") + 1]
