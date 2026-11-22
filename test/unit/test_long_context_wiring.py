"""Wiring for the long-context arm that the locked spec tests don't reach.

docs/specs/long-context-arm.md: the Postgres ``FilingResolver`` (its SQL mapping, with
a fake DB-API connection at the database boundary), the ``entry_url`` → store-key
mapping, ``filing_text`` on real-shaped input (an XML declaration, an HTML entity),
the prompt templates, and ``run_suite.py --model``. No network, database or LLM.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from prompts.prompt_registry import get_xbrl_registry
from src.eval.systems.long_context import (
    ANNUAL_FORMS,
    CACHE_POINT_MARKER,
    PostgresFilingResolver,
    canonical_key_from_entry_url,
    filing_text,
    split_user_template,
)

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval" / "run_suite.py"
_WIN_ROOT = "D:/Work/Repos/XbrlRag/data/storage/sec_edgar"


# --------------------------------------------------------------------------------
# Fake DB-API connection (the database boundary)
# --------------------------------------------------------------------------------


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple) -> None:
        self._conn.executed.append((sql, params))

    def fetchall(self) -> list[tuple]:
        return list(self._conn.rows)


class _Conn:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows
        self.executed: list[tuple[str, tuple]] = []
        self.closed = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def close(self) -> None:
        self.closed = True


def _resolver(rows: list[tuple]) -> tuple[PostgresFilingResolver, _Conn]:
    conn = _Conn(rows)
    return PostgresFilingResolver(connect=lambda: conn, sec_edgar_root=_WIN_ROOT), conn


# --------------------------------------------------------------------------------
# PostgresFilingResolver
# --------------------------------------------------------------------------------


def test_resolver_binds_ticker_year_and_annual_forms_as_parameters():
    resolver, conn = _resolver([])
    assert resolver.canonical_key("AAPL", 2024) is None
    (sql, params), = conn.executed
    assert params == ("AAPL", 2024, list(ANNUAL_FORMS))
    assert "AAPL" not in sql and "2024" not in sql
    for clause in ("em.cik = fr.cik", "em.trading_symbol = %s", "fr.fiscal_year = %s",
                   "fr.fiscal_period = 'FY'", "fr.superseded_by IS NULL",
                   "fr.form_type = ANY(%s)"):
        assert clause in sql
    assert conn.closed


def test_resolver_maps_the_single_row_to_its_canonical_key():
    resolver, _ = _resolver(
        [("0000320193-24-000123", "10-K",
          "D:\\Work\\Repos\\XbrlRag\\data\\storage\\sec_edgar\\AAPL\\2024\\10-K\\aapl-20240928.htm")]
    )
    assert resolver.canonical_key("AAPL", 2024) == "AAPL/2024/10-K/canonical.htm"


def test_resolver_maps_a_restatement_to_its_amendment_directory():
    resolver, _ = _resolver(
        [("0000002488-26-000010", "10-K/A",
          "D:\\Work\\Repos\\XbrlRag\\data\\storage\\sec_edgar\\AMD\\2025\\10-K_A\\amd-20251227.htm")]
    )
    assert resolver.canonical_key("AMD", 2025) == "AMD/2025/10-K_A/canonical.htm"


def test_resolver_refuses_to_guess_between_two_canonical_filings():
    resolver, _ = _resolver(
        [("a-1", "10-K", f"{_WIN_ROOT}/XYZ/2024/10-K/x.htm"),
         ("a-2", "20-F", f"{_WIN_ROOT}/XYZ/2024/20-F/x.htm")]
    )
    assert resolver.canonical_key("XYZ", 2024) is None


def test_resolver_closes_the_connection_when_the_query_fails():
    class _Boom(_Conn):
        def cursor(self):
            raise RuntimeError("db down")

    conn = _Boom([])
    resolver = PostgresFilingResolver(connect=lambda: conn, sec_edgar_root=_WIN_ROOT)
    with pytest.raises(RuntimeError):
        resolver.canonical_key("AAPL", 2024)
    assert conn.closed


@pytest.mark.parametrize(
    "entry_url, root, expected",
    [
        (f"{_WIN_ROOT}/TSM/2024/20-F/tsm-20241231.htm", _WIN_ROOT, "TSM/2024/20-F/canonical.htm"),
        # Windows paths compare case-insensitively and with either separator.
        ("d:\\work\\repos\\XbrlRag\\data\\storage\\sec_edgar\\MU\\2025\\10-K\\mu.htm",
         _WIN_ROOT + "/", "MU/2025/10-K/canonical.htm"),
        ("/data/sec_edgar/NVDA/2026/10-K/nvda.htm", "/data/sec_edgar",
         "NVDA/2026/10-K/canonical.htm"),
        ("/data/other/NVDA/2026/10-K/nvda.htm", "/data/sec_edgar", None),
        ("/data/sec_edgar/nvda.htm", "/data/sec_edgar", None),
        (None, "/data/sec_edgar", None),
        ("", "/data/sec_edgar", None),
    ],
)
def test_entry_url_maps_under_the_download_root(entry_url, root, expected):
    assert canonical_key_from_entry_url(entry_url, root) == expected


# --------------------------------------------------------------------------------
# filing_text on real-shaped input
# --------------------------------------------------------------------------------


def test_filing_text_accepts_an_xml_declaration():
    html = (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Net sales</p></body></html>'
    )
    assert filing_text(html) == "Net sales"


def test_filing_text_falls_back_to_the_html_parser():
    # &nbsp; is not an XML entity, so the XML parse fails and the HTML parser takes over
    html = "<html><body><p>Net&nbsp;sales</p><table><tr><td>A</td><td>1</td></tr></table>"
    assert filing_text(html) == "Net sales\nA | 1"


def test_filing_text_drops_empty_cells_and_keeps_block_cells_on_the_row():
    html = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><table>'
        "<tr><td><p>Revenue</p></td><td> </td><td>$</td><td><span>391,035</span></td></tr>"
        "</table></body></html>"
    )
    assert filing_text(html) == "Revenue | $ | 391,035"


# --------------------------------------------------------------------------------
# Prompt templates
# --------------------------------------------------------------------------------


def test_long_context_templates_take_filing_then_question():
    prompt = get_xbrl_registry().resolve("long_context")
    assert prompt.version == "v2"
    assert prompt.user.variables == {"filing", "question"}
    assert prompt.system.variables == set()
    assert prompt.user.template.index("{filing}") < prompt.user.template.index("{question}")
    # v2 splits the two at the cache-point marker line.
    filing, question = split_user_template(prompt.user)
    assert filing.variables == {"filing"} and question.variables == {"question"}
    assert CACHE_POINT_MARKER not in filing.template + question.template
    system = prompt.system.format()
    for key in ('"value"', '"unit"', '"rows"', '"refused"', '"hedged"', '"cited_chunks"'):
        assert key in system
    assert "{{" not in system


# --------------------------------------------------------------------------------
# run_suite.py --model
# --------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def run_suite():
    spec = importlib.util.spec_from_file_location("run_suite_lc_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_suite_lc_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["xbrlrag_full", "closed_book", "naive_rag_plain"])
def test_run_suite_rejects_model_for_other_systems(run_suite, name):
    with pytest.raises(SystemExit):
        run_suite.main(["--system", name, "--model", "gpt-5", "--no-store"])


def test_run_suite_records_the_long_context_model(run_suite, tmp_path, monkeypatch):
    # The DB points at a closed port, so the resolver fails fast and the case becomes an
    # error answer; no LLM call is allowed either way.
    monkeypatch.setenv("XBRLRAG_RUN_MAX_LLM_CALLS", "0")
    monkeypatch.setenv("XBRL_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("XBRL_DB_PORT", "9")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-unit-offline")
    suite = tmp_path / "suite.json"
    suite.write_text(
        json.dumps(
            {
                "name": "unit",
                "version": "v1",
                "cases": [{"id": "c1", "question": "What was AAA revenue in 2024?",
                           "expected_value": 1.0, "ticker": "AAA", "fy": 2024}],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "report.json"

    rc = run_suite.main(
        ["--suite-path", str(suite), "--system", "long_context", "--model", "gpt-5-mini",
         "--no-store", "--workers", "1", "--out", str(out)]
    )

    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["system"] == "long_context_oracle"
    assert report["config"]["model"] == "gpt-5-mini"
