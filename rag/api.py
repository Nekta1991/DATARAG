"""HTTP API for the dashboard. POST /api/query streams the pipeline as SSE.

    uvicorn rag.api:app --host 127.0.0.1 --port 8000

Hosting, round 1 (2026-09-21): this runs on the local PC (GPU reranker) and
the Vercel frontend reaches it through a tunnel. So every route except
/api/health demands a Neon Auth JWT from the single admin account - a public
tunnel URL must not be able to spend the Anthropic budget.

Auth: the JWT's signature is checked against the branch's JWKS
(NEON_AUTH_JWKS_URL), then the user is looked up in neon_auth."user" and must
carry role=admin and not be banned. Open sign-up on Neon Auth therefore grants
nothing: a registered non-admin gets 403.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from functools import lru_cache

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from rag import agent as A
from rag import config  # noqa: F401  - loads .env, pins HF_HOME
from rag import db
from rag import retrieval as R

ALLOWED_ORIGINS = [o.strip() for o in
                   os.getenv("API_ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o.strip()]

# One query at a time, per process. This used to be what kept two concurrent
# runs from both passing the budget guard; it is not any more - the ledger
# reservation does that in Postgres, across processes and instances, because a
# threading.Lock means nothing to a second serverless instance (rag/ledger.py).
#
# It stays for two smaller reasons that are still true locally: the bge
# reranker shares one GPU, and RERANK_BACKEND=voyage gets 10,000 tokens a
# minute against ~9,500 per query, so a second concurrent query would fail on
# a rate limit rather than queue. It is a courtesy, no longer a guarantee.
_busy = threading.Lock()
_warm = {"ready": False, "seconds": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pay every cold cost here, never on a user's first query. Measured:
    # bge cold load 113 s, BM25 build 2.3 s, chunk fetch 2.1 s.
    #
    # Only the bge load is worth 113 s of startup, and only the local backend
    # has it - RERANK_BACKEND=voyage has no weights to load, so warming it
    # would just burn a rerank request against a 10k-token minute. The BM25
    # index is built either way: it is in-process, so a serverless instance
    # rebuilds it per cold start (~4.4 s total, 1.9 MB resident).
    t = time.time()
    if R.RERANK_BACKEND == "local":
        await asyncio.to_thread(R._reranker)
    await asyncio.to_thread(R._bm25_index)
    await asyncio.to_thread(A.build_agent)
    _warm.update(ready=True, seconds=round(time.time() - t, 1))
    yield


app = FastAPI(title="DATARAG", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                   allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])


# -- auth -------------------------------------------------------------------

@lru_cache(maxsize=1)
def _jwks() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(os.environ["NEON_AUTH_JWKS_URL"], cache_keys=True)


def require_admin(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        key = _jwks().get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["EdDSA"],
                            options={"verify_aud": False, "require": ["exp", "sub"]})
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"invalid token: {type(e).__name__}")
    with db.connection() as conn:
        row = conn.execute('SELECT email, role, banned FROM neon_auth."user" WHERE id = %s',
                           (claims["sub"],)).fetchone()
    if row is None:
        raise HTTPException(403, "unknown user")
    email, role, banned = row
    if banned or "admin" not in [r.strip() for r in (role or "").split(",")]:
        raise HTTPException(403, "admin only")
    return {"id": claims["sub"], "email": email}


# -- routes -----------------------------------------------------------------

class QueryIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    stub: bool = Field(default=False, description="free run: stubbed model, real retrieval")


@app.get("/api/health")
def health():
    return {"ok": True, "warm": _warm["ready"], "warm_seconds": _warm["seconds"]}


@app.get("/api/status")
def status(user: dict = Depends(require_admin)):
    with db.connection() as conn:
        docs, chunks = conn.execute(
            "SELECT (SELECT count(*) FROM documents), (SELECT count(*) FROM chunks)").fetchone()
    return {"documents": docs, "chunks": chunks, "model": A.MODEL, "effort": A.EFFORT,
            "threshold": A.THRESHOLD, "spent_usd": round(A.spent_so_far(), 4),
            "budget_usd": A.BUDGET_USD, "busy": _busy.locked(), "user": user["email"]}


@app.post("/api/query")
async def query(body: QueryIn, user: dict = Depends(require_admin)):
    if not _busy.acquire(blocking=False):
        raise HTTPException(429, "a query is already running")

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    t0 = time.time()

    def emit(event: str, data: dict) -> None:
        payload = {**data, "t_ms": int((time.time() - t0) * 1000)}
        loop.call_soon_threadsafe(queue.put_nowait, (event, payload))

    def work() -> None:
        try:
            model = None
            if body.stub:
                # A scripted stub that searches and cites, not TestModel: that
                # filled the schema with defaults, so `answerable` was always
                # False and every free run declined at gate 2 regardless of
                # retrieval. Still $0 and no extra Voyage request (the search
                # is a cache hit on gate 1's). See rag.agent.stub_model.
                model = A.stub_model()
            A.answer_question(body.question, model=model, emit=emit, source="api")
            emit("done", {"ok": True})
        except Exception as e:  # already reported as an `error` event where it arose
            emit("done", {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"})
        finally:
            _busy.release()
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=work, daemon=True).start()

    async def stream():
        while (item := await queue.get()) is not None:
            event, data = item
            yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}

    return EventSourceResponse(stream())
