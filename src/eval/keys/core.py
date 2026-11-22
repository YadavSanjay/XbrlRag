# Answer keys for non-dimensional numbers from SEC companyfacts and frames, and their
# cross-check against an independent source. Policies: docs/design/XbrlRag_Eval_Semantics.md.
# Spec: docs/specs/sec-keys.md (requirements 2-10).

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import date
from typing import Any, TypedDict

from src.eval.keys.base import SecSource
from src.ingest.sec_edgar.fiscal_labels import ANNUAL_FORMS, label_filings

KEY_SOURCES = ("sec_companyfacts", "sec_frames", "fact_store", "html_hand")

# The forms a key may come from: the corpus's annual and quarterly reports (S1, "the latest
# filing in the corpus"). Proxies, 8-Ks and every other form are ignored as if absent
# (docs/specs/sec-keys-report-forms.md); 10-KT is out because S2 cannot label a transition
# period as a fiscal year.
REPORT_FORMS = ANNUAL_FORMS | frozenset({"10-Q", "10-Q/A"})
# Inclusive day window of an annual duration: 52/53-week years in, quarters out.
_ANNUAL_MIN_DAYS = 350
_ANNUAL_MAX_DAYS = 380
# The cover page's share count, a disclosed alternate reading of a shares-outstanding key
# (docs/specs/shares-outstanding-reading.md).
COVER_SHARES_CONCEPT = "dei:EntityCommonStockSharesOutstanding"
# Frames calendar periods: a duration year or the year-end instant (requirement 8).
_CCP_RE = re.compile(r"CY(\d{4})(Q4I)?")
# A currency unit: an ISO 4217 code (so ``USD/shares``, ``shares`` and ``pure`` are not).
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def is_report_form(form: str | None) -> bool:
    """Whether ``form`` is one of ``REPORT_FORMS`` (docs/specs/sec-keys-report-forms.md).

    Args:
        form: A companyfacts entry's ``form``, or ``None`` when it has none.

    Returns:
        ``True`` exactly when ``form`` is a str equal to a report form (case-sensitive).
    """
    return isinstance(form, str) and form in REPORT_FORMS


def carries_financial_data(company_facts: dict, accession: str) -> bool:
    """Whether a filing tags anything beyond its cover page.

    Spec: docs/specs/restatement-s1.md, requirement 6. A Part III-only 10-K/A carries only
    ``dei`` cover facts and is ignored by ``latest_filed_key``.

    Args:
        company_facts: A companyfacts document in the SEC API's shape.
        accession: The filing's accession number.

    Returns:
        ``True`` exactly when the accession has a non-nil entry in a taxonomy other than
        ``dei``.
    """
    return bool(_financial_accessions(company_facts, {accession}))


def _financial_accessions(company_facts: dict, accessions: set[str]) -> set[str]:
    """The ``accessions`` with a non-nil entry in a taxonomy other than ``dei``."""
    found: set[str] = set()
    for taxonomy, concepts in company_facts.get("facts", {}).items():
        if taxonomy == "dei":
            continue
        for node in concepts.values():
            for entries in node.get("units", {}).values():
                for entry in entries:
                    accn = entry.get("accn")
                    if accn in accessions and entry.get("val") is not None:
                        found.add(accn)
                        if found == accessions:
                            return found
    return found


def _drop_cover_only_amendments(company_facts: dict, entries: list[dict]) -> list[dict]:
    """``entries`` without those of an amendment that tags only its cover page.

    A Part III-only 10-K/A carries only ``dei`` cover facts; its entries are ignored as if
    absent (docs/specs/restatement-s1.md, requirement 6). Any other amendment stays.

    Args:
        company_facts: The companyfacts document the entries come from.
        entries: Entries of one concept and unit.

    Returns:
        The kept entries, in input order.
    """
    amendments = {e["accn"] for e in entries
                  if isinstance(e.get("form"), str) and e["form"].endswith("/A")}
    if not amendments:
        return entries
    financial = _financial_accessions(company_facts, amendments)
    return [e for e in entries if e["accn"] not in amendments or e["accn"] in financial]


