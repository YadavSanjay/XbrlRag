"""Render a call-level HTML diagnostic report for one or more completed eval runs.

Read-only and $0: no LLM call, no pipeline, no write to any database. Each source is either
a run id in the ``eval`` schema or a ``run_suite.py --out`` JSON report. Per case it joins
the stored result to that case's trace events by ``query_id``: from ``audit.query_event``
when the run used ``--persist-audit`` (LLM bodies need ``XBRLRAG_TRACE_LLM_BODIES``), else
from the per-process logs (``logs/xbrlrag_run_suite_<pid>.log``, the ``xbrlrag.events``
lines) given with ``--logs``. Every LLM call's cost is recomputed from its tokens and
``configs/llm/pricing.yaml`` (``src.llm.profiles.cost_usd``) and shown beside the stored
cost, with differences flagged.

The report is one self-contained HTML file (inline CSS and JS, no external fetch):
a summary per run, which file belongs to which run, tokens and cost by system x stage x
model, and every case with its question, key, answer, grade, plan, SQL, citations and
each LLM call (prompt and response collapsible, capped by ``--max-body-chars``). All text
is HTML-escaped: filing text and model output are untrusted.

Held-out runs (H-F, H-X, set B, H-T) are refused before their cases are read: only their
aggregates may be shared.

    # a stored run (its audit rows give every event and LLM body)
    conda run -n py312 python scripts/eval/run_report.py eval-8918f010fd77 `
        --out data/eval/run_reports/eval-8918f010fd77.html

    # run_suite --out reports plus the process logs they wrote
    conda run -n py312 python scripts/eval/run_report.py `
        data/eval/anthropic_smoke/2026-11-05/*.json --logs logs/ `
        --out data/eval/run_reports/anthropic_smoke.html
"""

from __future__ import annotations

import argparse
import datetime
import glob
import html
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.core.regrade import HELDOUT_SPLITS  # noqa: E402
from src.eval.core.run_columns import slice_fields_from_tags  # noqa: E402
from src.llm.profiles import cost_usd  # noqa: E402

EVENTS_LOGGER = "xbrlrag.events"
# Suites built from held-out material are refused whatever split their rows record: set B
# was stored before the split column existed and reads as ``legacy``.
HELDOUT_SUITE_PREFIX = "heldout"
HELDOUT_CASE_TAGS = frozenset({"set-b"})
DEFAULT_MAX_BODY_CHARS = 20_000
# Stored and recomputed costs further apart than this are flagged (USD).
COST_TOLERANCE_USD = 1e-6
# The marker src/llm/gateway/gateway.py appends when it truncates a body at the source.
SOURCE_TRUNCATION_MARKER = "… [truncated "
GRADES: tuple[str, ...] = (
    "CORRECT", "PARTIAL", "IMPRECISE", "INCORRECT", "REFUSED", "HEDGED_MISS", "ERROR",
)
# Cost parts: token count field, rate key, label.
COST_PARTS: tuple[tuple[str, str], ...] = (
    ("uncached_in", "input"),
    ("cache_write", "cache_write"),
    ("cache_read", "cached_input"),
    ("output", "output"),
)


class HeldOutRefused(ValueError):  # noqa: N818 - parallels RegradeRefused / AuditRefused
    """A source is a held-out run, whose per-case content may not be reported."""


# ---------------------------------------------------------------------------- held-out


def check_run_allowed(run_id: str, split: str | None, suite: str | None,
                      config: Mapping[str, Any] | None = None) -> None:
    """Refuses a held-out run from its run-level fields, before any case is read.

    Args:
        run_id: The run, for the message.
        split: The run's recorded split (``None`` reads as legacy).
        suite: The run's suite name.
        config: The run's config; its ``split`` is checked too.

    Raises:
        HeldOutRefused: When either split is held-out or the suite is a held-out suite.
    """
    splits = {split, (config or {}).get("split")}
    held = sorted(s for s in splits if s in HELDOUT_SPLITS)
    if held:
        raise HeldOutRefused(
            f"run {run_id} has held-out split {held[0]!r}; only its aggregates may be "
            "shared, so no call-level report is rendered"
        )
    if suite and suite.lower().startswith(HELDOUT_SUITE_PREFIX):
        raise HeldOutRefused(
            f"run {run_id} is over held-out suite {suite!r}; only its aggregates may be "
            "shared, so no call-level report is rendered"
        )


def check_cases_allowed(run_id: str, cases: Iterable[Mapping[str, Any]]) -> None:
    """A second guard: no case may carry a held-out split or a held-out tag.

    Args:
        run_id: The run, for the message.
        cases: The run's case rows (``split`` and ``tags`` are read).

    Raises:
        HeldOutRefused: On a held-out case.
    """
    for case in cases:
        tags = list(case.get("tags") or [])
        try:
            tag_split = slice_fields_from_tags(tags)["split"]
        except ValueError:
            tag_split = None
        for split in (case.get("split"), tag_split):
            if split in HELDOUT_SPLITS:
                raise HeldOutRefused(
                    f"run {run_id} holds case {case.get('case_id')} of held-out split "
                    f"{split!r}; no call-level report is rendered"
                )
        if HELDOUT_CASE_TAGS & set(tags):
            raise HeldOutRefused(
                f"run {run_id} holds case {case.get('case_id')} tagged as held-out "
                f"{sorted(HELDOUT_CASE_TAGS & set(tags))}; no call-level report is rendered"
            )


# ---------------------------------------------------------------------------- model


@dataclass
class Run:
    """One eval run with its cases and where each piece came from."""

    run_id: str
    system: str
    suite: str | None
    split: str | None
    source: str  # "db" or the JSON report's path
    started_at: str | None = None
    finished_at: str | None = None
    status: str | None = None
    total_cost_usd: float | None = None
    config: dict[str, Any] = field(default_factory=dict)
    cases: list[dict[str, Any]] = field(default_factory=list)
    console_log: str | None = None
    # Log file name -> number of this run's cases it holds events for.
    log_files: Counter = field(default_factory=Counter)


