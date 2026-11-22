# The tool set of the ``frontier_sec_tools`` arm, over the public SEC APIs.
#
# Eleven tools, fixed and published with the results. Each returns a JSON-safe payload built
# from what the SEC returned. Nothing here reads the fact store, Qdrant or the corpus.
#
# Spec: docs/specs/frontier-sec-tools.md.

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import statistics
import threading
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import yaml

from src.eval.keys.base import HttpTransport
from src.eval.sec_tools.client import (
    SecApiClient,
    SecRequestError,
    SecSnapshot,
    SecSnapshotMissError,
    check_date,
)
from src.eval.sec_tools.filing_text import filing_text
from src.eval.sec_tools.paging import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    ToolError,
    fit_rows,
    json_size,
)
from src.llm.gateway.tools import ToolSpec
from src.llm.run_budget import RunHalt
from src.logging_config import get_logger

logger = get_logger(__name__)

__all__ = [
    "SecToolbox",
    "ToolOutcome",
    "analyze_rows",
    "calculate",
    "default_withheld_path",
    "load_universes",
    "load_withheld_ciks",
    "tool_specs",
    "tools_fingerprint",
]

# The repository root: this file is src/eval/sec_tools/toolbox.py.
_REPO_ROOT = Path(__file__).resolve().parents[3]

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
FACTS_BASE = "https://data.sec.gov/api/xbrl/companyfacts"
FRAMES_BASE = "https://data.sec.gov/api/xbrl/frames"
FTS_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"

DEFAULT_MAX_RESULT_CHARS = 40_000
DEFAULT_READ_CHARS = 20_000
DEFAULT_CONTEXT_CHARS = 400
MAX_CONTEXT_CHARS = 5_000
MAX_TICKERS = 200
# The most filers one get_concept call reads (frontier-tools-v2 requirement 2).
MAX_CONCEPT_CIKS = 50
FTS_PAGE = 100
# EDGAR full-text search covers filings from 2001 on.
FTS_START = "2001-01-01"
MAX_EXPRESSION_CHARS = 2_000
MAX_EXPONENT = 64
_TEXT_CACHE_SIZE = 8

_CIK = re.compile(r"\d{1,10}")
_ACCESSION_DASHED = re.compile(r"(\d{10})-(\d{2})-(\d{6})")
_ACCESSION_PLAIN = re.compile(r"\d{18}")
_NAME = re.compile(r"[A-Za-z0-9_.-]+")
_UNIT = re.compile(r"[A-Za-z0-9_./-]+")
_FORM = re.compile(r"[A-Za-z0-9 ./-]{1,20}")
_TICKER = re.compile(r"[A-Za-z0-9.-]{1,12}")

class ToolOutcome:
    """What one tool call produced."""

    def __init__(
        self,
        payload: dict[str, Any],
        urls: list[str],
        error_code: str | None = None,
        rows: list[dict[str, Any]] | None = None,
        derived_from: list[str] | None = None,
    ) -> None:
        """Holds the outcome.

        Args:
            payload: The JSON-safe result the model sees, one page of it for a list.
            urls: The SEC URLs the result came from.
            error_code: None on success, else the requirement's error code.
            rows: Every row of a list result, before paging; None for a tool without rows.
            derived_from: The source ids :func:`analyze_rows` read; None for any other tool.
        """
        self.payload = payload
        self.urls = urls
        self.error_code = error_code
        self.rows = rows
        self.derived_from = derived_from

    def __repr__(self) -> str:
        return (f"ToolOutcome(error_code={self.error_code!r}, urls={len(self.urls)}, "
                f"rows={None if self.rows is None else len(self.rows)})")


def error_outcome(code: str, message: str, urls: list[str], **extra: Any) -> ToolOutcome:
    """A failed tool call as an outcome the model reads (requirement 16).

    Args:
        code: The error code.
        message: What went wrong, for the model.
        urls: The URLs read before the failure.
        **extra: Further payload fields.

    Returns:
        ``ToolOutcome({"error": code, "message": message, ...}, urls, error_code=code)``.
    """
    return ToolOutcome({"error": code, "message": message, **extra}, list(urls),
                       error_code=code)


# ---------------------------------------------------------------------------------
# The tool definitions (requirement 12). Their fingerprint is published with a run.
# ---------------------------------------------------------------------------------

_PAGING = {
    "offset": {"type": "integer", "minimum": 0,
               "description": "The first row to return (default 0); pass next_offset to "
                              "continue."},
    "limit": {"type": "integer", "minimum": 0,
              "description": "The most rows to return (default 100, at most 1000)."},
}
_PAGING_NOTE = (
    " A list result carries total, offset, next_offset (null at the end) and truncated; a "
    "result is cut to whole rows to stay under the size cap, so call again with "
    "offset=next_offset to read the rest. Nothing is dropped silently."
)
_CIK_ARG = {"type": "string", "description": "The SEC CIK, 1 to 10 digits, padded or not."}
_ACCESSION_ARG = {"type": "string",
                  "description": "The accession number, ##########-##-###### or 18 digits."}
_DATE_ARG = {"type": "string", "description": "A date, YYYY-MM-DD."}
_FORMS_ARG = {"type": "array", "items": {"type": "string"},
              "description": "SEC form types to keep, such as 10-K, 10-K/A, 10-Q, 20-F."}
_WHERE_ITEM = {
    "type": "object",
    "properties": {
        "column": {"type": "string"},
        "op": {"type": "string",
               "enum": ["eq", "ne", "lt", "le", "gt", "ge", "in", "contains", "is_number"]},
        "value": {},
    },
    "required": ["column", "op"],
}
_SIDE = {"type": "object", "properties": {"where": {"type": "array", "items": _WHERE_ITEM}},
         "required": ["where"]}


def _spec(name: str, description: str, properties: dict[str, Any],
          required: list[str]) -> ToolSpec:
    parameters: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = required
    return ToolSpec(name, description, parameters)