def _own_period_ends(company_facts: dict, accessions: set[str]) -> dict[str, str]:
    """Each accession's own period end: its latest annual-length duration end not after filing.

    The rule ``annual_filing_labels`` uses (docs/specs/restatement-s1.md, requirement 7).

    Args:
        company_facts: A companyfacts document in the SEC API's shape.
        accessions: The filings to date.

    Returns:
        Accession -> ISO end, for the accessions that carry an annual-length duration.
    """
    ends: dict[str, str] = {}
    for concepts in company_facts.get("facts", {}).values():
        for node in concepts.values():
            for entries in node.get("units", {}).values():
                for e in entries:
                    accn = e.get("accn")
                    if accn not in accessions or not _is_annual_duration(e):
                        continue
                    if isinstance(e.get("filed"), str) and e["end"] <= e["filed"] \
                            and e["end"] > ends.get(accn, ""):
                        ends[accn] = e["end"]
    return ends


class KeyNotFoundError(LookupError):
    """No usable entry for the requested key (requirement 3)."""


class UnitMismatchError(ValueError):
    """The concept or frame exists, but not in the requested unit (requirement 3)."""


class KeyAmbiguousError(ValueError):
    """Entries tie under the selection rule with different values (requirement 3)."""


class AnswerKey(TypedDict):
    """One answer key with its provenance (requirement 2).

    Keys: value (float | None), unit (str), decimals (int | str | None; "INF" kept),
    concept (str, "<taxonomy>:<tag>"), cik (str, 10 digits), period_start (str | None),
    period_end (str), fiscal_year (int | None), calendar_year (int | None),
    accession (str), filed (str | None), form (str | None), key_source (str),
    selection (str), policies (list[str]), status (str), verified_by (list[str]).
    """

    value: float | None
    unit: str
    decimals: int | str | None
    concept: str
    cik: str
    period_start: str | None
    period_end: str
    fiscal_year: int | None
    calendar_year: int | None
    accession: str
    filed: str | None
    form: str | None
    key_source: str
    selection: str
    policies: list[str]
    status: str
    verified_by: list[str]


class Disagreement(TypedDict):
    """A recorded disagreement between two key sources (requirement 9).

    Keys: reason (str), concept (str), unit (str), period_start (str | None),
    period_end (str), key_source (str), key_value (float | None), key_accession (str),
    other_source (str), other_value (float | None), other_unit (str),
    other_accession (str).
    """

    reason: str
    concept: str
    unit: str
    period_start: str | None
    period_end: str
    key_source: str
    key_value: float | None
    key_accession: str
    other_source: str
    other_value: float | None
    other_unit: str
    other_accession: str


class CrossCheck(TypedDict):
    """The result of a cross-check (requirement 9).

    Keys: key (AnswerKey), disagreement (Disagreement | None).
    """

    key: AnswerKey
    disagreement: Disagreement | None


def _split_concept(concept: str) -> tuple[str, str]:
    """Splits ``"<taxonomy>:<tag>"``; a concept without ``:`` raises ``ValueError``."""
    taxonomy, sep, tag = concept.partition(":")
    if not sep or not taxonomy or not tag:
        raise ValueError(f"concept must be '<taxonomy>:<tag>', got {concept!r}")
    return taxonomy, tag


def _pad_cik(cik: Any) -> str:
    """A CIK as 10 zero-padded digits, whether given as int or (padded) str."""
    return f"{int(cik):010d}"


