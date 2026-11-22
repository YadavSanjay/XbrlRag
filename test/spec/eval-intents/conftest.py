"""Fixtures and independent oracles for the eval-intents spec tests.

Spec: docs/specs/eval-intents.md. Everything under test is pure apart from the two YAML
loaders, which read the committed fixtures next to this file (or a file a test writes to a
temporary directory). The oracles below restate the spec's rules with ``hashlib``, ``json``
and ``re`` and read the fixture YAML directly with ``yaml.safe_load``, never through the code
under test. Exposed through one session-scoped ``kit`` fixture so hypothesis tests can use it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PHRASEBOOK = HERE / "phrasebook.yaml"
LEXICON = HERE / "lexicon.yaml"

SHAPES = (
    "lookup", "dimensional", "breakdown",
    "compute.yoy_growth", "compute.difference", "compute.cagr", "compute.ratio",
    "trend", "point_in_time",
    "screen.top", "screen.bottom", "screen.sum", "screen.median",
    "peer_relative",
)
PLACEHOLDERS = ("company", "metric", "metric_2", "dimension", "period", "first_period",
                "last_period", "filing_period", "universe", "n")

# The spec's placeholder table: shape -> (fillable, required).
_SPAN = {"company", "metric", "first_period", "last_period"}
FILL = {
    "lookup": ({"company", "metric", "period"},) * 2,
    "dimensional": ({"company", "metric", "dimension", "period"},) * 2,
    "breakdown": ({"company", "metric", "dimension", "period"},) * 2,
    "compute.yoy_growth": (_SPAN, {"company", "metric", "last_period"}),
    "compute.difference": (_SPAN, _SPAN),
    "compute.cagr": (_SPAN, _SPAN),
    "trend": (_SPAN, _SPAN),
    "compute.ratio": ({"company", "metric", "metric_2", "period"},) * 2,
    "point_in_time": ({"company", "metric", "period", "filing_period"},) * 2,
    "screen.top": ({"metric", "period", "universe", "n"},) * 2,
    "screen.bottom": ({"metric", "period", "universe", "n"},) * 2,
    "screen.sum": ({"metric", "period", "universe"},) * 2,
    "screen.median": ({"metric", "period", "universe"},) * 2,
    "peer_relative": ({"company", "metric", "period", "universe"},) * 2,
}

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
PERIOD_PLACEHOLDERS = {"period", "first_period", "last_period", "filing_period"}


def raw_phrasebook() -> list[dict]:
    return yaml.safe_load(PHRASEBOOK.read_text(encoding="utf-8"))["templates"]


def raw_lexicon() -> dict:
    lex = yaml.safe_load(LEXICON.read_text(encoding="utf-8"))
    lex["companies"] = {k.upper(): v for k, v in lex["companies"].items()}
    lex["period_ends"] = {k.upper(): {int(y): d for y, d in v.items()}
                          for k, v in lex["period_ends"].items()}
    return lex


def norm(kw: dict) -> dict:
    """The nine normalised intent fields of a kwargs dict (the spec's normalisation rules)."""
    return {
        "kind": kw["kind"],
        "tickers": [t.strip().upper() for t in kw.get("tickers", ())],
        "fiscal_years": sorted(set(kw.get("fiscal_years", ()))),
        "metrics": list(kw.get("metrics", ())),
        "dimension": kw.get("dimension"),
        "operation": kw.get("operation"),
        "universe": kw.get("universe"),
        "n": kw.get("n"),
        "filing_year": kw.get("filing_year"),
    }


def oracle_id(kw: dict) -> str:
    blob = json.dumps(norm(kw), sort_keys=True, separators=(",", ":"))
    return "int-" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def shape_of(kw: dict) -> str:
    return f"{kw['kind']}.{kw['operation']}" if kw["kind"] in ("compute", "screen") else kw["kind"]


def oracle_axis(key: str, share: float, salt: str) -> bool:
    return int(hashlib.sha1(f"{salt}:{key}".encode()).hexdigest()[:8], 16) / 2**32 < share


def uses(text: str) -> set[str]:
    return set(re.findall(r"\{([a-z_0-9]+)\}", text))


def period_text(form: str, year: int, ticker: str | None, lex: dict) -> str:
    if form == "fy_short":
        return f"FY{year % 100:02d}"
    if form == "fiscal":
        return f"fiscal {year}"
    if form == "fiscal_year":
        return f"fiscal year {year}"
    d = dt.date.fromisoformat(lex["period_ends"][ticker][year])
    return f"the fiscal year ended {MONTHS[d.month - 1]} {d.day}, {d.year}"


def candidates(kw: dict, templates: list[dict], lex: dict) -> list[tuple[str, str, str, str]]:
    """The spec's candidate phrasings (template_id, company, metric, period) for one intent."""
    n = norm(kw)
    shape = shape_of(kw)
    tickers, metrics = n["tickers"], n["metrics"]
    years = list(n["fiscal_years"]) + ([n["filing_year"]] if n["filing_year"] else [])
    n_syn = min(len(lex["metrics"][m].get("synonyms", [])) for m in metrics)
    n_sh = min(len(lex["metrics"][m].get("shorthand", [])) for m in metrics)
    ye_ok = len(tickers) == 1 and all(y in lex["period_ends"].get(tickers[0], {}) for y in years)
    out = []
    for t in templates:
        if shape not in t["shapes"]:
            continue
        u = uses(t["text"])
        if "company" in u:
            cf = ["ticker", "short", "legal"]
        else:
            cf = ["ticker"] if tickers else ["none"]
        mf = ["label"]
        if u & {"metric", "metric_2"}:
            mf += [f"synonym:{i}" for i in range(n_syn)] + [f"shorthand:{i}" for i in range(n_sh)]
        pf = (["fy_short", "fiscal", "fiscal_year"] + (["year_ended"] if ye_ok else [])
              if u & PERIOD_PLACEHOLDERS else ["fiscal"])
        out += [(t["id"], c, m, p) for c in cf for m in mf for p in pf]
    return out


def phrasing_digest(kw: dict, cand: tuple[str, str, str, str]) -> str:
    key = f"{oracle_id(kw)}|{cand[0]}|{cand[1]}|{cand[2]}|{cand[3]}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def expected_selection(kw: dict, per_intent: int, templates: list[dict],
                       lex: dict) -> dict[str, tuple]:
    """case_id -> (template_id, company, metric, period) the spec's selection keeps."""
    ranked = sorted(candidates(kw, templates, lex), key=lambda c: phrasing_digest(kw, c))
    return {f"{oracle_id(kw)}-{phrasing_digest(kw, c)[:8]}": c for c in ranked[:per_intent]}


