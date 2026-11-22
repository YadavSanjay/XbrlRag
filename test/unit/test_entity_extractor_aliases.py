"""EntityExtractor's company resolution, wired to the margin resolver.

Specs: docs/specs/company-aliases.md and docs/specs/entity-resolution-margin.md. Order is
exact ticker (SQL) -> committed alias (SQL) -> exact normalized name / fuzzy name with
threshold and margin (``resolve_company`` over all of entity_master). The DB is the I/O
boundary, so a fake connection stands in for Postgres; the resolver logic runs for real.
The pipeline's refusal for an unresolved company is tested at the bottom.
"""

from __future__ import annotations

from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline
from src.retriever.xbrl.branch_dispatcher import BRANCH, DispatchDecision
from src.retriever.xbrl.entity_extractor import EntityExtractor, ResolvedCompany
from src.retriever.xbrl.intent_router import XBRL_INTENT, IntentRouterResult


def _row(cik: str, ticker: str, name: str) -> dict:
    return {"cik": cik, "trading_symbol": ticker, "company_name": name,
            "fye_month": 12, "fye_day": 31, "last_seen_fy": 2025}


# CIKs from SEC EDGAR; names as entity_master stores them.
TSMC_CIK = "0001046179"
TSMC_ROW = _row(TSMC_CIK, "TSM", "Taiwan Semiconductor Manufacturing Company Limited")
MU_ROW = _row("0000723125", "MU", "Micron Technology, Inc.")
MSFT_ROW = _row("0000789019", "MSFT", "MICROSOFT CORPORATION")
CEG_ROW = _row("0001868275", "CEG", "CONSTELLATION ENERGY CORPORATION")
TXN_ROW = _row("0000097476", "TXN", "TEXAS INSTRUMENTS INC")
CORPUS = [TSMC_ROW, MU_ROW, MSFT_ROW, CEG_ROW, TXN_ROW]


class _FakeCursor:
    """Answers the entity_master queries (ticker, CIK, whole table) from memory."""

    def __init__(self, rows: list[dict], log: list[str]):
        self._rows = rows
        self._log = log
        self._result: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        if "upper(trading_symbol)" in sql:
            self._log.append("ticker")
            hits = sorted((r for r in self._rows
                           if r["trading_symbol"].upper() == params[0].upper()),
                          key=lambda r: r["cik"])
            ciks = [r["cik"] for r in hits]
            self._result = [{**h, "ticker_ciks": ciks} for h in hits[:1]]
        elif "WHERE cik" in sql:
            self._log.append("alias")
            self._result = [r for r in self._rows if r["cik"] == params[0]]
        else:
            self._log.append("universe")
            self._result = list(self._rows)

    def fetchone(self) -> dict | None:
        return self._result[0] if self._result else None

    def fetchall(self) -> list[dict]:
        return list(self._result)


class _FakeConn:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.log: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self.rows, self.log)


class _FakeDbExtractor(EntityExtractor):
    def __init__(self, rows: list[dict], aliases: dict[str, str] | None = None):
        super().__init__(db_conn_params={}, company_aliases=aliases)
        self.conn = _FakeConn(rows)

    def _connect(self):
        return self.conn


def _extractor(aliases: dict[str, str]) -> EntityExtractor:
    return EntityExtractor(db_conn_params={}, company_aliases=aliases)


def test_alias_resolves_before_name_matching():
    conn = _FakeConn([TSMC_ROW])
    resolved = _extractor({"tsmc": TSMC_CIK})._resolve_one_company(conn, "TSMC's")

    assert resolved.cik == TSMC_CIK
    assert resolved.ticker == "TSM"
    assert resolved.resolution == "alias"
    assert resolved.similarity == 1.0
    assert not resolved.is_ambiguous
    assert conn.log == ["ticker", "alias"]  # the name tiers never consulted


def test_exact_ticker_still_wins_over_an_alias():
    conn = _FakeConn([TSMC_ROW])
    resolved = _extractor({"tsm": "0000000001"})._resolve_one_company(conn, "TSM")

    assert resolved.resolution == "exact_ticker"
    assert resolved.cik == TSMC_CIK
    assert conn.log == ["ticker"]


def test_a_ticker_held_by_two_filers_is_ambiguous():
    rows = [_row("0000000002", "DUP", "Beta Gadgets"), _row("0000000001", "DUP", "Alpha Widgets")]
    resolved = _extractor({})._resolve_one_company(_FakeConn(rows), "dup")

    assert (resolved.resolution, resolved.cik) == ("ambiguous", None)
    assert [c["cik"] for c in resolved.candidates] == ["0000000001", "0000000002"]


def test_alias_to_an_uningested_cik_is_unresolved_not_guessed():
    conn = _FakeConn([TSMC_ROW])
    resolved = _extractor({"acme": "0000000042"})._resolve_one_company(conn, "Acme")

    assert resolved.resolution == "unresolved"
    assert resolved.cik is None
    assert resolved.is_ambiguous
    assert conn.log == ["ticker", "alias"]  # no fall-through to name matching