def _unit_entries(company_facts: dict, concept: str, unit: str) -> list[dict]:
    """The report-form entries of one concept in one unit (requirement 4).

    Entries from any other form (proxies, 8-Ks, ...) are dropped first, so a unit or a
    concept that only they carry counts as absent (docs/specs/sec-keys-report-forms.md).

    Raises:
        ValueError: ``concept`` is malformed.
        KeyNotFoundError: the concept is absent, or only non-report forms carry it.
        UnitMismatchError: the concept is reported, but not in ``unit``.
    """
    taxonomy, tag = _split_concept(concept)
    node = company_facts.get("facts", {}).get(taxonomy, {}).get(tag)
    if node is None:
        raise KeyNotFoundError(f"{concept} not in companyfacts")
    units = {
        name: kept
        for name, entries in node.get("units", {}).items()
        if (kept := [e for e in entries if is_report_form(e.get("form"))])
    }
    if not units:
        raise KeyNotFoundError(f"{concept} is carried only by non-report forms")
    if unit not in units:
        raise UnitMismatchError(f"{concept} has units {sorted(units)}, not {unit!r}")
    return units[unit]


def _candidates(company_facts: dict, concept: str, unit: str, period_start: str | None,
                period_end: str) -> list[dict]:
    """The non-nil entries reporting exactly the period (requirement 4)."""
    found = []
    for entry in _unit_entries(company_facts, concept, unit):
        if entry.get("end") != period_end or entry.get("val") is None:
            continue
        if period_start is None:
            if "start" in entry and entry["start"] is not None:
                continue
        elif entry.get("start") != period_start:
            continue
        found.append(entry)
    if not found:
        raise KeyNotFoundError(
            f"no non-nil {concept} [{unit}] entry for {period_start}..{period_end}")
    return found


def _pick(candidates: list[dict], latest: bool) -> dict:
    """The entry with the greatest (or smallest) ``filed``; ties as in requirements 5-6.

    Raises:
        KeyAmbiguousError: entries share the deciding ``filed`` date with different values.
    """
    filed = max(e["filed"] for e in candidates) if latest else min(e["filed"] for e in candidates)
    tied = [e for e in candidates if e["filed"] == filed]
    _require_equal_values(tied)
    return max(tied, key=lambda e: e["accn"]) if latest else min(tied, key=lambda e: e["accn"])


def _require_equal_values(entries: list[dict]) -> None:
    """Raises ``KeyAmbiguousError`` when ``entries`` carry different values."""
    values = {e["val"] for e in entries}
    if len(values) > 1:
        raise KeyAmbiguousError(
            f"entries {sorted(e['accn'] for e in entries)} tie with values {sorted(values)}")


def _refuse_sign_flip(entries: list[dict], concept: str, period_start: str | None,
                      period_end: str) -> None:
    """Refuse a period whose filings disagree only in sign.

    The source cannot say which filing has the sign right, so the key is dropped rather
    than keyed with a guess (docs/specs/sign-and-nil.md R3).

    Args:
        entries: The period's candidate entries.
        concept: The concept, for the message.
        period_start: The duration's start, or ``None`` for an instant.
        period_end: The period's end.

    Raises:
        KeyAmbiguousError: the entries hold a value ``v != 0`` and ``-v``.
    """
    values = {e["val"] for e in entries}
    flipped = sorted(v for v in values if v > 0 and -v in values)
    if flipped:
        accns = sorted(e["accn"] for e in entries if abs(e["val"]) in flipped)
        raise KeyAmbiguousError(
            f"{concept} {period_start}..{period_end}: filings disagree in sign "
            f"(+/-{flipped}) across {accns}")


def _companyfacts_key(company_facts: dict, entry: dict, concept: str, unit: str,
                      period_start: str | None, period_end: str, selection: str) -> AnswerKey:
    """An ``AnswerKey`` for one companyfacts entry (S1 and S6 policies)."""
    return AnswerKey(
        value=entry["val"], unit=unit, decimals=None, concept=concept,
        cik=_pad_cik(company_facts["cik"]), period_start=period_start, period_end=period_end,
        fiscal_year=None, calendar_year=None, accession=entry["accn"],
        filed=entry.get("filed"), form=entry.get("form"), key_source="sec_companyfacts",
        selection=selection, policies=["S1", "S6"], status="single-source", verified_by=[],
    )


