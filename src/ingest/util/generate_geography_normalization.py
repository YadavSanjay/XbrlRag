#!/usr/bin/env python3
"""
Generate geography_normalization seed data for the XbrlRag target-company universe.

IMPORTANT — QName format:
  geography_normalization.member_qname must use FULL URI format to match what
  dimension_catalog and mv_financial_facts store (q.namespace || ':' || q.local_name).
  Example: 'http://xbrl.sec.gov/country/2025:US'  NOT 'country:US'
           'http://fasb.org/srt/2025:NonUsMember'  NOT 'srt:NonUsMember'
           'http://www.nvidia.com/20260125:OtherCountriesMember'  NOT 'nvda:OtherCountriesMember'

Three tiers:

  Tier 1 — SEC country taxonomy members (http://xbrl.sec.gov/country/YYYY:XX)
            Generated for each TAXONOMY_YEARS version so filings from any recent
            year are covered without needing annual CSV updates.

  Tier 2 — FASB SRT geographic aggregates (http://fasb.org/srt/YYYY:*Member)
            Same multi-year approach.

  Tier 3 — Filer extension members
            VERIFIED (from dimension_catalog when --from-db is set):
              actual namespace URI is read directly from the DB.
            UNVERIFIED (static fallback for not-yet-ingested filers):
              clearly marked as pending verification; excluded unless
              --include-unverified is passed.

Usage:
  # Generate Tier 1 + Tier 2 only (safe, no DB needed):
  python src/ingest/util/generate_geography_normalization.py

  # Add verified Tier 3 from dimension_catalog (recommended after each new ingest):
  python src/ingest/util/generate_geography_normalization.py --from-db

  # Include unverified static Tier 3 entries (companies not yet ingested):
  python src/ingest/util/generate_geography_normalization.py --from-db --include-unverified
"""

import argparse
import csv
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import yaml

from src.ingest.sec_edgar.document_set import arelle_entry_name
from src.ingest.util.arelle_cmd import arelle_cmd_from_env, resolve_arelle_cmd
from src.ingest.util.arelle_run import RC_TIMEOUT, connectivity_args, run_arelle

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Standard taxonomy years to generate entries for.
# Each year maps to namespace URIs like http://xbrl.sec.gov/country/2024,
# http://fasb.org/srt/2023, etc.  Add future years here as new taxonomy
# versions are published.
# ---------------------------------------------------------------------------
TAXONOMY_YEARS = [2022, 2023, 2024, 2025]

# Country ISO alpha-2 code → geo_name for the SEC country taxonomy.
# Extend when new countries appear in filings.
_COUNTRY_ROWS: list[tuple[str, str]] = [
    ("AE", "United Arab Emirates"),
    ("AU", "Australia"),
    ("BE", "Belgium"),
    ("BR", "Brazil"),
    ("CA", "Canada"),
    ("CH", "Switzerland"),
    ("CN", "China"),
    ("CZ", "Czech Republic"),
    ("DE", "Germany"),
    ("DK", "Denmark"),
    ("ES", "Spain"),
    ("FI", "Finland"),
    ("FR", "France"),
    ("GB", "United Kingdom"),
    ("HK", "Hong Kong"),
    ("HU", "Hungary"),
    ("ID", "Indonesia"),
    ("IE", "Ireland"),
    ("IL", "Israel"),
    ("IN", "India"),
    ("IT", "Italy"),
    ("JP", "Japan"),
    ("KR", "South Korea"),
    ("MX", "Mexico"),
    ("MY", "Malaysia"),
    ("NL", "Netherlands"),
    ("NO", "Norway"),
    ("NZ", "New Zealand"),
    ("PH", "Philippines"),
    ("PL", "Poland"),
    ("RO", "Romania"),
    ("SE", "Sweden"),
    ("SG", "Singapore"),
    ("TH", "Thailand"),
    ("TW", "Taiwan"),
    ("US", "United States"),
    ("VN", "Vietnam"),
    ("ZA", "South Africa"),
]

