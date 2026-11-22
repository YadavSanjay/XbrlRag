# `scripts/ddl/base/` — Arelle public-schema DDL (vendored)

The **base layer** of `xbrl_db`: the XBRL-US "public schema" that the Arelle
`xbrlDB` plugin loads facts into. Everything else in `scripts/ddl/` builds on top
of it (`layer_0/` → `mv/`, plus the separate `audit/` schema).

Normally the plugin creates this schema *itself* on the first `--arelle-ingest`
run ([guide 4](../../../docs/guides/xbrl-flow/04-ingest-and-populate.md)). This
copy is committed so the base schema can be recreated **standalone** on a fresh
Postgres instance — a second dev box, a container, RDS — without installing
Arelle first.

## Provenance

| | |
|---|---|
| File | `xbrlPublicPostgresDB.ddl` |
| Vendored from | `<ARELLE_HOME>/plugin/xbrlDB/sql/public/xbrlPublicPostgresDB.ddl` |
| Copied | 2026-07-26, **verbatim** (md5 `ad4cb6bee6ad09b5aa6f90d8b0bcdbe1`, 984,473 bytes, UTF-8 **with BOM**, **CRLF**) |
| Upstream | [Arelle](https://github.com/Arelle/Arelle) `xbrlDB` plugin (Apache-2.0), derived from the XBRL-US public database |
| License | Copyright © 2011-present Workiva, Inc. Licensed under the Apache License, Version 2.0 — full text in [`LICENSE-Apache-2.0.txt`](LICENSE-Apache-2.0.txt). This file alone is under Apache-2.0; the rest of the repo is under the root [`LICENSE`](../../../LICENSE) (MIT). |

Do **not** reformat, re-encode, or strip the BOM/CRLF — the checksum is what lets
us assert this file is the one that built the live schema. Local deviations
belong in a separate migration file next to it, not in edits to this one.

Verified 2026-07-26 against the live `xbrl_db`: all **44** tables present with
**identical column sets**, all **11** views present, and the three seed `INSERT`s
match row-for-row (`industry` 4,333 / `industry_level` 9,326 /
`industry_structure` 13). This DDL is confirmed as the one that created the
public schema.

## What it creates

2 composite types, 40 sequences, **44 tables**, **11 views**, ~82 indexes,
and ~130 functions — plus the industry/SIC seed rows (which is why the file is
~1 MB).

Tables are the Arelle/XBRL-US core: `accession`, `document`, `entity`, `fact`,
`fact_aug`, `context`, `context_dimension`, `element`, `qname`, `namespace`,
`network`, `relationship`, `resource`, `label_resource`, `unit`, `unit_measure`,
`taxonomy`, `industry*`, `sic_code`, … Views: `fact_element_view`,
`relationship_view`, `presentation_view`, `calculation_view`, … (`mv_*` are XbrlRag's
own — see [`../mv/`](../mv/)).

## ⚠️ It starts by dropping the public schema

Line 22 is:

```sql
DROP SCHEMA public CASCADE; create SCHEMA public;
```

Upstream does this deliberately ("clear everything prior"), and it is **fine on a
fresh database** — which is the only way we use it. But on an existing `xbrl_db`
it destroys **every** XbrlRag object too: all `layer_0` tables, all `mv_*`
materialized views, and all loaded facts. (The `audit` schema is a separate
schema and would survive, but its views over public objects would not.)

**Only ever run this against a database you intend to be empty.** If you need to
re-apply it to a populated instance, drop and recreate the *database*, or comment
out line 22 and hand-drop what you actually mean to replace.

## Requirements on the target instance

- **Role named `postgres`** — the dump carries 227 `ALTER … OWNER TO postgres`
  statements and will error if that role does not exist.
- **`plpgsql`** (default in every Postgres) — 92 functions use it.
- **The `pgcrypto` shared library.** The dump predates extension-aware
  `pg_dump`, so it inlines 33 pgcrypto functions as bare
  `CREATE FUNCTION … LANGUAGE c AS '$libdir/pgcrypto', …` rather than
  `CREATE EXTENSION pgcrypto`. Consequences:
  - Vanilla Postgres (incl. the official Docker images) ships the library — works.
  - **Managed Postgres such as RDS/Aurora generally refuses `LANGUAGE c`
    `CREATE FUNCTION` for non-superusers**, so this section can fail there.
    Nothing in XbrlRag calls `armor`/`crypt`/`digest`/`hmac`/`pgp_*` — if the C
    functions fail on your target, they are safe to skip.
- **`pg_trgm`** is *not* created by this file but is required by XbrlRag's Layer-0
  (`entity_master` has a GIN trigram index):
  `CREATE EXTENSION IF NOT EXISTS pg_trgm;`

Built and verified against **PostgreSQL 18.4**.

## Recreating `xbrl_db` on a new instance

```bash
export PGPASSWORD=postgres

# 1. fresh database
createdb -h "$PGHOST" -U postgres xbrl_db
psql -h "$PGHOST" -U postgres -d xbrl_db -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"

# 2. base — Arelle public schema (DROPS public; fresh DB only)
psql -h "$PGHOST" -U postgres -d xbrl_db -v ON_ERROR_STOP=1 \
     -f scripts/ddl/base/xbrlPublicPostgresDB.ddl

# 3. XbrlRag Layer-0
for f in scripts/ddl/layer_0/*.sql; do
  psql -h "$PGHOST" -U postgres -d xbrl_db -v ON_ERROR_STOP=1 -f "$f"
done

# 4. XbrlRag audit schema
python scripts/ddl/audit/_apply.py
```

PowerShell equivalents for steps 3–4 are in
[guide 2 §2.3–2.4](../../../docs/guides/xbrl-flow/02-postgres-setup.md).

Order matters: **base → layer_0 → audit**, because step 2 drops the public schema.
The materialized views (`scripts/ddl/mv/`, applied by `scripts/rebuild_mvs.py`)
come **last**, after facts and Layer-0 rows are actually populated — they select
from both.

> On `ON_ERROR_STOP=1`: use it, but expect the pgcrypto block to be the one thing
> that legitimately fails on managed Postgres (see above). If it does, rerun
> without the flag and check that only `armor`/`crypt`/`digest`/`gen_salt`/`hmac`/
> `pgp_*` errored.

## Verify the result

```sql
-- 44 base tables + 11 views, before any XbrlRag DDL is applied
SELECT table_type, count(*) FROM information_schema.tables
WHERE table_schema = 'public' GROUP BY table_type;

-- seed data landed
SELECT (SELECT count(*) FROM industry)           AS industry,            -- 4333
       (SELECT count(*) FROM industry_level)     AS industry_level,      -- 9326
       (SELECT count(*) FROM industry_structure) AS industry_structure;  -- 13
```

## Known gaps in the base schema (not bugs in the copy)

- **`entity` and `sic_code` stay empty**, and `accession.entity_id` is `0` for
  every row — Arelle only populates `entity` on the RSS/semantic path, and the
  upstream header records that `fk_accession_entity` was deliberately commented
  out for exactly this reason. XbrlRag does not read them; per-filer metadata lives
  in [`../layer_0/entity_master.sql`](../layer_0/entity_master.sql) instead.
- Several other upstream constraints and triggers are commented out by Arelle to
  allow bulk loading — see the change notes at the top of the `.ddl`.
