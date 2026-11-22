"""Company alias table: resolve well-known short names and acronyms to a CIK.

``pg_trgm`` similarity cannot bridge an acronym to a legal name — "TSMC" is not close to
"Taiwan Semiconductor Manufacturing Company Limited" — so every TSMC question degraded
to the narrative branch. A committed, validated alias table fixes that deterministically:
the LLM never invents a CIK, and a conflicting or malformed file fails loud instead of
silently resolving a mention to the wrong filer.

Spec: [docs/specs/company-aliases.md](../../../docs/specs/company-aliases.md). The table
lives in ``configs/entities/company_aliases.yaml``; adding a filer's aliases is a config
change, not a code change.
"""

from __future__ import annotations

import string
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src import PROJECT_ROOT

DEFAULT_ALIAS_FILE = Path(PROJECT_ROOT) / "configs" / "entities" / "company_aliases.yaml"

# Surrounding characters that never belong to a company name.
_STRIP_CHARS = string.punctuation + " "


class _AliasEntry(BaseModel):
    """One filer and the names it is commonly called by."""

    model_config = ConfigDict(extra="forbid")

    cik: str = Field(pattern=r"^\d{10}$")  # zero-padded, as entity_master stores it
    aliases: list[str] = Field(min_length=1)


class _AliasFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companies: list[_AliasEntry]

    @field_validator("companies")
    @classmethod
    def _no_empty_aliases(cls, companies: list[_AliasEntry]) -> list[_AliasEntry]:
        for entry in companies:
            for alias in entry.aliases:
                if not normalize_company_mention(alias):
                    raise ValueError(f"empty alias for CIK {entry.cik}: {alias!r}")
        return companies


def normalize_company_mention(mention: str) -> str:
    """Normalize a company mention for exact alias lookup.

    Case-folds, maps a typographic apostrophe to ``'``, collapses whitespace runs,
    strips surrounding whitespace and punctuation, then drops a trailing possessive
    ``'s``.

    Args:
        mention: The company mention as the router extracted it.

    Returns:
        The normalized mention (possibly empty).
    """
    normalized = " ".join(mention.casefold().replace("’", "'").split())
    # Repeat until stable so the result is idempotent: dropping "'s" can expose more
    # trailing punctuation ("x.'s" -> "x." -> "x").
    while True:
        previous = normalized
        normalized = normalized.strip(_STRIP_CHARS)
        if normalized.endswith("'s"):
            normalized = normalized[:-2]
        if normalized == previous:
            return normalized


def match_company_alias(mention: str, aliases: dict[str, str]) -> str | None:
    """The CIK whose alias equals the normalized mention, exactly.

    Args:
        mention: The company mention as the router extracted it.
        aliases: Normalized alias to 10-digit CIK, as returned by
            :func:`load_company_aliases`.

    Returns:
        The CIK, or None when the normalized mention is not an alias.
    """
    normalized = normalize_company_mention(mention)
    if not normalized:
        return None
    return aliases.get(normalized)


def load_company_aliases(path: Path | None = None) -> dict[str, str]:
    """Load and validate the company alias table.

    Args:
        path: The alias YAML file; None loads the committed
            ``configs/entities/company_aliases.yaml``.

    Returns:
        Normalized alias to 10-digit zero-padded CIK.

    Raises:
        ValueError: An alias maps to two different CIKs, or a CIK is malformed.
    """
    source = Path(path) if path is not None else DEFAULT_ALIAS_FILE
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    # pydantic's ValidationError is a ValueError: a malformed CIK or an unknown key
    # raises here, before any alias is used.
    parsed = _AliasFile.model_validate(raw)

    table: dict[str, str] = {}
    for entry in parsed.companies:
        for alias in entry.aliases:
            key = normalize_company_mention(alias)
            existing = table.get(key)
            if existing is not None and existing != entry.cik:
                raise ValueError(
                    f"{source}: alias {alias!r} maps to both {existing} and {entry.cik}"
                )
            table[key] = entry.cik
    return table