# SRT geographic aggregate members: (local_name, iso_code, geo_name, geo_type)
_SRT_ROWS: list[tuple[str, str, str, str]] = [
    ("UnitedStatesMember", "US", "United States", "country"),
    ("NonUsMember", "NON-US", "Outside United States", "region"),
    ("OutsideTheUnitedStatesMember", "NON-US", "Outside United States", "region"),
    ("AmericasMember", "AM", "Americas", "region"),
    ("NorthAmericaMember", "NA", "North America", "region"),
    ("SouthAmericaMember", "SA", "South America", "region"),
    ("LatinAmericaMember", "LATAM", "Latin America", "region"),
    ("EuropeMember", "EU", "Europe", "region"),
    (
        "EuropeMiddleEastAndAfricaMember",
        "EMEA",
        "Europe, Middle East and Africa",
        "region",
    ),
    ("MiddleEastMember", "ME", "Middle East", "region"),
    ("AfricaMember", "AF", "Africa", "region"),
    ("AsiaMember", "AS", "Asia", "region"),
    ("AsiaPacificMember", "APAC", "Asia Pacific", "region"),
    ("AustraliaAndNewZealandMember", "ANZ", "Australia and New Zealand", "region"),
    ("InternationalMember", "INTL", "International", "region"),
    (
        "SegmentGeographicalGroupsOfCountriesGroupOneMember",
        "",
        "Geographic Group 1",
        "segment",
    ),
    (
        "SegmentGeographicalGroupsOfCountriesGroupTwoMember",
        "",
        "Geographic Group 2",
        "segment",
    ),
    (
        "SegmentGeographicalGroupsOfCountriesGroupThreeMember",
        "",
        "Geographic Group 3",
        "segment",
    ),
]

# ---------------------------------------------------------------------------
# Label-to-geo mapping for Tier-3 extension member auto-classification.
# Each entry: (label_fragment_lower, iso_code, geo_type)
# Applied to dimension_catalog.member_label (case-insensitive contains match).
# First match wins; more specific patterns listed first.
# ---------------------------------------------------------------------------
_LABEL_TO_GEO: list[tuple[str, str, str]] = [
    # More-specific patterns must precede overlapping general ones.
    # Americas excluding US → Latin America, not US
    ("americas excluding united states", "LATAM", "region"),
    ("americas excluding the united states", "LATAM", "region"),
    # US + Canada combined segment
    ("us & canada", "US-CA", "segment"),
    ("us and canada", "US-CA", "segment"),
    ("united states and canada", "US-CA", "segment"),
    ("united states & canada", "US-CA", "segment"),
    # United States alone
    ("united states", "US", "country"),
    # Greater China / China+HK / CN+TW+HK
    # Match various parenthetical forms: "China (including Hong Kong)",
    # "China Including Hong Kong", "China, including Hong Kong"
    ("greater china", "GCN", "segment"),
    ("china including hong kong", "GCN", "segment"),
    ("china, including hong kong", "GCN", "segment"),
    ("china (including hong kong", "GCN", "segment"),
    ("china (including", "GCN", "segment"),
    # Country-specific
    ("china", "CN", "country"),
    ("taiwan", "TW", "country"),
    ("japan", "JP", "country"),
    ("south korea", "KR", "country"),
    ("korea", "KR", "country"),
    ("germany", "DE", "country"),
    ("uk", "GB", "country"),
    ("united kingdom", "GB", "country"),
    ("israel", "IL", "country"),
    ("singapore", "SG", "country"),
    ("malaysia", "MY", "country"),
    # Regions
    ("emea", "EMEA", "region"),
    ("europe, middle east and africa", "EMEA", "region"),
    ("europe, middle east & africa", "EMEA", "region"),
    ("europe middle east", "EMEA", "region"),
    ("europe", "EU", "region"),
    ("asia pacific", "APAC", "region"),
    ("asia-pacific", "APAC", "region"),
    ("apac", "APAC", "region"),
    ("north america", "NA", "region"),
    ("americas", "AM", "region"),
    ("latin america", "LATAM", "region"),
    ("rest of asia", "APAC", "region"),
    ("other asia", "APAC", "region"),
    # Rest-of-world / other
    ("rest of world", "ROW", "region"),
    ("rest of the world", "ROW", "region"),
    ("other countries", "ROW", "region"),
    ("other", "ROW", "region"),
    ("international", "INTL", "region"),
    ("non-us", "NON-US", "region"),
    ("outside the united states", "NON-US", "region"),
    ("outside united states", "NON-US", "region"),
]

