"""Cover-page identity for ``entity_master``: company name and trading symbol.

``scripts/ddl/populate/entity_master.sql`` fills every other column and leaves these
two alone; this module sets them from each filer's latest filing. Picking them in SQL
with ``MAX()``/``MIN()`` named CEG after its co-registrant and, on every re-run, turned
Alphabet's GOOGL into GOOG. See docs/specs/identity-and-stand-in.md.

Run (after entity_master.sql; ``bootstrap_db.py``'s ``layer0-populate`` step does both):
    conda run -n py312 python -m src.ingest.sec_edgar.ingest.entity_identity [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import re

logger = logging.getLogger(__name__)

_LEGAL_ENTITY_AXIS = "dei:LegalEntityAxis"
_EXCHANGE_AXIS = "dei:SecurityExchangeAxis"
_STANDARD_PREFIXES = frozenset({"us-gaap", "srt", "dei", "exch", "country"})
# Namespace URI fragment -> prefix for the standard taxonomies; anything else is the
# filer's own extension.
_NAMESPACE_PREFIXES = (
    ("fasb.org/us-gaap/", "us-gaap"),
    ("fasb.org/srt/", "srt"),
    ("xbrl.sec.gov/dei/", "dei"),
    ("xbrl.sec.gov/exch/", "exch"),
    ("xbrl.sec.gov/country/", "country"),
)


def select_entity_identity(facts: list[dict], existing: dict | None) -> dict:
    """Choose a filer's company name and trading symbol from one filing's DEI cover facts.

    Args:
        facts: The filing's DEI facts for one CIK, each
            ``{"name": <DEI local name>, "value": <str>, "dims": {<axis>: <member>}}`` with
            prefixed QNames.
        existing: The stored ``entity_master`` row (``company_name``, ``trading_symbol``),
            or None.

    Returns:
        ``{"company_name": str | None, "trading_symbol": str | None}``; None keeps the
        stored value.
    """
    # A co-registrant's facts (combined filings) describe another company.
    own = [f for f in facts if _LEGAL_ENTITY_AXIS not in (f.get("dims") or {})]

    names = sorted({
        (f.get("value") or "").strip()
        for f in own
        if f["name"] == "EntityRegistrantName" and not f.get("dims")
    } - {""})
    company_name = names[0] if names else None

    primary: dict[str, bool] = {}   # symbol -> listed as a primary listing
    for f in own:
        if f["name"] != "TradingSymbol":
            continue
        symbol = (f.get("value") or "").strip()
        if not symbol or re.search(r"\s", symbol):
            continue   # "ORCL PRD" names a security, not the listing's ticker
        primary[symbol] = primary.get(symbol, False) or _is_primary_listing(
            f.get("dims") or {}
        )

    stored = (existing or {}).get("trading_symbol")
    if not primary:
        symbol = None
    elif stored in primary:
        symbol = stored
    else:
        symbol = min(primary, key=lambda s: (not primary[s], len(s), s))
    return {"company_name": company_name, "trading_symbol": symbol}


def _is_primary_listing(dims: dict) -> bool:
    """No dimension but an exchange, or the share class is a standard member."""
    return all(
        axis == _EXCHANGE_AXIS or member.split(":", 1)[0] in _STANDARD_PREFIXES
        for axis, member in dims.items()
    )


def _prefixed(namespace: str | None, local_name: str) -> str:
    for fragment, prefix in _NAMESPACE_PREFIXES:
        if fragment in (namespace or ""):
            return f"{prefix}:{local_name}"
    return f"ext:{local_name}"


# Only explicit dimensions: Arelle's context_dimension also stores every axis's default
# (domain) member, with which every cover fact would look dimensioned.
_COVER_FACTS_SQL = """
SELECT f.fact_id, q.local_name, f.fact_value,
       aq.namespace AS axis_ns, aq.local_name AS axis,
       mq.namespace AS member_ns, mq.local_name AS member
FROM   accession a
JOIN   fact     f   ON f.accession_id = a.accession_id
JOIN   context  ctx ON ctx.context_id = f.context_id
JOIN   element  el  ON el.element_id  = f.element_id
JOIN   qname    q   ON q.qname_id     = el.qname_id
LEFT   JOIN context_dimension cd ON cd.context_id = f.context_id AND cd.is_default = FALSE
LEFT   JOIN qname aq ON aq.qname_id = cd.dimension_qname_id
LEFT   JOIN qname mq ON mq.qname_id = cd.member_qname_id
WHERE  a.filing_accession_number = %(accession)s
  AND  LPAD(ctx.entity_identifier, 10, '0') = %(cik)s
  AND  q.namespace LIKE 'http://xbrl.sec.gov/dei/%%'
  AND  q.local_name IN ('EntityRegistrantName', 'TradingSymbol')
"""


def cover_facts(cur, cik: str, accession: str) -> list[dict]:
    """One filing's name and symbol DEI facts for *cik*, in ``select_entity_identity`` form."""
    cur.execute(_COVER_FACTS_SQL, {"cik": cik, "accession": accession})
    facts: dict[int, dict] = {}
    for fact_id, name, value, axis_ns, axis, member_ns, member in cur.fetchall():
        f = facts.setdefault(fact_id, {"name": name, "value": value, "dims": {}})
        if axis:
            f["dims"][_prefixed(axis_ns, axis)] = _prefixed(member_ns, member)
    return list(facts.values())


def populate_entity_identity(conn, dry_run: bool = False) -> list[tuple[str, dict, dict]]:
    """Set ``company_name`` and ``trading_symbol`` for every ``entity_master`` row.

    Each row is read from its ``last_seen_accession``. A None choice keeps the stored
    value. Returns ``(cik, stored, chosen)`` for each row whose identity changes.
    """
    changes = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT cik, company_name, trading_symbol, last_seen_accession "
            "FROM entity_master ORDER BY cik"
        )
        for cik, name, symbol, accession in cur.fetchall():
            if not accession:
                continue
            stored = {"company_name": name, "trading_symbol": symbol}
            chosen = select_entity_identity(cover_facts(cur, cik, accession), stored)
            new = {k: chosen[k] if chosen[k] is not None else stored[k] for k in stored}
            if new == stored:
                continue
            changes.append((cik, stored, new))
            if not dry_run:
                cur.execute(
                    "UPDATE entity_master SET company_name = %(company_name)s, "
                    "trading_symbol = %(trading_symbol)s WHERE cik = %(cik)s",
                    {**new, "cik": cik},
                )
    if not dry_run:
        conn.commit()
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="Report changes, write nothing.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from src.db.conn import connect

    conn = connect()
    try:
        changes = populate_entity_identity(conn, dry_run=args.dry_run)
    finally:
        conn.close()
    verb = "would change" if args.dry_run else "changed"
    for cik, before, after in changes:
        logger.info("%s %s: %s -> %s", verb, cik, before, after)
    logger.info("entity identity: %d row(s) %s", len(changes), verb)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