def _num(value: Any) -> float:
    """A stored number (int, float, Decimal, numeric string or None) as a float."""
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float | Decimal):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return 0.0


def _int(value: Any) -> int:
    return int(_num(value))


def _json(value: Any) -> Any:
    """A JSON column as Python (psycopg2 may already have decoded it)."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return str(value)


def run_from_report(report: Mapping[str, Any], path: str) -> Run:
    """A run from a ``run_suite.py --out`` JSON report, held-out checks first.

    Args:
        report: The parsed report.
        path: Where it was read from.

    Returns:
        The run with its cases (events not yet attached).

    Raises:
        HeldOutRefused: When the run is held-out.
    """
    run_id = str(report.get("run_id") or Path(path).stem)
    config = dict(report.get("config") or {})
    check_run_allowed(run_id, report.get("split"), report.get("suite"), config)
    cases = [dict(c) for c in report.get("results") or []]
    check_cases_allowed(run_id, cases)
    metrics = report.get("metrics") or {}
    console = Path(path).with_name(Path(path).name.split(".")[0] + ".log")
    return Run(
        run_id=run_id, system=str(report.get("system") or "?"), suite=report.get("suite"),
        split=report.get("split") or config.get("split"), source=str(path),
        started_at=report.get("started_at"), finished_at=report.get("finished_at"),
        status=report.get("status"),
        total_cost_usd=_num(metrics.get("total_cost_usd")) if metrics else None,
        config=config, cases=cases,
        console_log=str(console) if console.exists() else None,
    )


# ---------------------------------------------------------------------------- DB


class RunReader(Protocol):
    """Reads stored runs and their audit trail, SELECT only."""

    def run_row(self, run_id: str) -> Mapping[str, Any] | None:
        """The ``eval.eval_run`` row, or None when unknown."""
        ...

    def case_rows(self, run_id: str) -> Sequence[Mapping[str, Any]]:
        """The run's ``eval.eval_case_result`` rows."""
        ...

    def audit(self, query_ids: Sequence[str]) -> tuple[dict[str, dict], dict[str, list]]:
        """``query_audit`` rows by query id, and each query's events in ``seq`` order."""
        ...