def latest_filed_key(company_facts: dict, concept: str, unit: str, period_start: str | None,
                     period_end: str) -> AnswerKey:
    """The key for one period from the latest filing that reports it (S1, requirement 5).

    An amendment that tags only its cover page (``carries_financial_data`` false) is ignored
    as if absent (docs/specs/restatement-s1.md, requirement 6).

    Args:
        company_facts: A companyfacts document in the SEC API's shape.
        concept: ``"<taxonomy>:<tag>"``.
        unit: The unit as the source names it; never converted or aliased.
        period_start: The duration's start, or ``None`` for an instant.
        period_end: The period's end (ISO date).

    Returns:
        The key of the latest filed non-nil entry; on a same-day tie of equal values, the
        one with the greatest accession.

    Raises:
        ValueError: ``concept`` is malformed.
        KeyNotFoundError: the concept or a usable entry is absent.
        UnitMismatchError: the concept is not reported in ``unit``.
        KeyAmbiguousError: same-day entries disagree, or the period's filings hold a value
            and its exact negation (docs/specs/sign-and-nil.md R3).
    """
    candidates = _drop_cover_only_amendments(
        company_facts, _candidates(company_facts, concept, unit, period_start, period_end))
    if not candidates:
        raise KeyNotFoundError(
            f"only cover-page amendments report {concept} [{unit}] for "
            f"{period_start}..{period_end}")
    _refuse_sign_flip(candidates, concept, period_start, period_end)
    entry = _pick(candidates, latest=True)
    return _companyfacts_key(company_facts, entry, concept, unit, period_start, period_end,
                             "latest_filed")


def as_reported_key(company_facts: dict, concept: str, unit: str, period_start: str | None,
                    period_end: str, accession: str | None = None) -> AnswerKey:
    """The key for one period as originally reported (S1, requirement 6).

    Args:
        company_facts: A companyfacts document in the SEC API's shape.
        concept: ``"<taxonomy>:<tag>"``.
        unit: The unit as the source names it.
        period_start: The duration's start, or ``None`` for an instant.
        period_end: The period's end (ISO date).
        accession: When given, the value this accession reports for the period; otherwise
            the earliest filed one (same-day equal values: the smallest accession).

    Returns:
        The key, with ``selection "as_reported"``.

    Raises:
        ValueError: ``concept`` is malformed.
        KeyNotFoundError: no usable entry (or none from ``accession``).
        UnitMismatchError: the concept is not reported in ``unit``.
        KeyAmbiguousError: tied entries disagree.
    """
    candidates = _candidates(company_facts, concept, unit, period_start, period_end)
    if accession is None:
        entry = _pick(candidates, latest=False)
    else:
        own = [e for e in candidates if e["accn"] == accession]
        if not own:
            raise KeyNotFoundError(
                f"{accession} reports no {concept} [{unit}] for {period_start}..{period_end}")
        _require_equal_values(own)
        entry = own[0]
    return _companyfacts_key(company_facts, entry, concept, unit, period_start, period_end,
                             "as_reported")


def _duration_days(entry: dict) -> int:
    """Days from an entry's ``start`` to its ``end``."""
    return (date.fromisoformat(entry["end"]) - date.fromisoformat(entry["start"])).days


def _is_annual_duration(entry: dict) -> bool:
    """Whether an entry is a duration of annual length (52/53-week years in, quarters out)."""
    return (entry.get("start") is not None
            and _ANNUAL_MIN_DAYS <= _duration_days(entry) <= _ANNUAL_MAX_DAYS)


def _annual_filing_entries(company_facts: dict) -> dict[str, list[dict]]:
    """Accession → its entries of an annual form and ``fp`` FY, across every concept."""
    filings: dict[str, list[dict]] = {}
    for concepts in company_facts.get("facts", {}).values():
        for node in concepts.values():
            for entries in node.get("units", {}).values():
                for entry in entries:
                    if entry.get("form") in ANNUAL_FORMS and entry.get("fp") == "FY":
                        filings.setdefault(entry["accn"], []).append(entry)
    return filings


