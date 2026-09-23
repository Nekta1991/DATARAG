"""The $5 budget ceiling, enforced in Postgres instead of a local file.

    python -m rag.ledger              # what has been spent
    python -m rag.ledger --rows       # every run, newest first
    python -m rag.ledger --release-stale 30

Cost: $0. Neon is free tier and nothing here calls a model.

Why not the JSONL file it replaces: the file is per-machine and per-instance.
Moving the API to Vercel would have started every cold function with an empty
ledger, so `spent_so_far() + worst_case > BUDGET` would have been false every
time and the ceiling would have quietly stopped existing. Nothing would have
errored; the budget would just not have been a budget any more.

The reservation, not just the total, is the point. `read the total -> decide ->
call Claude` is a check-then-act race: two runs can both read $0.23, both find
room, and both spend. Locally that was closed by a threading.Lock, which does
not span processes, let alone serverless instances. Here the decision and the
write are one transaction, serialized by a Postgres advisory lock, so the
ceiling holds no matter how many instances run.

A reservation holds the worst case ($0.19) until the run settles at its real
cost. If a process dies mid-run the reservation stands and over-counts - the
same safe direction as recording unknown usage at worst case (2026-09-21).
`--release-stale` is the deliberate, manual way to reclaim those.
"""

from __future__ import annotations

import argparse
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass


from rag import config  # noqa: F401  - loads .env
from rag import db

BUDGET_USD = float(os.getenv("BUDGET_USD", "5.00"))

# One 64-bit key, chosen once, namespacing every budget decision in this
# project. Any connection taking it is asking the same question: "is there
# room to spend?" - so they queue instead of racing.
_LOCK_KEY = 0x5241475F42554447  # "RAG_BUDG"


class BudgetExceeded(RuntimeError):
    """Raised instead of spending. Carries the numbers for the message."""

    def __init__(self, spent: float, worst: float, budget: float):
        self.spent, self.worst, self.budget = spent, worst, budget
        super().__init__(
            f"budget guard: ${spent:.4f} spent + worst case ${worst:.2f} "
            f"would exceed ${budget:.2f}")


@dataclass
class Reservation:
    id: int
    spent_before: float


def _connect():
    """A pooled connection (rag/db.py). Every write here already wraps itself
    in `with conn.transaction():`, which is what makes the swap safe: a pooled
    connection is returned to the pool rather than closed, so it does NOT
    commit on exit the way `psycopg.connect()` did. The explicit transactions
    were already load-bearing for the advisory lock, and they carry the commit
    too."""
    return db.connection()


@contextmanager
def _guarded(conn):
    """Serialize budget decisions across every process and instance.
    pg_advisory_xact_lock releases with the transaction, including on crash -
    a lock table row would not."""
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
    yield


def spent_so_far() -> float:
    with _connect() as conn:
        return float(conn.execute("SELECT spent_usd FROM spend_total").fetchone()[0])


def totals() -> dict:
    with _connect() as conn:
        spent, settled, open_ = conn.execute(
            "SELECT spent_usd, settled_runs, open_reservations FROM spend_total").fetchone()
    return {"spent_usd": float(spent), "budget_usd": BUDGET_USD,
            "settled_runs": settled, "open_reservations": open_}


def reserve(model: str, question: str, worst_case: float,
            source: str = "api") -> Reservation:
    """Hold `worst_case` against the budget, or raise BudgetExceeded.

    The check and the insert share one transaction under the advisory lock, so
    a concurrent run cannot squeeze past between them.
    """
    with _connect() as conn, conn.transaction():
        with _guarded(conn):
            spent = float(conn.execute("SELECT spent_usd FROM spend_total").fetchone()[0])
            if spent + worst_case > BUDGET_USD:
                raise BudgetExceeded(spent, worst_case, BUDGET_USD)
            rid = conn.execute(
                """INSERT INTO spend_ledger (model, question, state, status, cost_usd, source)
                   VALUES (%s, %s, 'reserved', 'running', %s, %s) RETURNING id""",
                (model, question, worst_case, source)).fetchone()[0]
    return Reservation(id=rid, spent_before=spent)


def settle(reservation_id: int, *, status: str, reason: str, usage: dict,
           cost_usd: float, trace: list[str], answer: str = "",
           citations: list | None = None, rejected_quotes: list | None = None,
           draft: dict | None = None) -> None:
    """Replace the held worst case with what the run actually cost, and record
    what it produced.

    The output is stored, not just the price. A paid run whose answer lives
    only in a browser tab cannot be checked afterwards, which made "was that
    answer right?" unanswerable for anything run from the dashboard.
    """
    with _connect() as conn, conn.transaction():
        conn.execute(
            """UPDATE spend_ledger
                  SET state = 'final', status = %s, reason = %s, usage = %s,
                      trace = %s, cost_usd = %s, ts = now(), answer = %s,
                      citations = %s, rejected_quotes = %s, draft = %s
                WHERE id = %s AND state = 'reserved'""",
            (status, reason, json.dumps(usage, ensure_ascii=False),
             json.dumps(trace, ensure_ascii=False), cost_usd, answer,
             json.dumps(citations or [], ensure_ascii=False),
             json.dumps(rejected_quotes or [], ensure_ascii=False),
             json.dumps(draft, ensure_ascii=False) if draft is not None else None,
             reservation_id))