def test_unknown_mention_falls_through_to_the_whole_universe():
    conn = _FakeConn([TSMC_ROW])
    resolved = _extractor({"tsmc": TSMC_CIK})._resolve_one_company(conn, "Some Other Co")

    assert conn.log == ["ticker", "universe"]
    assert (resolved.resolution, resolved.cik, resolved.candidates) == ("unresolved", None, [])


def test_default_construction_uses_the_committed_alias_table():
    conn = _FakeConn([TSMC_ROW])
    resolved = EntityExtractor(db_conn_params={})._resolve_one_company(conn, "TSMC")

    assert resolved.resolution == "alias"
    assert resolved.cik == TSMC_CIK


# ------------------------------------------------- wiring: entity-resolution-margin
def test_lookalikes_no_longer_resolve_through_the_extractor():
    # The old pg_trgm rule took the best similarity >= 0.30: "Microchip" -> Micron,
    # "Constellation Brands" -> Constellation Energy. Both are now ambiguous, naming them.
    extractor = _FakeDbExtractor(CORPUS, aliases={})
    microchip, brands = extractor.resolve_companies(["Microchip Technology",
                                                     "Constellation Brands"])

    assert (microchip.resolution, microchip.cik, microchip.ticker) == ("ambiguous", None, None)
    assert microchip.candidates[0]["ticker"] == "MU"
    assert (brands.resolution, brands.cik) == ("ambiguous", None)
    assert [c["ticker"] for c in brands.candidates] == ["CEG"]
    assert "did you mean CEG (CONSTELLATION ENERGY CORPORATION)?" in brands.clarification()
    assert extractor.conn.log.count("universe") == 1  # loaded once per batch


def test_bare_microchip_is_not_micron():
    (resolved,) = _FakeDbExtractor(CORPUS, aliases={}).resolve_companies(["Microchip"])
    assert resolved.is_ambiguous
    assert resolved.cik is None


def test_committed_aliases_and_exact_names_still_resolve_through_the_extractor():
    extractor = _FakeDbExtractor(CORPUS)  # the committed alias table
    tsmc, micron, ti, name, typo = extractor.resolve_companies(
        ["TSMC", "Micron", "TI", "Micron Technology", "Texas Instrument"])

    assert (tsmc.resolution, tsmc.ticker) == ("alias", "TSM")
    assert (micron.resolution, micron.ticker) == ("alias", "MU")
    assert (ti.resolution, ti.ticker) == ("alias", "TXN")
    assert (name.resolution, name.ticker, name.similarity) == ("exact_name", "MU", 1.0)
    assert (typo.resolution, typo.ticker) == ("fuzzy_name", "TXN")
    assert typo.similarity == 16 / 19  # "texas instrument" vs "texas instruments", hand count
    assert typo.fye_month == 12 and typo.last_seen_fy == 2025  # the full entity_master row


# ------------------------------------------------- the pipeline refuses, naming candidates
def _decision(intent: XBRL_INTENT, *companies: ResolvedCompany) -> DispatchDecision:
    result = IntentRouterResult(intent=intent, companies=list(companies),
                                plan={"steps": [{"id": "n1", "op": "narrative"}]})
    return DispatchDecision(branch=BRANCH.NARRATIVE_BRANCH, reason="test",
                            intent_result=result, degraded=True)


class _Unused:
    def __getattr__(self, name):
        raise AssertionError(f"stage used for an unresolved company: {name}")


def _pipeline() -> XbrlRetrievalPipeline:
    pipeline = XbrlRetrievalPipeline.__new__(XbrlRetrievalPipeline)
    pipeline.plan_generator = _Unused()
    pipeline.synthesizer = _Unused()
    pipeline.faithfulness_verifier = _Unused()
    pipeline.narrative_retriever = _Unused()
    return pipeline


AMBIGUOUS_MICROCHIP = ResolvedCompany(
    mention="Microchip Technology", resolution="ambiguous", similarity=16 / 23,
    candidates=[{"cik": "0000723125", "ticker": "MU", "company_name": "Micron Technology, Inc."}],
)


def test_an_ambiguous_company_is_refused_with_a_did_you_mean():
    response = _pipeline()._run("Microchip Technology revenue in FY2024?",
                                _decision(XBRL_INTENT.NUMERIC_LOOKUP, AMBIGUOUS_MICROCHIP))

    assert response.answer_summary == ""
    assert response.metadata["fully_answered"] is False
    assert response.metadata["unresolved_company"] is True
    reason = response.unanswered_parts[0]["reason"]
    assert "did you mean MU (Micron Technology, Inc.)?" in reason
    assert response.execution_result["status"] == "ERROR"  # never cached


def test_an_unresolved_company_is_refused_even_for_a_narrative_question():
    nobody = ResolvedCompany(mention="Nobody Corp", resolution="unresolved")
    response = _pipeline()._run("What risks does Nobody Corp discuss?",
                                _decision(XBRL_INTENT.NARRATIVE, nobody))

    assert response.metadata["unresolved_company"] is True
    assert "'Nobody Corp' does not match any filer" in response.unanswered_parts[0]["reason"]


def test_a_screen_ignores_unresolved_mentions():
    result = _decision(XBRL_INTENT.NUMERIC_SCREEN, AMBIGUOUS_MICROCHIP).intent_result
    assert XbrlRetrievalPipeline._unresolved_company_mentions(result) == []
