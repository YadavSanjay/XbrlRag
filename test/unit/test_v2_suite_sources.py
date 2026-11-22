"""Unit tests for the v2 suite build's production seams and the branches its spec tests skip.

Covers src/eval/suites/v2_sources.py (inventory rows, concept and unit mapping, the
Postgres fact-store source over a fake DB-API connection, the refusing transport),
the CLI's group loader, and v2_suite.py caller errors and per-CIK fetching. The database is
faked at the cursor; every expected value is read off the rows built here.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

from src.eval.keys.base import SecSource
from src.eval.keys.core import KeyNotFoundError
from src.eval.suites.intents import Intent, Lexicon
from src.eval.suites.v2_sources import (
    CacheOnlyError,
    PostgresFactStoreSource,
    RefusingTransport,
    companyfacts_concept,
    companyfacts_unit,
    inventory_from_rows,
    read_corpus_inventory,
    restrict_to_lexicon,
)
from src.eval.suites.v2_suite import (
    IntentDroppedError,
    build_v2_suites,
    key_intent,
)

REPO = Path(__file__).resolve().parents[2]
US = "http://fasb.org/us-gaap/2024"


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn
        self._rows: list[tuple] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple) -> None:
        self._conn.calls.append((sql, params))
        self._rows = self._conn.rows_for(params)

    def fetchall(self) -> list[tuple]:
        return list(self._rows)


class _Conn:
    """A DB-API connection serving fixed rows per parameter tuple; records each query."""

    def __init__(self, rows: dict[tuple, list[tuple]] | list[tuple]) -> None:
        self._rows = rows
        self.calls: list[tuple] = []

    def rows_for(self, params: tuple) -> list[tuple]:
        if isinstance(self._rows, list):
            return self._rows
        return self._rows.get(tuple(params), [])

    def cursor(self) -> _Cursor:
        return _Cursor(self)


def test_concept_and_unit_mapping():
    assert companyfacts_concept(f"{US}:Revenues") == "us-gaap:Revenues"
    assert companyfacts_concept("https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full:Revenue") \
        == "ifrs-full:Revenue"
    assert companyfacts_concept("http://www.apple.com/20240928:Extension") is None
    assert companyfacts_concept("NoColon") is None
    assert companyfacts_unit("shares*USD") == "USD/shares"
    assert companyfacts_unit("TWD*shares") == "TWD/shares"
    assert companyfacts_unit("USD") == "USD"
    assert companyfacts_unit("pure") == "pure"


def test_inventory_from_rows_maps_and_skips_extensions():
    rows = [
        ("aapl", "320193", 2024, "Revenue", f"{US}:Revenues", "USD"),
        ("AAPL", "0000320193", 2024, "EarningsPerShareBasic", f"{US}:EarningsPerShareBasic",
         "shares*USD"),
        ("AAPL", "0000320193", 2023, "Custom", "http://www.apple.com/2024:Thing", "USD"),
    ]
    inv = inventory_from_rows(rows)
    assert inv == {"AAPL": {"cik": "0000320193", "years": {2024: {
        "Revenue": {"concept": "us-gaap:Revenues", "unit": "USD"},
        "EarningsPerShareBasic": {"concept": "us-gaap:EarningsPerShareBasic",
                                  "unit": "USD/shares"}}}}}
    with pytest.raises(ValueError):
        inventory_from_rows([rows[0], ("AAPL", "1", 2023, "Revenue", f"{US}:Revenues", "USD")])


def test_read_corpus_inventory_passes_the_forms_as_a_parameter():
    conn = _Conn([("MSFT", "789019", 2025, "Revenue", f"{US}:Revenues", "USD", 1)])
    inv = read_corpus_inventory(conn)
    assert inv["MSFT"]["years"][2025]["Revenue"]["concept"] == "us-gaap:Revenues"
    ((sql, params),) = conn.calls
    assert "%s" in sql and params == (["10-K", "20-F", "40-F"],)


def test_restrict_to_lexicon():
    lexicon = Lexicon(companies={"AAPL": {"short": "Apple", "legal": "Apple Inc."}},
                      metrics={"Revenue": {"label": "Revenue", "synonyms": (),
                                           "shorthand": ()}},
                      dimensions={}, universes={"covered": ("the covered companies",)},
                      period_ends={})
    spec = {"concept": "us-gaap:Revenues", "unit": "USD"}
    inv = {"AAPL": {"cik": "0000320193", "years": {2024: {"Revenue": spec, "Odd": spec},
                                                   2023: {"Odd": spec}}},
           "HELD": {"cik": "0000000001", "years": {2024: {"Revenue": spec}}}}
    kept, left_out = restrict_to_lexicon(inv, lexicon)
    assert kept == {"AAPL": {"cik": "0000320193", "years": {2024: {"Revenue": spec}}}}
    assert left_out == {"tickers": ["HELD"], "metrics": ["Odd"]}


def _fact_rows():
    # stored ends are Arelle's next midnight: 2024-09-29 is the XBRL end 2024-09-28
    return [
        (f"{US}:Revenues", "USD", dt.date(2023, 10, 1), dt.date(2024, 9, 29), 391.0e9, -6),
        (f"{US}:Revenues", "USD", dt.date(2023, 10, 1), dt.date(2024, 9, 29), 391.035e9, -3),
        (f"{US}:EarningsPerShareBasic", "shares*USD", dt.date(2023, 10, 1),
         dt.date(2024, 9, 29), 6.11, 2),
        (f"{US}:Assets", "USD", None, dt.date(2023, 10, 1), 352.583e9, None),
    ]


def test_fact_store_source_matches_store_conventions():
    conn = _Conn({("0000320193", "ACC-1"): _fact_rows()})
    store = PostgresFactStoreSource(conn)
    rev = store.value("0000320193", "us-gaap:Revenues", "USD", "2023-10-01", "2024-09-28",
                      "ACC-1")
    assert rev is not None
    assert rev["value"] == 391.035e9 and rev["decimals"] == -3  # the more precise duplicate
    assert rev["key_source"] == "fact_store" and rev["accession"] == "ACC-1"
    eps = store.value("320193", "us-gaap:EarningsPerShareBasic", "USD/shares", "2023-10-01",
                      "2024-09-28", "ACC-1")
    assert eps is not None and eps["value"] == 6.11 and eps["cik"] == "0000320193"
    assets = store.value("0000320193", "us-gaap:Assets", "USD", None, "2023-09-30", "ACC-1")
    assert assets is not None and assets["decimals"] is None
    assert store.value("0000320193", "us-gaap:Revenues", "USD", "2023-10-01", "2024-09-29",
                       "ACC-1") is None
    assert store.value("0000320193", "us-gaap:Revenues", "EUR", "2023-10-01", "2024-09-28",
                       "ACC-1") is None
    assert len(conn.calls) == 1  # one query per filing, then memory


def test_fact_store_source_ranks_an_inf_fact_most_precise():
    # FILING_FACTS_SQL returns decimals as text: 'INF' for an exact fact (whose
    # decimals_value is NULL, like a nil's), else the integer (sign-and-nil Notes)
    end = dt.date(2024, 9, 29)
    rows = [
        (f"{US}:CommonStockDividendsPerShareDeclared", "shares*USD", dt.date(2023, 10, 1),
         end, 0.98, "-2"),
        (f"{US}:CommonStockDividendsPerShareDeclared", "shares*USD", dt.date(2023, 10, 1),
         end, 0.975, "INF"),
    ]
    store = PostgresFactStoreSource(_Conn({("0000320193", "ACC-1"): rows}))
    key = store.value("0000320193", "us-gaap:CommonStockDividendsPerShareDeclared",
                      "USD/shares", "2023-10-01", "2024-09-28", "ACC-1")
    assert key is not None
    assert key["value"] == 0.975 and key["decimals"] == "INF"


def test_refusing_transport():
    with pytest.raises(CacheOnlyError):
        RefusingTransport().get("https://data.sec.gov/x", {})


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "build_v2_suite", REPO / "scripts" / "eval" / "build_v2_suite.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_groups(tmp_path):
    cli = _load_cli()
    assert cli.load_groups(None, {"covered": ()}) == {}
    head = ('as_of: "2026-10-29"\nsectors_file: configs/entities/sectors.yaml\n'
            "definitions:\n  {gid}: {{tickers: [AAPL, MSFT]}}\ngroups:\n  {gid}: [AAPL, MSFT]\n")
    good = tmp_path / "g.yaml"
    good.write_text(head.format(gid="peers"), encoding="utf-8")
    assert cli.load_groups(good, {"covered": (), "peers": ()}) == {"peers": ["AAPL", "MSFT"]}
    with pytest.raises(ValueError):
        cli.load_groups(good, {"covered": ()})
    covered = tmp_path / "c.yaml"
    covered.write_text(head.format(gid="covered"), encoding="utf-8")
    with pytest.raises(ValueError):
        cli.load_groups(covered, {"covered": ()})
    flat = tmp_path / "f.yaml"
    flat.write_text("peers: [AAPL, MSFT]\n", encoding="utf-8")
    with pytest.raises(ValueError):
        cli.load_groups(flat, {"covered": (), "peers": ()})
    # The committed groups file loads, and every id is phrased by the committed lexicon.
    from src.eval.suites.intents import load_lexicon
    lexicon = load_lexicon(REPO / "configs" / "eval" / "v2" / "lexicon.yaml")
    committed = cli.load_groups(REPO / "configs" / "eval" / "v2" / "groups.yaml",
                                lexicon.universes)
    assert {"semiconductors", "retailers", "big_tech"} <= set(committed)


def test_cli_sec_source_wiring(monkeypatch):
    """The CLI builds the live source on the sec_edgar store; cache-only refuses fetches."""
    import src.eval.keys.sec_live as sec_live
    import src.ingest.sec_edgar.storage as storage

    built: list[tuple[tuple, dict]] = []

    class _FakeLive:
        def __init__(self, *args: object, **kwargs: object) -> None:
            built.append((args, kwargs))

    monkeypatch.setattr(sec_live, "LiveSecSource", _FakeLive)
    monkeypatch.setattr(storage.FilingStore, "from_env",
                        classmethod(lambda cls, role: f"store:{role}"))
    cli = _load_cli()

    cli._sec_source(cache_only=True, identity=None)
    args, kwargs = built[-1]
    assert args == ("store:sec_edgar",)
    assert isinstance(kwargs["transport"], RefusingTransport)
    assert kwargs["identity"]  # a placeholder identity: no request is ever sent

    cli._sec_source(cache_only=False, identity="Name name@example.com")
    args, kwargs = built[-1]
    assert args == ("store:sec_edgar",)
    assert "transport" not in kwargs
    assert kwargs["identity"] == "Name name@example.com"


# --- v2_suite caller errors and fetching -------------------------------------------------

_INV = {"AAA": {"cik": "0000900101", "years": {2024: {
    "Revenue": {"concept": "us-gaap:Revenues", "unit": "USD"}}}},
    "BBB": {"cik": "0000900102", "years": {2024: {
        "Revenue": {"concept": "us-gaap:Revenues", "unit": "USD"}}}}}


class _CountingSec(SecSource):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def company_facts(self, cik: str) -> dict:
        self.calls.append(cik)
        raise KeyNotFoundError(f"no companyfacts for {cik}")

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        raise AssertionError("frames are not read")


class _NoStore:
    def value(self, *args: object) -> None:
        raise AssertionError("nothing is keyed, so nothing is cross-checked")


def test_unknown_quota_shape_and_universe_are_caller_errors():
    with pytest.raises(ValueError):
        build_v2_suites(_INV, {"covered": ["AAA"]}, _CountingSec(), _NoStore(),  # type: ignore[arg-type]  # a stand-in store
                        None, None, quotas={"dimensional": 1}, per_intent=1,  # type: ignore[arg-type]  # never reached
                        share=0.5, salt="s")
    screen = Intent("screen", operation="sum", universe="nowhere", fiscal_years=[2024],
                    metrics=["Revenue"])
    with pytest.raises(ValueError):
        key_intent(screen, _INV, {"covered": ["AAA"]}, _CountingSec(), _NoStore())  # type: ignore[arg-type]  # a stand-in store


def test_a_failed_filer_is_fetched_once_per_build():
    # every key fails (no_key): each CIK is still requested exactly once across all shapes
    sec = _CountingSec()
    build = build_v2_suites(_INV, {"covered": ["AAA", "BBB"]}, sec, _NoStore(),  # type: ignore[arg-type]  # a stand-in store
                            None, None,  # type: ignore[arg-type]  # nothing is phrased
                            quotas={"lookup": 5, "screen.top": 5}, per_intent=1, share=0.5,
                            salt="s")
    assert sorted(sec.calls) == ["0000900101", "0000900102"]
    assert build["manifest"]["intents"]["dropped"] == {"no_key": 2, "too_few_ranked": 1}
    assert build["dev"]["cases"] == [] and build["validation"]["cases"] == []


def test_dropped_error_carries_the_reason():
    intent = Intent("lookup", tickers=["AAA"], fiscal_years=[2024], metrics=["Revenue"])
    with pytest.raises(IntentDroppedError) as err:
        key_intent(intent, _INV, {}, _CountingSec(), _NoStore())  # type: ignore[arg-type]  # a stand-in store
    assert err.value.args == ("no_key",)
