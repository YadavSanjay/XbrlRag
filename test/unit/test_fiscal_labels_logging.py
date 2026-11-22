"""A period moved off its DEI shows up at ingest, once, and not once per answer key.

So does a 10-Q placed off its DEI (WARNING) or left on it unplaced (a ``decision`` event).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date

import pytest

from src.eval.keys.core import annual_filing_labels
from src.ingest.sec_edgar import fiscal_labels
from src.ingest.sec_edgar.fiscal_labels import assign_fiscal_labels, label_filings


@pytest.fixture
def logged(caplog) -> Iterator[pytest.LogCaptureFixture]:
    """``caplog`` on the module's logger: ``configs/logging.yaml`` stops ``src`` propagating."""
    logger = logging.getLogger("src.ingest.sec_edgar.fiscal_labels")
    logger.addHandler(caplog.handler)
    yield caplog
    logger.removeHandler(caplog.handler)


def _filing(accession: str, end: date, dei: int | None) -> dict:
    return {"accession_no": accession, "form_type": "10-K", "period_end": end,
            "filed_date": date(end.year, 3, 1), "dei_fiscal_year": dei}


def test_each_moved_period_warns_once(logged):
    filings = [_filing("a", date(2025, 1, 31), 2025), _filing("b", date(2026, 1, 31), 2025)]
    assert assign_fiscal_labels(filings) == {"a": 2025, "b": 2026}
    assert [r.levelname for r in logged.records] == ["WARNING"]
    message = logged.records[0].getMessage()
    assert "2026-01-31" in message and "(b)" in message


def test_a_filing_on_its_own_dei_does_not_warn(logged):
    filings = [_filing("a", date(2025, 1, 31), 2025), _filing("b", date(2026, 1, 31), 2026),
               _filing("c", date(2027, 1, 31), None)]
    assign_fiscal_labels(filings)
    assert logged.records == []


def test_the_moved_period_is_reported_to_the_caller():
    filings = [_filing("a", date(2025, 1, 31), 2025), _filing("b", date(2026, 1, 31), 2025)]
    _labels, moved = label_filings(filings)
    assert [(m.period_end, m.dei, m.label, m.accessions) for m in moved] == [
        (date(2026, 1, 31), 2025, 2026, ("b",))]


def _tenq(accession: str, end: date, dei: int | None) -> dict:
    return {"accession_no": accession, "form_type": "10-Q", "period_end": end,
            "filed_date": end, "dei_fiscal_year": dei}


def test_a_10q_placed_off_its_dei_warns_once_by_name(logged):
    # docs/specs/tenq-fiscal-labels.md, requirement 2: ORCL's Q1 FY2027 tagged 2026.
    filings = [_filing("k", date(2026, 5, 31), 2026),
               _tenq("q", date(2026, 8, 31), 2026)]
    assert assign_fiscal_labels(filings) == {"k": 2026, "q": 2027}
    assert [r.levelname for r in logged.records] == ["WARNING"]
    message = logged.records[0].getMessage()
    assert all(part in message for part in ("q,", "2026-08-31", "2026", "2027"))


def test_a_10q_on_its_dei_does_not_warn(logged):
    filings = [_filing("k", date(2026, 5, 31), 2026), _tenq("q", date(2026, 8, 31), 2027),
               _tenq("n", date(2026, 11, 30), None)]
    assert assign_fiscal_labels(filings) == {"k": 2026, "q": 2027, "n": 2027}
    assert logged.records == []


def test_an_unplaced_10q_is_a_decision_event(monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(fiscal_labels, "log_event",
                        lambda kind, **fields: events.append((kind, fields)))
    filings = [_filing("k", date(2024, 5, 31), 2024), _tenq("far", date(2027, 8, 31), 2028)]
    assert assign_fiscal_labels(filings) == {"k": 2024, "far": 2028}
    assert events == [("decision", {
        "decision": "tenq_unplaced_dei_fallback", "source": "fiscal_labels",
        "accession_no": "far", "period_end": "2027-08-31", "dei_fiscal_year": 2028,
        "fiscal_year": 2028})]
    # The key path reports nothing: label_filings never emits.
    events.clear()
    label_filings(filings)
    assert events == []


def test_the_key_path_does_not_warn(logged):
    def entry(start: str, end: str, accn: str, filed: str) -> dict:
        return {"start": start, "end": end, "val": 1.0, "accn": accn, "fy": 2025, "fp": "FY",
                "form": "10-K", "filed": filed}

    doc = {"cik": 1, "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        entry("2024-02-01", "2025-01-31", "a", "2025-03-05"),
        entry("2025-02-01", "2026-01-31", "b", "2026-03-02"),
    ]}}}}}
    assert annual_filing_labels(doc) == {"a": 2025, "b": 2026}
    assert logged.records == []
