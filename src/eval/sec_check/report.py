"""Renders the SEC companyfacts check: the JSON report, its Markdown and the hand-check list.

Pure functions over ``CaseCheck``s, the summary and our fact records; the CLI
(``scripts/eval/check_against_sec.py``) does the I/O.

Spec: docs/specs/sec-companyfacts-check.md (requirement 11).
"""

from __future__ import annotations

from collections.abc import Mapping

from src.eval.sec_check.base import FactRecord

# Case statuses in report order; a status no case has still gets a column.
CASE_STATUSES = ("match", "mismatch", "unverifiable", "hand_check", "template_unknown")

_EDGAR_INDEX = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/"


def filing_index_url(cik: str, accession_no: str) -> str:
    """The EDGAR filing-index URL of one filing.

    Args:
        cik: The filer's CIK (zero-padded or not).
        accession_no: The accession number, with or without dashes.

    Returns:
        ``https://www.sec.gov/Archives/edgar/data/<cik int>/<accession, no dashes>/``.
    """
    return _EDGAR_INDEX.format(cik=int(str(cik).strip()),
                               accession=accession_no.replace("-", ""))


def _fmt(value: float | None) -> str:
    if value is None:
        return "—"
    if value != 0 and abs(value) < 10:
        return f"{value:.6g}"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.4f}"


def _rate(rate: float | None) -> str:
    return "—" if rate is None else f"{rate:.1%}"


def _summary_table(summary: Mapping) -> list[str]:
    lines = ["| kind | total | " + " | ".join(CASE_STATUSES) + " | checked | match rate |",
             "|---|" + "---:|" * (len(CASE_STATUSES) + 3)]
    kinds = sorted(k for k in summary if k not in ("all", "fact_status_counts")) + ["all"]
    for kind in kinds:
        row = summary.get(kind) or {}
        total = sum(row.get(s, 0) for s in CASE_STATUSES)
        cells = [str(row.get(s, 0)) for s in CASE_STATUSES]
        lines.append(f"| {kind} | {total} | " + " | ".join(cells)
                     + f" | {row.get('checked', 0)} | {_rate(row.get('match_rate'))} |")
    return lines


def render_markdown(report: Mapping) -> str:
    """The report as Markdown: a status table per kind, then every non-match case.

    Args:
        report: The JSON report (``build`` keys: ``summary``, ``case_checks``, ...).

    Returns:
        The Markdown text.
    """
    summary = report["summary"]
    lines = ["# SEC companyfacts check", "",
             f"- Suite: `{report.get('suite_path')}` (corpus fingerprint "
             f"`{report.get('corpus_fingerprint')}`)",
             f"- Run: {report.get('run_date')}, git `{report.get('git_sha')}`, "
             f"seed {report.get('seed')}", "",
             "## Cases by kind", ""]
    lines += _summary_table(summary)
    lines += ["", "## Facts by status", "", "| fact status | facts |", "|---|---:|"]
    for status, n in sorted((summary.get("fact_status_counts") or {}).items()):
        lines.append(f"| {status} | {n} |")

    others = [cc for cc in report["case_checks"]
              if cc["status"] not in ("match", "hand_check")]
    lines += ["", f"## Non-match cases ({len(others)})", ""]
    if not others:
        lines.append("None.")
    for cc in others:
        reason = f" ({cc['reason']})" if cc.get("reason") else ""
        lines += [f"### `{cc['case_id']}` — {cc['status']}{reason}", ""]
        for it in cc["items"]:
            mark = "agrees" if it["agree"] else "differs"
            lines.append(f"- item {it['label']}: expected {_fmt(it['expected'])}, "
                         f"SEC {_fmt(it['sec_value'])} — {mark}")
        for fc in cc["facts"]:
            flip = ", sign flip" if fc.get("sign_flip") else ""
            lines.append(f"- fact {fc['fact_id']}: {fc['status']}, SEC "
                         f"{_fmt(fc['sec_value'])}{flip}")
            for r in fc.get("restatements") or []:
                lines.append(f"  - restated in {r['accession_no']}: {_fmt(r['value'])}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_hand_check(cases: list[dict], records: Mapping[int, FactRecord]) -> str:
    """The hand-check list: each selected case with its cited facts and filings.

    Args:
        cases: The selected suite cases (``select_hand_checks``).
        records: Our fact records, by fact id.

    Returns:
        The Markdown text.
    """
    lines = ["# SEC companyfacts check — hand-check list", "",
             f"{len(cases)} dimensional and breakdown cases, which companyfacts cannot "
             "cover. Check each expected value against the filing itself.", ""]
    for case in cases:
        lines += [f"## `{case['id']}`", "",
                  f"- Question: {case.get('question', '')}",
                  f"- Kind: {case['kind']}; ticker {case.get('ticker', '—')}; "
                  f"fiscal year {case.get('fy', '—')}"]
        if "expected_value" in case:
            lines.append(f"- Expected: {_fmt(case['expected_value'])} "
                         f"{case.get('unit', '')}".rstrip())
        for row in case.get("expected_rows") or []:
            lines.append(f"- Expected row: {row.get('entity')} = {_fmt(row.get('value'))}")
        lines.append("- Cited facts:")
        for fact_id in case.get("expected_fact_ids") or []:
            rec = records.get(int(fact_id))
            if rec is None:
                lines.append(f"  - {fact_id}: not in the fact store")
                continue
            period = (f"{rec['period_start']} to {rec['period_end']}" if rec["period_start"]
                      else f"at {rec['period_end']}")
            lines.append(f"  - {fact_id}: `{rec['concept'].rpartition(':')[2]}` "
                         f"{_fmt(rec['value'])} {rec['unit']}, {period}; accession "
                         f"{rec['accession_no']} — "
                         f"{filing_index_url(rec['cik'], rec['accession_no'])}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def console_summary(summary: Mapping) -> str:
    """A one-screen plain-text summary of the run.

    Args:
        summary: ``summarize`` output.

    Returns:
        The text.
    """
    out = []
    kinds = sorted(k for k in summary if k not in ("all", "fact_status_counts")) + ["all"]
    for kind in kinds:
        row = summary.get(kind) or {}
        counts = ", ".join(f"{s} {row[s]}" for s in CASE_STATUSES if row.get(s))
        out.append(f"{kind:>12}: {counts}; checked {row.get('checked', 0)}, "
                   f"match rate {_rate(row.get('match_rate'))}")
    facts = ", ".join(f"{s} {n}" for s, n in
                      sorted((summary.get("fact_status_counts") or {}).items()))
    out.append(f"{'facts':>12}: {facts}")
    return "\n".join(out)
