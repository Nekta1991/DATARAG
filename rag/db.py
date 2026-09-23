"""One shared connection pool for every query-time database call.

Measured 2026-09-23, and it was the dashboard's real latency: opening a fresh
connection to Neon costs ~1,130 ms (TLS handshake plus auth to a cloud
Postgres), while five queries on an already-open connection cost 722 ms in
total. Every call site used to open its own connection with
`with psycopg.connect(...)`, and a single paid query touches the database five
or six times - vector candidates, hydrating lexical-only hits, the admin
lookup, the budget reservation, the settle - so roughly five seconds per
question was spent on handshakes rather than work. That is what made
`VECTOR 1.79s` and `BM25 1.21s` appear in the console for operations that take
about 150 ms.

The pool is opened lazily and never closed: the process is either the API
server, which wants it for its whole life, or a short CLI run, where letting
it die with the interpreter is correct.

`min_size=1` keeps one connection warm so the first query of a session does not
pay the handshake either. `max_size` is small on purpose - the API serves one
query at a time, and Neon's free tier has a modest connection ceiling that a
greedy client-side pool would waste.
"""

from __future__ import annotations

import atexit
import os
from contextlib import contextmanager
from functools import lru_cache

from psycopg_pool import ConnectionPool

from rag import config  # noqa: F401  - loads .env

POOL_MIN = int(os.getenv("DB_POOL_MIN", "1"))
POOL_MAX = int(os.getenv("DB_POOL_MAX", "4"))


@lru_cache(maxsize=1)
def _pool() -> ConnectionPool:
    pool = _build_pool()
    # Close the pool while the interpreter can still join threads. Left to the
    # garbage collector, ConnectionPool.__del__ runs during finalization and
    # dies with PythonFinalizationError: "cannot join thread at interpreter
    # shutdown" - harmless, but it prints a traceback after every CLI command,
    # which trains people to ignore tracebacks.
    atexit.register(pool.close)
    return pool


def _build_pool() -> ConnectionPool:
    return ConnectionPool(
        os.environ["DATABASE_URL"],
        min_size=POOL_MIN,
        max_size=POOL_MAX,
        # Neon closes idle connections and can scale to zero, so a pooled
        # connection may be dead by the time it is handed out. Check it first:
        # a wasted round trip beats an OperationalError on a user's query.
        check=ConnectionPool.check_connection,
        open=True,
        name="datarag",
    )


@contextmanager
def connection():
    """A pooled connection. Mirrors `psycopg.connect()` as a context manager,
    so call sites change only by swapping the import.

    Note the difference from `with psycopg.connect(...)`: that closes - and so
    commits - on exit. The pool returns the connection instead, so anything
    writing must manage its own transaction, exactly as rag/ledger.py already
    does with `with conn.transaction():`.
    """
    with _pool().connection() as conn:
        yield conn


def stats() -> dict:
    """Pool counters, for the health endpoint and for proving reuse."""
    s = _pool().get_stats()
    return {k: s.get(k) for k in
            ("pool_size", "pool_available", "requests_waiting", "connections_num")}