def tool_specs() -> list[ToolSpec]:
    """The fixed tool set, in a stable order.

    Returns:
        One definition per tool.
    """
    return [
        _spec(
            "find_company",
            "Finds SEC filers in the SEC's company tickers file. With query: an exact ticker "
            "match first, then every company whose name contains the query "
            "(case-insensitive). With tickers (a list of up to 200): the company for each "
            "ticker, and not_found for the rest. Returns companies: [{cik, ticker, name}], "
            "with cik as ten digits. This searches company names only, not filings."
            + _PAGING_NOTE,
            {"query": {"type": "string", "description": "A ticker or part of a name."},
             "tickers": {"type": "array", "items": {"type": "string"}, "maxItems": 200},
             **_PAGING},
            [],
        ),
        _spec(
            "list_filings",
            "Lists a filer's filings from its EDGAR submissions history, older pages "
            "included, newest first. Returns rows: [{accession, form, filing_date, "
            "report_date, primary_document}]. Filter with forms and filed_from / filed_to."
            + _PAGING_NOTE,
            {"cik": _CIK_ARG, "forms": _FORMS_ARG, "filed_from": _DATE_ARG,
             "filed_to": _DATE_ARG, **_PAGING},
            ["cik"],
        ),
        _spec(
            "list_concepts",
            "Lists the XBRL concepts a filer has reported (from SEC companyfacts). Returns "
            "rows: [{taxonomy, tag, label, units, first_end, last_end, facts}]. query keeps "
            "concepts whose tag or label contains it (case-insensitive). A filer may report "
            "one metric under several concepts, or change concept between years."
            + _PAGING_NOTE,
            {"cik": _CIK_ARG, "query": {"type": "string"}, **_PAGING},
            ["cik"],
        ),
        _spec(
            "get_concept",
            "Returns every fact one or more filers reported for one concept (from SEC "
            "companyfacts): label, description, units, not_found, and rows: the SEC's entries "
            "unchanged (end, start, val, accn, fy, fp, form, filed, frame), each with its "
            "unit, cik (ten digits), taxonomy and tag, sorted newest first: by end "
            "descending, then cik, then filed descending, then accn descending. Give cik for "
            f"one filer, or ciks for 1 to {MAX_CONCEPT_CIKS} filers, not both. Nothing is "
            "deduplicated: a restated period appears once per filing that reported it; fy is "
            "the fiscal year of the filing, not of the fact. Filter with unit, form, "
            "fiscal_year (the filing's fy) and end_from / end_to; each filer is filtered "
            "alike. not_found lists the filers that do not report the concept; the call is "
            "not_found only when none does. label and description are the first reporting "
            "filer's." + _PAGING_NOTE,
            {"cik": _CIK_ARG,
             "ciks": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                      "maxItems": MAX_CONCEPT_CIKS,
                      "description": "Several filers' CIKs, in place of cik."},
             "taxonomy": {"type": "string", "description": "us-gaap, ifrs-full, dei or srt."},
             "tag": {"type": "string"}, "unit": {"type": "string"},
             "form": {"type": "string"}, "fiscal_year": {"type": "integer"},
             "end_from": _DATE_ARG, "end_to": _DATE_ARG, **_PAGING},
            ["taxonomy", "tag"],
        ),
        _spec(
            "get_frame",
            "Returns one concept for every filer for one period (SEC frames API): label, "
            "description, and rows: the SEC's entries unchanged (accn, cik, entityName, loc, "
            "start, end, val), sorted by val (order desc by default, or asc), then cik. "
            "period is calendar-aligned: CY2023 (annual), CY2023Q1 (a quarter), CY2023Q4I "
            "(an instant). Frames give no filing date, so as_of_filtered is false. ciks keeps "
            "only those filers. A top N takes one call with limit=N." + _PAGING_NOTE,
            {"taxonomy": {"type": "string"}, "tag": {"type": "string"},
             "unit": {"type": "string", "description": "USD, shares, USD/shares, pure, ..."},
             "period": {"type": "string"},
             "ciks": {"type": "array", "items": {"type": "string"}},
             "order": {"type": "string", "enum": ["desc", "asc"]}, **_PAGING},
            ["taxonomy", "tag", "unit", "period"],
        ),
        _spec(
            "search_filings_text",
            "EDGAR full-text search over filings' documents. query uses its syntax (quote a "
            "phrase). Returns total (the SEC's hit count) and rows: [{accession, form, "
            "filing_date, period_ending, ciks, companies, document}], 100 to a page; pass "
            "page=2, 3, ... for more (next_page says whether there is one). Filter with forms, "
            "ciks and filed_from / filed_to.",
            {"query": {"type": "string"}, "forms": _FORMS_ARG,
             "ciks": {"type": "array", "items": {"type": "string"}},
             "filed_from": _DATE_ARG, "filed_to": _DATE_ARG,
             "page": {"type": "integer", "minimum": 1}},
            ["query"],
        ),
        _spec(
            "list_filing_documents",
            "Lists the documents in one filing's EDGAR folder. Returns rows: [{name, size}]."
            + _PAGING_NOTE,
            {"cik": _CIK_ARG, "accession": _ACCESSION_ARG, **_PAGING},
            ["cik", "accession"],
        ),
        _spec(
            "read_filing",
            "Reads one document of a filing as plain text, one paragraph or table row per "
            "line (table cells joined by ' | '). document defaults to the filing's primary "
            "document. Returns text, offset, total_chars, next_offset (null at the end), "
            "document, form and filing_date. Reads max_chars characters (default 20000) from "
            "offset; call again with offset=next_offset for more, or start at a position "
            "from search_filing.",
            {"cik": _CIK_ARG, "accession": _ACCESSION_ARG, "document": {"type": "string"},
             "offset": {"type": "integer", "minimum": 0},
             "max_chars": {"type": "integer", "minimum": 1}},
            ["cik", "accession"],
        ),
        _spec(
            "search_filing",
            "Finds a literal phrase (case-insensitive, not a regular expression) in one "
            "document of a filing, as read_filing reads it. Returns rows: [{position, "
            "snippet}], where position is an offset read_filing accepts and snippet holds "
            "context_chars characters (default 400) on each side." + _PAGING_NOTE,
            {"cik": _CIK_ARG, "accession": _ACCESSION_ARG, "query": {"type": "string"},
             "document": {"type": "string"},
             "context_chars": {"type": "integer", "minimum": 0}, **_PAGING},
            ["cik", "accession", "query"],
        ),
        _spec(
            "calculate",
            "Evaluates an arithmetic expression: numbers, + - * / **, parentheses, and abs, "
            "round, min, max, sum, median. Returns value. Reads nothing. For anything over "
            "the rows of a result, use analyze_rows instead of copying numbers here.",
            {"expression": {"type": "string"}},
            ["expression"],
        ),
        _spec(
            "analyze_rows",
            "Computes over every row of earlier results in this case, named by their source "
            "ids (S1, S2, ...), including rows past the page you saw. Reads no data of its own. "
            "op: sum, mean, median, min, max, count, sort, rank, percent_change, ratio. column "
            "names a numeric column (required except for count). where: [{column, op, value}], "
            "all must hold; op eq, ne, lt, le, gt, ge, in (value a list), contains, is_number. "
            "Every row also has the column source_id, the source it was read from (a row's own "
            "source_id field, as in a sort or rank result, wins). latest_by: columns; keeps, "
            "per distinct key and filer (cik), the row with the latest filed, then accn: rows "
            "of different filers are never merged. by: one column, for one result per group. "
            "percent_change takes current and "
            "base, ratio takes numerator and denominator, each {where: [...]}, applied on top of "
            "the top-level selection. Returns value (or rows for sort, rank and by, paged), "
            "count, inputs and row_refs. The result is itself a source." + _PAGING_NOTE,
            {"sources": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                         "maxItems": 50},
             "op": {"type": "string",
                    "enum": ["sum", "mean", "median", "min", "max", "count", "sort", "rank",
                             "percent_change", "ratio"]},
             "column": {"type": "string"},
             "where": {"type": "array", "items": _WHERE_ITEM},
             "latest_by": {"type": "array", "items": {"type": "string"}},
             "by": {"type": "string"},
             "order": {"type": "string", "enum": ["desc", "asc"]},
             "current": _SIDE, "base": _SIDE, "numerator": _SIDE, "denominator": _SIDE,
             **_PAGING},
            ["sources", "op"],
        ),
    ]