class PostgresRunReader:
    """Reads the ``eval`` and ``audit`` schemas in a read-only session."""

    _RUN = (
        "SELECT run_id, suite, system, split, status, started_at, finished_at, "
        "total_cost_usd, config FROM eval.eval_run WHERE run_id = %s"
    )
    _CASES = (
        "SELECT case_id, kind, grade, passed, silent_wrong, confident_wrong, needs_review, "
        "value_correct, question, expected, produced, matched_value, has_citation, "
        "grounding_precision, grounding_recall, screen_metrics, reason, error, latency_ms, "
        "cost_usd, query_id, ticker, fy, tags, origin, intent_id, phrasing_family, "
        "key_source, key_status, judge_correctness, judge_completeness, "
        "judge_citation_support, judge_unsupported_claims "
        "FROM eval.eval_case_result WHERE run_id = %s ORDER BY case_id"
    )
    _AUDIT = "SELECT * FROM audit.query_audit WHERE query_id = ANY(%s)"
    _EVENTS = (
        "SELECT query_id, seq, kind, stage, payload, created_at FROM audit.query_event "
        "WHERE query_id = ANY(%s) ORDER BY query_id, seq"
    )

    def __init__(self) -> None:
        self._conn: Any = None

    def _cursor(self) -> Any:
        if self._conn is None:
            from src.db.conn import connect  # lazy: the module imports without a driver

            self._conn = connect(options="-c default_transaction_read_only=on")
            self._conn.set_session(readonly=True)
        return self._conn.cursor()

    def _rows(self, sql: str, param: Any) -> list[dict[str, Any]]:
        with self._cursor() as cur:
            cur.execute(sql, (param,))
            columns = [d[0] for d in cur.description or ()]
            return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]

    def run_row(self, run_id: str) -> Mapping[str, Any] | None:
        """The run row, or None."""
        rows = self._rows(self._RUN, run_id)
        return rows[0] if rows else None

    def case_rows(self, run_id: str) -> Sequence[Mapping[str, Any]]:
        """The case rows, JSON columns decoded."""
        rows = self._rows(self._CASES, run_id)
        for row in rows:
            for key in ("expected", "produced", "screen_metrics"):
                row[key] = _json(row.get(key))
        return rows

    def audit(self, query_ids: Sequence[str]) -> tuple[dict[str, dict], dict[str, list]]:
        """The audit rollups and events of these queries."""
        ids = sorted({q for q in query_ids if q})
        if not ids:
            return {}, {}
        summaries = {r["query_id"]: r for r in self._rows(self._AUDIT, ids)}
        events: dict[str, list] = defaultdict(list)
        for r in self._rows(self._EVENTS, ids):
            payload = dict(_json(r["payload"]) or {})
            payload.setdefault("kind", r["kind"])
            payload.setdefault("stage", r["stage"])
            payload["_ts"] = _iso(r["created_at"])
            events[r["query_id"]].append(payload)
        return summaries, dict(events)

    def close(self) -> None:
        """Closes the connection, if one was opened."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def run_from_db(run_id: str, reader: RunReader) -> Run:
    """A stored run: its row is read and checked before any case row.

    Args:
        run_id: The run id in ``eval.eval_run``.
        reader: The store reader.

    Returns:
        The run with its cases (events not yet attached).

    Raises:
        HeldOutRefused: When the run is held-out.
        SystemExit: When the run is unknown.
    """
    row = reader.run_row(run_id)
    if row is None:
        raise SystemExit(f"unknown run {run_id!r} (not in eval.eval_run, or DB unreachable)")
    config = dict(_json(row.get("config")) or {})
    check_run_allowed(run_id, row.get("split"), row.get("suite"), config)
    cases = [dict(c) for c in reader.case_rows(run_id)]
    check_cases_allowed(run_id, cases)
    return Run(
        run_id=run_id, system=str(row.get("system") or "?"), suite=row.get("suite"),
        split=row.get("split"), source="db", started_at=_iso(row.get("started_at")),
        finished_at=_iso(row.get("finished_at")), status=row.get("status"),
        total_cost_usd=_num(row.get("total_cost_usd")), config=config, cases=cases,
    )


# ---------------------------------------------------------------------------- logs


@dataclass
class LogIndex:
    """Every log line with a query id, grouped by query id, and which file held it."""

    by_query: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    file_of: dict[str, str] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)


def parse_log_lines(lines: Iterable[str], name: str, index: LogIndex) -> None:
    """Adds one process log's lines to the index.

    An ``xbrlrag.events`` line becomes its event dict (``_ts`` and ``_src`` added); any
    other line carrying a query id is kept as a raw line (``_raw``). Lines that are not
    JSON are skipped.

    Args:
        lines: The file's lines.
        name: The file's name, recorded as each entry's ``_src``.
        index: The index to add to.
    """
    for raw in lines:
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        entry: dict[str, Any] | None = None
        if rec.get("logger") == EVENTS_LOGGER:
            try:
                ev = json.loads(rec.get("message") or "")
            except ValueError:
                ev = None
            if isinstance(ev, dict) and ev.get("query_id"):
                entry = {**ev, "_ts": rec.get("ts"), "_src": name}
        if entry is None:
            qid = rec.get("query_id")
            if not qid or qid == "-":
                continue
            entry = {"_raw": True, "query_id": qid, "_ts": rec.get("ts"), "_src": name,
                     "logger": rec.get("logger"), "level": rec.get("level"),
                     "message": rec.get("message")}
        index.by_query[entry["query_id"]].append(entry)
        index.file_of.setdefault(entry["query_id"], name)


def load_logs(paths: Sequence[str]) -> LogIndex:
    """Reads log files and directories (``xbrlrag_*.log`` inside a directory).

    Args:
        paths: Files, directories or glob patterns.

    Returns:
        The index of every line with a query id.
    """
    index = LogIndex()
    files: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            files.extend(sorted(path.glob("xbrlrag_*.log")))
        elif path.exists():
            files.append(path)
        else:
            files.extend(Path(m) for m in sorted(glob.glob(p)))
    for f in files:
        if f.stat().st_size == 0:
            continue
        with open(f, encoding="utf-8", errors="replace") as handle:
            parse_log_lines(handle, f.name, index)
        index.files.append(str(f))
    return index


def attach_events(runs: Sequence[Run], logs: LogIndex | None,
                  reader: RunReader | None) -> None:
    """Joins each case to its trace by ``query_id``: audit rows first, else log lines.

    Sets each case's ``_events`` (list), ``_event_source`` (``audit``, a log file name or
    ``none``), ``_audit`` (the ``query_audit`` row or None) and ``_answer`` (its stored
    final answer or None), and counts each run's log files.

    Args:
        runs: The runs whose cases to join.
        logs: The log index, or None.
        reader: The store reader for audit rows, or None to skip the audit schema.
    """
    summaries: dict[str, dict] = {}
    events: dict[str, list] = {}
    if reader is not None:
        qids = [str(c["query_id"]) for r in runs for c in r.cases if c.get("query_id")]
        summaries, events = reader.audit(qids)
    for run in runs:
        for case in run.cases:
            qid = case.get("query_id")
            audit_row = summaries.get(qid) if qid else None
            case["_audit"] = audit_row
            case["_answer"] = _json(audit_row.get("answer")) if audit_row else None
            if qid and events.get(qid):
                case["_events"], case["_event_source"] = events[qid], "audit"
            elif qid and logs is not None and logs.by_query.get(qid):
                case["_events"] = logs.by_query[qid]
                case["_event_source"] = logs.file_of[qid]
                run.log_files[logs.file_of[qid]] += 1
            else:
                case["_events"], case["_event_source"] = [], "none"


# ---------------------------------------------------------------------------- cost


@dataclass
class CallCost:
    """One LLM call's tokens and its cost recomputed from the pricing card."""

    uncached_in: int
    cache_write: int
    cache_read: int
    output: int
    stored: float
    parts: dict[str, float] | None  # None when the model has no pricing row
    total: float | None
    error: str | None = None

    @property
    def differs(self) -> bool:
        """Whether the stored cost disagrees with the recomputed one (or none exists)."""
        return self.total is None or abs(self.total - self.stored) > COST_TOLERANCE_USD


def _part_rates(rates: Mapping[str, Any], key: str) -> dict[str, Any]:
    """A rate row billing only one token class, with the same tier thresholds.

    ``cost_usd`` applied to this row with a call's full counts gives that class's share
    of the call's cost, because the tier choice depends only on the prompt size.
    """

    def only(level: Mapping[str, Any]) -> dict[str, Any]:
        inp = float(level["input"])
        resolved = {"input": inp, "output": float(level["output"])}
        for name in ("cached_input", "cache_write"):  # each falls back to input
            rate = level.get(name)
            resolved[name] = inp if rate is None else float(rate)
        return {k: (v if k == key else 0.0) for k, v in resolved.items()}

    row = only(rates)
    tiers = rates.get("tiers") or []
    if tiers:
        row["tiers"] = [
            {**only(t), "above_prompt_tokens": t["above_prompt_tokens"]} for t in tiers
        ]
    return row