def release(reservation_id: int, note: str = "") -> None:
    """The call never reached Claude, so it should not count. Use only where
    that is certain - if in doubt, settle at the worst case instead."""
    with _connect() as conn, conn.transaction():
        conn.execute(
            """UPDATE spend_ledger
                  SET state = 'released', status = 'released', reason = %s
                WHERE id = %s AND state = 'reserved'""",
            (note or "released", reservation_id))


def record(*, model: str, question: str, status: str, reason: str, usage: dict,
           cost_usd: float, trace: list[str], source: str = "cli") -> None:
    """Write a settled run directly, for paths that did not reserve first."""
    with _connect() as conn, conn.transaction():
        conn.execute(
            """INSERT INTO spend_ledger
                   (model, question, state, status, reason, usage, trace, cost_usd, source)
               VALUES (%s, %s, 'final', %s, %s, %s, %s, %s, %s)""",
            (model, question, status, reason, json.dumps(usage, ensure_ascii=False),
             json.dumps(trace, ensure_ascii=False), cost_usd, source))


def release_stale(minutes: int) -> int:
    """Reservations older than `minutes` whose process is certainly gone.
    Manual on purpose: an automatic sweeper would eventually release a
    reservation for a call that really was billed."""
    with _connect() as conn, conn.transaction():
        rows = conn.execute(
            """UPDATE spend_ledger
                  SET state = 'released', reason = 'stale reservation'
                WHERE state = 'reserved' AND ts < now() - make_interval(mins => %s)
            RETURNING id""", (minutes,)).fetchall()
    return len(rows)


# -- CLI --------------------------------------------------------------------

def _show(row_id: int | None) -> None:
    """One run in full: what was asked, what came back, and what backed it."""
    where = "WHERE id = %s" if row_id else "WHERE state <> 'released'"
    params = (row_id,) if row_id else ()
    with _connect() as conn:
        r = conn.execute(
            f"""SELECT id, ts, source, model, status, reason, cost_usd, question,
                       answer, citations, rejected_quotes, draft, trace, usage
                  FROM spend_ledger {where} ORDER BY ts DESC LIMIT 1""", params).fetchone()
    if r is None:
        print("no such run")
        return
    (rid, ts, source, model, status, reason, cost, q, answer,
     cites, rejected, draft, trace, usage) = r

    print(f"#{rid}  {ts:%Y-%m-%d %H:%M}  {source}  {model}")
    print(f"{status} / {reason}   ${float(cost):.4f}   {trace}")
    print(f"\nQ: {q}\n")
    if answer:
        print("ANSWER:")
        print(answer)
    elif status == "answered":
        print("(no answer stored - run predates answer persistence, 2026-09-23)")
    if cites:
        print(f"\nCITATIONS ({len(cites)}) - each verified verbatim by gate 2:")
        for c in cites:
            print(f"  [{c.get('source_doc')}]")
            print(f"    {c.get('quote', '')[:150]}")
    if rejected:
        print(f"\nREJECTED QUOTES ({len(rejected)}) - why gate 2 declined:")
        for qt in rejected:
            print(f"  {qt[:150]}")
    if draft:
        print("\nDRAFT the model proposed and was refused:")
        print(json.dumps(draft, ensure_ascii=False, indent=1)[:1200])
    if usage:
        print(f"\nusage: {usage.get('requests')} requests, "
              f"{usage.get('input_tokens')} in / {usage.get('output_tokens')} out")



def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", action="store_true", help="list every run")
    ap.add_argument("--show", type=int, metavar="ID", nargs="?", const=-1,
                    help="print one run in full (default: the newest)")
    ap.add_argument("--release-stale", type=int, metavar="MINUTES",
                    help="release reservations older than MINUTES")
    args = ap.parse_args()

    if args.show is not None:
        _show(None if args.show == -1 else args.show)
        return

    if args.release_stale is not None:
        print(f"released {release_stale(args.release_stale)} stale reservation(s)")

    t = totals()
    print(f"spent ${t['spent_usd']:.4f} of ${t['budget_usd']:.2f}  "
          f"({t['settled_runs']} settled, {t['open_reservations']} reserved)")

    if args.rows:
        with _connect() as conn:
            for ts, model, state, status, reason, cost, q in conn.execute(
                    """SELECT ts, model, state, status, reason, cost_usd, question
                         FROM spend_ledger ORDER BY ts DESC""").fetchall():
                print(f"{ts:%Y-%m-%d %H:%M}  {state:<8} {status or '-':<9} "
                      f"${float(cost):.4f}  {(reason or '')[:18]:<18} {q[:40]}")


if __name__ == "__main__":
    _main()
