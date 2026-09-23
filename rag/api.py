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
import uuid
import time
from contextlib import asynccontextmanager
from functools import lru_cache

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request
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
    confirm: bool = Field(
        default=True,
        description="True runs straight through. False pauses after gate 1 - which is "
                    "free - and waits for POST /api/confirm before spending anything.")


class ConfirmIn(BaseModel):
    run_id: str
    proceed: bool


# Runs waiting for a confirmation, and runs asked to stop. Keyed by run_id.
# A plain dict is enough because /api/query already serves one query at a time
# (_busy); if that ever changes these need a lock.
_pending: dict[str, dict] = {}
CONFIRM_TIMEOUT_SEC = float(os.getenv("CONFIRM_TIMEOUT_SEC", "180"))


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


@app.post("/api/confirm")
def confirm(body: ConfirmIn, user: dict = Depends(require_admin)):
    """Release, or abandon, a run paused at the confirmation point.

    Also the cancel button's endpoint: `proceed=false` sets the same flag the
    client disconnecting would, so a run can be stopped whether or not it is
    currently waiting.
    """
    state = _pending.get(body.run_id)
    if state is None:
        raise HTTPException(404, "no such run (it may have already finished)")
    if not body.proceed:
        state["cancelled"].set()
    state["proceed"] = body.proceed
    state["decided"].set()
    return {"ok": True, "run_id": body.run_id, "proceed": body.proceed}


@app.post("/api/query")
async def query(request: Request, body: QueryIn, user: dict = Depends(require_admin)):
    if not _busy.acquire(blocking=False):
        raise HTTPException(429, "a query is already running")

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    t0 = time.time()
    run_id = uuid.uuid4().hex
    state = {"cancelled": threading.Event(), "decided": threading.Event(), "proceed": True}
    _pending[run_id] = state

    def emit(event: str, data: dict) -> None:
        payload = {**data, "t_ms": int((time.time() - t0) * 1000)}
        loop.call_soon_threadsafe(queue.put_nowait, (event, payload))

    def wait_for_confirmation(top_score: float) -> bool:
        """Pause between the free part and the paid part.

        Everything before this point - retrieval, both reranks, gate 1 - costs
        nothing, so a question abandoned here costs exactly nothing. That is
        the whole value of asking here rather than after.
        """
        emit("confirm.required", {"run_id": run_id, "top_score": round(top_score, 4),
                                  "estimated_usd": 0.03, "max_usd": round(A.WORST_CASE_USD, 2),
                                  "timeout_sec": CONFIRM_TIMEOUT_SEC})
        if not state["decided"].wait(timeout=CONFIRM_TIMEOUT_SEC):
            emit("confirm.timeout", {"after_sec": CONFIRM_TIMEOUT_SEC})
            return False
        return bool(state["proceed"]) and not state["cancelled"].is_set()

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
            # Confirmation only guards spending, so a stub run never asks.
            need_confirm = (not body.stub) and (not body.confirm)
            A.answer_question(body.question, model=model, emit=emit, source="api",
                              cancelled=state["cancelled"],
                              confirm=wait_for_confirmation if need_confirm else None)
            emit("done", {"ok": True})
        except Exception as e:  # already reported as an `error` event where it arose
            emit("done", {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"})
        finally:
            _pending.pop(run_id, None)
            _busy.release()
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=work, daemon=True).start()

    async def stream():
        # run.id first, so the client can cancel or confirm from the very start
        # rather than only once gate 1 has reported.
        yield {"event": "run.id", "data": json.dumps({"run_id": run_id})}
        try:
            while (item := await queue.get()) is not None:
                if await request.is_disconnected():
                    # The browser went away. Tell the worker, which stops at
                    # the next checkpoint; anything already sent to Claude is
                    # billed regardless and is settled normally.
                    state["cancelled"].set()
                    state["decided"].set()
                    break
                event, data = item
                yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}
        finally:
            state["cancelled"].set()
            state["decided"].set()

    return EventSourceResponse(stream())
