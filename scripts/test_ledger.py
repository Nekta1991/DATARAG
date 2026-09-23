"""Free test of the Postgres budget guard: reserve / settle / release / race.

    python scripts/test_ledger.py

Cost: $0 — Neon only, no model call. Every row this writes is removed again,
so the real ledger total is unchanged; the test asserts that at the end.

The race test is the point. The old file-based guard read a total and then
decided, which two concurrent runs can both pass. Here two real connections
try to reserve at once against a ceiling with room for only one, and exactly
one must win.
"""

from __future__ import annotations

import os
import pathlib
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import psycopg

from rag import ledger as L

FAILED = 0
RAN = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global FAILED, RAN
    RAN += 1
    FAILED += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")


def cleanup(ids: list[int]) -> None:
    if not ids:
        return
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.transaction():
        conn.execute("DELETE FROM spend_ledger WHERE id = ANY(%s)", (ids,))


def main() -> int:
    start = L.spent_so_far()
    made: list[int] = []
    print(f"ledger starts at ${start:.4f} of ${L.BUDGET_USD:.2f}\n")

    # 1. a reservation counts against the budget immediately
    r = L.reserve("test-model", "reservation counts?", 0.19, source="cli")
    made.append(r.id)
    after = L.spent_so_far()
    check("reservation raises the total", abs(after - (start + 0.19)) < 1e-6,
          f"${start:.4f} -> ${after:.4f}")

    # 2. settling replaces the held worst case with the real cost
    L.settle(r.id, status="answered", reason="answered",
             usage={"requests": 2}, cost_usd=0.0123, trace=["search", "final_result"])
    after = L.spent_so_far()
    check("settle replaces the worst case", abs(after - (start + 0.0123)) < 1e-6,
          f"${after:.4f}")

    # 3. settling twice must not double-count
    L.settle(r.id, status="answered", reason="answered", usage={}, cost_usd=0.0123, trace=[])
    check("settle is not re-appliable", abs(L.spent_so_far() - (start + 0.0123)) < 1e-6)

    # 3b. settle stores what the run produced, not just what it cost
    r_out = L.reserve("test-model", "stores its output?", 0.19, source="cli")
    made.append(r_out.id)
    L.settle(r_out.id, status="declined", reason="unverified_quote", usage={"requests": 2},
             cost_usd=0.01, trace=["search", "final_result"],
             answer="", citations=[{"source_doc": "通常枠 交付規程", "quote": "| 補助額 |"}],
             rejected_quotes=["補助額 ５万円 補助額 １５０万円"],
             draft={"answerable": True, "answer": "draft text"})
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        cites, rej, draft = conn.execute(
            "SELECT citations, rejected_quotes, draft FROM spend_ledger WHERE id = %s",
            (r_out.id,)).fetchone()
    check("settle stores citations, rejects and the draft",
          len(cites) == 1 and cites[0]["source_doc"] == "通常枠 交付規程"
          and rej == ["補助額 ５万円 補助額 １５０万円"]
          and (draft or {}).get("answer") == "draft text",
          "a paid run is auditable after the tab is closed")

    # 4. a released reservation leaves no trace in the total. Measured against
    # the total immediately before, not a running sum: an earlier case that
    # settles a different amount should not be able to break this one.
    before_release = L.spent_so_far()
    r2 = L.reserve("test-model", "released?", 0.19, source="cli")
    made.append(r2.id)
    L.release(r2.id, "test")
    check("release removes it from the total",
          abs(L.spent_so_far() - before_release) < 1e-6,
          f"${before_release:.4f} unchanged")

    # 5. the ceiling actually refuses
    room = L.BUDGET_USD - L.spent_so_far()
    try:
        r3 = L.reserve("test-model", "over ceiling", room + 0.01, source="cli")
        made.append(r3.id)
        check("over-budget reservation refused", False, "it was allowed")
    except L.BudgetExceeded as e:
        check("over-budget reservation refused", True, f"${e.worst:.2f} > ${room:.4f} left")

    # 6. the race: two threads, room for exactly one
    room = L.BUDGET_USD - L.spent_so_far()
    each = room * 0.6          # two of these cannot both fit
    results: list = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            res = L.reserve("test-model", "race", each, source="cli")
            with lock:
                results.append(res.id)
                made.append(res.id)
        except L.BudgetExceeded:
            with lock:
                results.append(None)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    won = [x for x in results if x is not None]
    check("concurrent reservations: exactly one wins", len(won) == 1,
          f"{len(won)} of 2 got through (${each:.4f} each, ${room:.4f} left)")

    cleanup(made)
    end = L.spent_so_far()
    check("test rows removed, total restored", abs(end - start) < 1e-6,
          f"${end:.4f}")

    print(f"\n{RAN - FAILED}/{RAN} passed, $0 spent")
    return FAILED


if __name__ == "__main__":
    sys.exit(main())
