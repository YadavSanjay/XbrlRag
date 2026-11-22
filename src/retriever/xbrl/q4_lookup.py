# The fourth quarter of a duration measure: the filed quarter, else FY minus nine months.
#
# Spec: docs/specs/q4-lookup.md. A pure function over stored ``mv_financial_facts`` rows of
# one filer and one measure, as the flat lookup's S1 query reads them. It decides which rows
# answer (the year's own column, a filed fourth quarter, the same label's nine-month
# year-to-date, each under the S1 rule) and leaves the arithmetic and its refusals to
# ``period_disclosure.derive_q4``.

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from src.numerics.restatement import current_column, latest_restatement
from src.numerics.stated_numbers import display_unit
from src.retriever.xbrl.family_resolution import candidate_order
from src.retriever.xbrl.period_disclosure import _same_concept, derive_q4, duration_class

_ONE_DAY = dt.timedelta(days=1)
_KEYS = ("status", "value", "unit", "period_start", "period_end", "days", "decimals",
         "fact_ids", "accessions", "basis", "note", "reason")


def _refusal(reason: str) -> dict[str, Any]:
    """R7: a refusal carries only its status and reason."""
    out: dict[str, Any] = dict.fromkeys(_KEYS)
    out["status"] = "refused"
    out["reason"] = reason
    return out


def _day(value: Any) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def _class(row: Mapping[str, Any]) -> str:
    return duration_class(row.get("period_start"), row["period_end"])


def _first(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The row ``candidate_order`` puts first, or None."""
    order = candidate_order(rows)
    return rows[order[0]] if order else None


def _latest(row: Mapping[str, Any], considered: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """S1: the row's latest restatement among the considered rows (itself when none)."""
    return _first(latest_restatement([row], considered)) or row


def _amount(value: Any) -> str:
    """A value as the note states it: an integral value with thousands separators."""
    if isinstance(value, (int, Decimal)) and value == int(value):
        return f"{int(value):,}"
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):,}"
    return str(value)


def _derive(year: Mapping[str, Any], nine: Mapping[str, Any], shown: str | None,
            basis: str, extra_note: str | None) -> dict[str, Any]:
    """``derive_q4`` on the chosen rows (compared in the shown unit), as R5/R7's result."""
    got = derive_q4(dict(year, unit=shown), dict(nine, unit=shown))
    if got["status"] != "derived":
        return _refusal(got["reason"])
    out = {k: got.get(k) for k in _KEYS}
    out["unit"] = shown
    out["basis"] = basis
    out["fact_ids"] = tuple(got["fact_ids"])
    out["accessions"] = tuple(got["accessions"])
    out["note"] = got["note"] if extra_note is None else f"{got['note']} {extra_note}"
    out["reason"] = None
    return out


def select_q4(
    rows: Sequence[Mapping[str, Any]],
    fy: int,
    unit: str | None = None,
    as_originally_reported: bool = False,
) -> dict[str, Any]:
    """The fourth quarter of fiscal-year label ``fy``, filed or derived, or a refusal.

    Args:
        rows: Stored rows of one filer and one measure, any filing and period, with the
            S1 and candidate-order columns.
        fy: The fiscal-year label.
        unit: The unit to answer in (requested, else the reporting currency), or None.
        as_originally_reported: Read each side's original filing instead of its latest
            restatement.

    Returns:
        A dict with keys ``status``, ``value``, ``unit``, ``period_start``,
        ``period_end``, ``days``, ``decimals``, ``fact_ids``, ``accessions``, ``basis``,
        ``note`` and ``reason``, as in the spec's R7.
    """
    non_dimensional = [r for r in rows if r.get("segment_axis") is None]
    if non_dimensional and all(r.get("period_start") is None for r in non_dimensional):
        return _refusal("the measure is an instant (a balance); a fourth quarter is derived "
                        "only for a duration")
    considered = [r for r in non_dimensional if r.get("period_start") is not None]
    if unit is not None:
        wanted = display_unit(unit)
        considered = [r for r in considered if display_unit(r.get("unit")) == wanted]

    # R2: the year's own column.
    own_year = current_column(considered, fy, "FY", as_originally_reported)
    years = [r for r in own_year if _class(r) == "annual"]
    if unit is None and len({display_unit(r.get("unit")) for r in years}) > 1:
        return _refusal(f"the FY{fy} year is filed in more than one unit; the unit must be "
                        "chosen first")
    year = _first(years)
    if year is None:
        return _refusal(f"no FY{fy} annual figure is filed for this measure"
                        + (f" in {unit}" if unit is not None else ""))
    shown = display_unit(year.get("unit"))
    year_end = _day(year["period_end"])

    def _matches(r: Mapping[str, Any]) -> bool:
        return (_same_concept(r.get("concept"), year.get("concept"))
                and display_unit(r.get("unit")) == shown)

    # R4 and R6: the nine months, and the basis both sides share.
    nines = [r for r in current_column(considered, fy, "Q3", as_originally_reported)
             if _class(r) == "nine_month" and _matches(r)
             and _day(r["period_start"]) == _day(year["period_start"])]
    nine = _first(nines)
    if nine is None:
        derived = _refusal(f"no FY{fy} nine-month year-to-date figure on the year's start "
                           f"date is filed, so Q4 cannot be derived")
    elif as_originally_reported:
        derived = _derive(year, nine, shown, "original", None)
    else:
        year_latest, nine_latest = _latest(year, considered), _latest(nine, considered)
        year_restated = year_latest.get("value") != year.get("value")
        nine_restated = nine_latest.get("value") != nine.get("value")
        if year_restated != nine_restated:
            side = "year" if year_restated else "nine-month year-to-date"
            other = "nine-month year-to-date" if year_restated else "year"
            derived = _derive(
                year, nine, shown, "as_filed",
                f"The {side} was later restated but the {other} was not, so both are taken "
                "as originally filed (one basis).")
        else:
            derived = _derive(year_latest, nine_latest, shown, "latest", None)

    # R3: a filed fourth quarter answers first.
    quarters = [r for r in own_year
                if _class(r) == "quarter" and _matches(r) and _day(r["period_end"]) == year_end
                and r.get("value") is not None and r.get("value") != year.get("value")]
    quarter = _first(quarters)
    if quarter is None:
        return derived
    basis = "original"
    if not as_originally_reported:
        quarter, basis = _latest(quarter, considered), "latest"
    start = _day(quarter["period_start"])
    end = _day(quarter["period_end"])
    note = (f"Q4 ({start.isoformat()} to {(end - _ONE_DAY).isoformat()}) is the fourth "
            "quarter as filed in the annual report.")
    if derived["status"] == "derived" and derived["value"] != quarter.get("value"):
        note += (f" The fiscal year minus the nine-month year-to-date gives "
                 f"{_amount(derived['value'])} instead; the filed figure is used.")
    return {
        "status": "filed", "value": quarter.get("value"), "unit": shown,
        "period_start": start.isoformat(), "period_end": (end - _ONE_DAY).isoformat(),
        "days": (end - start).days, "decimals": quarter.get("decimals"),
        "fact_ids": (quarter.get("fact_id"),), "accessions": (quarter.get("accession_no"),),
        "basis": basis, "note": note, "reason": None,
    }