# Static Tier-3 placeholder entries for companies NOT YET ingested.
# These use PLACEHOLDER_PREFIX format which cannot match dimension_catalog.
# Included only when --include-unverified is passed.
# Will be replaced by DB-sourced entries once those filings are ingested.
_TIER3_UNVERIFIED: list[dict] = [
    # GOOGL — prefixes and namespace URIs not confirmed until ingested
    {
        "member_qname": "PENDING:goog:UnitedStatesMember",
        "iso_code": "US",
        "geo_name": "United States (GOOGL) [UNVERIFIED]",
        "geo_type": "country",
    },
    {
        "member_qname": "PENDING:goog:EuropeMiddleEastAndAfricaMember",
        "iso_code": "EMEA",
        "geo_name": "EMEA (GOOGL) [UNVERIFIED]",
        "geo_type": "region",
    },
    {
        "member_qname": "PENDING:goog:AsiaPacificMember",
        "iso_code": "APAC",
        "geo_name": "Asia Pacific (GOOGL) [UNVERIFIED]",
        "geo_type": "region",
    },
    {
        "member_qname": "PENDING:goog:OtherAmericasMember",
        "iso_code": "LATAM",
        "geo_name": "Other Americas (GOOGL) [UNVERIFIED]",
        "geo_type": "region",
    },
    # META
    {
        "member_qname": "PENDING:meta:UnitedStatesAndCanadaMember",
        "iso_code": "US-CA",
        "geo_name": "US and Canada (META) [UNVERIFIED]",
        "geo_type": "segment",
    },
    {
        "member_qname": "PENDING:meta:RestOfWorldMember",
        "iso_code": "ROW",
        "geo_name": "Rest of World (META) [UNVERIFIED]",
        "geo_type": "region",
    },
    # AAPL
    {
        "member_qname": "PENDING:aapl:AmericasSegmentMember",
        "iso_code": "AM",
        "geo_name": "Americas (AAPL) [UNVERIFIED]",
        "geo_type": "segment",
    },
    {
        "member_qname": "PENDING:aapl:EuropeSegmentMember",
        "iso_code": "EU",
        "geo_name": "Europe (AAPL) [UNVERIFIED]",
        "geo_type": "segment",
    },
    {
        "member_qname": "PENDING:aapl:GreaterChinaSegmentMember",
        "iso_code": "GCN",
        "geo_name": "Greater China (AAPL) [UNVERIFIED]",
        "geo_type": "segment",
    },
    {
        "member_qname": "PENDING:aapl:JapanSegmentMember",
        "iso_code": "JP",
        "geo_name": "Japan (AAPL) [UNVERIFIED]",
        "geo_type": "segment",
    },
    {
        "member_qname": "PENDING:aapl:RestOfAsiaPacificSegmentMember",
        "iso_code": "APAC",
        "geo_name": "Rest of APAC (AAPL) [UNVERIFIED]",
        "geo_type": "segment",
    },
    # TSLA, ORCL, AMD, TSM — pending ingestion
    {
        "member_qname": "PENDING:tsla:OtherCountriesMember",
        "iso_code": "ROW",
        "geo_name": "Other Countries (TSLA) [UNVERIFIED]",
        "geo_type": "region",
    },
    {
        "member_qname": "PENDING:orcl:AmericasSegmentMember",
        "iso_code": "AM",
        "geo_name": "Americas (ORCL) [UNVERIFIED]",
        "geo_type": "region",
    },
    {
        "member_qname": "PENDING:orcl:EuropeMiddleEastAfricaMember",
        "iso_code": "EMEA",
        "geo_name": "EMEA (ORCL) [UNVERIFIED]",
        "geo_type": "region",
    },
    {
        "member_qname": "PENDING:orcl:AsiaPacificMember",
        "iso_code": "APAC",
        "geo_name": "Asia Pacific (ORCL) [UNVERIFIED]",
        "geo_type": "region",
    },
    {
        "member_qname": "PENDING:amd:OtherCountriesMember",
        "iso_code": "ROW",
        "geo_name": "Other Countries (AMD) [UNVERIFIED]",
        "geo_type": "region",
    },
    {
        "member_qname": "PENDING:tsm:TaiwanMember",
        "iso_code": "TW",
        "geo_name": "Taiwan (TSM) [UNVERIFIED]",
        "geo_type": "country",
    },
    {
        "member_qname": "PENDING:tsm:NorthAmericaMember",
        "iso_code": "NA",
        "geo_name": "North America (TSM) [UNVERIFIED]",
        "geo_type": "region",
    },
    {
        "member_qname": "PENDING:tsm:AsiaPacificMember",
        "iso_code": "APAC",
        "geo_name": "Asia Pacific (TSM) [UNVERIFIED]",
        "geo_type": "region",
    },
    {
        "member_qname": "PENDING:tsm:EuropeMiddleEastAndAfricaMember",
        "iso_code": "EMEA",
        "geo_name": "EMEA (TSM) [UNVERIFIED]",
        "geo_type": "region",
    },
    {
        "member_qname": "PENDING:tsm:JapanMember",
        "iso_code": "JP",
        "geo_name": "Japan (TSM) [UNVERIFIED]",
        "geo_type": "country",
    },
]


