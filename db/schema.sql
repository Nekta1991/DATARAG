-- ─────────────────────────────────────────────────────────────────────────────
-- DATARAG — schema
--
-- Two tables. `documents` holds one row per source PDF plus its full text
-- (backing retrieve_full_document); `chunks` holds the embedded passages
-- (backing search_knowledge_base).
--
-- Provenance is NOT denormalized onto chunks. Retrieval goes through the
-- `chunk_sources` view, so `documents` stays the single source of truth for
-- which 枠 and which authority tier a passage came from.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS vector;

-- ── documents ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS documents (
    id             bigserial PRIMARY KEY,

    -- identity
    filename       text NOT NULL UNIQUE,        -- 01_dx_ai_hojokin_2026_koubo_fukusu.pdf
    title          text NOT NULL,               -- as printed on page 1
    source_url     text,
    sha256         text,

    -- classification. program + waku together answer "which 枠 is this?",
    -- the question a naive RAG over this corpus gets wrong.
    program        text NOT NULL,               -- デジタル化・AI導入補助金2026 / スマート農業…
    fiscal_year    smallint,
    waku           text,                        -- 通常枠 / インボイス枠（電子取引類型）/ … NULL if n/a
    doc_type       text NOT NULL
                   CHECK (doc_type IN ('公募要領', '交付規程', '加点項目一覧')),

    -- 交付規程 outranks 公募要領 when they conflict. Generated, not hand-set,
    -- so it can never drift out of sync with doc_type. Lower = more binding.
    authority_rank smallint GENERATED ALWAYS AS (
                       (CASE doc_type
                            WHEN '交付規程'     THEN 1
                            WHEN '公募要領'     THEN 2
                            WHEN '加点項目一覧' THEN 3
                        END)::smallint
                   ) STORED,

    -- payload
    page_count     integer,
    char_count     integer,
    full_text      text,                        -- for retrieve_full_document

    ingested_at    timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN documents.authority_rank IS
    '1=交付規程 (binding), 2=公募要領 (guidance), 3=加点項目一覧. Lower wins on conflict.';
COMMENT ON COLUMN documents.waku IS
    'Funding frame. Literal overlap between the five 公募要領 is 0.5-5.3% Jaccard '
    'but semantic overlap is high, so answers must state which 枠 they came from.';

-- ── chunks ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chunks (
    id           bigserial PRIMARY KEY,
    document_id  bigint NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index  integer NOT NULL,            -- position within the document
    content      text NOT NULL,
    heading      text,                        -- Docling section path, e.g. 第2章 > 2-1 補助対象者
    page_no      integer,
    token_count  integer,                     -- Voyage tokens, for batch sizing
    embedding    vector(1024),                -- voyage-3.5; must match EMBEDDING_DIM
    created_at   timestamptz NOT NULL DEFAULT now(),

    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS chunks_document_id_idx ON chunks (document_id);

-- Cosine distance: Voyage returns normalized vectors.
-- HNSW is approximate. At this corpus size (~1.5k chunks) a sequential scan
-- would be exact and fast enough; the index is here because the query shape
-- should be the one that survives a corpus 100x this size.
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx
    ON chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ── retrieval view ──────────────────────────────────────────────────────────
-- What search_knowledge_base selects from. Every retrieved passage arrives
-- already carrying its 枠 and its authority tier, so provenance cannot be
-- dropped on the way to the prompt by forgetting a join.

CREATE OR REPLACE VIEW chunk_sources AS
SELECT
    c.id,
    c.document_id,
    c.chunk_index,
    c.content,
    c.heading,
    c.page_no,
    c.token_count,
    c.embedding,
    d.filename,
    d.title,
    d.program,
    d.waku,
    d.doc_type,
    d.authority_rank,
    d.fiscal_year,
    -- single human-readable provenance label for the citation line
    d.program
        || COALESCE(' ' || d.waku, '')
        || ' ' || d.doc_type       AS source_doc
FROM chunks c
JOIN documents d ON d.id = c.document_id;