def recompute_cost(call: Mapping[str, Any], pricing: Mapping[str, Mapping[str, Any]]) -> CallCost:
    """Recomputes one ``llm_call`` event's cost from its tokens and the pricing card.

    Args:
        call: The event (``model``, ``prompt_tokens``, ``completion_tokens``,
            ``cached_prompt_tokens``, ``cache_write_tokens``, ``cost_usd``).
        pricing: Model -> rate row (``configs/llm/pricing.yaml``).

    Returns:
        The token classes, the per-class and total recomputed cost, and the stored cost.
    """
    prompt = _int(call.get("prompt_tokens"))
    completion = _int(call.get("completion_tokens"))
    read = _int(call.get("cached_prompt_tokens"))
    write = _int(call.get("cache_write_tokens"))
    stored = _num(call.get("cost_usd"))
    base = CallCost(uncached_in=prompt - read - write, cache_write=write, cache_read=read,
                    output=completion, stored=stored, parts=None, total=None)
    rates = pricing.get(str(call.get("model")))
    if rates is None:
        base.error = f"no pricing row for {call.get('model')!r}"
        return base
    counts = dict(prompt_tokens=prompt, completion_tokens=completion,
                  cached_prompt_tokens=read, cache_write_tokens=write)
    try:
        base.total = cost_usd(rates, **counts)
        base.parts = {
            name: cost_usd(_part_rates(rates, key), **counts) for name, key in COST_PARTS
        }
    except ValueError as exc:
        base.error = str(exc)
        base.total = base.parts = None
    return base


