"""The H-X filing locator's web app: a JSON API over the filings and the fact store.

Local and read-only. It binds to 127.0.0.1, writes no access log and logs no query, so
what you search never lands in a file an agent could read. Held-out filers are refused
everywhere (``corpus.heldout_tickers``), and only the development database is opened.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from src.ingest.sec_edgar.storage import local_root
from tools.hx_locator import analysis
from tools.hx_locator.corpus import Corpus, heldout_tickers, valid_ticker
from tools.hx_locator.factstore import FactStore, sql_templates
from tools.hx_locator.families import family_names, family_tree
from tools.hx_locator.snippets import assess, default_scale, format_amount

STATIC = Path(__file__).resolve().parent / "static"
_DOC_NAME = re.compile(r"^[A-Za-z0-9_.\-]+\.html?$")
_YEAR_RANGE = (2000, 2100)

# Injected into a served filing: highlights the fact the URL's #id names and scrolls to it.
_HIGHLIGHT = """
<style>
  .hxl-hit { outline: 3px solid #e8590c !important; background: #fff3bf !important;
             scroll-margin: 40vh; }
  #hxl-bar { position: fixed; top: 0; left: 0; right: 0; z-index: 99999; font: 13px
             system-ui, sans-serif; background: #1f2933; color: #f5f7fa; padding: 6px 12px; }
  #hxl-bar a { color: #9fd3ff; }
  body { margin-top: 34px !important; }
</style>
<script>
  function hxlShow() {
    document.querySelectorAll('.hxl-hit').forEach(e => e.classList.remove('hxl-hit'));
    const id = decodeURIComponent(location.hash.slice(1));
    if (!id) return;
    const el = document.getElementById(id);
    const bar = document.getElementById('hxl-bar');
    if (el) { el.classList.add('hxl-hit'); el.scrollIntoView({block: 'center'});
              bar.querySelector('span').textContent = 'Highlighted: ' + id; }
    else { bar.querySelector('span').textContent = 'Element ' + id + ' not in this document'; }
  }
  window.addEventListener('hashchange', hxlShow);
  window.addEventListener('load', hxlShow);
