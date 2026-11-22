"""The H-X filing locator: raw iXBRL reading, the S1 rule on raw filings, case drafts, app.

Synthetic filings only (no database, no network). Expected values are worked out by hand
from the fixtures below, not from the code under test.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from src.eval.set_b.core import load_authoring, parse_amount
from tools.hx_locator import __main__ as hx_main
from tools.hx_locator import analysis
from tools.hx_locator.app import create_app
from tools.hx_locator.corpus import Corpus, dei_fiscal_year
from tools.hx_locator.factstore import quiet_error_logging, short_concept, sql_templates
from tools.hx_locator.families import family_of, mapping_qname
from tools.hx_locator.ixbrl import parse_documents, parse_number, parse_number_words
from tools.hx_locator.snippets import assess, default_scale, format_amount, render

# ------------------------------------------------------------------ fixtures

_NS = {
    "": "http://www.w3.org/1999/xhtml", "ix": "http://www.xbrl.org/2013/inlineXBRL",
    "xbrli": "http://www.xbrl.org/2003/instance", "xbrldi": "http://xbrl.org/2006/xbrldi",
    "iso4217": "http://www.xbrl.org/2003/iso4217", "us-gaap": "http://fasb.org/us-gaap/2024",
    "dei": "http://xbrl.sec.gov/dei/2024", "srt": "http://fasb.org/srt/2024",
    "ixt": "http://www.xbrl.org/inlineXBRL/transformation/2020-02-12",
    "acme": "http://acme.example/2024", "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
_UNITS = (
    '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
    '<xbrli:unit id="usdps"><xbrli:divide>'
    "<xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator>"
    "<xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator>"
    "</xbrli:divide></xbrli:unit>"
)


def _ctx(cid: str, start: str | None, end: str, member: str | None = None) -> str:
    seg = ('<xbrli:segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">'
           f"{member}</xbrldi:explicitMember></xbrli:segment>") if member else ""
    period = (f"<xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>"
              if start else f"<xbrli:instant>{end}</xbrli:instant>")
    return (f'<xbrli:context id="{cid}"><xbrli:entity>'
            f'<xbrli:identifier scheme="x">1</xbrli:identifier>{seg}</xbrli:entity>'
            f"<xbrli:period>{period}</xbrli:period></xbrli:context>")


def _fact(fid: str, name: str, ctx: str, text: str, *, unit: str = "usd", decimals: str = "-6",
          scale: str | None = "6", sign: str | None = None,
          fmt: str = "ixt:num-dot-decimal") -> str:
    attrs = {"name": name, "contextRef": ctx, "unitRef": unit, "decimals": decimals,
             "format": fmt, "id": fid}
    if scale:
        attrs["scale"] = scale
    if sign:
        attrs["sign"] = sign
    joined = " ".join(f'{k}="{v}"' for k, v in attrs.items())
    return f"<p><ix:nonFraction {joined}>{text}</ix:nonFraction></p>"


def _doc(fy: int, contexts: list[str], facts: list[str], prose: str = "") -> str:
    ns = " ".join(f'xmlns{":" + k if k else ""}="{v}"' for k, v in _NS.items())
    cover = ('<p><ix:nonNumeric name="dei:DocumentFiscalYearFocus" contextRef="c0" id="dfy">'
             f"{fy}</ix:nonNumeric></p>")
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            f"<html {ns}><body>"
            f'<div style="display:none"><ix:header><ix:resources>{_UNITS}'
            f"{''.join(contexts)}</ix:resources></ix:header></div>"
            f"{cover}{''.join(facts)}{prose}</body></html>")


def _write_filing(root: Path, ticker: str, folder: int, accession: str, filed: str,
                  start: str, end: str, html: str, cik: int = 999) -> None:
    d = root / ticker / str(folder) / "10-K"
    d.mkdir(parents=True)
    doc = f"{ticker.lower()}-{end.replace('-', '')}.htm"
    (d / doc).write_text(html, encoding="utf-8")
    (d / "manifest.yaml").write_text(yaml.safe_dump({
        "ticker": ticker, "cik": cik, "accession_no": accession, "form": "10-K",
        "fiscal_year": folder, "filing_date": filed, "period_start": start, "period_end": end,
        "primary_document": doc, "ixbrl_documents": [doc]}), encoding="utf-8")


@pytest.fixture()
def corpus(tmp_path: Path) -> Corpus:
    """ACME: FY2023 (Revenue 1,000.5M, R&D 200M, EPS 4.00, product member 600M),
    FY2024 filing restating FY2023 revenue to 990M and repeating EPS 4.00 with fewer
    digits; HELD is a refused filer."""
    year23 = ("2023-01-01", "2023-12-31")
    c23 = [_ctx("c0", *year23), _ctx("cP", *year23, "srt:ProductMember"),
           _ctx("cB", None, "2023-12-31")]
    f23 = [
        _fact("r23", "us-gaap:Revenues", "c0", "1,000.5", decimals="-5"),
        _fact("r23b", "us-gaap:Revenues", "c0", "1,000.5", decimals="-5"),
        _fact("rd23", "us-gaap:ResearchAndDevelopmentExpense", "c0", "200"),
        _fact("neg23", "us-gaap:OtherNonoperatingIncomeExpense", "c0", "(12)", sign="-"),
        _fact("eps23", "us-gaap:EarningsPerShareDiluted", "c0", "4.00", unit="usdps",
              decimals="2", scale=None),
        _fact("prod23", "us-gaap:Revenues", "cP", "600"),
        _fact("z23", "acme:Widgets", "cB", "—", decimals="0", scale=None,
              fmt="ixt:fixed-zero"),
    ]
    prose23 = "<p>Item 1A. Risk Factors. Our supply chain depends on a single foundry.</p>"
    c24 = [_ctx("c0", "2024-01-01", "2024-12-31"), _ctx("cPrior", *year23)]
    f24 = [
        _fact("r24", "us-gaap:Revenues", "c0", "1,100"),
        _fact("r24p", "us-gaap:Revenues", "cPrior", "990"),
        _fact("eps24p", "us-gaap:EarningsPerShareDiluted", "cPrior", "4.0", unit="usdps",
              decimals="1", scale=None),
    ]
    body23 = (c23, f23, prose23)
    body24 = (c24, f24, "")
    raw = tmp_path / "raw"
    _write_filing(raw, "ACME", 2023, "0000000999-24-000001", "2024-02-15", "2023-01-01",
                  "2023-12-31", _doc(2023, *body23))
    # Folder 2025 holds the FY2024 report: the cover's label must win over the folder's.
    _write_filing(raw, "ACME", 2025, "0000000999-25-000002", "2025-02-14", "2024-01-01",
                  "2024-12-31", _doc(2024, *body24))
    _write_filing(raw, "HELD", 2024, "0000000888-25-000003", "2025-02-01", "2024-01-01",
                  "2024-12-31", _doc(2024, *body24), cik=888)
    return Corpus(raw, None, frozenset({"HELD"}))


# ------------------------------------------------------------------ iXBRL reading


@pytest.mark.parametrize(("text", "fmt", "want"), [
    ("1,234.5", "ixt:num-dot-decimal", 1234.5),
    ("1.234,5", "ixt:num-comma-decimal", 1234.5),
    ("—", "ixt:fixed-zero", 0.0),
    ("-", None, 0.0),
    ("(17)", "ixt:numdotdecimal", 17.0),
    ("three", "ixt-sec:numwordsen", 3.0),
    ("", "ixt:fixed-empty", None),
])
def test_parse_number(text: str, fmt: str | None, want: float | None) -> None:
    assert parse_number(text, fmt) == want


@pytest.mark.parametrize(("text", "want"), [
    ("twenty-one", 21.0), ("one hundred and five", 105.0), ("no", 0.0),
    ("two thousand three hundred", 2300.0), ("several", None)])
def test_parse_number_words(text: str, want: float | None) -> None:
    assert parse_number_words(text) == want


def test_parse_documents_applies_scale_sign_and_dims(corpus: Corpus) -> None:
    own = corpus.own_filing("ACME", 2023)
    assert own is not None
    facts = {f.fact_id: f for f in parse_documents([own.raw_dir / own.primary_document])}
    assert facts["r23"].value == pytest.approx(1_000_500_000.0)
    assert facts["neg23"].value == pytest.approx(-12_000_000.0)
    assert facts["eps23"].unit == "USD/shares"
    assert facts["prod23"].context.dims == (("srt:ProductOrServiceAxis", "srt:ProductMember"),)
    assert facts["z23"].value == 0.0
    assert facts["z23"].context.instant
    assert facts["dfy"].numeric is False and facts["dfy"].text == "2023"


# ------------------------------------------------------------------ corpus


def test_corpus_labels_refusal_and_dei_year(corpus: Corpus) -> None:
    assert corpus.tickers() == ["ACME"]
    assert corpus.skipped_refused == 1
    f24 = corpus.own_filing("ACME", 2024)
    assert f24 is not None and f24.folder_year == 2025 and f24.label_source == "dei"
    assert dei_fiscal_year(f24.raw_dir / f24.primary_document) == 2024
    assert f24.sec_viewer_url().endswith("/999/000000099925000002/" + f24.primary_document)


def test_registry_label_beats_cover(tmp_path: Path, corpus: Corpus) -> None:
    relabelled = Corpus(corpus.raw_root, None, frozenset(), {"0000000999-25-000002": 2030})
    f = relabelled.by_key("000000099925000002")
    assert f is not None and f.fiscal_year == 2030 and f.label_source == "registry"


# ------------------------------------------------------------------ S1 on raw filings


def test_family_values_original_and_latest(corpus: Corpus) -> None:
    lib = analysis.Library(corpus)
    res = analysis.family_values(lib, "ACME", 2023, "Revenue")
    pick = analysis.headline(res["rows"])
    assert pick is not None
    assert pick["original"]["value"] == pytest.approx(1_000_500_000.0)
    assert pick["original"]["also_at"] == ["r23b"]
    assert pick["latest"]["value"] == pytest.approx(990_000_000.0)
    assert pick["latest"]["accession"] == "0000000999-25-000002"
    assert pick["restated"] is True and pick["precision_only"] is False


def test_precision_change_is_not_a_restatement(corpus: Corpus) -> None:
    lib = analysis.Library(corpus)
    eps = analysis.headline(analysis.family_values(lib, "ACME", 2023,
                                                   "EarningsPerShareDiluted")["rows"])
    assert eps is not None
    assert eps["restated"] is False
    assert eps["precision_only"] is False  # 4.00 and 4.0 are the same value
    assert eps["latest"]["accession"] == "0000000999-25-000002"


def test_member_filter_and_cross_filer(corpus: Corpus) -> None:
    lib = analysis.Library(corpus)
    res = analysis.family_values(lib, "ACME", 2023, "Revenue", member="Product", dimensional=True)
    assert [r["original"]["value"] for r in res["rows"]] == [pytest.approx(600_000_000.0)]
    cf = analysis.cross_filer(lib, ["ACME"], 2022, "Revenue")
    assert cf["filers"][0]["exclusion"] == "not_filed"
    cf = analysis.cross_filer(lib, ["ACME"], 2023, "ResearchAndDevelopment")
    assert cf["aggregates"]["sum"] == pytest.approx(200_000_000.0)
    assert cf["filers"][0]["rank"] == 1


def test_cross_period_cagr_uses_latest_values(corpus: Corpus) -> None:
    lib = analysis.Library(corpus)
    cp = analysis.cross_period(lib, "ACME", [2023, 2024], "Revenue")
    # (1,100 / 990) ** (1 / 1) - 1, on FY2023's latest (restated) value.
    assert cp["cagr"] == pytest.approx(1100 / 990 - 1)


def test_text_search_finds_all_words(corpus: Corpus) -> None:
    lib = analysis.Library(corpus)
    res = analysis.text_search(lib, "ACME", 2023, '"supply chain" foundry')
    assert len(res["hits"]) == 1 and "single foundry" in res["hits"][0]["text"]
    assert analysis.text_search(lib, "ACME", 2023, "foundry tariffs")["hits"] == []


# ------------------------------------------------------------------ families and SQL


def test_mapping_qname_and_family_of() -> None:
    assert mapping_qname("msft:DepreciationAmortization") == "ext:DepreciationAmortization"
    assert mapping_qname("us-gaap:Revenues") == "us-gaap:Revenues"
    row = family_of("us-gaap:ResearchAndDevelopmentExpense", 1)
    assert row is not None and row["concept_family"] == "ResearchAndDevelopment"


def test_short_concept() -> None:
    assert short_concept("http://fasb.org/us-gaap/2024:Revenues") == "us-gaap:Revenues"
    assert short_concept("http://www.oracle.com/20260228:SalesRevenueServicesNet1") == \
        "ext:SalesRevenueServicesNet1"


def test_sql_templates_refuse_unquotable_input() -> None:
    sqls = sql_templates(["AAPL"], [2024], "Revenue", "0000320193-25-000079")
    assert all("'AAPL'" in s["sql"] for s in sqls[:4])
    with pytest.raises(ValueError):
        sql_templates(["AAPL'; DROP TABLE x; --"], [2024], "Revenue")
    with pytest.raises(ValueError):
        sql_templates(["AAPL"], [2024], "Revenue'--")


# ------------------------------------------------------------------ amounts and drafts


@pytest.mark.parametrize(("value", "scale", "decimals", "want"), [
    (391_035_000_000.0, "millions", "-6", "391,035"),
    (34_124_100_000.0, "millions", "-5", "34,124.1"),
    (6.08, "units", "2", "6.08"),
    (1_234_000.0, "thousands", "-3", "1,234"),
    (12.5, "units", "INF", "12.5"),
])
def test_format_amount_matches_parse_amount(value: float, scale: str, decimals: str,
                                            want: str) -> None:
    assert format_amount(value, scale, decimals) == want
    parsed, _half = parse_amount(want, None if scale == "units" else scale)
    assert parsed == pytest.approx(value)


def test_default_scale() -> None:
    assert default_scale(391_035_000_000.0, "USD", "-6") == "millions"
    assert default_scale(34_124_100_000.0, "USD", "-5") == "millions"
    assert default_scale(6.08, "USD/shares", "2") == "units"
    assert default_scale(1_234_000.0, "USD", "-3") == "thousands"


def test_render_round_trips_and_validates() -> None:
    case = {"id": "t1", "question": "Q?", "kind": "cross_filer", "subtype": "screen_top",
            "fy": 2024, "unit": "USD", "concept": "Revenue", "universe": ["AAA", "BBB"],
            "rows": [{"ticker": "BBB", "expected": "9,000", "scale": "millions"}]}
    parsed = load_authoring("cases:\n" + render(case))
    assert parsed == [case]
    out = assess(case, frozenset({"AAA", "BBB"}), frozenset({"CCC"}), None)
    assert out["errors"] == [] and out["check"] is None
    bad = assess({**case, "universe": ["AAA", "CCC"]}, frozenset({"AAA", "BBB"}),
                 frozenset({"CCC"}), None)
    assert bad["errors"]


# ------------------------------------------------------------------ app


class _FakeStore:
    """The fact store seam: no database in unit tests."""

    def status(self) -> dict[str, Any]:
        return {"ok": False, "error": "no database in unit tests"}

    def registry_labels(self) -> None:
        return None


def test_app_refuses_held_out_and_traversal(corpus: Corpus) -> None:
    client = TestClient(create_app(corpus=corpus, store=_FakeStore()))
    meta = client.get("/api/meta").json()
    assert meta["tickers"] == ["ACME"] and meta["db"]["ok"] is False
    assert client.get("/api/family", params={"ticker": "HELD", "fy": 2024,
                                             "family": "Revenue"}).status_code == 403
    assert client.get("/api/family", params={"ticker": "ACME", "fy": 2023,
                                             "family": "NotAFamily"}).status_code == 400
    f = corpus.own_filing("ACME", 2023)
    assert f is not None
    page = client.get(f"/doc/{f.key}/{f.primary_document}")
    assert page.status_code == 200 and 'id="hxl-bar"' in page.text
    assert client.get(f"/doc/{f.key}/manifest.yaml").status_code == 404
    assert client.get(f"/doc/{f.key}/..%2F..%2Fsecret.htm").status_code == 404


def test_responses_are_not_cached_and_send_no_referrer(corpus: Corpus) -> None:
    client = TestClient(create_app(corpus=corpus, store=_FakeStore()))
    f = corpus.own_filing("ACME", 2023)
    assert f is not None
    for path in ("/", "/api/meta", f"/doc/{f.key}/{f.primary_document}",
                 "/api/family?ticker=HELD&fy=2024&family=Revenue"):
        r = client.get(path)
        assert r.headers["cache-control"] == "no-store", path
        assert r.headers["referrer-policy"] == "no-referrer", path


class _Cursor:
    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.sql: list[str] = []

    def execute(self, sql: str) -> None:
        if self.fail:
            raise PermissionError("must be superuser to set parameter")
        self.sql.append(sql)


class _Conn:
    def __init__(self, fail: bool) -> None:
        self.cur = _Cursor(fail)

    def cursor(self) -> _Cursor:
        return self.cur


def test_quiet_error_logging_sets_the_session_and_reports_a_refusal() -> None:
    ok = _Conn(fail=False)
    assert quiet_error_logging(ok) is True
    assert ok.cur.sql == ["SET log_min_error_statement = panic", "SET log_min_messages = panic"]
    assert quiet_error_logging(_Conn(fail=True)) is False


def test_drop_log_files_closes_handlers_and_deletes_the_file(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "xbrlrag_hx_locator_1.log"
    handler = logging.FileHandler(log)
    lg = logging.getLogger("hx_locator_test_drop")
    lg.addHandler(handler)
    monkeypatch.setattr("src.logging_config.current_log_file", lambda: log)
    try:
        hx_main._drop_log_files()
        assert handler not in lg.handlers and handler.stream is None
        assert not log.exists()
    finally:
        lg.removeHandler(handler)
        handler.close()
