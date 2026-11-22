"""Committed aliases for well-known name variants and common misspellings.

"Nvida" and "Wal-Mart" drew a "did you mean" refusal: they miss the fuzzy threshold or the
runner-up margin (docs/specs/entity-resolution-margin.md), which stay as they are. The
committed alias table (configs/entities/company_aliases.yaml) resolves them exactly. These
tests check each new alias against the corpus, that lookalikes which are not aliases still
do not resolve, and that no alias collides with another filer's ticker or name.
"""

from __future__ import annotations

import pytest

from src.retriever.xbrl.entity_aliases import load_company_aliases, normalize_company_mention
from src.retriever.xbrl.entity_extractor import EntityExtractor
from src.retriever.xbrl.entity_resolution import company_name_key, resolve_company

# The in-loop corpus as entity_master stores it: (cik, ticker, company_name). CIKs from
# SEC EDGAR.
CORPUS = [
    ("0000320193", "AAPL", "Apple Inc."),
    ("0000001800", "ABT", "Abbott Laboratories"),
    ("0000796343", "ADBE", "ADOBE INC."),
    ("0000002488", "AMD", "ADVANCED MICRO DEVICES, INC"),
    ("0001018724", "AMZN", "AMAZON.COM, INC."),
    ("0001730168", "AVGO", "Broadcom Inc."),
    ("0001868275", "CEG", "CONSTELLATION ENERGY CORPORATION"),
    ("0000909832", "COST", "COSTCO WHOLESALE CORP /NEW"),
    ("0001108524", "CRM", "Salesforce, Inc."),
    ("0000858877", "CSCO", "CISCO SYSTEMS, INC."),
    ("0001652044", "GOOGL", "Alphabet Inc."),
    ("0000354950", "HD", "HOME DEPOT, INC."),
    ("0000050863", "INTC", "INTEL CORPORATION"),
    ("0000021344", "KO", "COCA COLA CO"),
    ("0000059478", "LLY", "ELI LILLY AND COMPANY"),
    ("0000936468", "LMT", "LOCKHEED MARTIN CORPORATION"),
    ("0000063908", "MCD", "McDONALD’S CORPORATION"),
    ("0001326801", "META", "Meta Platforms, Inc."),
    ("0000310158", "MRK", "Merck & Co., Inc."),
    ("0000789019", "MSFT", "MICROSOFT CORPORATION"),
    ("0000723125", "MU", "Micron Technology, Inc."),
    ("0001065280", "NFLX", "Netflix, Inc."),
    ("0001045810", "NVDA", "NVIDIA CORP"),
    ("0001341439", "ORCL", "Oracle Corporation"),
    ("0000080424", "PG", "PROCTER & GAMBLE CO"),
    ("0000804328", "QCOM", "QUALCOMM INC/DE"),
    ("0001318605", "TSLA", "Tesla, Inc."),
    ("0001046179", "TSM", "Taiwan Semiconductor Manufacturing Company Limited"),
    ("0000097476", "TXN", "TEXAS INSTRUMENTS INCORPORATED"),
    ("0000100885", "UNP", "UNION PACIFIC CORP"),
    ("0001090727", "UPS", "United Parcel Service, Inc."),
    ("0001692819", "VST", "Vistra Corp."),
    ("0000104169", "WMT", "Walmart Inc."),
]

# Each new alias and the CIK it names (SEC EDGAR).
NEW_ALIASES = {
    "Wal-Mart": "0000104169",
    "Wal Mart": "0000104169",
    "Wal-Mart Stores": "0000104169",
    "Nvida": "0001045810",
    "Nvidea": "0001045810",
    "Nividia": "0001045810",
    "Nvdia": "0001045810",
    "Mircosoft": "0000789019",
    "Microsft": "0000789019",
    "Micorsoft": "0000789019",
    "Amazom": "0001018724",
    "Telsa": "0001318605",
    "Qualcom": "0000804328",
    "Broadcomm": "0001730168",
    "Eli Lily": "0000059478",
    "Proctor & Gamble": "0000080424",
    "Proctor and Gamble": "0000080424",
    "Salesforce.com": "0001108524",
}

ALIASES = load_company_aliases()


@pytest.mark.parametrize(("mention", "cik"), sorted(NEW_ALIASES.items()))
def test_new_alias_resolves_to_its_filer(mention: str, cik: str):
    match = resolve_company(mention, CORPUS, ALIASES)
    assert (match.status, match.cik) == ("alias", cik)


@pytest.mark.parametrize("mention", sorted(NEW_ALIASES))
def test_new_alias_was_not_already_resolved_without_the_table(mention: str):
    # Each entry earns its place: without it, the mention is not resolved.
    assert resolve_company(mention, CORPUS, {}).cik is None


def test_variants_resolve_through_the_extractor():
    extractor = EntityExtractor(db_conn_params={})  # the committed alias table
    rows = {cik: {"cik": cik, "trading_symbol": ticker, "company_name": name,
                  "fye_month": 12, "fye_day": 31, "last_seen_fy": 2025}
            for cik, ticker, name in CORPUS}
    conn = _FakeConn(rows)

    nvida = extractor._resolve_one_company(conn, "Nvida's")
    walmart = extractor._resolve_one_company(conn, "Wal-Mart")

    assert (nvida.resolution, nvida.ticker) == ("alias", "NVDA")
    assert (walmart.resolution, walmart.ticker) == ("alias", "WMT")


@pytest.mark.parametrize(
    ("mention", "not_ciks"),
    [
        ("Microchip", {"0000723125", "0000789019"}),  # Micron, Microsoft
        ("Microchip Technology", {"0000723125", "0000789019"}),
        ("Microsoft Teams", {"0000789019"}),  # a product, never an alias
        ("Walgreens", {"0000104169"}),
        ("Qualys", {"0000804328"}),
        ("Broadridge", {"0001730168"}),
    ],
)
def test_lookalike_that_is_not_an_alias_does_not_resolve(mention: str, not_ciks: set[str]):
    assert normalize_company_mention(mention) not in ALIASES
    match = resolve_company(mention, CORPUS, ALIASES)
    assert match.cik not in not_ciks
    assert match.cik is None


@pytest.mark.parametrize(("alias", "cik"), sorted(ALIASES.items()))
def test_no_alias_collides_with_another_filers_ticker_or_name(alias: str, cik: str):
    for other_cik, ticker, name in CORPUS:
        if other_cik == cik:
            continue
        assert alias != ticker.casefold(), f"{alias!r} is {ticker}'s ticker"
        assert company_name_key(alias) != company_name_key(name), f"{alias!r} is {name}"


class _FakeCursor:
    """Answers the extractor's ticker and CIK queries from memory; no ticker matches."""

    def __init__(self, rows: dict[str, dict]):
        self._rows = rows
        self._result: dict | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        if "WHERE cik" in sql:
            self._result = self._rows.get(params[0])
        else:
            assert "upper(trading_symbol)" in sql, "a name tier was consulted"
            self._result = next((r for r in self._rows.values()
                                 if r["trading_symbol"].upper() == params[0].upper()), None)

    def fetchone(self) -> dict | None:
        return self._result


class _FakeConn:
    def __init__(self, rows: dict[str, dict]):
        self._rows = rows

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self._rows)