def own_period_end(company_facts: dict, accession: str) -> str | None:
    """A filing's own period end: its latest annual-length duration end not after filing.

    The public form of the rule ``annual_filing_labels`` uses, for one filing.

    Args:
        company_facts: A companyfacts document in the SEC API's shape.
        accession: The filing.

    Returns:
        The ISO end, or None when the filing carries no annual-length duration.
    """
    return _own_period_ends(company_facts, {accession}).get(accession)


def annual_filing_labels(company_facts: dict) -> dict[str, int]:
    """The fiscal-year label of each annual filing in a companyfacts document.

    Spec: docs/specs/fiscal-label-alignment.md, requirement 10. A filing's DEI is its
    greatest ``fy`` and its period end the latest annual-length duration not after the
    entry's ``filed`` date. Instants never set a period end: cover-page instants date after
    the year end.

    Args:
        company_facts: A companyfacts document in the SEC API's shape.

    Returns:
        Accession → label, for the annual-form filings that carry an annual-length
        duration; any other filing is absent.
    """
    rows = []
    for accession, entries in _annual_filing_entries(company_facts).items():
        ends = [e["end"] for e in entries if _is_annual_duration(e) and e["end"] <= e["filed"]]
        if not ends:
            continue
        first = min(entries, key=lambda e: (e["filed"], e["form"]))
        rows.append({
            "accession_no": accession,
            "form_type": first["form"],
            "period_end": date.fromisoformat(max(ends)),
            "filed_date": date.fromisoformat(first["filed"]),
            "dei_fiscal_year": max((e["fy"] for e in entries if e.get("fy") is not None),
                                   default=None),
        })
    labels, _moved = label_filings(rows)
    return labels


def filed_under_year(entry: dict, labels: dict[str, int], fiscal_year: int) -> bool:
    """Whether an annual entry belongs to the filing labelled ``fiscal_year``.

    Args:
        entry: A companyfacts entry of an annual form and ``fp`` FY.
        labels: The document's ``annual_filing_labels``.
        fiscal_year: The filer's own fiscal-year label.

    Returns:
        ``True`` when its filing has that label, or, for a filing with no label, when the
        entry's own ``fy`` is ``fiscal_year``.
    """
    accession = entry.get("accn")
    return (labels[accession] if accession in labels else entry.get("fy")) == fiscal_year


def fiscal_year_key(company_facts: dict, concept: str, unit: str, fiscal_year: int) -> AnswerKey:
    """The key for the year the filer labels ``fiscal_year`` (S2, requirement 7).

    The period is the current-year column of the annual filings labelled ``fiscal_year``
    (``annual_filing_labels``; a filing with no label counts under its ``fy``); the value is
    the latest filed one for that period (S1), so a later filing's recast comparative wins.

    Args:
        company_facts: A companyfacts document in the SEC API's shape.
        concept: ``"<taxonomy>:<tag>"``.
        unit: The unit as the source names it.
        fiscal_year: The filer's own fiscal-year label.

    Returns:
        The key, with ``fiscal_year`` set and policies ``["S1", "S2", "S6"]``.

    Raises:
        ValueError: ``concept`` is malformed.
        KeyNotFoundError: no annual filing labels the year, or no usable entry.
        UnitMismatchError: the concept is not reported in ``unit``.
        KeyAmbiguousError: the year's end has two starts, or tied entries disagree.
    """
    return labelled_fiscal_year_key(company_facts, annual_filing_labels(company_facts), concept,
                                    unit, fiscal_year)


