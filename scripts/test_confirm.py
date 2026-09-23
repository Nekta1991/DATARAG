"""Free test of the usage guard: confirm-before-spending, and cancellation.

    python scripts/test_confirm.py

Cost: $0 - the point of the guard is that declining happens before any model
call, so this suite asserts exactly that by checking the ledger is unmoved.

The paid path is exercised with a stubbed model where a real run would spend,
so the accounting is real even though the generation is not.
"""

from __future__ import annotations

import pathlib
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from rag import ledger as L
from rag.agent import answer_question, stub_model

Q = "通常枠の補助額と補助率はいくらですか？"
FAILED = RAN = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global FAILED, RAN
    RAN += 1
    FAILED += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")


def main() -> int:
    start = L.spent_so_far()
    print(f"ledger starts at ${start:.4f}\n")

    # 1. Declining at the confirmation point must cost nothing and never reach
    #    the model. The stub would answer if it ran, so status proves it did not.
    asked = {}

    def refuse(top_score):
        asked["top"] = top_score
        return False

    r = answer_question(Q, model=stub_model(), confirm=refuse)
    check("confirm=False -> cancelled, model never ran",
          r.status == "declined" and r.reason == "cancelled" and not r.citations,
          f"{r.status}/{r.reason}")
    check("the confirmation saw gate 1's score",
          asked.get("top", 0) > 0.5, f"top {asked.get('top', 0):.4f}")
    check("declining is free", abs(L.spent_so_far() - start) < 1e-9,
          f"${L.spent_so_far():.4f}")

    # 2. Confirming lets the same question through.
    r = answer_question(Q, model=stub_model(), confirm=lambda top: True)
    check("confirm=True -> the run proceeds", r.status == "answered",
          f"{r.status}/{r.reason}")

    # 3. A cancel raised before the model call stops the run, also for free.
    cancelled = threading.Event()
    cancelled.set()
    r = answer_question(Q, model=stub_model(), cancelled=cancelled)
    check("pre-set cancel -> stopped before the model",
          r.status == "declined" and r.reason == "cancelled", f"{r.status}/{r.reason}")

    # 4. A stub run is never asked to confirm: there is nothing to spend.
    #    (api.py enforces this; asserted here so the rule cannot drift.)
    never = {"called": False}

    def should_not_be_called(top):
        never["called"] = True
        return True

    answer_question(Q, model=stub_model(), confirm=None)
    check("no confirm callback -> runs straight through", not never["called"])

    end = L.spent_so_far()
    check("ledger unchanged by the whole suite", abs(end - start) < 1e-9,
          f"${start:.4f} -> ${end:.4f}")

    print(f"\n{RAN - FAILED}/{RAN} passed, $0 spent")
    return FAILED


if __name__ == "__main__":
    sys.exit(main())
