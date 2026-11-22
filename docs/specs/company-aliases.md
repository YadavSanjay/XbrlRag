# company-aliases — resolve common company names and acronyms deterministically

Phase 1 of the accuracy plan.

## Requirement

A company mention that is a well-known short name or acronym ("TSMC", "Google",
"Facebook") must resolve to the right CIK by a deterministic, committed alias table — not
by the LLM, and not by `pg_trgm` similarity, which cannot bridge "TSMC" →
"Taiwan Semiconductor Manufacturing Company Limited". Today every TSMC question (14 cases
in `sql_derived_v1`) degrades to the narrative branch because the mention never resolves.

Aliases live in a committed, validated config (`configs/entities/company_aliases.yaml`),
loaded through a typed model; a malformed or conflicting file raises instead of silently
resolving a mention to the wrong filer.

## Acceptance criteria

- **C1**: `normalize_company_mention` case-folds, maps `’` to `'`, collapses whitespace
  runs, strips surrounding whitespace and punctuation, then removes a trailing possessive
  `'s` (so "  TSMC’s. " → "tsmc", "Taiwan   Semiconductor" → "taiwan semiconductor").
  `match_company_alias(mention, aliases)` returns the CIK whose alias equals the
  normalized mention: "TSMC", " tsmc ", "TSMC's" and "TSMC’s" all resolve to the same
  CIK. Oracle: the normalization rules above, applied by hand in the test.
- **C2**: `match_company_alias` returns `None` for a mention that is not an alias after
  normalization — no partial, prefix or fuzzy matching ("TSM Corp", "TSMCX", "" → `None`).
  Oracle: requirement (exact-match only; fuzzy matching stays in `pg_trgm`).
- **C3**: `load_company_aliases(path)` returns `{normalized alias: CIK}` for a valid file
  (format below), and raises `ValueError` when the file maps one normalized alias to two
  different CIKs, or when a CIK is not a 10-digit zero-padded string of digits. Oracle:
  requirement (fail loud on config conflicts), synthetic files written by the test.
- **C4**: The committed `configs/entities/company_aliases.yaml` maps "TSMC" and
  "Taiwan Semiconductor" to `0001046179`, "Google" to `0001652044`, and "Facebook" to
  `0001326801`. Oracle: SEC EDGAR company records (TSM CIK 1046179; Alphabet Inc. CIK
  1652044; Meta Platforms CIK 1326801).
- **C5**: Normalization is idempotent and case-insensitive: for any ASCII mention `m` and
  alias table `a`, `normalize(normalize(m)) == normalize(m)` and
  `match_company_alias(m, a) == match_company_alias(m.upper(), a) ==
  match_company_alias(normalize(m), a)`. Oracle: property of the normalization rules.
  (ASCII because Unicode case mapping is not round-trip safe — e.g. dotless `ı` upper-cases
  to `I`; the committed aliases are ASCII.)

### Alias file format

```yaml
companies:
  - cik: "0001046179"
    aliases: ["TSMC", "Taiwan Semiconductor", "Taiwan Semi"]
  - cik: "0001652044"
    aliases: ["Google"]
```

Keys are stored normalized; the same CIK may appear in more than one entry, and an alias
repeated for the *same* CIK is not a conflict.

## Interface

- `src.retriever.xbrl.entity_aliases.normalize_company_mention(mention: str) -> str`
- `src.retriever.xbrl.entity_aliases.match_company_alias(mention: str, aliases: dict[str, str]) -> str | None`
- `src.retriever.xbrl.entity_aliases.load_company_aliases(path: Path | None = None) -> dict[str, str]`
  (normalized alias → 10-digit CIK; `None` loads the committed default file)

## Notes

- Integration (implementer): `EntityExtractor._resolve_one_company` tries, in order, the
  exact `trading_symbol` match, then the alias table (fetching the `entity_master` row by
  CIK, `resolution="alias"`, `similarity=1.0`), then `pg_trgm`. A CIK in the alias file
  that is absent from `entity_master` resolves as unresolved, never as a guess. This path
  touches the DB and is verified by the live TSMC eval slice (`--ticker TSM`), not by spec
  tests.
- Verify the CIKs in C4 against `entity_master` before locking (they must match the
  ingested filers); EDGAR is the oracle, `entity_master` is the cross-check.
- The alias file is data, not code: adding a filer's aliases is a config change.