def labelled_fiscal_year_key(company_facts: dict, labels: dict[str, int], concept: str, unit: str,
                             fiscal_year: int) -> AnswerKey:
    """``fiscal_year_key`` with the document's labels already computed.

    ``annual_filing_labels`` scans the whole document, so a caller keying many numbers from
    one filer computes it once and comes here.

    Args:
        company_facts: A companyfacts document in the SEC API's shape.
        labels: That document's ``annual_filing_labels``.
        concept: ``"<taxonomy>:<tag>"``.
        unit: The unit as the source names it.
        fiscal_year: The filer's own fiscal-year label.

    Returns:
        As ``fiscal_year_key``.

    Raises:
        ValueError: ``concept`` is malformed.
        KeyNotFoundError: no annual filing labels the year, or no usable entry.
        UnitMismatchError: the concept is not reported in ``unit``.
        KeyAmbiguousError: the year's end has two starts, or tied entries disagree.
    """
    entries = _drop_cover_only_amendments(company_facts,
                                          _unit_entries(company_facts, concept, unit))
    is_duration = any(e.get("start") is not None for e in entries)
    annual = []
    for e in entries:
        if e.get("form") not in ANNUAL_FORMS or e.get("fp") != "FY":
            continue
        if not filed_under_year(e, labels, fiscal_year):
            continue
        if is_duration:
            if not _is_annual_duration(e):
                continue
        elif e.get("start") is not None:
            continue
        annual.append(e)
    if not annual:
        raise KeyNotFoundError(
            f"no annual filing labels fiscal year {fiscal_year} for {concept} [{unit}]")
    # Only the labelled filing's own period answers, never an earlier column; a filing with
    # no annual-length duration keeps the latest end (docs/specs/restatement-s1.md R7).
    own_end = _own_period_ends(company_facts, {e["accn"] for e in annual})
    annual = [e for e in annual if e["accn"] not in own_end or e["end"] == own_end[e["accn"]]]
    if not annual:
        raise KeyNotFoundError(
            f"fiscal year {fiscal_year} reports no {concept} [{unit}] at its own period end")
    period_end = max(e["end"] for e in annual)
    period_start: str | None = None
    if is_duration:
        starts = {e["start"] for e in annual if e["end"] == period_end}
        if len(starts) > 1:
            raise KeyAmbiguousError(
                f"fiscal year {fiscal_year} of {concept} ends {period_end} with starts "
                f"{sorted(starts)}")
        period_start = starts.pop()
    key = latest_filed_key(company_facts, concept, unit, period_start, period_end)
    key["fiscal_year"] = fiscal_year
    key["policies"] = ["S1", "S2", "S6"]
    return key


def calendar_year_key(frames: dict, cik: str, unit: str) -> AnswerKey:
    """The key for one filer's calendar year from a frames payload (S2, requirement 8).

    Args:
        frames: A frames document in the SEC API's shape (``CY####`` or ``CY####Q4I``).
        cik: The filer's CIK; zero padding is ignored.
        unit: The unit as the source names it; must equal the frame's ``uom``.

    Returns:
        The key of the filer's row, with ``calendar_year`` set.

    Raises:
        ValueError: the frame's ``ccp`` is not a calendar year or year-end instant.
        UnitMismatchError: the frame's unit is not ``unit``.
        KeyNotFoundError: the filer has no row, or its row is nil.
    """
    ccp = frames.get("ccp", "")
    match = _CCP_RE.fullmatch(ccp) if isinstance(ccp, str) else None
    if match is None:
        raise ValueError(f"frame ccp must be CY#### or CY####Q4I, got {ccp!r}")
    if frames.get("uom") != unit:
        raise UnitMismatchError(f"frame unit is {frames.get('uom')!r}, not {unit!r}")
    wanted = int(cik)
    row = next((r for r in frames.get("data", []) if int(r["cik"]) == wanted), None)
    if row is None:
        raise KeyNotFoundError(f"CIK {cik} not in frame {ccp}")
    if row.get("val") is None:
        raise KeyNotFoundError(f"CIK {cik} has a nil value in frame {ccp}")
    return AnswerKey(
        value=row["val"], unit=unit, decimals=None,
        concept=f"{frames['taxonomy']}:{frames['tag']}", cik=_pad_cik(cik),
        period_start=row.get("start"), period_end=row["end"], fiscal_year=None,
        calendar_year=int(match.group(1)), accession=row["accn"], filed=None, form=None,
        key_source="sec_frames", selection="sec_frame", policies=["S1", "S2", "S6"],
        status="single-source", verified_by=[],
    )