def _classify_label(label: str) -> tuple[str, str]:
    """Return (iso_code, geo_type) for a dimension member label, or ('ROW', 'region')."""
    lower = label.lower().strip()
    for fragment, iso_code, geo_type in _LABEL_TO_GEO:
        if fragment in lower:
            return iso_code, geo_type
    return "ROW", "region"


# Geographic axis local-name patterns used to filter dimension members.
_GEO_AXIS_PATTERNS = (
    "StatementGeographicalAxis",
    "GeographicAreasAxis",
    "GeographicalAreasAxis",
    "CountryAxis",
)

# Standard taxonomy namespace bases — members under these namespaces
# are already covered by Tier 1 / Tier 2 multi-year entries.
_STANDARD_NS_BASES = (
    "http://fasb.org/us-gaap/",
    "http://fasb.org/srt/",
    "http://xbrl.sec.gov/",
    "http://www.xbrl.org/",
    "http://xbrl.ifrs.org/",
    "https://xbrl.ifrs.org/",
)

# Path to the Arelle plugin (relative to project root)
_PLUGIN_PATH = (
    Path(__file__).parent.parent
    / "sec_edgar"
    / "ingest"
    / "arelle_plugins"
    / "export_concept_labels.py"
)


def _is_geo_axis(axis_qname: str) -> bool:
    if ":" not in axis_qname:
        return False
    local = axis_qname.rsplit(":", 1)[1]
    return any(pat in local for pat in _GEO_AXIS_PATTERNS)


def _is_standard_ns(member_qname: str) -> bool:
    return any(member_qname.startswith(b) for b in _STANDARD_NS_BASES)


