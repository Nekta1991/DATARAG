"""search_knowledge_base: (pgvector top-K ∪ BM25 top-L) -> cross-encoder -> top-N.

    python -m rag.retrieval "北海道内で事業を実施する場合の要件"
    python -m rag.retrieval "..." --candidates     # show the whole pool, all ranks

Cost: $0. One Voyage query embedding (free tier) and a local cross-encoder.
No Anthropic call happens anywhere in this file.

Why a reranker: measured on the 北海道 query, dense retrieval put the gold
chunk at rank 13 of 32 - it matches a chunk's dominant topic, not a sub-clause
inside it. The gold chunk IS in the candidate set, just badly ordered, which is
exactly what a cross-encoder repairs. So RETRIEVAL_TOP_K stays wide (20) and
the reranker does the precision work. Do not lower TOP_K to save latency.

Why a lexical stage too: on the full 1,025-chunk corpus the same gold chunk
fell to vector rank 211 - out of any pool a reranker can afford (TOP_K=100
still misses it). Only 4 chunks in the corpus contain 北海道. A query naming a
rare, exact term is where dense retrieval is weakest and lexical matching is
strongest, so both stages feed the reranker and it arbitrates.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

import voyageai

from rag import config  # noqa: F401  - loads .env, pins HF_HOME
from rag import db

VOYAGE_MODEL = os.getenv("VOYAGE_MODEL", "voyage-3.5")
MIN_INTERVAL = float(os.getenv("VOYAGE_MIN_REQUEST_INTERVAL_SEC", "21"))
MAX_RETRIES = int(os.getenv("VOYAGE_MAX_RETRIES", "6"))
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
# "local" = the bge cross-encoder on this machine's GPU. "voyage" = Voyage's
# rerank API, which is what a serverless host can run: Vercel has no GPU, and
# bge is 2.29 GB of weights plus torch.
#
# These are NOT interchangeable at the same threshold. Measured 2026-09-23
# (scripts/compare_rerankers.py), bge scores an off-topic question 0.0000 while
# Voyage scores the same question ~0.33 - Voyage's scale is compressed with a
# high floor. Switching backend without moving RERANK_SCORE_THRESHOLD would
# send off-topic questions to Claude as paid calls. See docs/rerank_comparison.md.
RERANK_BACKEND = os.getenv("RERANK_BACKEND", "local")
VOYAGE_RERANK_MODEL = os.getenv("VOYAGE_RERANK_MODEL", "rerank-2.5")
# Token budget for ONE rerank request, applied only to the voyage backend.
# The local cross-encoder scores the pool in batches on its own GPU and has no
# such limit, so capping it there would throw away candidates for nothing.
#
# Measured 2026-09-23. The unpaid tier advertises 10,000 tokens/minute, but the
# usable size is well under that and the limit is not a clean token bucket:
# repeating one payload three times, 30 s apart, 3,000 and 5,000-token requests
# succeeded 3/3 while 6,500 was refused on the first attempt, and ~8,000-token
# requests failed intermittently even spaced 76 s apart. A full candidate pool
# for this corpus is 2,872-10,262 tokens, so the pool has to be trimmed.
#
# 5,000 is where reliability and quality meet. Compared against the local
# reranker over the full pool, a 5,000-token budget keeps the same top passage
# for 12 of 14 calibration questions (the exceptions: Q7, which clears gate 1
# via named_doc rather than score, and Q5, whose top-1 changes and scores
# 0.5203 instead of 0.7573). At 3,000 only 8 of 14 survive, which is a real
# loss of answer quality, not just of score.
#
# This ceiling is a property of the unpaid tier, not of rerank-2.5. With a
# payment method on the Voyage account the rate limits lift and the full pool
# fits; reranking itself stays free either way, inside the 200M allowance.
VOYAGE_RERANK_TOKEN_BUDGET = int(os.getenv("VOYAGE_RERANK_TOKEN_BUDGET", "5000"))
TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "20"))
LEXICAL_TOP_K = int(os.getenv("LEXICAL_TOP_K", "10"))
TOP_N = int(os.getenv("RERANK_TOP_N", "5"))

# Pair length for the cross-encoder, in the reranker's own tokens. Chunks are
# capped near 512 Voyage tokens; XLM-R tokenizes Japanese differently, so leave
# headroom rather than silently truncating the end of a table.
RERANK_MAX_LENGTH = 1024


@dataclass
class Hit:
    chunk_id: int
    source_doc: str          # program + 枠 + doc_type - must reach the prompt
    authority_rank: int      # 1=交付規程 beats 2=公募要領 on conflict
    filename: str
    heading: str | None
    page_no: int | None
    content: str
    vector_rank: int | None  # 1-based position from pgvector; None = lexical only
    similarity: float        # cosine similarity, 0-1
    rerank_score: float | None = None   # sigmoid(cross-encoder), 0-1
    lexical_rank: int | None = None     # 1-based BM25 position; None = not matched


# -- stage 0: query embedding -----------------------------------------------

@lru_cache(maxsize=1)
def _voyage() -> voyageai.Client:
    return voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])


def embed_query(query: str) -> list[float]:
    """input_type="query", not "document" - Voyage embeds the two sides of
    retrieval asymmetrically, and mixing them up quietly costs recall."""
    for attempt in range(MAX_RETRIES):
        try:
            return _voyage().embed([query], model=VOYAGE_MODEL,
                                   input_type="query").embeddings[0]
        except Exception:
            # Unpaid tier is 3 RPM, shared with any ingest that is running.
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(MIN_INTERVAL * (2 ** attempt))


# -- query normalization ----------------------------------------------------
#
# Rerank scores are computed against a question's actual wording, and a
# naturally phrased question can score far below the same question asked in the
# documents' vocabulary. Measured 2026-09-23 on 「どんな企業が応募できますか」:
# 0.0587 as asked, 0.2764 with the interrogative scaffolding removed, and 0.76+
# once domain nouns were added - while the corpus answers it well (the 中小企業
# 等の定義 table reranks 0.82 for a query using its own words). Gate 1 was
# therefore declining a question the corpus answers, at $0, before the agent -
# which CAN reformulate a search - ever ran.
#
# This strips interrogative and polite scaffolding only. It is deliberately not
# a synonym map: hand-listing 応募 -> 補助対象者 would answer the questions
# someone thought of in advance, which is the actual complaint. Removing
# question-words is a property of Japanese, not of this corpus, so it
# generalizes to questions nobody predicted.
#
# It cannot make retrieval worse, because gate 1 takes the MAX of the raw and
# normalized scores. Measured on the known negatives, normalization leaves them
# where they were: weather 0.0000 -> 0.0000, tax 0.0033 -> 0.0136, insurance
# 0.0015 -> 0.0043, against 0.2764 for the real question.

_INTERROGATIVE = re.compile(
    r"(どのような|どのように|どんな|どういう|どちら|どれくらい|どのくらい|"
    r"いくらくらい|いくつ|いくら|何ですか|なんですか|なに|何|いつ|どこ|どう|"
    r"だれ|誰|なぜ|どうして)")
_POLITE_TAIL = re.compile(
    r"(を?教えて(ください|下さい)?|について(教えて)?|でしょうか|ますでしょうか|"
    r"ですか|ますか|できますか|ください|下さい|かな|のか|ですね|です|ます|"
    r"[？?。、]\s*)$")
_TRAILING_PARTICLE = re.compile(r"(は|が|を|に|へ|と|より|から|の)$")


def normalize_query(query: str) -> str:
    """The question with interrogative and polite scaffolding removed.

    Returns "" when nothing meaningful is left, so callers can skip it.
    """
    q = unicodedata.normalize("NFKC", query).strip()
    for _ in range(3):                       # 「…を教えてください。」 nests
        q = _POLITE_TAIL.sub("", q).strip()
    q = _INTERROGATIVE.sub(" ", q)
    # Particles that only glued the question together now dangle.
    q = re.sub(r"[\s　]+", " ", q).strip()
    q = _TRAILING_PARTICLE.sub("", q).strip()
    return q if len(q) >= 2 and q != unicodedata.normalize("NFKC", query).strip() else ""


# -- stage 1a: vector candidates --------------------------------------------

def _vec_literal(qvec: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in qvec) + "]"


def vector_candidates(qvec: list[float], k: int = TOP_K) -> list[Hit]:
    with db.connection() as conn:
        rows = conn.execute(
            """SELECT id, source_doc, authority_rank, filename, heading, page_no,
                      content, 1 - (embedding <=> %(q)s::vector) AS similarity
                 FROM chunk_sources
                ORDER BY embedding <=> %(q)s::vector
                LIMIT %(k)s""",
            {"q": _vec_literal(qvec), "k": k},
        ).fetchall()
    return [Hit(*r[:7], vector_rank=i + 1, similarity=float(r[7]))
            for i, r in enumerate(rows)]


def _hydrate(ids: list[int], qvec: list[float]) -> dict[int, Hit]:
    """Full rows for chunks found only lexically, with their cosine similarity
    computed too, so every candidate carries both signals."""
    if not ids:
        return {}
    with db.connection() as conn:
        rows = conn.execute(
            """SELECT id, source_doc, authority_rank, filename, heading, page_no,
                      content, 1 - (embedding <=> %(q)s::vector)
                 FROM chunk_sources WHERE id = ANY(%(ids)s)""",
            {"q": _vec_literal(qvec), "ids": ids},
        ).fetchall()
    return {r[0]: Hit(*r[:7], vector_rank=None, similarity=float(r[7])) for r in rows}


# -- stage 1b: lexical candidates (character-bigram BM25) --------------------
#
# Japanese has no spaces and Postgres full-text search has no Japanese
# segmenter, so the unit is the character bigram: 北海道 -> 北海, 海道. BM25's
# IDF does the rest - 事業 / 実施 / 要件 occur in most chunks and weigh almost
# nothing, while 北海 occurs in a handful and dominates. Held in memory; the
# index is built once per process, so a re-ingest needs a restart to be seen.

_K1, _B = 1.2, 0.75
_SPLIT = re.compile(r"[\s\W_]+")


def _bigrams(text: str) -> list[str]:
    # NFKC folds the corpus's mixed widths: ２０２６ -> 2026, ＡＩ -> AI.
    text = unicodedata.normalize("NFKC", text).lower()
    out: list[str] = []
    for run in _SPLIT.split(text):
        if len(run) == 1:
            out.append(run)
        out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return out


@lru_cache(maxsize=1)
def _bm25_index():
    with db.connection() as conn:
        rows = conn.execute("SELECT id, content FROM chunks").fetchall()
    ids, tfs = [], []
    df: Counter = Counter()
    for cid, content in rows:
        tf = Counter(_bigrams(content))
        ids.append(cid)
        tfs.append(tf)
        df.update(tf.keys())
    n = len(ids)
    idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
    lens = [sum(tf.values()) for tf in tfs]
    return ids, tfs, lens, sum(lens) / n, idf


def lexical_candidates(query: str, k: int = LEXICAL_TOP_K) -> list[tuple[int, float]]:
    """(chunk_id, bm25) best first. Chunks sharing no query bigram are dropped."""
    ids, tfs, lens, avg, idf = _bm25_index()
    terms = [t for t in set(_bigrams(query)) if t in idf]
    scored = []
    for cid, tf, ln in zip(ids, tfs, lens):
        s = 0.0
        for t in terms:
            f = tf.get(t)
            if f:
                s += idf[t] * f * (_K1 + 1) / (f + _K1 * (1 - _B + _B * ln / avg))
        if s > 0:
            scored.append((cid, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


def hybrid_candidates(query: str, qvec: list[float], top_k: int = TOP_K,
                      lexical_k: int = LEXICAL_TOP_K, emit=None) -> list[Hit]:
    """Union of both stages, deduplicated. Order is irrelevant - the reranker
    re-scores every candidate; per-stage ranks are kept for diagnosis.
    `emit(event, data)`, if given, reports each stage (the dashboard console)."""
    t = time.time()
    vec = vector_candidates(qvec, top_k)
    if emit:
        emit("vector.done", {"k": top_k, "ms": int((time.time() - t) * 1000),
                             "best_similarity": round(vec[0].similarity, 4) if vec else None})
    hits = {h.chunk_id: h for h in vec}
    t = time.time()
    lex = lexical_candidates(query, lexical_k)
    extra = _hydrate([cid for cid, _ in lex if cid not in hits], qvec)
    hits.update(extra)
    for rank, (cid, _) in enumerate(lex, 1):
        hits[cid].lexical_rank = rank
    if emit:
        emit("bm25.done", {"k": lexical_k, "ms": int((time.time() - t) * 1000),
                           "lexical_only": len(extra), "pool": len(hits)})
    return list(hits.values())


# -- stage 2: rerank --------------------------------------------------------

@lru_cache(maxsize=1)
def _reranker():
    import torch
    from sentence_transformers import CrossEncoder

    # Sigmoid is passed explicitly rather than left to the library default,
    # so RERANK_SCORE_THRESHOLD is a 0-1 number for any configured model -
    # the English MiniLM fallback emits raw logits otherwise.
    return CrossEncoder(
        RERANKER_MODEL,
        device="cuda" if torch.cuda.is_available() else "cpu",
        max_length=RERANK_MAX_LENGTH,
        activation_fn=torch.nn.Sigmoid(),
    )


def _rerank_local(query: str, hits: list[Hit]) -> None:
    scores = _reranker().predict([(query, h.content) for h in hits],
                                 show_progress_bar=False)
    for h, s in zip(hits, scores):
        h.rerank_score = float(s)


def _rerank_local_scored(query: str, hits: list[Hit]) -> list[Hit]:
    """Score with the local model regardless of RERANK_BACKEND, and return the
    ranking. Used to compare the two backends over one identical pool."""
    _rerank_local(query, hits)
    return sorted(hits, key=lambda h: h.rerank_score, reverse=True)


TOKENIZER_FILE = config.ROOT / "rag" / "assets" / "voyage-3.5-tokenizer.json"


@lru_cache(maxsize=1)
def _voyage_tokenizer():
    """Voyage's tokenizer, loaded from a file vendored into the repo.

    Deliberately `tokenizers` (the small Rust library) and not `transformers`:
    the budget below is needed wherever the voyage backend runs, including a
    Vercel function, and transformers is a large dependency to carry for one
    token count. The file is vendored rather than fetched because
    `Tokenizer.from_pretrained` reaches huggingface.co at import time, which on
    a cold serverless start is both slow and a third party in the request path.
    """
    from tokenizers import Tokenizer
    if TOKENIZER_FILE.exists():
        tok = Tokenizer.from_file(str(TOKENIZER_FILE))
        tok.no_truncation()
        return tok
    # Local fallback: the HF cache the ingest path already populated.
    from huggingface_hub import hf_hub_download
    tok = Tokenizer.from_file(hf_hub_download("voyageai/voyage-3.5", "tokenizer.json",
                                              local_files_only=True))
    tok.no_truncation()
    return tok


def voyage_tokens(text: str) -> int:
    return len(_voyage_tokenizer().encode(text, add_special_tokens=False).ids)


def budget_pool(hits: list[Hit], budget: int) -> list[Hit]:
    """The largest prefix of the pool that fits `budget` tokens.

    Order is by a candidate's BEST position in either stage, not by vector
    rank. That distinction is the whole point: the 北海道 gold chunk is found
    only by BM25 (vector rank None, lexical rank 1), so ordering by vector rank
    would sort it last and the budget would drop exactly the chunk the hybrid
    stage exists to rescue. Verified: it survives an 8,000-token cap and still
    reranks to #1.

    Dropping is by length, implicitly - a long chunk that does not fit is
    skipped while shorter, lower-priority ones still get in. That is deliberate:
    a 3,000-token table should not evict four whole candidates.
    """
    inf = float("inf")
    order = sorted(hits, key=lambda h: (min(h.vector_rank or inf, h.lexical_rank or inf),
                                        h.vector_rank or inf))
    kept, used = [], 0
    for h in order:
        n = voyage_tokens(h.content)
        if used + n <= budget:
            kept.append(h)
            used += n
    return kept


def _rerank_voyage(query: str, hits: list[Hit]) -> list[Hit]:
    """One API call, over as much of the pool as the token budget allows.

    Returns the scored subset - callers must use it rather than the pool they
    passed in, because anything dropped here has no score.
    """
    kept = budget_pool(hits, VOYAGE_RERANK_TOKEN_BUDGET)
    res = _voyage().rerank(query=query, documents=[h.content for h in kept],
                           model=VOYAGE_RERANK_MODEL)
    for r in res.results:
        kept[r.index].rerank_score = float(r.relevance_score)
    for h in kept:                      # defensive: the API returned every doc
        if h.rerank_score is None:
            h.rerank_score = 0.0
    return kept


def rerank(query: str, hits: list[Hit]) -> list[Hit]:
    if not hits:
        return hits
    if RERANK_BACKEND == "voyage":
        hits = _rerank_voyage(query, hits)   # may be a subset: see budget_pool
    elif RERANK_BACKEND == "local":
        _rerank_local(query, hits)
    else:
        raise ValueError(f"RERANK_BACKEND must be 'local' or 'voyage', not {RERANK_BACKEND!r}")
    return sorted(hits, key=lambda h: h.rerank_score, reverse=True)


def reranker_name() -> str:
    """What actually scored this run, for the dashboard console and the ledger."""
    return VOYAGE_RERANK_MODEL if RERANK_BACKEND == "voyage" else RERANKER_MODEL


# -- the tool ---------------------------------------------------------------

def search_knowledge_base(query: str, top_n: int = TOP_N,
                          top_k: int = TOP_K) -> list[Hit]:
    """Top-N passages for `query`, best first, each carrying its source_doc.

    Candidates are the union of vector top-K and lexical top-L; the
    cross-encoder scores every one of them.

    Scores are returned, not filtered. The decline-to-answer gate belongs to
    the caller and runs unconditionally before generation - retrieval reports
    how good the evidence is; it does not decide whether to answer.
    """
    return rerank(query, hybrid_candidates(query, embed_query(query), top_k))[:top_n]


# -- CLI --------------------------------------------------------------------

def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--candidates", action="store_true",
                    help="show the whole candidate pool, not just the top N")
    args = ap.parse_args()

    t0 = time.time()
    qvec = embed_query(args.query)
    t1 = time.time()
    hits = hybrid_candidates(args.query, qvec)
    t2 = time.time()
    ranked = rerank(args.query, hits)
    t3 = time.time()

    n_lex_only = sum(h.vector_rank is None for h in hits)
    print(f"query     {args.query}")
    print(f"timing    embed {t1 - t0:.2f}s / candidates {t2 - t1:.2f}s / "
          f"rerank {t3 - t2:.2f}s  ({RERANKER_MODEL})")
    print(f"pool      {len(hits)} candidates ({n_lex_only} found only lexically)")
    print(f"{'rr':>3} {'vr':>4} {'lr':>3} {'rerank':>7} {'sim':>6}  source / heading")
    for i, h in enumerate(ranked if args.candidates else ranked[:TOP_N], 1):
        mark = "*" if i <= TOP_N else " "
        print(f"{i:>3}{mark}{h.vector_rank or '-':>4} {h.lexical_rank or '-':>3} "
              f"{h.rerank_score:7.4f} {h.similarity:6.4f}  {h.source_doc} "
              f"p{h.page_no}  #{h.chunk_id}  {h.heading or ''}")
        print(f"          {h.content[:90].replace(chr(10), ' ')}")


if __name__ == "__main__":
    _main()