def _disagreement_reason(key: AnswerKey, other: AnswerKey, rel_tol: float) -> str | None:
    """The first failing check in requirement 9's order, or ``None`` when they agree."""
    a, b = key["value"], other["value"]
    if a is None or b is None:
        return "nil"
    if key["concept"] != other["concept"]:
        return "concept"
    if key["unit"] != other["unit"]:
        return "unit"
    if (key["period_start"], key["period_end"]) != (other["period_start"],
                                                    other["period_end"]):
        return "period"
    if abs(a - b) > rel_tol * max(abs(a), abs(b)):
        return "value"
    return None


def cross_check(key: AnswerKey, other: AnswerKey, rel_tol: float = 1e-9) -> CrossCheck:
    """Verifies a key against another source, or records the disagreement (requirement 9).

    The key's value is never replaced by the other source's.

    Args:
        key: The key to verify.
        other: The same number from an independent key source.
        rel_tol: Relative tolerance on the values.

    Returns:
        On agreement, a verified copy of ``key`` (``decimals`` adopted from ``other`` when
        unknown) and no disagreement; otherwise ``key`` unchanged and the disagreement.

    Raises:
        ValueError: either key source is unknown, or both are the same.
    """
    for source in (key["key_source"], other["key_source"]):
        if source not in KEY_SOURCES:
            raise ValueError(f"unknown key_source {source!r}")
    if key["key_source"] == other["key_source"]:
        raise ValueError(f"both keys come from {key['key_source']!r}; not independent")
    result = copy.deepcopy(key)
    reason = _disagreement_reason(key, other, rel_tol)
    if reason is not None:
        return CrossCheck(key=result, disagreement=Disagreement(
            reason=reason, concept=key["concept"], unit=key["unit"],
            period_start=key["period_start"], period_end=key["period_end"],
            key_source=key["key_source"], key_value=key["value"],
            key_accession=key["accession"], other_source=other["key_source"],
            other_value=other["value"], other_unit=other["unit"],
            other_accession=other["accession"],
        ))
    result["status"] = "verified"
    if other["key_source"] not in result["verified_by"]:
        result["verified_by"].append(other["key_source"])
    if result["decimals"] is None:
        result["decimals"] = other["decimals"]
    return CrossCheck(key=result, disagreement=None)


def fiscal_key_from_source(source: SecSource, cik: str, concept: str, unit: str,
                           fiscal_year: int) -> AnswerKey:
    """``fiscal_year_key`` over the filer's companyfacts from the source (requirement 10).

    Args:
        source: Where the companyfacts payload comes from.
        cik: The filer's CIK.
        concept: ``"<taxonomy>:<tag>"``.
        unit: The unit as the source names it.
        fiscal_year: The filer's own fiscal-year label.

    Returns:
        The fiscal-year key.
    """
    return fiscal_year_key(source.company_facts(cik), concept, unit, fiscal_year)


def calendar_key_from_source(source: SecSource, cik: str, concept: str, unit: str,
                             calendar_year: int, instant: bool = False) -> AnswerKey:
    """``calendar_year_key`` over the frame fetched from the source (requirement 10).

    Args:
        source: Where the frames payload comes from.
        cik: The filer's CIK.
        concept: ``"<taxonomy>:<tag>"``.
        unit: The unit as the source names it.
        calendar_year: The calendar year.
        instant: Use the year-end instant frame ``CY####Q4I`` instead of ``CY####``.

    Returns:
        The calendar-year key.

    Raises:
        ValueError: ``concept`` is malformed.
    """
    taxonomy, tag = _split_concept(concept)
    period = f"CY{calendar_year}Q4I" if instant else f"CY{calendar_year}"
    return calendar_year_key(source.frames(taxonomy, tag, unit, period), cik, unit)


