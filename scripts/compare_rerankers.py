"""Does Voyage's rerank API separate the same cases the local bge reranker does?

    python scripts/compare_rerankers.py            # measure (slow, cached)
    python scripts/compare_rerankers.py --report   # re-read the cache, $0, instant

Cost: $0. Voyage rerank-2.5 has the same 200M free-token allowance as the
embeddings, and no Anthropic call happens in this file.

Why this exists: the Vercel migration cannot ship `BAAI/bge-reranker-v2-m3`
(2.29 GB + CUDA against a 250 MB function limit), so reranking has to move to
Voyage's API. RERANK_SCORE_THRESHOLD=0.3 was calibrated against bge's sigmoid
output and does not survive that swap - a different model on a different scale.
Gate 1 is the only thing standing between an off-topic question and a paid
Claude call, so it gets re-derived from measurements, not guessed.

What matters is not whether the two models agree on absolute scores. It is
whether a threshold exists under Voyage that puts every must-pass question
above it and every must-stop question below it, with usable clearance.

The 3 RPM / 10K TPM unpaid throttle applies to rerank too, so every Voyage
result is cached to data/rerank_compare.json and requests are spaced by hand.
Re-runs read the cache; --report never calls out at all.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from functools import lru_cache

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import voyageai

from rag import retrieval as R

CACHE = pathlib.Path(__file__).resolve().parent.parent / "data" / "rerank_compare.json"
VOYAGE_RERANK_MODEL = "rerank-2.5"

# Unpaid tier: 3 requests/min AND 10,000 tokens/min. The token ceiling is the
# binding one: reranking a candidate pool for this corpus costs 2,900-10,300
# tokens, so a single rerank can spend the entire minute on its own. Spacing by
# request count alone hits RateLimitError immediately. The limiter therefore
# tracks a rolling 60-second window of both requests and tokens and waits for
# whichever is short.
TPM_LIMIT = 10_000
RPM_LIMIT = 3
# Voyage's accounting is stricter than a naive 60-second rolling window.
# Measured: 8,651 and 9,272-token requests succeeded with a clear window, but
# 9,400 and 9,530-token ones were refused after waiting out 61 seconds. Rather
# than model their bookkeeping, anything large simply waits for a demonstrably
# empty window plus a margin. Slower, and it stops losing cases to retries.
WINDOW_SEC = 75
LARGE_REQUEST = 6_000
# Estimating the payload from character count does not work on this corpus and
# the first version of this script got it badly wrong. Chars-per-token ranges
# from ~1.3 on dense prose to ~2.8 overall, because the markdown tables are
# padded with alignment spaces that cost characters and almost no tokens
# (collapsing every run of spaces drops 38% of the characters and 2.2% of the
# tokens). A chars/1.4 estimate predicted 44,020 tokens for a pool that is
# really 9,430 - which looked exactly like "the corpus is too big to rerank"
# and was not true. So: count with Voyage's own tokenizer, which is already
# cached locally for the ingest path and costs nothing to call.

_window: list[tuple[float, int]] = []   # (timestamp, tokens charged)


@lru_cache(maxsize=1)
def _tokenizer():
    from huggingface_hub import snapshot_download
    from transformers import AutoTokenizer
    # voyage-3.5 ships no config.json, so from_pretrained(repo_id) fails
    # offline; resolve the snapshot directory first (MANUAL §5).
    return AutoTokenizer.from_pretrained(
        snapshot_download("voyageai/voyage-3.5", local_files_only=True))


def count_tokens(texts: list[str]) -> int:
    tok = _tokenizer()
    return sum(len(tok(t, add_special_tokens=False)["input_ids"]) for t in texts)


def _throttle(tokens: int) -> None:
    """Block until a request of `tokens` fits inside both ceilings.

    A request larger than TPM_LIMIT can never fit - one request cannot be
    spread across minutes - so waiting for it would loop forever. Those are
    let through against an empty window so the API's own answer is recorded
    rather than guessed at; that is the measurement this script exists for.
    """
    while True:
        now = time.time()
        _window[:] = [(t, n) for t, n in _window if now - t < WINDOW_SEC]
        used = sum(n for _, n in _window)
        # A large request needs the whole minute to itself; a small one only
        # needs to fit beside what is already in flight.
        ok = (not _window) if tokens >= LARGE_REQUEST else (
            len(_window) < RPM_LIMIT and used + tokens <= TPM_LIMIT)
        if ok:
            return
        if tokens > TPM_LIMIT and not _window:
            print(f"      over-ceiling: this request alone needs ~{tokens} tokens "
                  f"of a {TPM_LIMIT}/min budget - sending it to see what happens")
            return
        oldest = min(t for t, _ in _window)
        wait = max(1.0, WINDOW_SEC + 1 - (now - oldest))
        print(f"      throttle: {len(_window)} req / {used} tok in window, "
              f"need {tokens} - waiting {wait:.0f}s")
        time.sleep(wait)


def spaced(fn, *a, tokens: int = 100, **kw):
    _throttle(tokens)
    try:
        return fn(*a, **kw)
    finally:
        _window.append((time.time(), tokens))


def cold_start_wait() -> None:
    """The rolling window lives in this process; Voyage's does not.

    A re-run starting seconds after the previous one stopped begins with an
    empty window and immediately spends ~9,500 tokens that Voyage still counts
    against the last minute - which is how the first case of a re-run kept
    dying on RateLimitError while looking perfectly within budget. The cache
    file's mtime dates the last request closely enough to wait it out.
    """
    if not CACHE.exists():
        return
    idle = time.time() - CACHE.stat().st_mtime
    if idle < WINDOW_SEC + 1:
        wait = WINDOW_SEC + 1 - idle
        print(f"last run ended {idle:.0f}s ago; waiting {wait:.0f}s for Voyage's "
              f"own token window to clear")
        time.sleep(wait)


# -- the calibration set ----------------------------------------------------
#
# must_pass: gate 1 has to let these through, or a real question dies for free.
# must_stop: gate 1 has to stop these, or an off-topic question reaches Claude
#   and is billed. Q8 is the suite's only must-stop, which is too thin a basis
#   for a threshold, so four more negatives are added here. Two are deliberately
#   adjacent - Japanese business/tax questions that sound like they belong to
#   this corpus and do not - because the easy negatives (weather, Python) were
#   already known to score ~0.000 and prove nothing about where to put the line.
CASES = [
    # id, question, expectation, note
    (1, "北海道内で事業を実施する場合の要件は何ですか？", "must_pass", "clause inside a paragraph"),
    (2, "複数者連携デジタル化・AI導入枠の補助率と補助上限額を教えてください。", "must_pass", "numeric, table"),
    (3, "通常枠の補助額と補助率はいくらですか？", "must_pass", "枠 discrimination"),
    (4, "小規模事業者がセキュリティ対策推進枠を利用する場合の補助率はいくらですか？", "must_pass", "枠 + 事業者区分"),
    (5, "スマート農業の補助事業とデジタル化・AI導入補助金を併用して、同じ機械の購入費を両方に申請できますか？", "must_pass", "cross-programme"),
    (6, "補助金の交付決定が取り消されるのはどのような場合ですか？", "must_pass", "交付規程 binding"),
    (7, "通常枠の交付規程は全体としてどのような構成（どのような条項の流れ）になっていますか？", "must_pass", "named doc; passes via named_doc at 0.254"),
    (8, "明日の東京の天気を教えてください。", "must_stop", "off-topic"),
    (9, "ものづくり補助金の補助上限額はいくらですか？", "must_pass", "on-topic, absent -> model declines at gate 2"),
    (10, "2027年度のデジタル化・AI導入補助金の公募スケジュールはいつですか？", "must_pass", "wrong year -> model declines at gate 2"),
    # extra negatives, this script only
    (101, "東京駅から新宿駅までの行き方を教えてください。", "must_stop", "off-topic, navigation"),
    (102, "Pythonでリストをソートする方法を教えてください。", "must_stop", "off-topic, programming"),
    (103, "法人税の実効税率は何パーセントですか？", "must_stop", "adjacent: tax, not in corpus"),
    (104, "健康保険の被扶養者の認定基準を教えてください。", "must_stop", "adjacent: social insurance, not in corpus"),
]

# Q9 and Q10 pass gate 1 on purpose: they are on-topic questions the *model*
# must decline at gate 2, having seen the evidence. A gate-1 threshold that
# stopped them would turn a reasoned "the corpus does not say" into a silent
# score decline, and the run-1 report grades them on reason, not just status.


def measure(cache: dict) -> dict:
    client = voyageai.Client()
    for cid, q, expect, note in CASES:
        key = str(cid)
        if key in cache and cache[key].get("voyage_top") is not None:
            print(f"  [{cid}] cached")
            continue
        row = cache.get(key, {})
        row.update(question=q, expect=expect, note=note)

        try:
            qvec = row.get("qvec")
            if qvec is None:
                qvec = spaced(R.embed_query, q, tokens=100)
                row["qvec"] = qvec
            hits = R.hybrid_candidates(q, qvec)
            row["pool"] = len(hits)

            ranked = R.rerank(q, list(hits))
            row["bge_top"] = round(ranked[0].rerank_score, 6) if ranked else 0.0
            row["bge_top5"] = [[h.chunk_id, round(h.rerank_score, 6)] for h in ranked[:5]]

            docs = [h.content for h in hits]
            row["rerank_chars"] = sum(len(d) for d in docs)
            est = count_tokens(docs) + 100   # + the query and framing
            res = spaced(client.rerank, query=q, documents=docs,
                         model=VOYAGE_RERANK_MODEL, tokens=est)
            order = [(hits[r.index].chunk_id, r.relevance_score) for r in res.results]
            row["voyage_top"] = round(order[0][1], 6) if order else 0.0
            row["voyage_top5"] = [[c, round(s, 6)] for c, s in order[:5]]
            row["voyage_tokens"] = getattr(res, "total_tokens", None)
            row["error"] = None
            print(f"  [{cid}] bge {row['bge_top']:.4f}  voyage {row['voyage_top']:.4f}  "
                  f"pool {row['pool']}  tok {row['voyage_tokens']}")
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            print(f"  [{cid}] ERROR {row['error']}")

        cache[key] = row
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return cache


# -- reporting --------------------------------------------------------------

def separation(rows: list[dict], field: str) -> tuple[float, float, float]:
    """(lowest must_pass, highest must_stop, gap). gap <= 0 means no threshold
    can split them - the model cannot drive gate 1 at all."""
    ok = [r[field] for r in rows if r["expect"] == "must_pass" and r.get(field) is not None]
    no = [r[field] for r in rows if r["expect"] == "must_stop" and r.get(field) is not None]
    lo, hi = (min(ok) if ok else 0.0), (max(no) if no else 0.0)
    return lo, hi, lo - hi


def report(cache: dict) -> int:
    rows = [cache[str(c[0])] | {"id": c[0]} for c in CASES if str(c[0]) in cache]
    rows = [r for r in rows if r.get("error") is None]
    if not rows:
        print("nothing measured yet")
        return 1

    print(f"\n{'id':>4} {'expect':<10} {'bge':>8} {'voyage':>8}  note")
    for r in sorted(rows, key=lambda r: r["id"]):
        print(f"{r['id']:>4} {r['expect']:<10} {r['bge_top']:8.4f} {r['voyage_top']:8.4f}  {r['note']}")

    print()
    for field, label in (("bge_top", "bge (current)"), ("voyage_top", "voyage rerank-2.5")):
        lo, hi, gap = separation(rows, field)
        verdict = "SEPARATES" if gap > 0 else "NO THRESHOLD EXISTS"
        print(f"{label:<20} lowest must-pass {lo:.4f} · highest must-stop {hi:.4f} · "
              f"gap {gap:+.4f}  {verdict}")
        if gap > 0:
            print(f"{'':<20} usable range ({hi:.4f}, {lo:.4f}] · midpoint "
                  f"{(hi + lo) / 2:.4f}")

    # Q7 is exempt: it clears gate 1 through named_documents(), not on score.
    lo7, hi7, gap7 = separation([r for r in rows if r["id"] != 7], "voyage_top")
    print(f"\n{'voyage, Q7 excluded':<20} lowest must-pass {lo7:.4f} · highest must-stop "
          f"{hi7:.4f} · gap {gap7:+.4f}   (Q7 passes via named_doc, not score)")

    toks = [r["voyage_tokens"] for r in rows if r.get("voyage_tokens")]
    if toks:
        print(f"\nrerank request size: {min(toks)}-{max(toks)} tokens "
              f"(unpaid tier is 10,000 TPM; one rerank plus one embed per query)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="re-read the cache only, no API calls")
    args = ap.parse_args()

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    if not args.report:
        n = sum(1 for c in CASES if cache.get(str(c[0]), {}).get("voyage_top") is None)
        print(f"measuring {n} of {len(CASES)} cases (~{n:.0f}-{n * 1.5:.0f} min: one "
              f"rerank is ~9k of the 10k-token minute), $0")
        cold_start_wait()
        cache = measure(cache)
    return report(cache)


if __name__ == "__main__":
    sys.exit(main())