def llm_calls(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The case's ``llm_call`` events, in order."""
    return [e for e in case.get("_events") or [] if e.get("kind") == "llm_call"]


# ---------------------------------------------------------------------------- render

E = html.escape

CSS = """
:root{--bg:#fbfbfa;--fg:#1d1f21;--mut:#666;--line:#ddd;--card:#fff;--bad:#b3261e;
--ok:#1e6b3a;--warn:#8a5a00;--hl:#f3efe2}
@media (prefers-color-scheme:dark){:root{--bg:#17181a;--fg:#e6e6e6;--mut:#9a9a9a;
--line:#333;--card:#1f2023;--bad:#ff8a80;--ok:#7fd49a;--warn:#e6b85c;--hl:#2a2820}}
body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,Segoe UI,sans-serif;
margin:0 auto;max-width:1400px;padding:16px}
h1{font-size:22px}h2{font-size:18px;margin-top:32px;border-top:2px solid var(--line);
padding-top:16px}h3{font-size:15px;margin:4px 0}h4{font-size:13px;margin:10px 0 4px}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px;display:block;
overflow-x:auto}
td,th{border:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}
th{background:var(--hl)}td.n{text-align:right;font-variant-numeric:tabular-nums}
pre{white-space:pre-wrap;word-break:break-word;background:var(--card);
border:1px solid var(--line);padding:8px;max-height:600px;overflow:auto;font-size:12px}
details{margin:4px 0}summary{cursor:pointer;color:var(--mut)}
.case{background:var(--card);border:1px solid var(--line);padding:10px;margin:12px 0;
border-radius:6px}
.bad{color:var(--bad);font-weight:600}.ok{color:var(--ok);font-weight:600}
.warn{color:var(--warn);font-weight:600}.mut{color:var(--mut)}
#filters{position:sticky;top:0;background:var(--bg);padding:8px 0;
border-bottom:1px solid var(--line);z-index:2}
#filters label{margin-right:10px;white-space:nowrap}
"""

JS = """
function applyFilter(){
  var sys=[...document.querySelectorAll('input[data-f=system]:checked')].map(e=>e.value);
  var gr=[...document.querySelectorAll('input[data-f=grade]:checked')].map(e=>e.value);
  var n=0;
  document.querySelectorAll('.case').forEach(function(c){
    var show=sys.includes(c.dataset.system)&&gr.includes(c.dataset.grade);
    c.style.display=show?'':'none'; if(show){n++;}
  });
  document.getElementById('shown').textContent=n;
}
function setOpen(o){document.querySelectorAll('.case details').forEach(d=>{d.open=o;});}
document.querySelectorAll('#filters input').forEach(e=>e.addEventListener('change',applyFilter));
applyFilter();
"""


def _usd(x: float | None, places: int = 6) -> str:
    return "-" if x is None else f"${x:.{places}f}"


def _pre(text: Any) -> str:
    return f"<pre>{E(str(text))}</pre>"


def _jdump(value: Any) -> str:
    return json.dumps(value, indent=1, default=str, ensure_ascii=False)


def _cap(text: str, limit: int) -> tuple[str, str]:
    """The text cut to *limit* characters, and a note saying what was cut."""
    notes = []
    if SOURCE_TRUNCATION_MARKER in text:
        notes.append("already truncated at the source (LLM_BODY_MAX_CHARS)")
    if len(text) > limit:
        notes.append(f"shown {limit:,} of {len(text):,} chars (--max-body-chars)")
        text = text[:limit]
    return text, "; ".join(notes)


def _body(label: str, text: Any, limit: int) -> str:
    s = "" if text is None else str(text)
    shown, note = _cap(s, limit)
    note_html = f" — <b>{E(note)}</b>" if note else ""
    return (f"<details><summary>{E(label)} ({len(s):,} chars{note_html})</summary>"
            f"{_pre(shown)}</details>")


def _table(head: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    """A table; cells are pre-rendered HTML (callers escape their text)."""
    out = ["<table><tr>", *(f"<th>{E(h)}</th>" for h in head), "</tr>"]
    for row in rows:
        out.append("<tr>" + "".join(row) + "</tr>")
    out.append("</table>")
    return "".join(out)


def _td(value: Any, num: bool = False, cls: str = "") -> str:
    classes = " ".join(c for c in ("n" if num else "", cls) if c)
    attr = f" class='{classes}'" if classes else ""
    return f"<td{attr}>{E('' if value is None else str(value))}</td>"


def _grade_class(grade: str) -> str:
    if grade == "CORRECT":
        return "ok"
    if grade in ("IMPRECISE", "PARTIAL", "REFUSED", "HEDGED_MISS"):
        return "warn"
    return "bad"


def _anchor(run_id: str) -> str:
    return "run-" + "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in run_id)


def _summary_section(runs: Sequence[Run], pricing: Mapping[str, Any]) -> str:
    rows = []
    tot: Counter[str] = Counter()
    tot_stored = tot_re = 0.0
    for run in runs:
        grades = Counter(str(c.get("grade")) for c in run.cases)
        passed = sum(1 for c in run.cases if c.get("passed"))
        case_cost = sum(_num(c.get("cost_usd")) for c in run.cases)
        recomputed = sum(recompute_cost(e, pricing).total or 0.0
                         for c in run.cases for e in llm_calls(c))
        n = len(run.cases)
        stored = run.total_cost_usd if run.total_cost_usd is not None else case_cost
        tot.update(grades)
        tot["_n"] += n
        tot["_pass"] += passed
        tot_stored += stored
        tot_re += recomputed
        rows.append([
            f"<td><a href='#{_anchor(run.run_id)}'>{E(run.system)}</a></td>",
            _td(run.run_id), _td(run.suite), _td(run.split), _td(n, True),
            _td(passed, True, "ok"), _td(n - passed, True, "bad" if n - passed else ""),
            *(_td(grades.get(g, 0), True) for g in GRADES),
            _td(_usd(stored), True), _td(_usd(recomputed), True),
            _td(_usd(stored / n if n else None), True),
        ])
    n = tot["_n"]
    rows.append([
        "<th colspan=4>total</th>", _td(n, True), _td(tot["_pass"], True),
        _td(n - tot["_pass"], True), *(_td(tot.get(g, 0), True) for g in GRADES),
        _td(_usd(tot_stored), True), _td(_usd(tot_re), True),
        _td(_usd(tot_stored / n if n else None), True),
    ])
    head = ["system", "run_id", "suite", "split", "cases", "pass", "fail", *GRADES,
            "run cost (stored)", "Σ recomputed call cost", "cost / case"]
    return ("<h2>Summary</h2>" + _table(head, rows)
            + "<p class='mut'>Run cost is the stored run total (else Σ case cost). The "
            "recomputed column covers only calls whose events were found.</p>")


def _files_section(runs: Sequence[Run], pricing: Mapping[str, Any]) -> str:
    rows = []
    for run in runs:
        calls = [e for c in run.cases for e in llm_calls(c)]
        sources = Counter(c.get("_event_source") for c in run.cases)
        rows.append([
            _td(run.system), _td(run.run_id), _td(run.source),
            _td(", ".join(f"{k} ({v} cases)" for k, v in sorted(run.log_files.items()))
                or "-"),
            _td(Path(run.console_log).name if run.console_log else "-"),
            _td(", ".join(f"{k}: {v}" for k, v in sorted(sources.items(), key=str))),
            _td(f"{run.started_at or '?'} → {run.finished_at or '?'}"),
            _td(run.status), _td(len(run.cases), True), _td(len(calls), True),
            _td(_usd(sum(_num(e.get("cost_usd")) for e in calls)), True),
            _td(_usd(sum(recompute_cost(e, pricing).total or 0.0 for e in calls)), True),
        ])
    head = ["system", "run_id", "result source", "process log(s)", "console log",
            "events from", "started → finished (UTC)", "status", "cases", "LLM calls",
            "Σ stored call cost", "Σ recomputed"]
    return ("<h2>1. Which file belongs to which run</h2>" + _table(head, rows)
            + "<p class='mut'>A process log is matched to a run by query_id, not by name. "
            "“events from: audit” means audit.query_event rows; “none” means no trace "
            "was found for that case (no --persist-audit and no matching log).</p>")


def _rollup_section(runs: Sequence[Run], pricing: Mapping[str, Any]) -> str:
    agg: dict[tuple[str, str, str], Counter] = defaultdict(Counter)
    money: dict[tuple[str, str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for run in runs:
        for case in run.cases:
            for ev in llm_calls(case):
                cc = recompute_cost(ev, pricing)
                k = (run.system, str(ev.get("stage")), str(ev.get("model")))
                agg[k].update(calls=1, uncached=cc.uncached_in, write=cc.cache_write,
                              read=cc.cache_read, out=cc.output,
                              reasoning=_int(ev.get("reasoning_tokens")),
                              unpriced=int(cc.total is None))
                m = money[k]
                for name, v in (cc.parts or {}).items():
                    m[name] += v
                m["total"] += cc.total or 0.0
                m["stored"] += cc.stored
                m["ms"] += _num(ev.get("latency_ms"))
    rows = []
    for k in sorted(agg):
        a, m = agg[k], money[k]
        diff = abs(m["total"] - m["stored"]) > COST_TOLERANCE_USD * max(a["calls"], 1)
        rows.append([
            *(_td(x) for x in k),
            *(_td(f"{a[x]:,}", True) for x in
              ("calls", "uncached", "write", "read", "out", "reasoning")),
            *(_td(f"{m[name]:.4f}", True) for name, _ in COST_PARTS),
            _td(f"{m['total']:.4f}", True, "bad" if diff else ""),
            _td(f"{m['stored']:.4f}", True),
            _td(f"{m['ms'] / 1000:.1f}", True),
            _td(a["unpriced"] or "", True, "bad" if a["unpriced"] else ""),
        ])
    head = ["system", "stage", "model", "calls", "uncached in", "cache write", "cache read",
            "output", "reasoning", "$ uncached", "$ write", "$ read", "$ output",
            "$ recomputed", "$ stored", "Σ latency s", "unpriced calls"]
    return ("<h2>2. Tokens and cost by system × stage × model</h2>" + _table(head, rows)
            + "<p class='mut'>Recomputed from configs/llm/pricing.yaml as it is now "
            "(src.llm.profiles.cost_usd: prompt-size tiers, cache read/write rates); a red "
            "total differs from the stored cost, e.g. because the card changed since the "
            "run.</p>")


def _plan_html(case: Mapping[str, Any]) -> str:
    out = []
    answer = case.get("_answer") or {}
    plan = (answer.get("plan") or {}) if isinstance(answer, dict) else {}
    exec_steps = {s.get("step_id"): s for s in
                  ((answer.get("execution_result") or {}).get("steps") or [])
                  } if isinstance(answer, dict) else {}
    if plan.get("steps"):
        rows = []
        for st in plan["steps"]:
            ex = exec_steps.get(st.get("id")) or {}
            cite = ex.get("citation") or {}
            spec = {k: v for k, v in st.items() if k not in ("id", "op")}
            rows.append([
                _td(st.get("id")), _td(st.get("op")), _td(_jdump(spec)),
                _td(ex.get("status"), cls="" if ex.get("status") in (None, "OK") else "bad"),
                _td(ex.get("value")), _td(ex.get("unit")), _td(ex.get("confidence")),
                _td(cite.get("fact_id")), _td(cite.get("section_path")), _td(ex.get("error")),
            ])
        out.append("<h4>Plan and step results</h4>" + _table(
            ["step", "op", "spec", "status", "value", "unit", "confidence", "fact_id",
             "section", "error"], rows))
    lineage = [e for e in case.get("_events") or [] if e.get("kind") == "numeric_lineage"]
    if lineage:
        rows = [[
            _td(e.get("step_id")), _td(e.get("op")),
            _td(e.get("status"), cls="" if e.get("status") in (None, "OK") else "bad"),
            _td(e.get("value", e.get("result"))), _td(e.get("unit")),
            _td(e.get("fact_id") or e.get("source_fact_ids")),
            _td(e.get("concept_family") or e.get("expr")), _td(e.get("fy")),
            _td(e.get("confidence")), _td(_jdump(e.get("resolution_notes") or "")),
            _td(e.get("error")),
        ] for e in lineage]
        out.append("<h4>Numeric lineage (per executed step)</h4>" + _table(
            ["step", "op", "status", "value", "unit", "fact_id(s)", "concept / expr", "fy",
             "confidence", "resolution notes", "error"], rows))
    return "".join(out)


def _sql_html(case: Mapping[str, Any], limit: int) -> str:
    sqls = [e for e in case.get("_events") or [] if e.get("kind") == "sql_query"]
    if not sqls:
        return ""
    parts = [f"<h4>SQL ({len(sqls)})</h4>"]
    for i, e in enumerate(sqls, 1):
        label = (f"#{i} {e.get('source')} · {e.get('n_rows')} rows · "
                 f"{_num(e.get('latency_ms')):.0f} ms")
        parts.append(_body(label, e.get("sql"), limit))
    return "".join(parts)


def _citations_html(case: Mapping[str, Any]) -> str:
    produced = case.get("produced") or {}
    out = [f"<p><b>cited fact_ids</b> {E(str(produced.get('fact_ids') or []))} · "
           f"<b>chunk_ids</b> {E(str(produced.get('chunk_ids') or []))}</p>"]
    answer = case.get("_answer") or {}
    details = answer.get("answer_details") if isinstance(answer, dict) else None
    verdicts = {}
    if isinstance(answer, dict):
        faith = (answer.get("metadata") or {}).get("faithfulness") or {}
        verdicts = {v.get("index"): v for v in faith.get("verdicts") or []}
    if details:
        rows = []
        for i, d in enumerate(details):
            v = verdicts.get(i) or {}
            rows.append([
                _td(d.get("statement")), _td(", ".join(map(str, d.get("fact_ids") or []))),
                _td(", ".join(map(str, d.get("chunk_ids") or []))),
                _td(v.get("verdict"), cls="" if v.get("verdict") in (None, "SUPPORTED")
                    else "bad"),
                _td(v.get("reason")),
            ])
        out.append("<h4>Statements and citations</h4>" + _table(
            ["statement", "fact_ids", "chunk_ids", "verifier", "verifier reason"], rows))
    return "".join(out)


def _tools_html(case: Mapping[str, Any]) -> str:
    tools = [e for e in case.get("_events") or [] if e.get("kind") == "tool_call"]
    if not tools:
        return ""
    rows = [[_td(e.get("source_id")), _td(e.get("tool")), _td(_jdump(e.get("arguments"))),
             _td(e.get("status"), cls="" if e.get("status") in (None, "ok") else "bad"),
             _td(e.get("chars"), True), _td(e.get("latency_ms"), True)] for e in tools]
    return "<h4>Tool calls</h4>" + _table(
        ["source", "tool", "arguments", "status", "chars", "ms"], rows)


def _calls_html(case: Mapping[str, Any], pricing: Mapping[str, Any], limit: int) -> str:
    calls = llm_calls(case)
    if not calls:
        return "<p class='mut'>No LLM call events for this case.</p>"
    rows, bodies = [], []
    for i, ev in enumerate(calls, 1):
        cc = recompute_cost(ev, pricing)
        rows.append([
            _td(i, True), _td(str(ev.get("_ts") or "")[11:23]), _td(ev.get("stage")),
            _td(ev.get("model")), _td(ev.get("served_model")),
            _td(", ".join(map(str, ev.get("prompt_templates") or []))
                + f" @{ev.get('prompt_version')}"),
            _td(f"{_int(ev.get('prompt_tokens')):,}", True), _td(f"{cc.uncached_in:,}", True),
            _td(f"{cc.cache_write:,}", True), _td(f"{cc.cache_read:,}", True),
            _td(f"{cc.output:,}", True), _td(_int(ev.get("reasoning_tokens")), True),
            _td(f"{cc.stored:.6f}", True),
            _td(cc.error or f"{cc.total:.6f}", True, "bad" if cc.differs else ""),
            _td(f"{_num(ev.get('latency_ms')):.0f}", True), _td(ev.get("finish_reason")),
            _td(f"{ev.get('attempts')}/{ev.get('retries')}r", True), _td(ev.get("outcome")),
        ])
        meta = {k: v for k, v in ev.items()
                if k not in ("system", "user", "response") and not k.startswith("_")}
        bodies.append(
            f"<details><summary>LLM call {i} · {E(str(ev.get('stage')))} · "
            f"{E(str(ev.get('model')))}</summary>"
            f"{_body('metadata', _jdump(meta), limit)}"
            f"{_body('system prompt', ev.get('system'), limit)}"
            f"{_body('user prompt', ev.get('user'), limit)}"
            f"{_body('response', ev.get('response'), limit)}</details>")
    head = ["#", "time", "stage", "model", "served", "templates @version", "prompt",
            "uncached", "write", "read", "out", "reasoning", "stored $", "recomputed $",
            "ms", "finish", "attempts", "outcome"]
    out = f"<h4>LLM calls ({len(calls)})</h4>" + _table(head, rows) + "".join(bodies)
    s_stored = sum(_num(e.get("cost_usd")) for e in calls)
    case_cost = case.get("cost_usd")
    if case_cost is not None and abs(s_stored - _num(case_cost)) > COST_TOLERANCE_USD:
        out += (f"<p class='bad'>Σ stored call cost {s_stored:.6f} ≠ case cost "
                f"{_num(case_cost):.6f}</p>")
    return out


def _timeline_html(case: Mapping[str, Any], limit: int) -> str:
    evs = case.get("_events") or []
    if not evs:
        return ""
    parts = [f"<details><summary>full event timeline ({len(evs)} entries, from "
             f"{E(str(case.get('_event_source')))})</summary>"]
    for ev in evs:
        ts = E(str(ev.get("_ts") or ""))
        if ev.get("_raw"):
            msg, _ = _cap(str(ev.get("message") or ""), min(limit, 4000))
            parts.append(f"<div class='mut'>{ts} {E(str(ev.get('logger')))} "
                         f"{E(str(ev.get('level')))}: {E(msg)}</div>")
            continue
        shown = {k: v for k, v in ev.items() if not k.startswith("_")}
        if ev.get("kind") == "llm_call":
            shown = {k: v for k, v in shown.items() if k not in ("system", "user", "response")}
        parts.append(_body(f"{ts} {ev.get('kind')} · {ev.get('stage')}", _jdump(shown), limit))
    parts.append("</details>")
    return "".join(parts)


def _case_html(run: Run, case: Mapping[str, Any], pricing: Mapping[str, Any],
               limit: int) -> str:
    grade = str(case.get("grade"))
    produced = case.get("produced") or {}
    flags = [name for name in ("silent_wrong", "confident_wrong", "needs_review")
             if case.get(name)]
    head = (f"<div class='case' data-system='{E(run.system)}' data-grade='{E(grade)}'>"
            f"<h3>{E(str(case.get('case_id')))} · {E(str(case.get('kind')))} · "
            f"{E(str(case.get('ticker')))} FY{E(str(case.get('fy')))} · "
            f"<span class='{_grade_class(grade)}'>{E(grade)}</span>"
            + (f" <span class='bad'>{E(', '.join(flags))}</span>" if flags else "")
            + f" <span class='mut'>{E(run.system)}</span></h3>")
    info = (f"<p><b>Q:</b> {E(str(case.get('question')))}<br>"
            f"<b>grader reason:</b> {E(str(case.get('reason')))}<br>"
            f"<b>query_id</b> {E(str(case.get('query_id')))} · <b>case cost</b> "
            f"{_usd(_num(case.get('cost_usd')))} · <b>latency</b> "
            f"{_num(case.get('latency_ms')) / 1000:.1f}s · <b>matched</b> "
            f"{E(str(case.get('matched_value')))} · <b>tags</b> "
            f"{E(', '.join(case.get('tags') or []))}</p>")
    audit = case.get("_audit") or {}
    if audit:
        info += (f"<p class='mut'>audit: branch {E(str(audit.get('branch')))} · intent "
                 f"{E(str(audit.get('intent')))} · degraded {E(str(audit.get('degraded')))} · "
                 f"fully answered {E(str(audit.get('fully_answered')))} · error "
                 f"{E(str(audit.get('error')))}</p>")
    answer_text = produced.get("text") or ""
    answer = case.get("_answer") or {}
    summary = answer.get("answer_summary") if isinstance(answer, dict) else None
    blocks = [head, info,
              f"<h4>Expected (key)</h4>{_pre(_jdump(case.get('expected')))}",
              "<h4>System answer</h4>" + _pre(_cap(answer_text, limit)[0])]
    if summary and summary != answer_text:
        blocks.append(_body("audit answer_summary", summary, limit))
    checks = case.get("checks")
    if checks:
        blocks.append(f"<p><b>checks</b> {E(_jdump(checks))}</p>")
    if case.get("error") or produced.get("error"):
        blocks.append(f"<p class='bad'>error: {E(str(case.get('error') or produced.get('error')))}"
                      "</p>")
    blocks += [
        _body("produced (full stored payload)", _jdump(produced), limit),
        _citations_html(case), _plan_html(case), _sql_html(case, limit), _tools_html(case),
        _calls_html(case, pricing, limit), _timeline_html(case, limit), "</div>",
    ]
    return "".join(blocks)


def _filters_html(runs: Sequence[Run]) -> str:
    systems = sorted({r.system for r in runs})
    grades = sorted({str(c.get("grade")) for r in runs for c in r.cases},
                    key=lambda g: (GRADES.index(g) if g in GRADES else len(GRADES), g))
    n = sum(len(r.cases) for r in runs)

    def boxes(kind: str, values: Sequence[str]) -> str:
        return "".join(f"<label><input type='checkbox' data-f='{kind}' value='{E(v)}' "
                       f"checked> {E(v)}</label>" for v in values)

    return (f"<div id='filters'><b>system</b> {boxes('system', systems)}<br>"
            f"<b>grade</b> {boxes('grade', grades)} · showing <span id='shown'>{n}</span> "
            f"of {n} cases · <a href='#' onclick='setOpen(true);return false'>expand all</a> "
            "· <a href='#' onclick='setOpen(false);return false'>collapse all</a></div>")


def render_report(runs: Sequence[Run], pricing: Mapping[str, Any], *,
                  max_body_chars: int = DEFAULT_MAX_BODY_CHARS,
                  title: str = "Eval run report", logs: LogIndex | None = None) -> str:
    """Renders the self-contained HTML report.

    Args:
        runs: The runs, events attached (:func:`attach_events`).
        pricing: Model -> rate row.
        max_body_chars: The cap on any one prompt, response, SQL or payload shown.
        title: The page title.
        logs: The log index, to list the files read.

    Returns:
        The HTML document.
    """
    now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")
    out = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{E(title)}</title><style>{CSS}</style></head><body>",
        f"<h1>{E(title)}</h1>",
        f"<p class='mut'>Generated {E(now)} by scripts/eval/run_report.py (read-only, no LLM "
        "calls). Each case is joined to its trace by query_id: audit.query_event rows when "
        "the run used --persist-audit, else the process logs. All text is escaped. Bodies "
        f"are capped at {max_body_chars:,} characters.</p>",
    ]
    if logs is not None and logs.files:
        out.append(_body("log files read", "\n".join(logs.files), 100_000))
    out += [_summary_section(runs, pricing), _files_section(runs, pricing),
            _rollup_section(runs, pricing), "<h2>3. Every case and every call</h2>",
            _filters_html(runs)]
    for run in runs:
        out.append(f"<h2 id='{_anchor(run.run_id)}'>{E(run.system)} — {E(run.run_id)}</h2>")
        out.append(_body("run config", _jdump(run.config), max_body_chars))
        for case in run.cases:
            out.append(_case_html(run, case, pricing, max_body_chars))
        if run.console_log:
            text = Path(run.console_log).read_text(encoding="utf-8", errors="replace")
            out.append(_body(f"console log {Path(run.console_log).name} (tail)",
                             text[-max_body_chars:], max_body_chars))
    out.append(f"<script>{JS}</script></body></html>")
    return "\n".join(out)


# ---------------------------------------------------------------------------- CLI


def _expand_sources(sources: Sequence[str]) -> list[str]:
    out: list[str] = []
    for s in sources:
        if any(ch in s for ch in "*?[") and not Path(s).exists():
            matches = sorted(glob.glob(s))
            if not matches:
                raise SystemExit(f"no file matches {s!r}")
            out.extend(matches)
        else:
            out.append(s)
    return out


def _is_report_path(source: str) -> bool:
    return source.endswith(".json") and Path(source).exists()


def load_runs(sources: Sequence[str], reader_factory: Any = PostgresRunReader
              ) -> tuple[list[Run], RunReader | None]:
    """Loads every source, each checked for held-out before its cases are read.

    Args:
        sources: Run ids and JSON report paths.
        reader_factory: Builds the store reader when a run id is given.

    Returns:
        The runs, sorted by start time, and the reader (None when only files were given).
    """
    reader: RunReader | None = None
    runs = []
    for source in sources:
        if _is_report_path(source):
            report = json.loads(Path(source).read_text(encoding="utf-8"))
            runs.append(run_from_report(report, source))
        else:
            reader = reader or reader_factory()
            runs.append(run_from_db(source, reader))
    runs.sort(key=lambda r: (r.started_at or "", r.run_id))
    return runs, reader


def _load_pricing() -> dict[str, dict[str, Any]]:
    from src.llm.profiles import load_committed_settings

    return load_committed_settings().pricing_table()


def main(argv: list[str] | None = None) -> int:
    """Runs the CLI.

    Args:
        argv: Command-line arguments; None reads ``sys.argv``.

    Returns:
        0, or 2 when a source is held-out.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("sources", nargs="+",
                        help="eval run ids and/or run_suite --out JSON reports (globs ok)")
    parser.add_argument("--logs", nargs="*", default=[],
                        help="process log files or directories (xbrlrag_*.log), used for "
                             "cases without audit rows")
    parser.add_argument("--out", required=True, help="the HTML file to write")
    parser.add_argument("--max-body-chars", type=int, default=DEFAULT_MAX_BODY_CHARS,
                        help=f"cap on each prompt/response/SQL shown "
                             f"(default {DEFAULT_MAX_BODY_CHARS:,})")
    parser.add_argument("--no-audit", action="store_true",
                        help="do not read the audit schema (logs only)")
    parser.add_argument("--only-failed", action="store_true",
                        help="render only cases that did not pass")
    parser.add_argument("--case", action="append", default=[],
                        help="render only cases whose id contains this text (repeatable)")
    parser.add_argument("--title", default=None, help="page title")
    args = parser.parse_args(argv)

    reader: RunReader | None = None
    try:
        runs, reader = load_runs(_expand_sources(args.sources))
    except HeldOutRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    try:
        for run in runs:
            run.cases = [c for c in run.cases
                         if (not args.only_failed or not c.get("passed"))
                         and (not args.case or any(n in str(c.get("case_id"))
                                                   for n in args.case))]
        logs = load_logs(args.logs) if args.logs else None
        audit_reader = None
        if not args.no_audit:
            audit_reader = reader or PostgresRunReader()
            if reader is None:
                reader = audit_reader
        try:
            attach_events(runs, logs, audit_reader)
        except Exception as exc:  # isolation point: audit rows are optional for files
            if all(r.source == "db" for r in runs):
                raise
            print(f"warning: audit schema not read ({type(exc).__name__}); using logs only",
                  file=sys.stderr)
            attach_events(runs, logs, None)
        title = args.title or "Eval run report: " + ", ".join(r.run_id for r in runs)
        doc = render_report(runs, _load_pricing(), max_body_chars=args.max_body_chars,
                            title=title, logs=logs)
    finally:
        close = getattr(reader, "close", None)
        if close is not None:
            close()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    n_cases = sum(len(r.cases) for r in runs)
    n_calls = sum(len(llm_calls(c)) for r in runs for c in r.cases)
    print(f"wrote {out} ({out.stat().st_size:,} bytes): {len(runs)} run(s), {n_cases} "
          f"case(s), {n_calls} LLM call(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