def write_yaml(directory: Path, name: str, doc: object) -> Path:
    p = Path(directory) / name
    p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return p


# Valid intent kwargs, one or more per shape, over the fixture lexicon.
def _kw(kind, tickers, years, metrics, **extra):
    return dict(kind=kind, tickers=tickers, fiscal_years=years, metrics=metrics, **extra)


EXAMPLES = {
    "lookup": _kw("lookup", ["AAPL"], [2024], ["revenue"]),
    "dimensional": _kw("dimensional", ["NVDA"], [2025], ["revenue"], dimension="data_center"),
    "breakdown": _kw("breakdown", ["AAPL"], [2023], ["revenue"], dimension="segment"),
    "compute.yoy_growth": _kw("compute", ["AAPL"], [2023, 2024], ["revenue"],
                              operation="yoy_growth"),
    "compute.difference": _kw("compute", ["MSFT"], [2020, 2024], ["revenue"],
                              operation="difference"),
    "compute.cagr": _kw("compute", ["AAPL"], [2022, 2024], ["revenue"], operation="cagr"),
    "compute.ratio": _kw("compute", ["NVDA"], [2024], ["gross_profit", "revenue"],
                         operation="ratio"),
    "trend": _kw("trend", ["MSFT"], [2022, 2023, 2024], ["revenue"]),
    "point_in_time": _kw("point_in_time", ["AAPL"], [2022], ["revenue"], filing_year=2024),
    "screen.top": _kw("screen", [], [2024], ["revenue"], operation="top", universe="big_tech", n=3),
    "screen.bottom": _kw("screen", [], [2022], ["rd_expense"], operation="bottom",
                         universe="covered", n=5),
    "screen.sum": _kw("screen", [], [2024], ["gross_profit"], operation="sum",
                      universe="big_tech"),
    "screen.median": _kw("screen", [], [2023], ["operating_income"], operation="median",
                         universe="covered"),
    "peer_relative": _kw("peer_relative", ["AAPL"], [2024], ["rd_expense"], universe="covered"),
}


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    return SimpleNamespace(
        repo=REPO, phrasebook=PHRASEBOOK, lexicon=LEXICON, shapes=SHAPES,
        placeholders=PLACEHOLDERS,
        fill=FILL, raw_phrasebook=raw_phrasebook, raw_lexicon=raw_lexicon, norm=norm,
        oracle_id=oracle_id, shape_of=shape_of, oracle_axis=oracle_axis, uses=uses,
        period_text=period_text, candidates=candidates, phrasing_digest=phrasing_digest,
        expected_selection=expected_selection, write_yaml=write_yaml, examples=EXAMPLES,
    )
