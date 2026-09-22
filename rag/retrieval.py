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

import psycopg
import voyageai

from rag import config  # noqa: F401  - loads .env, pins HF_HOME

VOYAGE_MODEL = os.getenv("VOYAGE_MODEL", "voyage-3.5")
MIN_INTERVAL = float(os.getenv("VOYAGE_MIN_REQUEST_INTERVAL_SEC", "21"))
MAX_RETRIES = int(os.getenv("VOYAGE_MAX_RETRIES", "6"))
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
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


# -- stage 1a: vector candidates --------------------------------------------

def _vec_literal(qvec: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in qvec) + "]"


def vector_candidates(qvec: list[float], k: int = TOP_K) -> list[Hit]:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
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
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
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
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
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


def rerank(query: str, hits: list[Hit]) -> list[Hit]:
    if not hits:
        return hits
    scores = _reranker().predict([(query, h.content) for h in hits],
                                 show_progress_bar=False)
    for h, s in zip(hits, scores):
        h.rerank_score = float(s)
    return sorted(hits, key=lambda h: h.rerank_score, reverse=True)


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