</script>
"""


class CheckBody(BaseModel):
    """One amount to check the way ``check-keys`` will."""

    ticker: str
    fiscal_year: int
    expected: str
    scale: str | None = None
    unit: str
    concept: str | None = None
    member: str | None = None
    accession: str | None = None


class SqlBody(BaseModel):
    """A read-only SELECT to run."""

    sql: str


class CaseBody(BaseModel):
    """A draft H-X case built in the UI."""

    case: dict[str, Any]


class FormatBody(BaseModel):
    """A value to write at a scale."""

    value: float
    unit: str | None = None
    decimals: str | None = None
    scale: str | None = None


def _ticker(t: str) -> str:
    t = t.strip().upper()
    if not valid_ticker(t):
        raise HTTPException(400, "not a ticker")
    return t


def _year(y: int) -> int:
    if not _YEAR_RANGE[0] <= y <= _YEAR_RANGE[1]:
        raise HTTPException(400, "fiscal year out of range")
    return y


def create_app(corpus: Corpus | None = None, store: Any = None) -> FastAPI:
    """Builds the app: indexes the filings, connects lazily to ``xbrl_db``.

    Args:
        corpus: A prebuilt corpus (tests); None indexes the local SEC roots.
        store: A fact store (tests pass a fake); None uses ``FactStore``.

    Returns:
        The FastAPI app.
    """
    store = store if store is not None else FactStore()
    if corpus is None:
        corpus = Corpus(Path(local_root("sec_edgar")), Path(local_root("normalized")),
                        heldout_tickers(), store.registry_labels())
    refused = corpus.refused
    labels = corpus.registry_labels or None
    lib = analysis.Library(corpus)
    families = set(family_names())
    app = FastAPI(title="H-X filing locator", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def no_trace(request: Request, call_next: Any) -> Any:
        # The browser keeps no copy of a page or an answer in its disk cache, and a link
        # to sec.gov carries no Referer naming the local page.
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def known(ticker: str) -> str:
        t = _ticker(ticker)
        if t in refused:
            raise HTTPException(403, "held-out filer: refused")
        if t not in set(corpus.tickers()):
            raise HTTPException(404, "filer not in the development corpus on disk")
        return t

    def family(name: str) -> str:
        if name not in families:
            raise HTTPException(400, "unknown concept family")
        return name

    def tickers_of(text: str) -> list[str]:
        out = [known(t) for t in text.split(",") if t.strip()]
        if not out:
            raise HTTPException(400, "no tickers")
        return list(dict.fromkeys(out))

    def years_of(text: str) -> list[int]:
        out = sorted({_year(int(y)) for y in text.split(",") if y.strip()})
        if not out:
            raise HTTPException(400, "no years")
        return out

    @app.get("/", response_class=HTMLResponse)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/static/{name}")
    def static(name: str) -> FileResponse:
        if name not in ("app.js", "app.css"):
            raise HTTPException(404)
        return FileResponse(STATIC / name)

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        from src.retriever.xbrl.entity_sectors import load_universe_groups

        try:
            groups = load_universe_groups()["groups"]
        except (OSError, ValueError):
            groups = {}
        tickers = set(corpus.tickers())
        mismatched = [analysis.filing_summary(f) for f in corpus.filings
                      if f.label_source != "registry"
                      or (f.dei_year is not None and f.dei_year != f.fiscal_year)
                      or f.folder_year != f.fiscal_year]
        return {
            "tickers": corpus.tickers(),
            "years": corpus.years(),
            "groups": {g: [t for t in m if t in tickers] for g, m in groups.items()},
            "family_tree": family_tree(),
            "db": store.status(),
            "labels_from_registry": labels is not None,
            "label_notes": mismatched,
            "filings": len(corpus.filings),
        }

    @app.get("/api/filings")
    def filings(ticker: str) -> list[dict[str, Any]]:
        return [analysis.filing_summary(f) for f in corpus.for_ticker(known(ticker))]

    @app.get("/api/family")
    def family_values(ticker: str, fy: int, family_name: str = Query(alias="family"),
                      member: str | None = None, dims: bool = False) -> dict[str, Any]:
        return analysis.family_values(lib, known(ticker), _year(fy), family(family_name),
                                      member or None, dims)

    @app.get("/api/cross_filer")
    def cross_filer(tickers: str, fy: int, family_name: str = Query(alias="family"),
                    member: str | None = None) -> dict[str, Any]:
        return analysis.cross_filer(lib, tickers_of(tickers), _year(fy),
                                    family(family_name), member or None)

    @app.get("/api/cross_period")
    def cross_period(ticker: str, years: str, family_name: str = Query(alias="family"),
                     member: str | None = None) -> dict[str, Any]:
        return analysis.cross_period(lib, known(ticker), years_of(years),
                                     family(family_name), member or None)

    @app.get("/api/coverage")
    def coverage(family_name: str | None = Query(default=None, alias="family")
                 ) -> dict[str, Any]:
        return analysis.coverage(lib, family(family_name) if family_name else None)

    @app.get("/api/concepts")
    def concepts(ticker: str, fy: int, q: str, dims: bool = True) -> dict[str, Any]:
        if len(q.strip()) < 3:
            raise HTTPException(400, "type at least 3 characters")
        return analysis.concept_search(lib, known(ticker), _year(fy), q, dims)

    @app.get("/api/members")
    def members(ticker: str, fy: int, family_name: str = Query(alias="family")
                ) -> dict[str, Any]:
        return analysis.members(lib, known(ticker), _year(fy), family(family_name))

    @app.get("/api/text")
    def text(ticker: str, fy: int, q: str, section: str | None = None) -> dict[str, Any]:
        if len(q.strip()) < 3:
            raise HTTPException(400, "type at least 3 characters")
        return analysis.text_search(lib, known(ticker), _year(fy), q, section or None)

    @app.get("/api/store/candidates")
    def candidates(ticker: str, fy: int, family_name: str | None = Query(
            default=None, alias="family"), accession: str | None = None
                   ) -> dict[str, Any]:
        try:
            rows = store.candidates(known(ticker), _year(fy),
                                    family(family_name) if family_name else None,
                                    accession or None)
        except Exception as exc:  # isolation point: the database may be down
            return {"ok": False, "error": type(exc).__name__ + ": " + str(exc)[:300]}
        return {"ok": True, "rows": rows}

    @app.post("/api/store/check")
    def check(body: CheckBody) -> dict[str, Any]:
        try:
            return store.check(known(body.ticker), _year(body.fiscal_year), body.expected,
                               body.scale, body.unit, body.concept, body.member,
                               body.accession)
        except Exception as exc:  # isolation point: the database may be down
            return {"status": "error", "error": type(exc).__name__ + ": " + str(exc)[:300]}

    @app.get("/api/sql")
    def sql(tickers: str, years: str, family_name: str = Query(alias="family"),
            accession: str | None = None) -> list[dict[str, str]]:
        try:
            return sql_templates(tickers_of(tickers), years_of(years), family(family_name),
                                 accession or None)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/sql/run")
    def sql_run(body: SqlBody) -> dict[str, Any]:
        # The connection is pinned to xbrl_db, read-only, with a statement timeout.
        return store.run_sql(body.sql)

    @app.post("/api/format")
    def fmt(body: FormatBody) -> dict[str, str]:
        scale = body.scale or default_scale(body.value, body.unit, body.decimals)
        return {"scale": scale, "expected": format_amount(body.value, scale, body.decimals)}

    @app.post("/api/case")
    def case(body: CaseBody) -> dict[str, Any]:
        try:
            return assess(body.case, frozenset(corpus.tickers()), refused,
                          store.resolve if store.status()["ok"] else None)
        except (ValueError, KeyError, TypeError) as exc:
            return {"yaml": "", "errors": [f"draft: {type(exc).__name__}: {exc}"],
                    "check": None}

    @app.get("/doc/{key}/{document}", response_class=HTMLResponse)
    def doc(key: str, document: str) -> HTMLResponse:
        filing = corpus.by_key(key)
        if filing is None or filing.ticker in refused:
            raise HTTPException(404)
        if not _DOC_NAME.fullmatch(document) or document not in filing.ixbrl_documents:
            raise HTTPException(404)
        raw = (filing.raw_dir / document).read_text(encoding="utf-8", errors="replace")
        bar = (f'<div id="hxl-bar">{html.escape(filing.ticker)} FY{filing.fiscal_year} '
               f'{html.escape(filing.form)} · {html.escape(filing.accession)} · '
               f'<a href="{html.escape(filing.sec_viewer_url(document))}" '
               f'target="_blank" rel="noopener">open on sec.gov</a> · <span></span></div>')
        inject = _HIGHLIGHT + bar
        match = re.search(r"<body[^>]*>", raw, re.IGNORECASE)
        page = (raw[:match.end()] + inject + raw[match.end():]) if match else inject + raw
        return HTMLResponse(page)

    return app
