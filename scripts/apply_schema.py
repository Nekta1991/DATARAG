"""Apply db/schema.sql to the Neon branch.

Idempotent — every statement in schema.sql uses IF NOT EXISTS / OR REPLACE,
so re-running is safe. Uses the direct (unpooled) connection because pooled
connections can refuse some DDL.

    python scripts/apply_schema.py
"""

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")

dsn = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
if not dsn:
    sys.exit("DATABASE_URL_UNPOOLED not set — check .env")

schema = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")

with psycopg.connect(dsn) as conn:
    with conn.cursor() as cur:
        cur.execute(schema)
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_type, table_name
        """)
        for name, kind in cur.fetchall():
            print(f"  {kind.lower():10} {name}")

        cur.execute("SELECT extname, extversion FROM pg_extension ORDER BY extname")
        print("  extensions:", ", ".join(f"{n} {v}" for n, v in cur.fetchall()))

print("schema applied")