def _collect_geo_members_from_filings(
    sec_edgar_root: str | Path,
    arelle_cmd: str,
    annual_only: bool = True,
    arelle_timeout: int = 180,
) -> list[dict]:
    """Walk the SEC EDGAR storage tree, run Arelle on each filing, collect
    extension geographic dimension members.

    Parameters
    ----------
    sec_edgar_root:
        Root of the local SEC EDGAR storage (contains <TICKER>/<FY>/<FORM>/).
    arelle_cmd:
        Path to arelleCmdLine.exe.
    annual_only:
        If True (default), skip 10-Q filings — they add many invocations but
        rarely introduce new geographic members beyond what annual filings have.
    arelle_timeout:
        Per-filing Arelle timeout in seconds.

    Returns
    -------
    list of dicts: {member_qname, iso_code, geo_name, geo_type, ticker, fiscal_year}
    """
    root = Path(sec_edgar_root).expanduser().resolve()
    manifests = sorted(root.rglob("manifest.yaml"))

    if not manifests:
        print(f"  WARNING: no manifest.yaml files found under {root}")
        return []

    print(f"  Found {len(manifests)} manifest files under {root}")

    # Collect all unique extension geo members across all filings
    # key = member_qname; value = best dict
    seen: dict[str, dict] = {}
    ok = skipped = errors = 0

    for manifest_path in manifests:
        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            print(f"  SKIP {manifest_path}: {exc}")
            errors += 1
            continue

        form = str(raw.get("form", "")).upper()
        ticker = str(raw.get("ticker", "?")).upper()
        fiscal_year = raw.get("fiscal_year")
        primary_doc = raw.get("primary_document")

        if annual_only and "10-Q" in form and "20-F" not in form and "10-K" not in form:
            skipped += 1
            continue

        if not primary_doc:
            print(f"  SKIP {ticker} FY{fiscal_year}: no primary_document in manifest")
            skipped += 1
            continue

        try:
            filing_file = manifest_path.parent / arelle_entry_name(raw)
        except ValueError as exc:
            print(f"  FAIL   {ticker} FY{fiscal_year}: {exc}")
            errors += 1
            continue
        if not filing_file.exists():
            print(
                f"  SKIP {ticker} FY{fiscal_year}: file not found: {filing_file.name}"
            )
            skipped += 1
            continue

        # Call Arelle with --dimension-members-output
        safe = f"{ticker}_{fiscal_year}_{form.replace('/', '_')}"
        json_path = Path(tempfile.gettempdir()) / f"geo_dim_{safe}.json"
        json_path.unlink(missing_ok=True)

        cmd = [
            arelle_cmd,
            "-f",
            str(filing_file),
            "--plugin",
            str(_PLUGIN_PATH),
            "--dimension-members-output",
            str(json_path),
            "--logFile",
            "logToStdErr",
        ]

        # Shared streaming runner: same visibility + offline handling as every
        # other Arelle invocation (see src/ingest/util/arelle_run.py).
        cmd += connectivity_args(os.environ.get("ARELLE_INTERNET_CONNECTIVITY"))
        rc, _stdout, stderr = run_arelle(
            cmd,
            timeout=arelle_timeout,
            logger=_LOGGER,
            label=filing_file.name,
            heartbeat_secs=float(os.environ.get("ARELLE_HEARTBEAT_SECS", "60")),
        )
        if rc == RC_TIMEOUT:
            print(f"  TIMEOUT {ticker} FY{fiscal_year} ({form}): {stderr[-300:]}")
            errors += 1
            continue

        if rc != 0 or not json_path.exists():
            print(f"  FAIL   {ticker} FY{fiscal_year}: arelle rc={rc}")
            errors += 1
            continue

        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  FAIL   {ticker} FY{fiscal_year}: bad JSON: {exc}")
            errors += 1
            continue
        finally:
            json_path.unlink(missing_ok=True)

        pairs = payload.get("rows", [])
        geo_pairs = [
            p
            for p in pairs
            if _is_geo_axis(p.get("axis_qname", ""))
            and p.get("member_qname")
            and not _is_standard_ns(p["member_qname"])
        ]

        new_this_filing = 0
        for p in geo_pairs:
            mqname = p["member_qname"]
            if mqname not in seen:
                label = p.get("member_label") or ""
                iso_code, geo_type = _classify_label(label)
                # Strip [Member] / [member] suffixes for a cleaner display name
                import re as _re

                clean_label = _re.sub(
                    r"\s*\[member\]", "", label, flags=_re.IGNORECASE
                ).strip()
                geo_name = f"{clean_label} ({ticker})"
                seen[mqname] = {
                    "member_qname": mqname,
                    "iso_code": iso_code,
                    "geo_name": geo_name,
                    "geo_type": geo_type,
                }
                new_this_filing += 1

        status = f"+{new_this_filing} new" if new_this_filing else "no new"
        print(
            f"  OK     {ticker:<6s} FY{fiscal_year} ({form:<8s}) "
            f"geo_members={len(geo_pairs)}  {status}"
        )
        ok += 1

    print(f"\n  Filings processed={ok}  skipped={skipped}  errors={errors}")
    print(f"  Total unique extension geo members: {len(seen)}")
    return list(seen.values())