def tools_fingerprint() -> str:
    """The SHA-256 of the tool definitions, recorded with every run.

    Returns:
        The hex digest of the definitions as canonical JSON.
    """
    definitions = [
        {"name": s.name, "description": s.description, "parameters": s.parameters}
        for s in tool_specs()
    ]
    return hashlib.sha256(json.dumps(definitions, sort_keys=True).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------------
# calculate (requirement 15): the parsed tree, never eval.
# ---------------------------------------------------------------------------------

_BINARY: dict[type, Callable[[float, float], float]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}


def _median(*values: float) -> float:
    if not values:
        raise ValueError("median() needs at least one value")
    return float(statistics.median(values))


def _round(value: float, digits: float = 0) -> float:
    if digits != int(digits):
        raise ValueError("round() takes a whole number of digits")
    return float(round(value, int(digits)))


_FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": lambda x: abs(x),
    "round": _round,
    "min": lambda *xs: min(xs),
    "max": lambda *xs: max(xs),
    "sum": lambda *xs: math.fsum(xs),
    "median": _median,
}


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only numbers are allowed")
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _evaluate(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_EXPONENT:
                raise ValueError(f"an exponent above {MAX_EXPONENT} in size is not allowed")
            result = left ** right
            if isinstance(result, complex):
                raise ValueError("the result is not a real number")
            return float(result)
        operation = _BINARY.get(type(node.op))
        if operation is None:
            raise ValueError(f"operator {type(node.op).__name__} is not allowed")
        if isinstance(node.op, ast.Div) and right == 0:
            raise ValueError("division by zero")
        return operation(left, right)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise ValueError("only abs, round, min, max, sum and median may be called")
        if node.keywords:
            raise ValueError("keyword arguments are not allowed")
        args = [_evaluate(a) for a in node.args]
        if not args:
            raise ValueError(f"{node.func.id}() needs an argument")
        return float(_FUNCTIONS[node.func.id](*args))
    raise ValueError(f"{type(node).__name__} is not allowed in an expression")


def calculate(expression: str) -> float:
    """Evaluates an arithmetic expression without ``eval``.

    Args:
        expression: Numbers, ``+ - * / **``, parentheses, and ``abs``, ``round``, ``min``,
            ``max``, ``sum`` and ``median``.

    Returns:
        The value.

    Raises:
        ValueError: If the expression is not allowed, is too long, or has no finite value.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("the expression is empty")
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise ValueError(f"the expression is over {MAX_EXPRESSION_CHARS} characters")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"not an arithmetic expression: {exc.msg}") from exc
    try:
        value = _evaluate(tree)
    except (OverflowError, ZeroDivisionError, TypeError) as exc:
        raise ValueError(f"the expression has no finite value: {exc}") from exc
    if not math.isfinite(value):
        raise ValueError("the result is not finite")
    return value


def analyze_rows(
    arguments: dict[str, Any] | None,
    results: Mapping[str, ToolOutcome],
    *,
    max_result_chars: int = 40000,
) -> ToolOutcome:
    """Computes over the rows of earlier tool results, without code or the network.

    Args:
        arguments: The tool's arguments, as the model gave them.
        results: This case's successful outcomes, by source id.
        max_result_chars: The most characters of the result, as JSON.

    Returns:
        The outcome; a bad reference or selection is an outcome with an error code.
    """
    try:
        return _Analysis(arguments, results, max_result_chars).run()
    except ToolError as exc:
        return error_outcome(exc.code, exc.message, [], **exc.extra)


# ---------------------------------------------------------------------------------
# analyze_rows (requirements 30-35): a spreadsheet's filter and summary functions.
# ---------------------------------------------------------------------------------

MAX_SOURCES = 50
MAX_ROWS_READ = 100_000
MAX_ROW_REFS = 100
MAX_UNMATCHED = 100
_AGGREGATES = ("sum", "mean", "median", "min", "max", "count")
_RANKINGS = ("sort", "rank")
_COMPARISONS = {"percent_change": ("current", "base"), "ratio": ("numerator", "denominator")}
_OPS = (*_AGGREGATES, *_RANKINGS, *_COMPARISONS)
_FILTER_OPS = ("eq", "ne", "lt", "le", "gt", "ge", "in", "contains", "is_number")


def _number(value: Any) -> float | None:
    """A finite JSON number as a float; None for text, booleans, null and non-finite."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _cik_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _with_source_id(row: Mapping[str, Any], source_id: str) -> Mapping[str, Any]:
    """The row with the ``source_id`` column; a row's own ``source_id`` field wins."""
    if "source_id" in row:
        return row
    return {**row, "source_id": source_id}


class _Row:
    """A row read from a source, with where it came from."""

    __slots__ = ("source_id", "index", "data")

    def __init__(self, source_id: str, index: int, data: Mapping[str, Any]) -> None:
        self.source_id = source_id
        self.index = index
        self.data = data


def _matches(row: Mapping[str, Any], condition: Mapping[str, Any]) -> bool:
    column, op = condition["column"], condition["op"]
    wanted: Any = condition.get("value")
    if column not in row:
        return False
    value = row[column]
    if op == "is_number":
        return _number(value) is not None
    if column == "cik":
        value = _cik_int(value)
        wanted = [_cik_int(w) for w in wanted] if op == "in" else _cik_int(wanted)
        if value is None:
            return False
    if op == "in":
        return any(_equal(value, w) for w in wanted)
    if op == "eq":
        return _equal(value, wanted)
    if op == "ne":
        return not _equal(value, wanted)
    if op == "contains":
        return isinstance(value, str) and isinstance(wanted, str) \
            and wanted.lower() in value.lower()
    left, right = _number(value), _number(wanted)
    if left is None or right is None:
        if not (isinstance(value, str) and isinstance(wanted, str)):
            return False
        left_t, right_t = value, wanted
        return {"lt": left_t < right_t, "le": left_t <= right_t,
                "gt": left_t > right_t, "ge": left_t >= right_t}[op]
    return {"lt": left < right, "le": left <= right, "gt": left > right,
            "ge": left >= right}[op]


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return bool(a == b)


def _sort_key(value: Any) -> tuple[str, Any]:
    """A total order over JSON keys: numbers, then text, then the rest by their JSON."""
    number = _number(value)
    if number is not None:
        return ("0", number)
    if isinstance(value, str):
        return ("1", value)
    return ("2", json.dumps(value, sort_keys=True, default=str))


class _Analysis:
    """One ``analyze_rows`` call."""

    def __init__(self, arguments: Any, results: Mapping[str, ToolOutcome], cap: int) -> None:
        self.results = results
        self.cap = cap
        if not isinstance(arguments, dict):
            raise _bad("the arguments must be a JSON object")
        self.args = arguments
        sources = arguments.get("sources")
        if (not isinstance(sources, list) or not sources or len(sources) > MAX_SOURCES
                or not all(isinstance(s, str) for s in sources)):
            raise _bad(f"sources must be a list of 1 to {MAX_SOURCES} source ids")
        self.sources = list(dict.fromkeys(sources))
        op = arguments.get("op")
        if not isinstance(op, str) or op not in _OPS:
            raise _bad(f"op must be one of {', '.join(_OPS)}")
        self.op: str = op
        column = arguments.get("column")
        if column is None and self.op != "count":
            raise _bad(f"column is required for {self.op}")
        if column is not None and not isinstance(column, str):
            raise _bad("column must be a column name")
        self.column: str | None = column
        self.where = self._conditions(arguments.get("where"), "where")
        latest_by = arguments.get("latest_by")
        if latest_by is not None and (not isinstance(latest_by, list) or not latest_by
                                      or not all(isinstance(c, str) for c in latest_by)):
            raise _bad("latest_by must be a list of column names")
        self.latest_by: list[str] | None = latest_by
        by = arguments.get("by")
        if by is not None and not isinstance(by, str):
            raise _bad("by must be one column name")
        if by is not None and self.op in _RANKINGS:
            raise _bad("by does not apply to sort or rank; use where to select rows")
        self.by: str | None = by
        self.order = arguments.get("order", "desc")
        if self.order not in ("desc", "asc"):
            raise _bad("order must be desc or asc")
        self.offset, self.limit = _arg_paging(arguments)
        self.sides: dict[str, list[dict[str, Any]]] = {}
        for side in _COMPARISONS.get(self.op, ()):
            spec = arguments.get(side)
            if not isinstance(spec, dict):
                raise _bad(f"{self.op} needs {side}: {{\"where\": [...]}}")
            self.sides[side] = self._conditions(spec.get("where"), f"{side}.where")

    @staticmethod
    def _conditions(value: Any, name: str) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise _bad(f"{name} must be a list of {{column, op, value}}")
        conditions = []
        for item in value:
            if not isinstance(item, dict) or not isinstance(item.get("column"), str):
                raise _bad(f"each {name} entry needs a column")
            op = item.get("op")
            if op not in _FILTER_OPS:
                raise _bad(f"{name} op must be one of {', '.join(_FILTER_OPS)}")
            if op == "in" and not isinstance(item.get("value"), list):
                raise _bad(f"{name} op in needs a list value")
            if op != "is_number" and "value" not in item:
                raise _bad(f"{name} op {op} needs a value")
            conditions.append({k: item[k] for k in ("column", "op", "value") if k in item})
        return conditions

    # -- reading ---------------------------------------------------------------------

    def _read(self) -> list[_Row]:
        rows: list[_Row] = []
        for source_id in self.sources:
            outcome = self.results.get(source_id)
            if outcome is None:
                raise ToolError("bad_reference",
                                f"{source_id} is not a successful result of this case")
            if not outcome.rows:
                raise ToolError("bad_reference", f"{source_id} holds no rows")
            if len(rows) + len(outcome.rows) > MAX_ROWS_READ:
                raise _bad(f"more than {MAX_ROWS_READ} rows in one call; select fewer sources")
            rows.extend(_Row(source_id, i, _with_source_id(r, source_id))
                        for i, r in enumerate(outcome.rows) if isinstance(r, Mapping))
        return rows

    def _check_columns(self, rows: list[_Row]) -> None:
        seen: set[str] = set()
        for row in rows:
            seen.update(row.data.keys())
        wanted = [self.column] if self.column is not None else []
        wanted += [c["column"] for c in self.where]
        wanted += list(self.latest_by or [])
        wanted += [self.by] if self.by is not None else []
        for conditions in self.sides.values():
            wanted += [c["column"] for c in conditions]
        for column in wanted:
            if column is not None and column not in seen:
                raise ToolError("unknown_column", f"no row read has the column {column!r}")

    def _latest_key(self, row: _Row) -> str:
        """The row's ``latest_by`` key: its filer, as an integer CIK, and the named columns."""
        assert self.latest_by is not None
        values = []
        for column in self.latest_by:
            value = row.data.get(column)
            if column == "cik":
                as_int = _cik_int(value)
                value = as_int if as_int is not None else value
            values.append(value)
        filer = _cik_int(row.data.get("cik"))
        head: list[Any] = ["cik", filer] if filer is not None else ["-"]
        return json.dumps([*head, values], sort_keys=True, default=str)

    def _latest(self, rows: list[_Row]) -> list[_Row]:
        assert self.latest_by is not None
        best: dict[str, _Row] = {}
        for row in rows:
            key = self._latest_key(row)
            current = best.get(key)
            rank = (str(row.data.get("filed") or ""), str(row.data.get("accn") or ""))
            if current is None or rank > (str(current.data.get("filed") or ""),
                                          str(current.data.get("accn") or "")):
                best[key] = row
        kept = {id(r) for r in best.values()}
        return [r for r in rows if id(r) in kept]

    def _value(self, row: _Row) -> float:
        assert self.column is not None
        value = _number(row.data.get(self.column))
        assert value is not None
        return value

    def _check_numeric(self, rows: list[_Row]) -> None:
        if self.column is None or self.op == "count":
            return
        bad = [r for r in rows if _number(r.data.get(self.column)) is None]
        if bad:
            raise ToolError(
                "non_numeric",
                f"{len(bad)} selected row(s) have no finite number in {self.column!r}; add "
                f"{{\"column\": \"{self.column}\", \"op\": \"is_number\"}} to where to leave "
                "them out",
                rows=len(bad), first_source_id=bad[0].source_id, first_index=bad[0].index,
            )

    def _ref(self, row: _Row) -> dict[str, Any]:
        value = row.data.get(self.column) if self.column is not None else None
        return {"source_id": row.source_id, "index": row.index, "value": value}

    # -- computing -------------------------------------------------------------------

    def run(self) -> ToolOutcome:
        read = self._read()
        self._check_columns(read)
        selected = [r for r in read if all(_matches(r.data, c) for c in self.where)]
        if self.latest_by:
            selected = self._latest(selected)
        if not selected:
            raise ToolError("empty_selection", "no row matches the selection")
        self._check_numeric(selected)
        counts: dict[str, int] = {}
        for row in selected:
            counts[row.source_id] = counts.get(row.source_id, 0) + 1
        inputs = []
        for source_id in self.sources:
            outcome = self.results[source_id]
            held = len(outcome.rows or [])
            total = outcome.payload.get("total") if isinstance(outcome.payload, dict) else None
            rows_total = total if isinstance(total, int) and not isinstance(total, bool) \
                else held
            inputs.append({"source_id": source_id, "rows_total": rows_total,
                           "rows_selected": counts.get(source_id, 0),
                           "complete": held >= rows_total})
        head = {"op": self.op, "column": self.column, "where": self.where,
                "count": len(selected), "inputs": inputs}
        if self.by is not None:
            payload, rows = self._grouped(head, selected)
        elif self.op in _RANKINGS:
            payload, rows = self._ranking(head, selected)
        elif self.op in _COMPARISONS:
            payload, rows = self._compare(head, selected), None
        else:
            payload, rows = self._aggregate(head, selected), None
        urls: list[str] = []
        for source_id in self.sources:
            for url in self.results[source_id].urls:
                if url not in urls:
                    urls.append(url)
        return ToolOutcome(payload, urls, rows=rows, derived_from=list(self.sources))

    def _aggregate_value(self, rows: list[_Row]) -> tuple[float | int, list[_Row]]:
        """The value of an aggregate over *rows*, and the rows that fix it."""
        if self.op == "count":
            return len(rows), rows
        values = [self._value(r) for r in rows]
        if self.op == "sum":
            return math.fsum(values), rows
        if self.op == "mean":
            return math.fsum(values) / len(values), rows
        if self.op in ("min", "max"):
            pick = min if self.op == "min" else max
            target = pick(values)
            row = rows[values.index(target)]
            return target, [row]
        ordered = sorted(rows, key=self._value)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return self._value(ordered[middle]), [ordered[middle]]
        pair = ordered[middle - 1: middle + 1]
        return (self._value(pair[0]) + self._value(pair[1])) / 2.0, pair

    def _aggregate(self, head: dict[str, Any], rows: list[_Row]) -> dict[str, Any]:
        value, fixing = self._aggregate_value(rows)
        payload: dict[str, Any] = {**head, "value": value}
        if self.op in ("sum", "mean", "count"):
            payload["row_refs"] = [self._ref(r) for r in fixing[:MAX_ROW_REFS]]
            payload["row_refs_total"] = len(fixing)
        else:
            payload["row_refs"] = [self._ref(r) for r in fixing]
        return self._capped(payload)

    def _capped(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Drops row references from the end until the payload fits the cap."""
        refs = payload.get("row_refs")
        while isinstance(refs, list) and refs and json_size(payload) > self.cap:
            refs = refs[: len(refs) // 2]
            payload["row_refs"] = refs
            payload["row_refs_total"] = payload.get("row_refs_total", len(refs))
        return payload

    def _side(self, rows: list[_Row], side: str) -> list[_Row]:
        return [r for r in rows if all(_matches(r.data, c) for c in self.sides[side])]

    def _ratio(self, first: float, second: float) -> float | None:
        """The comparison's value, or None when it is not meaningful."""
        if self.op == "percent_change":
            if second <= 0:
                return None
            return (first - second) / abs(second) * 100.0
        if second == 0:
            return None
        return first / second

    def _not_meaningful(self) -> str:
        if self.op == "percent_change":
            return "growth is not meaningful when the base is zero or negative"
        return "the denominator is zero"

    def _compare(self, head: dict[str, Any], rows: list[_Row]) -> dict[str, Any]:
        first_name, second_name = _COMPARISONS[self.op]
        picked = []
        for side in (first_name, second_name):
            chosen = self._side(rows, side)
            if not chosen:
                raise ToolError("empty_selection", f"no row matches {side}")
            if len(chosen) > 1:
                raise ToolError(
                    "ambiguous_selection",
                    f"{side} selects {len(chosen)} rows; a comparison without by needs one",
                    rows=len(chosen),
                )
            picked.append(chosen[0])
        first, second = self._value(picked[0]), self._value(picked[1])
        value = self._ratio(first, second)
        if value is None:
            raise ToolError("not_meaningful", self._not_meaningful())
        return {**head, "value": value, first_name: self._raw(picked[0]),
                second_name: self._raw(picked[1]),
                "row_refs": [self._ref(r) for r in picked]}

    def _raw(self, row: _Row) -> Any:
        """The row's value in ``column``, as the source gave it."""
        return row.data.get(self.column) if self.column is not None else None

    def _group_key(self, row: _Row) -> Any:
        assert self.by is not None
        value = row.data.get(self.by)
        if self.by == "cik":
            as_int = _cik_int(value)
            return as_int if as_int is not None else value
        return value

    def _grouped(self, head: dict[str, Any], rows: list[_Row]
                 ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        groups: dict[str, tuple[Any, list[_Row]]] = {}
        for row in rows:
            key = self._group_key(row)
            marker = json.dumps(key, sort_keys=True, default=str)
            groups.setdefault(marker, (key, []))[1].append(row)
        results: list[dict[str, Any]] = []
        unmatched: list[dict[str, Any]] = []
        refs: list[_Row] = []
        for key, members in groups.values():
            if self.op in _COMPARISONS:
                first_name, second_name = _COMPARISONS[self.op]
                first, second = self._side(members, first_name), self._side(members, second_name)
                if len(first) != 1 or len(second) != 1:
                    unmatched.append({"key": key, "reason": (
                        f"{len(first)} {first_name} row(s) and {len(second)} {second_name} "
                        "row(s); one of each is needed")})
                    continue
                value = self._ratio(self._value(first[0]), self._value(second[0]))
                if value is None:
                    unmatched.append({"key": key, "reason": self._not_meaningful()})
                    continue
                results.append({"key": key, "value": value,
                                first_name: self._raw(first[0]),
                                second_name: self._raw(second[0])})
                refs.extend((first[0], second[0]))
            else:
                value, fixing = self._aggregate_value(members)
                results.append({"key": key, "value": value, "count": len(members)})
                refs.extend(fixing)
        results.sort(key=lambda r: _sort_key(r["key"]))
        results.sort(key=lambda r: r["value"], reverse=self.order == "desc")
        tail: dict[str, Any] = {}
        if self.op in _COMPARISONS:
            tail = {"unmatched": unmatched[:MAX_UNMATCHED], "unmatched_total": len(unmatched)}
        tail["row_refs"] = [self._ref(r) for r in refs[:MAX_ROW_REFS]]
        tail["row_refs_total"] = len(refs)
        payload = fit_rows(head, results, self.offset, self.limit, self.cap, tail=tail)
        return payload, results

    def _ranking(self, head: dict[str, Any], rows: list[_Row]
                 ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        ordered = sorted(rows, key=self._value, reverse=self.order == "desc")
        out: list[dict[str, Any]] = []
        values = [self._value(r) for r in ordered]
        tie_start = 0
        for position, row in enumerate(ordered):
            item = {"source_id": row.source_id, "index": row.index}
            item.update({k: v for k, v in row.data.items() if k not in item})
            if self.op == "rank":
                if position and values[position] != values[position - 1]:
                    tie_start = position
                item["rank"] = tie_start + 1
            out.append(item)
        payload = fit_rows(head, out, self.offset, self.limit, self.cap)
        return payload, out


# ---------------------------------------------------------------------------------
# Held-out filers and universes (requirement 28).
# ---------------------------------------------------------------------------------


def _pad_cik(value: Any) -> str:
    text = str(value).strip()
    if not _CIK.fullmatch(text):
        raise ValueError(f"not a CIK: {value!r}")
    return text.zfill(10)


def default_withheld_path() -> Path:
    """The committed held-out pool.

    Returns:
        The path of ``docs/eval/heldout_v2/heldout_v2_pool.json`` under the project root.
    """
    return _REPO_ROOT / "docs" / "eval" / "heldout_v2" / "heldout_v2_pool.json"


def load_withheld_ciks(path: Path) -> frozenset[str]:
    """The filers withheld from development runs: every candidate of the held-out pool.

    Args:
        path: A pool file.

    Returns:
        Their CIKs, ten digits each.

    Raises:
        ValueError: If the file is missing or lists no filer.
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"the held-out pool {path} is missing; the guard fails closed")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"the held-out pool {path} is not JSON: {exc}") from exc
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(candidates, list) or not candidates:
        raise ValueError(f"the held-out pool {path} lists no candidates")
    ciks: set[str] = set()
    for entry in candidates:
        if not isinstance(entry, dict) or "cik" not in entry:
            raise ValueError(f"a candidate in {path} has no cik")
        ciks.add(_pad_cik(entry["cik"]))
    return frozenset(ciks)


def load_universes(groups_path: Path, sectors_path: Path) -> dict[str, Any]:
    """The universes a question can name, from the committed configs.

    Args:
        groups_path: ``configs/eval/v2/groups.yaml``.
        sectors_path: ``configs/entities/sectors.yaml``.

    Returns:
        ``{"covered": [{"ticker", "cik"}], "groups": {id: [ticker]}, "as_of": str}``.

    Raises:
        ValueError: If either file is missing or malformed.
    """
    def read(path: Path) -> dict[str, Any]:
        path = Path(path)
        if not path.is_file():
            raise ValueError(f"{path} is missing")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a mapping")
        return data

    groups_doc, sectors_doc = read(groups_path), read(sectors_path)
    filers = sectors_doc.get("filers")
    if not isinstance(filers, list) or not filers:
        raise ValueError(f"{sectors_path} lists no filers")
    covered = []
    for filer in filers:
        if not isinstance(filer, dict) or "ticker" not in filer or "cik" not in filer:
            raise ValueError(f"a filer in {sectors_path} has no ticker or cik")
        covered.append({"ticker": str(filer["ticker"]), "cik": _pad_cik(filer["cik"])})
    groups = groups_doc.get("groups")
    if not isinstance(groups, dict):
        raise ValueError(f"{groups_path} has no groups")
    as_of = groups_doc.get("as_of") or sectors_doc.get("as_of")
    if not as_of:
        raise ValueError(f"{groups_path} has no as_of")
    return {
        "covered": covered,
        "groups": {str(k): [str(t) for t in v] for k, v in groups.items()},
        "as_of": str(as_of),
    }


# ---------------------------------------------------------------------------------
# Argument checks (requirement 17). A failed check is bad_arguments, and no request.
# ---------------------------------------------------------------------------------


def _bad(message: str) -> ToolError:
    return ToolError("bad_arguments", message)


def _arg_cik(args: Mapping[str, Any], key: str = "cik") -> str:
    if key not in args:
        raise _bad(f"{key} is required")
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _bad(f"{key} must be 1 to 10 digits")
    text = str(value).strip()
    if not _CIK.fullmatch(text):
        raise _bad(f"{key} must be 1 to 10 digits, got {value!r}")
    return text.zfill(10)


def _arg_accession(args: Mapping[str, Any]) -> str:
    value = args.get("accession")
    if not isinstance(value, str):
        raise _bad("accession is required, as ##########-##-###### or 18 digits")
    text = value.strip()
    if _ACCESSION_DASHED.fullmatch(text):
        return text
    if _ACCESSION_PLAIN.fullmatch(text):
        return f"{text[:10]}-{text[10:12]}-{text[12:]}"
    raise _bad(f"accession must be ##########-##-###### or 18 digits, got {value!r}")


def _arg_document(args: Mapping[str, Any]) -> str | None:
    if "document" not in args or args["document"] is None:
        return None
    value = args["document"]
    if not isinstance(value, str) or not _NAME.fullmatch(value) or ".." in value:
        raise _bad("document must be a single file name of letters, digits, _ . and -")
    return value


def _arg_date(args: Mapping[str, Any], key: str) -> str | None:
    if key not in args or args[key] is None:
        return None
    try:
        return check_date(args[key], key)
    except ValueError as exc:
        raise _bad(str(exc)) from exc


def _arg_name(args: Mapping[str, Any], key: str, *, required: bool,
              pattern: re.Pattern[str] = _NAME) -> str | None:
    if key not in args or args[key] is None:
        if required:
            raise _bad(f"{key} is required")
        return None
    value = args[key]
    if not isinstance(value, str) or not pattern.fullmatch(value) or ".." in value:
        raise _bad(f"{key} must match {pattern.pattern}, got {value!r}")
    return value


def _arg_text(args: Mapping[str, Any], key: str, *, required: bool) -> str | None:
    if key not in args or args[key] is None:
        if required:
            raise _bad(f"{key} is required")
        return None
    value = args[key]
    if not isinstance(value, str) or not value.strip():
        raise _bad(f"{key} must be a non-empty string")
    if len(value) > 1000:
        raise _bad(f"{key} is over 1000 characters")
    return value


def _arg_int(args: Mapping[str, Any], key: str, default: int, *, minimum: int = 0) -> int:
    if key not in args or args[key] is None:
        return default
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _bad(f"{key} must be an integer of at least {minimum}, got {value!r}")
    return value


def _arg_paging(args: Mapping[str, Any]) -> tuple[int, int]:
    offset = _arg_int(args, "offset", 0)
    limit = min(_arg_int(args, "limit", DEFAULT_LIMIT), MAX_LIMIT)
    return offset, limit


def _arg_forms(args: Mapping[str, Any]) -> list[str] | None:
    if "forms" not in args or args["forms"] is None:
        return None
    value = args["forms"]
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value or not all(
        isinstance(f, str) and _FORM.fullmatch(f) for f in value
    ):
        raise _bad("forms must be a list of form types such as 10-K")
    return [f.strip() for f in value]


def _arg_ciks(args: Mapping[str, Any]) -> list[str] | None:
    if "ciks" not in args or args["ciks"] is None:
        return None
    value = args["ciks"]
    if not isinstance(value, list) or not value or len(value) > MAX_TICKERS:
        raise _bad(f"ciks must be a list of 1 to {MAX_TICKERS} CIKs")
    return [_arg_cik({"cik": v}) for v in value]


def _arg_concept_ciks(args: Mapping[str, Any]) -> list[str]:
    """get_concept's filers: exactly one of ``cik`` and ``ciks``, ten digits, each once."""
    has_cik = args.get("cik") is not None
    has_ciks = args.get("ciks") is not None
    if has_cik == has_ciks:
        raise _bad("give exactly one of cik and ciks")
    if has_cik:
        return [_arg_cik(args)]
    value = args["ciks"]
    if not isinstance(value, list) or not value or len(value) > MAX_CONCEPT_CIKS:
        raise _bad(f"ciks must be a list of 1 to {MAX_CONCEPT_CIKS} CIKs")
    # Ten-digit padding makes CIKs that are equal as integers equal as text.
    return list(dict.fromkeys(_arg_cik({"ciks": v}, "ciks") for v in value))


def _accession_folder(cik: str, accession: str) -> str:
    return f"{ARCHIVES}/{int(cik)}/{accession.replace('-', '')}"


# ---------------------------------------------------------------------------------
# The toolbox.
# ---------------------------------------------------------------------------------


class _Call:
    """One tool call: the URLs it read and whether withheld filers are lifted."""

    def __init__(self, lift: bool) -> None:
        self.lift = lift
        self.urls: list[str] = []


class SecToolbox:
    """Runs the tools, as of one date. Thread-safe."""

    def __init__(
        self,
        client: SecApiClient,
        *,
        as_of: str,
        withheld_ciks: Collection[str],
        max_result_chars: int = 40000,
    ) -> None:
        """Builds the toolbox; makes no request.

        Args:
            client: The SEC client.
            as_of: The date treated as today, ``YYYY-MM-DD``.
            withheld_ciks: Filers no result may show or read, ten digits each.
            max_result_chars: The most characters of one result, as JSON.

        Raises:
            ValueError: If the date is malformed, a CIK is not digits, or the cap is not
                positive.
        """
        self.client = client
        self.as_of = check_date(as_of)
        self.withheld = frozenset(_pad_cik(c) for c in withheld_ciks)
        if int(max_result_chars) <= 0:
            raise ValueError("max_result_chars must be positive")
        self.max_result_chars = int(max_result_chars)
        self._text_lock = threading.Lock()
        self._texts: dict[str, str] = {}
        self._handlers: dict[str, Callable[[_Call, Mapping[str, Any]], ToolOutcome]] = {
            "find_company": self._find_company,
            "list_filings": self._list_filings,
            "list_concepts": self._list_concepts,
            "get_concept": self._get_concept,
            "get_frame": self._get_frame,
            "search_filings_text": self._search_filings_text,
            "list_filing_documents": self._list_filing_documents,
            "read_filing": self._read_filing,
            "search_filing": self._search_filing,
            "calculate": self._calculate,
        }

    @classmethod
    def from_env(cls, *, transport: HttpTransport | None = None) -> SecToolbox:
        """Builds the toolbox from the environment.

        Args:
            transport: The HTTP transport; httpx when None.

        Returns:
            The toolbox, on the snapshot under ``XBRLRAG_SEC_SNAPSHOT_DIR``, as of
            ``XBRLRAG_SEC_AS_OF``, frozen when ``XBRLRAG_SEC_FROZEN`` is set, withholding
            :func:`default_withheld_path`'s filers and those in ``XBRLRAG_SEC_WITHHELD_CIKS``.

        Raises:
            ValueError: If the as-of date or the identity is missing.
        """
        as_of = (os.environ.get("XBRLRAG_SEC_AS_OF") or "").strip()
        if not as_of:
            raise ValueError(
                "XBRLRAG_SEC_AS_OF is required (run_suite.py --sec-as-of): a run must say "
                "which date it stands on"
            )
        check_date(as_of, "XBRLRAG_SEC_AS_OF")
        frozen = (os.environ.get("XBRLRAG_SEC_FROZEN") or "").strip().lower() in {
            "1", "true", "yes", "on"}
        base = (os.environ.get("XBRLRAG_SEC_SNAPSHOT_DIR") or "").strip()
        if base:
            root = Path(base)
        else:
            from src import PROJECT_ROOT

            root = Path(PROJECT_ROOT) / "data" / "frontier_sec_snapshots"
        withheld = set(load_withheld_ciks(default_withheld_path()))
        extra = os.environ.get("XBRLRAG_SEC_WITHHELD_CIKS") or ""
        for item in extra.split(","):
            if item.strip():
                withheld.add(_pad_cik(item))
        snapshot = SecSnapshot(root / as_of, as_of=as_of, frozen=frozen)
        client = SecApiClient(snapshot, transport=transport)
        return cls(client, as_of=as_of, withheld_ciks=withheld)

    # -- dispatch ------------------------------------------------------------------

    def execute(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        *,
        lift_withheld: bool = False,
        results: Mapping[str, ToolOutcome] | None = None,
    ) -> ToolOutcome:
        """Runs one tool. A failure is an outcome, never an exception.

        Args:
            name: The tool's name, as the model gave it.
            arguments: The arguments, as the model gave them.
            lift_withheld: Show withheld filers (a held-out run's own cases).
            results: This case's successful outcomes by source id, for ``analyze_rows``.

        Returns:
            The outcome.

        Raises:
            RunHalt: The run's budget or circuit breaker stopped the run.
        """
        if name == "analyze_rows":
            return analyze_rows(arguments, results or {}, max_result_chars=self.max_result_chars)
        handler = self._handlers.get(name) if isinstance(name, str) else None
        if handler is None:
            return error_outcome("unknown_tool", f"there is no tool named {name!r}", [])
        call = _Call(lift_withheld)
        if not isinstance(arguments, dict):
            return error_outcome("bad_arguments", "the arguments must be a JSON object", [])
        try:
            return handler(call, arguments)
        except ToolError as exc:
            return error_outcome(exc.code, exc.message, call.urls, **exc.extra)
        except SecSnapshotMissError as exc:
            return error_outcome("not_in_snapshot", str(exc), call.urls)
        except SecRequestError as exc:
            return error_outcome("upstream", str(exc), call.urls)
        except RunHalt:
            raise
        except Exception as exc:  # isolation point: a failed tool is a result for the model
            logger.warning("tool %s failed: %r", name, exc)
            return error_outcome("upstream", f"{type(exc).__name__}: {exc}", call.urls)

    def provenance(self) -> dict[str, Any]:
        """The snapshot's provenance.

        Returns:
            :meth:`SecSnapshot.provenance`, plus ``fetched`` (requests this process sent).
        """
        return {**self.client.snapshot.provenance(), "fetched": self.client.fetched}

    # -- shared reads -----------------------------------------------------------------

    def _check_withheld(self, call: _Call, cik: str) -> None:
        if cik in self.withheld and not call.lift:
            raise ToolError("withheld", "this filer is withheld from this run")

    def _hidden(self, call: _Call, cik: Any) -> bool:
        if call.lift or not self.withheld:
            return False
        try:
            return _pad_cik(cik) in self.withheld
        except ValueError:
            return False

    def _get(self, call: _Call, url: str) -> bytes:
        status, body = self.client.get(url)
        call.urls.append(url)
        if status == 404:
            raise ToolError("not_found", "the SEC has no such resource (404)")
        return body

    def _get_json(self, call: _Call, url: str) -> Any:
        body = self._get(call, url)
        try:
            return json.loads(body.decode("utf-8"))
        except ValueError as exc:
            raise ToolError("upstream", f"the SEC returned JSON that does not parse: {exc}") \
                from exc

    @staticmethod
    def _filing_rows(columns: Mapping[str, Any]) -> list[dict[str, Any]]:
        accessions = columns.get("accessionNumber") or []
        def col(key: str, i: int) -> Any:
            values = columns.get(key) or []
            return values[i] if i < len(values) else ""
        return [
            {"accession": accessions[i], "form": col("form", i),
             "filing_date": col("filingDate", i), "report_date": col("reportDate", i),
             "primary_document": col("primaryDocument", i)}
            for i in range(len(accessions))
        ]

    def _submissions(self, call: _Call, cik: str, *, until: str | None = None
                     ) -> list[dict[str, Any]]:
        """A filer's filings, recent first and then its older pages.

        Args:
            call: The call.
            cik: Ten digits.
            until: Stop reading older pages once this accession is found.
        """
        data = self._get_json(call, f"{SUBMISSIONS_BASE}/CIK{cik}.json")
        filings = data.get("filings") if isinstance(data, dict) else None
        if not isinstance(filings, dict):
            raise ToolError("upstream", "the submissions file has no filings")
        rows = self._filing_rows(filings.get("recent") or {})
        if until is not None and any(r["accession"] == until for r in rows):
            return rows
        for page in filings.get("files") or []:
            name = page.get("name") if isinstance(page, dict) else None
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                continue
            older = self._get_json(call, f"{SUBMISSIONS_BASE}/{name}")
            rows.extend(self._filing_rows(older if isinstance(older, dict) else {}))
            if until is not None and any(r["accession"] == until for r in rows):
                break
        return rows

    def _filing(self, call: _Call, cik: str, accession: str) -> dict[str, Any]:
        """The submissions row of a filing that existed at ``as_of`` (requirement 11)."""
        for row in self._submissions(call, cik, until=accession):
            if row["accession"] == accession:
                if str(row["filing_date"]) > self.as_of:
                    raise ToolError("not_yet_filed",
                                    f"this filing was made after {self.as_of}")
                return row
        raise ToolError("not_found", "the filer's submissions do not list this accession")

    def _facts(self, call: _Call, cik: str) -> dict[str, Any]:
        data = self._get_json(call, f"{FACTS_BASE}/CIK{cik}.json")
        facts = data.get("facts") if isinstance(data, dict) else None
        return facts if isinstance(facts, dict) else {}

    def _known(self, fact: Mapping[str, Any]) -> bool:
        filed = fact.get("filed")
        return not isinstance(filed, str) or filed <= self.as_of

    def _page(self, head: dict[str, Any], rows: list[dict[str, Any]], offset: int, limit: int,
              *, key: str = "rows", tail: dict[str, Any] | None = None) -> dict[str, Any]:
        return fit_rows(head, rows, offset, limit, self.max_result_chars, key=key, tail=tail)

    def _withheld_tail(self, hidden: int) -> dict[str, Any]:
        return {"withheld": hidden} if hidden else {}

    def _document_text(self, call: _Call, cik: str, accession: str,
                       document: str | None) -> tuple[str, dict[str, Any], str]:
        filing = self._filing(call, cik, accession)
        name = document or str(filing.get("primary_document") or "")
        if not name or not _NAME.fullmatch(name) or ".." in name:
            raise ToolError("not_found", "the filing names no primary document")
        url = f"{_accession_folder(cik, accession)}/{name}"
        with self._text_lock:
            cached = self._texts.get(url)
        if cached is not None:
            call.urls.append(url)
            return cached, filing, name
        body = self._get(call, url)
        raw = body.decode("utf-8", errors="replace")
        text = filing_text(raw) if name.lower().endswith((".htm", ".html")) else raw
        with self._text_lock:
            if len(self._texts) >= _TEXT_CACHE_SIZE:
                self._texts.pop(next(iter(self._texts)))
            self._texts[url] = text
        return text, filing, name

    # -- the tools ---------------------------------------------------------------------

    def _find_company(self, call: _Call, args: Mapping[str, Any]) -> ToolOutcome:
        offset, limit = _arg_paging(args)
        tickers = args.get("tickers")
        query = None
        if tickers is not None:
            if (not isinstance(tickers, list) or not tickers or len(tickers) > MAX_TICKERS
                    or not all(isinstance(t, str) and _TICKER.fullmatch(t.strip())
                               for t in tickers)):
                raise _bad(f"tickers must be a list of 1 to {MAX_TICKERS} tickers")
        else:
            query = _arg_text(args, "query", required=True)
        data = self._get_json(call, TICKERS_URL)
        if isinstance(data, dict):
            entries: list[Any] = list(data.values())
        else:
            entries = data if isinstance(data, list) else []
        companies = []
        for entry in entries:
            if not isinstance(entry, dict) or "cik_str" not in entry:
                continue
            companies.append({"cik": str(entry["cik_str"]).zfill(10),
                              "ticker": str(entry.get("ticker", "")),
                              "name": str(entry.get("title", ""))})
        hidden = 0
        extra: dict[str, Any] = {}
        if tickers is not None:
            by_ticker: dict[str, dict[str, Any]] = {}
            for company in companies:
                by_ticker.setdefault(company["ticker"].upper(), company)
            found: list[dict[str, Any]] = []
            not_found: list[str] = []
            for ticker in tickers:
                match = by_ticker.get(ticker.strip().upper())
                if match is None or self._hidden(call, match["cik"]):
                    not_found.append(ticker)
                elif match not in found:
                    found.append(match)
            selected = found
            extra["not_found"] = not_found
        else:
            assert query is not None
            q = query.strip().lower()
            exact = [c for c in companies if c["ticker"].lower() == q]
            named = [c for c in companies if q in c["name"].lower() and c not in exact]
            selected = []
            for company in exact + named:
                if self._hidden(call, company["cik"]):
                    hidden += 1
                else:
                    selected.append(company)
        payload = self._page({}, selected, offset, limit, key="companies",
                             tail={**extra, **self._withheld_tail(hidden)})
        return ToolOutcome(payload, call.urls, rows=selected)

    def _list_filings(self, call: _Call, args: Mapping[str, Any]) -> ToolOutcome:
        cik = _arg_cik(args)
        forms = _arg_forms(args)
        filed_from, filed_to = _arg_date(args, "filed_from"), _arg_date(args, "filed_to")
        offset, limit = _arg_paging(args)
        self._check_withheld(call, cik)
        rows = [
            r for r in self._submissions(call, cik)
            if str(r["filing_date"]) <= self.as_of
            and (forms is None or r["form"] in forms)
            and (filed_from is None or str(r["filing_date"]) >= filed_from)
            and (filed_to is None or str(r["filing_date"]) <= filed_to)
        ]
        rows.sort(key=lambda r: str(r["filing_date"]), reverse=True)
        return ToolOutcome(self._page({}, rows, offset, limit), call.urls, rows=rows)

    def _list_concepts(self, call: _Call, args: Mapping[str, Any]) -> ToolOutcome:
        cik = _arg_cik(args)
        query = _arg_text(args, "query", required=False)
        offset, limit = _arg_paging(args)
        self._check_withheld(call, cik)
        facts = self._facts(call, cik)
        needle = query.strip().lower() if query else None
        rows = []
        for taxonomy, concepts in facts.items():
            if not isinstance(concepts, dict):
                continue
            for tag, concept in concepts.items():
                if not isinstance(concept, dict):
                    continue
                label = str(concept.get("label") or "")
                if needle and needle not in tag.lower() and needle not in label.lower():
                    continue
                units: list[str] = []
                ends: list[str] = []
                count = 0
                for unit, entries in (concept.get("units") or {}).items():
                    kept = [f for f in entries if isinstance(f, dict) and self._known(f)]
                    if kept:
                        units.append(unit)
                        count += len(kept)
                        ends.extend(str(f["end"]) for f in kept if f.get("end"))
                if not count:
                    continue
                rows.append({"taxonomy": taxonomy, "tag": tag, "label": label,
                             "units": units, "first_end": min(ends) if ends else None,
                             "last_end": max(ends) if ends else None, "facts": count})
        return ToolOutcome(self._page({}, rows, offset, limit), call.urls, rows=rows)

    def _get_concept(self, call: _Call, args: Mapping[str, Any]) -> ToolOutcome:
        ciks = _arg_concept_ciks(args)
        taxonomy = _arg_name(args, "taxonomy", required=True)
        tag = _arg_name(args, "tag", required=True)
        unit = _arg_name(args, "unit", required=False, pattern=_UNIT)
        form = _arg_name(args, "form", required=False, pattern=_FORM)
        fiscal_year = args.get("fiscal_year")
        if fiscal_year is not None and (isinstance(fiscal_year, bool)
                                        or not isinstance(fiscal_year, int)):
            raise _bad("fiscal_year must be an integer")
        end_from, end_to = _arg_date(args, "end_from"), _arg_date(args, "end_to")
        offset, limit = _arg_paging(args)
        for cik in ciks:
            self._check_withheld(call, cik)
        assert taxonomy is not None and tag is not None  # required=True
        units: list[str] = []
        rows: list[dict[str, Any]] = []
        not_found: list[str] = []
        first: dict[str, Any] | None = None
        for cik in ciks:
            concept = (self._facts(call, cik).get(taxonomy) or {}).get(tag)
            if not isinstance(concept, dict):
                not_found.append(cik)
                continue
            if first is None:
                first = concept
            for unit_name, entries in (concept.get("units") or {}).items():
                kept = [f for f in entries if isinstance(f, dict) and self._known(f)]
                if kept and unit_name not in units:
                    units.append(unit_name)
                for fact in kept:
                    end = str(fact.get("end") or "")
                    if unit is not None and unit_name != unit:
                        continue
                    if form is not None and fact.get("form") != form:
                        continue
                    if fiscal_year is not None and fact.get("fy") != fiscal_year:
                        continue
                    if end_from is not None and end < end_from:
                        continue
                    if end_to is not None and end > end_to:
                        continue
                    # The added fields win over an SEC field of the same name.
                    rows.append({**fact, "unit": unit_name, "cik": cik, "taxonomy": taxonomy,
                                 "tag": tag})
        if first is None:
            subject = "the filer reports" if len(ciks) == 1 else "none of the filers reports"
            raise ToolError("not_found", f"{subject} no {taxonomy}:{tag}")
        # Newest first: end desc, then cik asc, then filed desc, then accn desc (stable sorts,
        # least significant key first; a missing value sorts as "").
        rows.sort(key=lambda r: str(r.get("accn") or ""), reverse=True)
        rows.sort(key=lambda r: str(r.get("filed") or ""), reverse=True)
        rows.sort(key=lambda r: str(r.get("cik") or ""))
        rows.sort(key=lambda r: str(r.get("end") or ""), reverse=True)
        head = {"label": first.get("label"), "description": first.get("description"),
                "units": units, "not_found": not_found}
        return ToolOutcome(self._page(head, rows, offset, limit), call.urls, rows=rows)

    def _get_frame(self, call: _Call, args: Mapping[str, Any]) -> ToolOutcome:
        taxonomy = _arg_name(args, "taxonomy", required=True)
        tag = _arg_name(args, "tag", required=True)
        unit = _arg_name(args, "unit", required=True, pattern=_UNIT)
        period = _arg_name(args, "period", required=True)
        order = args.get("order", "desc")
        if order not in ("desc", "asc"):
            raise _bad("order must be desc or asc")
        ciks = _arg_ciks(args)
        offset, limit = _arg_paging(args)
        if ciks is not None:
            for cik in ciks:
                self._check_withheld(call, cik)
        assert unit is not None
        # The frames API names a ratio unit with "-per-" (USD-per-shares); "USD%2Fshares" is a 404.
        frames_unit = quote(unit.replace("/", "-per-"), safe="")
        url = f"{FRAMES_BASE}/{taxonomy}/{tag}/{frames_unit}/{period}.json"
        data = self._get_json(call, url)
        entries = data.get("data") if isinstance(data, dict) else None
        wanted = None if ciks is None else {int(c) for c in ciks}
        rows, hidden = [], 0
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            if self._hidden(call, entry.get("cik")):
                hidden += 1
                continue
            if wanted is not None and _as_int(entry.get("cik")) not in wanted:
                continue
            rows.append(entry)
        rows.sort(key=lambda r: _as_int(r.get("cik")) or 0)
        rows.sort(key=lambda r: _sort_number(r.get("val")), reverse=order == "desc")
        head = {"label": data.get("label"), "description": data.get("description"),
                "as_of_filtered": False}
        payload = self._page(head, rows, offset, limit, tail=self._withheld_tail(hidden))
        return ToolOutcome(payload, call.urls, rows=rows)

    def _search_filings_text(self, call: _Call, args: Mapping[str, Any]) -> ToolOutcome:
        query = _arg_text(args, "query", required=True)
        forms = _arg_forms(args)
        ciks = _arg_ciks(args)
        filed_from, filed_to = _arg_date(args, "filed_from"), _arg_date(args, "filed_to")
        page = _arg_int(args, "page", 1, minimum=1)
        if ciks is not None:
            for cik in ciks:
                self._check_withheld(call, cik)
        end = min(filed_to, self.as_of) if filed_to else self.as_of
        start = filed_from or FTS_START
        params: dict[str, str] = {"q": str(query), "dateRange": "custom", "startdt": start,
                                  "enddt": end}
        if forms:
            params["forms"] = ",".join(forms)
        if ciks:
            params["ciks"] = ",".join(ciks)
        params["from"] = str((page - 1) * FTS_PAGE)
        data = self._get_json(call, f"{FTS_URL}?{urlencode(params)}")
        hits_doc = data.get("hits") if isinstance(data, dict) else None
        hits_doc = hits_doc if isinstance(hits_doc, dict) else {}
        total_doc = hits_doc.get("total")
        sec_total = _as_int(total_doc.get("value") if isinstance(total_doc, dict)
                            else total_doc) or 0
        rows, hidden = [], 0
        for hit in hits_doc.get("hits") or []:
            source = hit.get("_source") if isinstance(hit, dict) else None
            if not isinstance(source, dict):
                continue
            hit_ciks = [str(c).zfill(10) for c in source.get("ciks") or []]
            if any(self._hidden(call, c) for c in hit_ciks):
                hidden += 1
                continue
            filed = str(source.get("file_date") or "")
            if filed and filed > self.as_of:
                continue
            document = str(hit.get("_id") or "").partition(":")[2]
            forms_of = source.get("root_forms") or [None]
            rows.append({
                "accession": source.get("adsh"),
                "form": source.get("form") or forms_of[0],
                "filing_date": filed,
                "period_ending": source.get("period_ending"),
                "ciks": hit_ciks,
                "companies": list(source.get("display_names") or []),
                "document": document,
            })
        offset = (page - 1) * FTS_PAGE
        more = offset + FTS_PAGE < sec_total
        head = {"page": page}
        tail = {"next_page": page + 1 if more else None, **self._withheld_tail(hidden)}
        payload = fit_rows(head, rows, 0, FTS_PAGE, self.max_result_chars, tail=tail)
        shown = len(payload["rows"])
        payload["total"] = sec_total
        payload["offset"] = offset
        payload["next_offset"] = offset + FTS_PAGE if more else None
        if shown < len(rows):
            payload["rows_not_shown"] = len(rows) - shown
        payload["truncated"] = more or shown < len(rows)
        return ToolOutcome(payload, call.urls, rows=rows)

    def _list_filing_documents(self, call: _Call, args: Mapping[str, Any]) -> ToolOutcome:
        cik = _arg_cik(args)
        accession = _arg_accession(args)
        offset, limit = _arg_paging(args)
        self._check_withheld(call, cik)
        self._filing(call, cik, accession)
        data = self._get_json(call, f"{_accession_folder(cik, accession)}/index.json")
        directory = data.get("directory") if isinstance(data, dict) else None
        items = directory.get("item") if isinstance(directory, dict) else None
        rows = [{"name": item.get("name"), "size": item.get("size")}
                for item in items if isinstance(item, dict)] if isinstance(items, list) else []
        return ToolOutcome(self._page({}, rows, offset, limit), call.urls, rows=rows)

    def _read_filing(self, call: _Call, args: Mapping[str, Any]) -> ToolOutcome:
        cik = _arg_cik(args)
        accession = _arg_accession(args)
        document = _arg_document(args)
        offset = _arg_int(args, "offset", 0)
        max_chars = _arg_int(args, "max_chars", DEFAULT_READ_CHARS, minimum=1)
        self._check_withheld(call, cik)
        text, filing, name = self._document_text(call, cik, accession, document)
        total = len(text)
        start = min(offset, total)

        def build(n: int) -> dict[str, Any]:
            end = start + n
            return {"text": text[start:end], "offset": start, "total_chars": total,
                    "next_offset": end if end < total else None, "document": name,
                    "form": filing.get("form"), "filing_date": filing.get("filing_date")}

        most = min(max_chars, total - start)
        low, high = 0, most
        while low < high:
            mid = (low + high + 1) // 2
            if json_size(build(mid)) <= self.max_result_chars:
                low = mid
            else:
                high = mid - 1
        if low == 0 and most > 0:
            low = 1  # always progress
        return ToolOutcome(build(low), call.urls)

    def _search_filing(self, call: _Call, args: Mapping[str, Any]) -> ToolOutcome:
        cik = _arg_cik(args)
        accession = _arg_accession(args)
        query = _arg_text(args, "query", required=True)
        document = _arg_document(args)
        context = min(_arg_int(args, "context_chars", DEFAULT_CONTEXT_CHARS), MAX_CONTEXT_CHARS)
        offset, limit = _arg_paging(args)
        self._check_withheld(call, cik)
        text, filing, name = self._document_text(call, cik, accession, document)
        assert query is not None
        rows = [
            {"position": m.start(),
             "snippet": text[max(0, m.start() - context): m.end() + context]}
            for m in re.finditer(re.escape(query), text, re.IGNORECASE)
        ]
        head = {"document": name, "form": filing.get("form"),
                "filing_date": filing.get("filing_date")}
        return ToolOutcome(self._page(head, rows, offset, limit), call.urls, rows=rows)

    def _calculate(self, call: _Call, args: Mapping[str, Any]) -> ToolOutcome:
        expression = args.get("expression")
        if not isinstance(expression, str):
            raise _bad("expression is required")
        try:
            value = calculate(expression)
        except ValueError as exc:
            raise _bad(str(exc)) from exc
        return ToolOutcome({"value": value}, [])


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _sort_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return -math.inf
    return float(value)


