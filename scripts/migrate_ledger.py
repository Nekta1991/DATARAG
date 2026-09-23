"""Apply db/ledger.sql and import data/spend_ledger.jsonl into it.

    python scripts/migrate_ledger.py --dry-run    # show what would be imported
    python scripts/migrate_ledger.py

Cost: $0 — schema DDL and inserts on the Neon free tier.

Idempotent: rows are keyed on (ts, question, cost_usd), so re-running imports
nothing twice. The JSONL file is left in place, not deleted - it is the only
record of the 2026-09-21/22 runs and the import should be verifiable against it
afterwards.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import os

import psycopg

from rag import config  # noqa: F401  - loads .env

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "db" / "ledger.sql"
JSONL = ROOT / "data" / "spend_ledger.jsonl"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = [json.loads(l) for l in JSONL.read_text(encoding="utf-8").splitlines() if l.strip()]
    total = sum(r["cost_usd"] for r in rows)
    print(f"{JSONL.name}: {len(rows)} rows, ${total:.4f}")

    if args.dry_run:
        for r in rows:
            print(f"  {r['ts']}  {r['status']:<9} ${r['cost_usd']:.4f}  {r['question'][:45]}")
        return 0

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(SCHEMA.read_text(encoding="utf-8"))
        conn.commit()
        print("schema applied")

        imported = 0
        for r in rows:
            got = conn.execute(
                """INSERT INTO spend_ledger
                       (ts, model, question, state, status, reason, usage, trace,
                        cost_usd, source)
                   SELECT %(ts)s, %(model)s, %(q)s, 'final', %(status)s, %(reason)s,
                          %(usage)s, %(trace)s, %(cost)s, 'import'
                    WHERE NOT EXISTS (
                        SELECT 1 FROM spend_ledger
                         WHERE ts = %(ts)s AND question = %(q)s AND cost_usd = %(cost)s)
                RETURNING id""",
                {"ts": r["ts"], "model": r["model"], "q": r["question"],
                 "status": r["status"], "reason": r.get("reason"),
                 "usage": json.dumps(r.get("usage") or {}, ensure_ascii=False),
                 "trace": json.dumps(r.get("trace") or [], ensure_ascii=False),
                 "cost": r["cost_usd"]}).fetchone()
            imported += got is not None
        conn.commit()

        spent, settled, open_ = conn.execute(
            "SELECT spent_usd, settled_runs, open_reservations FROM spend_total").fetchone()
        # Compare only the rows that came from the file. The table also holds
        # runs made since, so checking the grand total against the file would
        # report a mismatch every time a query is run - a warning that cries
        # wolf is worse than none.
        imported_total = float(conn.execute(
            "SELECT COALESCE(sum(cost_usd), 0) FROM spend_ledger WHERE source = 'import'"
        ).fetchone()[0])

    print(f"imported {imported} new row(s), skipped {len(rows) - imported} already present")
    print(f"spend_total: ${float(spent):.4f}  ({settled} settled, {open_} reserved)")
    if abs(imported_total - total) > 1e-6:
        print(f"⚠ imported rows total ${imported_total:.4f} != file total ${total:.4f}")
        return 1
    print(f"✓ imported rows match the file (${imported_total:.4f}); "
          f"${float(spent) - imported_total:.4f} more was spent since")
    return 0


if __name__ == "__main__":
    sys.exit(main())
