"""Free test of the HTTP layer: auth rejections and the SSE event stream.

    python scripts/test_api.py

Cost: $0. Uses stub=true (no Claude call). The admin check is overridden
only for the streaming test; the rejection tests hit the real require_admin.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from rag.api import app, require_admin


def events(text: str) -> list[tuple[str, dict]]:
    out, ev = [], None
    for line in text.splitlines():
        if line.startswith("event:"):
            ev = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and ev:
            out.append((ev, json.loads(line.split(":", 1)[1].strip())))
            ev = None
    return out


def main():
    fails = 0

    def check(name, ok, detail=""):
        nonlocal fails
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")

    with TestClient(app) as c:  # runs the lifespan: warms reranker + BM25
        h = c.get("/api/health").json()
        check("health, warmed at startup", h["warm"], f"warm in {h['warm_seconds']}s")

        r = c.post("/api/query", json={"question": "x"})
        check("no token -> 401", r.status_code == 401)
        r = c.post("/api/query", json={"question": "x"}, headers={"Authorization": "Bearer abc.def.ghi"})
        check("forged token -> 401", r.status_code == 401)
        r = c.get("/api/status")
        check("status without token -> 401", r.status_code == 401)

        app.dependency_overrides[require_admin] = lambda: {"id": "test", "email": "test@local"}
        try:
            r = c.post("/api/query", json={"question": "北海道内で事業を実施する場合の要件", "stub": True})
            evs = events(r.text)
            names = [e for e, _ in evs]
            print("      events:", " -> ".join(names))
            # agent.tool_call / agent.tool_result are here because the stub
            # really calls search_knowledge_base (rag.agent.stub_model). The
            # old TestModel stub called nothing, so a free run exercised none
            # of the tool path - and always declined, since TestModel filled
            # `answerable` with the bool default False.
            #
            # Note what does NOT repeat: there is no second embed/vector/bm25/
            # rerank block after the tool call. Gate 1 already searched with
            # this exact question and _search caches per query, so the tool
            # call is a cache hit - which is what keeps a stub run at zero
            # Voyage requests beyond the one gate 1 makes.
            want = ["run.start", "embed.done", "vector.done", "bm25.done", "rerank.done",
                    "gate1", "agent.start", "agent.tool_call", "agent.tool_result",
                    "gate2", "answer", "usage", "done"]
            check("stream carries every stage in order", names == want)
            d = dict(evs)
            check("gate1 passes at 0.8907", d["gate1"]["pass"] and d["gate1"]["top_score"] == 0.8907)
            check("rerank carries 5 candidates", len(d["rerank.done"]["candidates"]) == 5)
            check("stub cost is $0", d["usage"]["cost_usd"] == 0.0)

            r = c.post("/api/query", json={"question": "明日の東京の天気を教えてください", "stub": True})
            names = [e for e, _ in events(r.text)]
            print("      events:", " -> ".join(names))
            check("off-topic stops at gate 1, no agent", "agent.start" not in names
                  and dict(events(r.text))["answer"]["reason"] == "score_gate")
        finally:
            app.dependency_overrides.clear()

    print(f"\n{'all passed' if not fails else f'{fails} failed'}, $0 spent")
    sys.exit(fails)


if __name__ == "__main__":
    main()
