"""One-off helper: apply the scripts/ddl/eval/*.sql files to xbrl_db."""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv()
params = dict(
    host=os.environ.get("XBRL_DB_HOST", "localhost"),
    port=int(os.environ.get("XBRL_DB_PORT", "5432")),
    dbname=os.environ.get("XBRL_DB_NAME", "xbrl_db"),
    user=os.environ.get("XBRL_DB_USER", "postgres"),
    password=os.environ.get("XBRL_DB_PASSWORD", "postgres"),
)

here = Path(__file__).parent
# Single file today: tables and the views on top of them are defined together, and
# the views are CREATE OR REPLACE so re-applying is idempotent.
_DEFAULT_ORDER = ["eval_runs.sql"]
ddl_files = sys.argv[1:] or [str(here / name) for name in _DEFAULT_ORDER]

conn = psycopg2.connect(**params)
conn.autocommit = True
with conn.cursor() as cur:
    for f in ddl_files:
        cur.execute(Path(f).read_text(encoding="utf-8"))
        print("applied:", Path(f).name)
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'eval' ORDER BY table_name"
    )
    print("eval tables:", [r[0] for r in cur.fetchall()])
    cur.execute(
        "SELECT table_name FROM information_schema.views "
        "WHERE table_schema = 'eval' ORDER BY table_name"
    )
    print("eval views :", [r[0] for r in cur.fetchall()])
conn.close()