def cover_shares_key(company_facts: dict, labels: dict[str, int], fiscal_year: int,
                     period_end: str) -> AnswerKey | None:
    """The cover-page share count of the original annual report labelled ``fiscal_year``.

    Spec: docs/specs/shares-outstanding-reading.md, requirement 3. The reading is
    ``dei:EntityCommonStockSharesOutstanding``, dated after the fiscal year end and on or
    before the filing date.

    Args:
        company_facts: A companyfacts document in the SEC API's shape.
        labels: That document's ``annual_filing_labels``.
        fiscal_year: The filer's own fiscal-year label.
        period_end: The year-end balance-sheet date of the key (ISO date).

    Returns:
        The cover key, with ``selection "cover_page"``; ``None`` when the original filing
        has no such entry, or its entries disagree.
    """
    try:
        entries = _unit_entries(company_facts, COVER_SHARES_CONCEPT, "shares")
    except (KeyNotFoundError, UnitMismatchError):
        return None
    candidates = [
        e for e in entries
        if e.get("form") in ANNUAL_FORMS and e.get("fp") == "FY"
        and e.get("start") is None and e.get("val") is not None
        and isinstance(e.get("end"), str) and isinstance(e.get("filed"), str)
        and filed_under_year(e, labels, fiscal_year)
        and period_end < e["end"] <= e["filed"]
    ]
    if not candidates:
        return None
    original = min((e["filed"], e["accn"]) for e in candidates)[1]
    own = [e for e in candidates if e["accn"] == original]
    if len({e["val"] for e in own}) > 1:
        return None
    entry = own[0]
    return AnswerKey(
        value=entry["val"], unit="shares", decimals=None, concept=COVER_SHARES_CONCEPT,
        cik=_pad_cik(company_facts["cik"]), period_start=None, period_end=entry["end"],
        fiscal_year=fiscal_year, calendar_year=None, accession=entry["accn"],
        filed=entry.get("filed"), form=entry.get("form"), key_source="sec_companyfacts",
        selection="cover_page", policies=["S2", "S6"], status="single-source", verified_by=[],
    )


def reporting_currency(company_facts: dict, labels: Mapping[str, int],
                       fiscal_year: int) -> str | None:
    """The currency a filer reports its annual report for ``fiscal_year`` in.

    Spec: docs/specs/key-currency-and-definition.md, requirement 1. The observations are the
    ``(unit, end)`` pairs of every fact entry, on any concept, whose unit is a currency code
    (three upper-case letters) and whose accession the labels map to ``fiscal_year``. The
    rule is the system's reporting-currency rule (``choose_lookup_unit`` with no requested
    unit): the latest end's only currency, else the one of them at the most distinct ends.

    Args:
        company_facts: A companyfacts document in the SEC API's shape.
        labels: The document's ``annual_filing_labels``.
        fiscal_year: The filer's fiscal-year label.

    Returns:
        The currency code, or None when there is no observation or no single currency.
    """
    observed: set[tuple[str, str]] = set()
    for concepts in company_facts.get("facts", {}).values():
        for node in concepts.values():
            for unit, entries in node.get("units", {}).items():
                if not _CURRENCY_RE.match(unit):
                    continue
                for e in entries:
                    end = e.get("end")
                    if isinstance(end, str) and labels.get(e.get("accn")) == fiscal_year:
                        observed.add((unit, end))
    if not observed:
        return None
    latest = max(end for _u, end in observed)  # ISO dates order lexically
    at_latest = {u for u, end in observed if end == latest}
    if len(at_latest) == 1:
        return next(iter(at_latest))
    # A convenience translation covers fewer period ends than the reporting currency.
    coverage = {u: len({end for v, end in observed if v == u}) for u in at_latest}
    best = max(coverage.values())
    top = [u for u, n in coverage.items() if n == best]
    return top[0] if len(top) == 1 else None