def _query_extension_members(db_params: dict) -> list[dict]:
    """Query dimension_catalog for extension geographic members and classify them.

    Returns rows ready for geography_normalization with verified full-URI member_qname.

    Extension members = member_qname whose namespace is NOT one of the standard
    taxonomy namespaces (us-gaap, srt, dei, country, etc.).
    """
    import psycopg2

    _STANDARD_NS_BASES = (
        "http://fasb.org/us-gaap/",
        "http://fasb.org/srt/",
        "http://xbrl.sec.gov/",
        "http://www.xbrl.org/",
        "http://xbrl.ifrs.org/",
        "https://xbrl.ifrs.org/",
    )

    # Geo-related axis QName patterns
    _GEO_AXIS_PATTERNS = (
        "StatementGeographicalAxis",
        "GeographicAreasAxis",
        "GeographicalAreasAxis",
    )

    conn = psycopg2.connect(**db_params)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT dc.member_qname, dc.member_label, dc.cik, em.trading_symbol
              FROM dimension_catalog dc
              LEFT JOIN entity_master em ON em.cik = dc.cik
             WHERE (
                   dc.axis_qname LIKE %s
                OR dc.axis_qname LIKE %s
                OR dc.axis_qname LIKE %s
             )
               AND dc.member_qname IS NOT NULL
             ORDER BY dc.member_qname
        """,
            tuple(f"%{p}%" for p in _GEO_AXIS_PATTERNS),
        )
        all_members = cur.fetchall()
    finally:
        conn.close()

    rows = []
    standard_count = 0
    for member_qname, member_label, cik, ticker in all_members:
        # Skip standard taxonomy members — they're covered by Tier 1/2
        is_standard = any(member_qname.startswith(base) for base in _STANDARD_NS_BASES)
        if is_standard:
            standard_count += 1
            continue

        import re as _re

        label = member_label or ""
        iso_code, geo_type = _classify_label(label)
        clean_label = _re.sub(r"\s*\[member\]", "", label, flags=_re.IGNORECASE).strip()
        geo_name = f"{clean_label} ({ticker or cik})"

        rows.append(
            {
                "member_qname": member_qname,
                "iso_code": iso_code,
                "geo_name": geo_name,
                "geo_type": geo_type,
            }
        )

    print(
        f"  dimension_catalog: {len(all_members)} geo members total "
        f"({standard_count} standard skipped, {len(rows)} extension rows collected)."
    )
    return rows


def build_tier1(years: list[int]) -> list[dict]:
    """Tier 1: SEC country taxonomy members for each taxonomy year."""
    rows = []
    for iso, geo_name in _COUNTRY_ROWS:
        for year in years:
            rows.append(
                {
                    "member_qname": f"http://xbrl.sec.gov/country/{year}:{iso}",
                    "iso_code": iso,
                    "geo_name": geo_name,
                    "geo_type": "country",
                }
            )
    return rows


# US-GAAP geographic members that mirror SRT concepts but live in the us-gaap namespace.
# Some filers reference these instead of or in addition to the srt: equivalents.
_USGAAP_GEO_ROWS: list[tuple[str, str, str, str]] = [
    ("NonUsMember", "NON-US", "Outside United States", "region"),
    ("OutsideTheUnitedStatesMember", "NON-US", "Outside United States", "region"),
    ("DomesticCountryMember", "US", "United States (domestic)", "country"),
    ("ForeignCountryMember", "INTL", "Foreign Countries", "region"),
]


def build_tier2(years: list[int]) -> list[dict]:
    """Tier 2: FASB SRT geographic aggregates + us-gaap geographic members."""
    rows = []
    for local, iso_code, geo_name, geo_type in _SRT_ROWS:
        for year in years:
            rows.append(
                {
                    "member_qname": f"http://fasb.org/srt/{year}:{local}",
                    "iso_code": iso_code,
                    "geo_name": geo_name,
                    "geo_type": geo_type,
                }
            )
    # us-gaap geographic members (some filers use these instead of srt: equivalents)
    for local, iso_code, geo_name, geo_type in _USGAAP_GEO_ROWS:
        for year in years:
            rows.append(
                {
                    "member_qname": f"http://fasb.org/us-gaap/{year}:{local}",
                    "iso_code": iso_code,
                    "geo_name": geo_name,
                    "geo_type": geo_type,
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="data/storage/datasets/geography_normalization_seed.csv",
        help="Output CSV path.",
    )
    parser.add_argument(
        "--from-filings",
        default=None,
        metavar="SEC_EDGAR_ROOT",
        help=(
            "Walk the local SEC EDGAR storage tree, run Arelle on each annual "
            "filing, and extract actual geographic extension member QNames. "
            "This is the preferred approach — it verifies ALL downloaded filings "
            "without waiting for Arelle xbrlDB ingestion. "
            "Requires ARELLE_CMD env var (or --arelle-cmd). "
            "Example: --from-filings <repo>/data/storage/sec_edgar"
        ),
    )
    parser.add_argument(
        "--arelle-cmd",
        default=None,
        metavar="PATH",
        help="Path to arelleCmdLine.exe (overrides ARELLE_CMD env var).",
    )
    parser.add_argument(
        "--from-db",
        action="store_true",
        help=(
            "Query dimension_catalog for verified extension geographic members. "
            "Reads XBRL_DB_* env vars for connection."
        ),
    )
    parser.add_argument(
        "--include-unverified",
        action="store_true",
        help=(
            "Include static Tier-3 placeholder entries for companies not yet "
            "ingested. member_qname starts with 'PENDING:' — do NOT import "
            "these into the DB until verified."
        ),
    )
    parser.add_argument(
        "--years",
        nargs="+",
        type=int,
        default=TAXONOMY_YEARS,
        metavar="YEAR",
        help=f"Taxonomy years to generate standard entries for (default: {TAXONOMY_YEARS}).",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Generating geography_normalization seed (years={args.years}) ...")

    tier1 = build_tier1(args.years)
    tier2 = build_tier2(args.years)
    print(f"  Tier 1 (country:{{}}, {len(args.years)} years):  {len(tier1)} rows")
    print(f"  Tier 2 (srt:*,    {len(args.years)} years):  {len(tier2)} rows")

    tier3_verified: list[dict] = []

    if args.from_filings:
        from dotenv import load_dotenv

        load_dotenv()
        # PATH-aware: ARELLE_CMD is an absolute path on the Windows dev box but a
        # bare console-script name in the ingest container. Resolve, don't stat.
        #
        # This used to print an error and CONTINUE, emitting a CSV silently missing
        # every tier-3 row. --from-filings was asked for explicitly, so a missing
        # Arelle is a hard failure, not a degraded run.
        try:
            arelle_cmd = resolve_arelle_cmd(arelle_cmd_from_env(args.arelle_cmd))
        except FileNotFoundError as exc:
            raise SystemExit(f"  ERROR: {exc}\n  Set ARELLE_CMD or pass --arelle-cmd.")
        print(f"  Tier 3 (extension, from filings at {args.from_filings}):")
        print(f"  Using Arelle: {arelle_cmd}")
        tier3_verified = _collect_geo_members_from_filings(
            sec_edgar_root=args.from_filings,
            arelle_cmd=arelle_cmd,
        )
        print(f"  -> {len(tier3_verified)} verified extension rows from filings")

    if args.from_db:
        from dotenv import load_dotenv

        load_dotenv()
        db_params = {
            "host": os.environ.get("XBRL_DB_HOST", "localhost"),
            "port": int(os.environ.get("XBRL_DB_PORT", "5432")),
            "dbname": os.environ.get("XBRL_DB_NAME", "xbrl_db"),
            "user": os.environ.get("XBRL_DB_USER", "postgres"),
            "password": os.environ.get("XBRL_DB_PASSWORD", "postgres"),
        }
        print("  Tier 3 (extension, from DB):")
        tier3_verified = _query_extension_members(db_params)
        print(f"    -> {len(tier3_verified)} verified extension rows")

    tier3_unverified: list[dict] = []
    if args.include_unverified:
        tier3_unverified = _TIER3_UNVERIFIED
        print(f"  Tier 3 (unverified placeholders): {len(tier3_unverified)} rows")
        print("  WARNING: PENDING: entries must not be imported until verified!")

    all_rows = tier1 + tier2 + tier3_verified + tier3_unverified

    # Deduplicate by member_qname (first occurrence wins)
    seen: set[str] = set()
    deduped: list[dict] = []
    for row in all_rows:
        if row["member_qname"] not in seen:
            seen.add(row["member_qname"])
            deduped.append(row)

    fieldnames = ["member_qname", "iso_code", "geo_name", "geo_type"]
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(deduped)

    print(f"\nWrote {len(deduped)} rows to {output_path}")
    print()
    print("Import into PostgreSQL (exclude PENDING: rows if any):")
    print(f"  COPY geography_normalization FROM '{output_path.resolve()}'")
    print(f"    CSV HEADER NULL '';")
    if args.include_unverified:
        print()
        print("Or import only verified rows:")
        print(f"  \\COPY geography_normalization FROM '{output_path.resolve()}'")
        print(f"    CSV HEADER NULL '' WHERE member_qname NOT LIKE 'PENDING:%';")


if __name__ == "__main__":
    main()
