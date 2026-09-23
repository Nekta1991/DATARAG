-- ─────────────────────────────────────────────────────────────────────────────
-- DATARAG — spend ledger
--
-- The $5 budget ceiling used to live in data/spend_ledger.jsonl, read and
-- appended by the process that made the call. That works exactly as long as
-- one machine makes every call. On Vercel the function filesystem is ephemeral
-- and per-instance, so the file would start empty on every cold start and the
-- guard would wave through every request: the ceiling would stop existing
-- without anything failing loudly. Hence Postgres.
--
-- Two things move here, not one:
--
--   1. The running total, so any instance sees every other instance's spend.
--   2. The check-then-spend step itself. Reading a total and then deciding to
--      call Claude is a race; two concurrent runs both read $0.23, both decide
--      there is room, and both spend. The old code closed that race with a
--      threading.Lock, which means nothing across serverless instances.
--
-- So a paid run reserves its worst case BEFORE calling Claude and settles to
-- the real cost after. A crash between the two leaves the reservation standing
-- and over-counts, which is the safe direction for a budget - the same
-- decision as recording an unknown usage at worst case (2026-09-21).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS spend_ledger (
    id          bigserial PRIMARY KEY,

    ts          timestamptz NOT NULL DEFAULT now(),
    model       text        NOT NULL,
    question    text        NOT NULL,

    -- 'reserved' = worst case held before the call; counts against the budget.
    -- 'final'    = settled at the measured cost.
    -- 'released' = the call never happened; excluded from the total.
    state       text        NOT NULL DEFAULT 'final'
                            CHECK (state IN ('reserved', 'final', 'released')),

    status      text,                    -- answered | declined, once settled
    reason      text,
    usage       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    trace       jsonb       NOT NULL DEFAULT '[]'::jsonb,

    -- numeric, not float: a budget summed from binary floats drifts.
    cost_usd    numeric(12, 6) NOT NULL CHECK (cost_usd >= 0),

    -- provenance of the row itself, for reading the ledger later
    source      text        NOT NULL DEFAULT 'api'   -- api | cli | import
);

-- The guard sums the ledger on every paid run; keep it cheap and let the
-- planner skip released rows.
CREATE INDEX IF NOT EXISTS spend_ledger_billable_idx
    ON spend_ledger (state) INCLUDE (cost_usd)
    WHERE state <> 'released';

CREATE INDEX IF NOT EXISTS spend_ledger_ts_idx ON spend_ledger (ts DESC);

-- What the budget guard counts: reserved + final, never released.
CREATE OR REPLACE VIEW spend_total AS
    SELECT COALESCE(sum(cost_usd), 0)::numeric(12, 6) AS spent_usd,
           count(*) FILTER (WHERE state = 'final')    AS settled_runs,
           count(*) FILTER (WHERE state = 'reserved') AS open_reservations
      FROM spend_ledger
     WHERE state <> 'released';
