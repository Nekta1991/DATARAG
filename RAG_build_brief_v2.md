# RAG portfolio — build brief v2 (updated stack)

Handoff doc for the Claude Code session. Supersedes the earlier sprint brief's
architecture section — corpus and grounding requirements are unchanged.

## What changed from v1

- Chunking: hand-rolled fixed-length → **Docling** (HybridChunker, structure-aware)
- Vector store: Supabase → **Neon** (Postgres + pgvector) — no weekly auto-pause,
  better suited to an interview-demo schedule
- Agent framework: none (raw Python) → **Pydantic AI** — lighter than LangChain,
  doesn't abstract away chunking/embedding/retrieval decisions, only the
  tool-calling/orchestration layer
- Embeddings: **unchanged — still Voyage AI (`voyage-3.5`)**. The reference repo
  this stack is adapted from uses OpenAI embeddings; that part is NOT being
  adopted. Anthropic has no embeddings API, so Claude API remains
  generation-only, same as before.
- RAG type: baseline → **Agentic RAG with reranking**, built as one coherent
  system (see below), not three separate strategies

## Locked stack

| Component | Choice |
|---|---|
| Embeddings | Voyage AI, `voyage-3.5` |
| Chunking | Docling `HybridChunker` |
| Vector store | Neon (Postgres + pgvector), free tier |
| Agent framework | Pydantic AI |
| Generation | Claude API |
| Serving | FastAPI (unchanged — Pydantic AI doesn't replace this; wrap the agent in a `/query` endpoint) |

## Corpus (unchanged — verify links still resolve before downloading)

1. デジタル化・AI導入補助金2026 公募要領（複数者連携デジタル化・AI導入枠）
   https://mirasapo-plus.go.jp/wordpress/wp-content/uploads/2026/05/18164046/it2026_koubo_fukusu.pdf
2. スマート農業・農業支援サービス事業 公募要領（別記1）— maff.go.jp
3. スマート農業・農業支援サービス事業 公募要領（別記2）— maff.go.jp
4. 実施要領別記1（北海道内の複数総合振興局・振興局での事業実施要件）— maff.go.jp

Do not add the internal WebGen seminar report or any other company-confidential
document to this corpus — public official documents only.

## Architecture — one system, not three separate strategies

The three chosen strategies compose into a single pipeline rather than three
independent builds:

```
Ingestion:
  PDF → Docling HybridChunker → chunks → Voyage AI embeddings → Neon (pgvector)

Query time (Pydantic AI agent with two tools):
  ┌─ search_knowledge_base(query)
  │    1. vector search, retrieve ~20 candidates
  │    2. cross-encoder reranking (cross-encoder/ms-marco-MiniLM-L-6-v2 or similar)
  │    3. return top 5
  │
  └─ retrieve_full_document(title)
       plain SQL lookup — for when chunks alone lack context

  Agent decides which tool(s) to call based on the question.

  BEFORE generating: check top similarity/rerank score against a threshold.
  Below threshold → return "この情報からは判断できません", skip generation.
  This step is unconditional — it runs regardless of which tool(s) the agent
  used, and is not something either strategy replaces.
```

## Build order (cheapest/lowest-risk first)

1. **Ingestion**: Docling chunking → Voyage embeddings → Neon storage.
   Known-shape problem, lowest risk. Do this first.
2. **`search_knowledge_base` tool** with reranking. Moderate effort — first
   call downloads the cross-encoder model, budget a few extra minutes for
   that on first run.
3. **`retrieve_full_document` tool** — trivial once the ingestion schema
   exists (just needs a `documents` table alongside the `chunks` table).
4. **Agent wiring** with Pydantic AI: both tools registered, system prompt
   instructing the agent on when to use which, **plus the decline-to-answer
   threshold check preserved as a hard gate before generation** — this is
   not optional and not something the agent decides.
5. **Test** against the question set below.

## Test questions (prepare 8–10, must include the failure-path case)

- A straightforward eligibility question answerable from a single document
- A numeric/threshold question (補助率, 補助上限額)
- **The deliberate failure-path test**: the cross-program eligibility
  question (combining the DX subsidy and the smart-agriculture subsidy) —
  the system should decline rather than guess
- At least one question that should trigger `retrieve_full_document` (asks
  for something spanning most of a document, not a single fact)

## Interview narrative note

This build now directly demonstrates the distinction DIVE IN's own job
posting draws between plain RAG and "RAGやAIエージェント" work — being able
to show actual tool-selection behavior (not just retrieval) is a specific,
concrete answer to what they're evaluating candidates for.
