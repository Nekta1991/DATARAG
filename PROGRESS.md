# Progress log

## ▶ NEXT SESSION — START HERE (written 2026-09-21)

The dated session logs below are history. This section is the handoff: what exists,
what is waiting on the user, and the ordered steps with costs and done-criteria.

### Snapshot

| | State |
|---|---|
| **API spend** | **$0.0542 / $5.00** — ledger `data/spend_ledger.jsonl` (reconciled with the Anthropic console) |
| Corpus | 14 docs / 1,025 chunks in Neon `production` (`br-icy-butterfly-b3grfw5x`) |
| Retrieval | hybrid (vector 20 ∪ char-bigram BM25 10) → bge-reranker-v2-m3 on CUDA → top 5 |
| Agent | `rag/agent.py`, `claude-sonnet-5`, effort low, two-layer decline gate, hard limits, ledger |
| API | `rag/api.py` (FastAPI + SSE), admin-only via Neon Auth JWT + `role=admin` |
| Frontend | `web/` Next.js 16.3.5, sign-in + dashboard; stub ($0) default |
| Neon Auth | enabled; **0 users** — admin not created yet |
| Git | only `README.md` committed; **do not push** until the user says so |
| Tests | `scripts/test_gates.py` 7/7, `scripts/test_api.py` 10/10 (both free) |
| Docs | `MANUAL.md` + web mirror https://claude.ai/artifact/J1U69gY5YUBYznPztFrYY2 (rev 47), `docs/validation_questions.md`, `docs/dashboard_build_brief.md` |
| Design mock | https://claude.ai/artifact/S3ApZuuSALLVkCoBoBLoHW (official-site palette, 「非公式デモ」) |

### Waiting on the user

- [ ] **Create the admin account.** Two ways; never ask for the password:
      - Web (added 2026-09-22): `/auth/sign-in` → 「アカウントを新規登録」. It creates an ordinary account and
        signs in immediately (no email verification: `require_email_verification: false`). Then set
        role=admin, via MCP `update_auth_user_role` or `create_admin.py <email>` (it only sets the role if the account exists).
      - Terminal: `.venv/Scripts/python.exe scripts/create_admin.py <admin email>` (hidden prompt).

### Steps, in order

**1. Start both servers ($0)**
```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m uvicorn rag.api:app --host 127.0.0.1 --port 8000
cd web && npm run build && npx next start -p 3000
```
Open **http://localhost:3000** (not 127.0.0.1 — Neon Auth → `INVALID_ORIGIN` 403).
Done when: `/api/health` shows `"warm": true`.

**2. Verify sign-in end to end ($0)**
Sign in, reload (session persists), sign out, protected `/` redirects again.
Header shows corpus, model and spend from `/api/status`. A non-admin account gets
「管理者権限がありません」. Nothing here has been exercised with a real account yet.

**3. Stub run from the dashboard ($0)**
北海道 question, 本番モード off. Done when the console shows RUN → EMBED → VECTOR →
BM25 → RERANK → GATE 1 → AGENT → GATE 2 → DECLINE/ANSWER → COST, and the candidates
table fills (gold chunk 268 at rank 1, score 0.8907). Then 「明日の東京の天気」 stops at GATE 1.

**4. First paid run from the dashboard (~$0.03, cap $0.19) — ANNOUNCE COST AND CONFIRM FIRST**
北海道 question, 本番モード on. Checks: answer + verified citation render; COST line
matches the ledger; the one-citation-per-claim prompt left **no uncited paragraph**
(the 2026-09-21 answer had one).

**5. Third request — DONE 2026-09-22 ($0), confirm on the next paid run**
Cause (from token math): request 3 added ~1.4k cache-write tokens, too small for a
5-passage search (~2.6k), so it was a re-sent `final_result` after a validation retry.
pydantic-ai's output tool was non-strict by default. Fix: `ToolOutput(Answer, strict=True)`;
Anthropic accepted the strict schema (`count_tokens`, free: 2,596 tokens for request 1).
The ledger and CLI now log a per-request `trace` (tools called + any retry text), so the
next paid run proves it: an answered run should show 2 requests.

**6. Step 5 — validation suite (~$0.30) — ANNOUNCE COST AND CONFIRM FIRST**
- `scripts/run_validation.py` is BUILT. Free stub by default; `--paid` prints the estimate and
  sends nothing; `--paid --yes` runs. `--only 1,8` runs a subset; `--regrade <run.json>` re-scores
  a saved run for $0. Raw runs go to `data/validation_runs/`, and the paid report to `docs/validation_results.md`.
- Stub run 2026-09-22: Q7 (a whole-document structure question) scored 0.254 and was declined
  at gate 1. **Fixed (user chose the recommended option):** gate 1 also passes a question that names a corpus
  document (枠 + doc type): `named_documents()` in `rag/documents.py`. The `gate1` event has
  `via` + `named_docs`. `test_gates.py` gained case 8 (8/8), and `test_api.py` still passes 10/10.
- Also: Q1 as phrased in the suite (…要件は何ですか？) tops at 0.596, vs 0.8907 for the short form.
- **Q6: the user asked to validate both rules.** Free analysis is in `docs/validation_questions.md` Q6
  (取消し article identical in 4 交付規程, different in 複数者連携, which is not retrieved). The runner
  grades rule A and rule B on every run. Rule B's prompt line is behind `CITE_SHARED_RULES=1`
  (`--shared-rules`), off by default. Paid plan, pending confirmation: Q6 default + Q6 rule B, then
  Q2/Q3 under rule B to see whether the line makes the model mix 枠 figures.
- **PAID RUN 2026-09-22: 8/10, $0.1794** (ledger $0.2336). Report: `docs/validation_results.md`, raw:
  `data/validation_runs/20260922-224505_paid.json`. **Every answered run used 2 requests** (strict output confirmed).
  Q7 passed via the named-document gate ($0.0616). Q9/Q10 declined in 1 request.
  - Q3 FAIL `unverified_quote`: the answer was right, but the model stitched table cells into a quote
    (「補助額 ５万円～… 補助額 １５０万円～…」), which does not exist in the markdown table. The gate did its
    job; the fix belongs in the prompt (copy one table row verbatim) and/or a table-aware quote check.
  - Q6 FAIL `no_citation`: answerable=true with 0 citations (1,398 out tokens). The cause was not visible
    because declined drafts weren't kept. **Now kept:** `QueryResult.draft` (in run files, never shown to users).
  - Still to run (approved plan): Q6 under rule B, then Q2/Q3 under rule B (~$0.11).
- Afterwards: count uncited sentences (target 0); record in MANUAL.md + web mirror.

**Dashboard contract** — `docs/dashboard_build_brief.md` §2 was synced to the code on 2026-09-22
(field names, event order, request/status shapes). Preview fixture `answered-mock.sse` had
blank lines inside events (every event was parsed as an unnamed `message`), and is now fixed.

**7. Round-1 demo: Vercel UI + this PC via tunnel ($0 infra)**
- **DONE 2026-09-22:** pushed with the user's go-ahead (`bac3fc7` .gitignore, `5dd084e` code, after a secret scan).
  Preview route had already been deleted.
- **DONE:** same-origin proxy `web/app/rag/api/[...path]/route.ts` (health/status/query only)
  → `RAG_API_URL`; `page.tsx` passes `apiUrl="/rag"`. No CORS change or `NEXT_PUBLIC_API_URL`
  needed. The proxy also sends `ngrok-skip-browser-warning` (ngrok's warning page).
- **DONE:** Vercel project `web-gen-ai-teleapo/datarag` linked from `web/` (`.vercel/` gitignored).
- **LIVE 2026-09-22: https://datarag-rho.vercel.app** (CLI deploy from `web/`). Env (production): `NEON_AUTH_BASE_URL`,
  a new `NEON_AUTH_COOKIE_SECRET` (sensitive, not the local one), and `RAG_API_URL=https://bonsai-halogen-reprocess.ngrok-free.dev`
  (ngrok's free static dev domain; the same on every `ngrok http 8000`). Neon Auth trusted domain added.
  Smoke: sign-in 200 · `/` 307 · `/rag/api/health` 200 via tunnel · status 401 without a token · unknown 404.
- To serve: this PC runs uvicorn (:8000) + `ngrok http 8000`. The site is down when either is.
- Vercel build fixes: `web/package-lock.json` re-synced (npm ci rejected it). Figtree moved to
  `next/font/local` (`web/app/fonts/Figtree-latin.woff2`): on Vercel, Turbopack failed on the
  next/font/google Figtree URLs ("queries have exactly one entry"), and only for that font.
- Git auto-deploy: connect the repo in Vercel with root directory `web`.

**8. Round 2: all on Vercel (option A)**
Replace local bge with Voyage's rerank API; recalibrate the threshold and re-run
both free test scripts; move the spend ledger to a Neon table (serverless filesystem
is ephemeral); FastAPI as a Vercel Python function. Check the Voyage free allowance
covers rerank models, and that 3 RPM holds (embed + rerank = 2 requests per query).

### Open decisions / housekeeping

- `CLAUDE.md` cost table still says $0.011/query; measured ~$0.03 answered, ~$0.05
  with a full document. It's the user's file — offer the update, don't make it silently.
- Neon Auth sign-up is open (`disableSignUp: false`). Harmless (API requires role=admin);
  could be closed in the Neon console.
- Password-reset links need custom SMTP (not configured).
- At project end: `neon api-keys revoke 3349370` (account-wide key minted by `neon mcp`).

---

## Session 2026-09-19 — environment & credentials

**Status: all infrastructure verified. No application code written yet.**
**Total API spend to date: $0.00**

### Done

**Corpus** (pre-existing, verified this session)
- 14 official public PDFs in `data/raw/`, 372 pages, **362,928 characters**
- All carry real text layers — no OCR needed. See `data/raw/MANIFEST.md`.

**Voyage AI — verified working**
- `voyage-3.5`, 1024 dims confirmed live (HTTP 200)
- **Measured: Japanese runs 1.20 chars/token** on this corpus → full corpus
  ≈ **302k tokens**, which costs **$0** against the 200M free allowance
  (the API's own warning confirms "Voyage series 3" free tokens apply)
- ⚠️ **No payment method on account** → throttled to **3 RPM / 10K TPM**.
  Full-corpus ingest will take **~30 minutes** under this limit.
  `VOYAGE_BATCH_SIZE=6` is set for this ceiling; raise to 64 after adding a card.
  User decision: defer billing until the throttle actually blocks work.

**Neon Postgres — verified working**
- Project `lingering-fire-23301886`, branch `production` (`br-icy-butterfly-b3grfw5x`)
- **PostgreSQL 18.6**, db `neondb`, role `neondb_owner`
- **`vector` 0.8.6 available** (not yet `CREATE EXTENSION`'d), `pg_trgm` 1.6 also available
- **Branch is empty** — zero tables
- CLI 5.0.0 installed, linked, `neon.ts` = `defineConfig({})`, `neon deploy` clean
- `neon link` auto-wrote `DATABASE_URL` / `DATABASE_URL_UNPOOLED` / `NEON_BRANCH`
  into `.env` — **do not hand-edit those three lines**, `neon deploy` rewrites them

**Anthropic — verified working**
- First key was invalid (revoked); user regenerated, now validates HTTP 200
- 11 models available. **`claude-haiku-4-5` bare alias is NOT served** —
  must use `claude-haiku-4-5-20251001`
- `ANTHROPIC_MODEL=claude-sonnet-5` ($2/$10 per MTok, ~$0.011/query)

**Project files created**
- `.env` (all credentials live), `.env.example`, `.gitignore`
- `CLAUDE.md` — **$5 budget rule**: announce cost before any paid action
- Neon skills (`.claude/`), `neon.ts`, `package.json`, `skills-lock.json`

### Environment

- Python **3.14.2** venv at `.venv/`. All stack packages confirmed to have
  cp314 wheels, **including `torch 2.14.0`** — the main platform risk, cleared.
- Installed so far: `voyageai`, `python-dotenv`, `psycopg[binary]`, `requests`,
  `pymupdf`, `pdfplumber`
- **Not yet installed**: `docling`, `sentence-transformers`, `torch`,
  `pydantic-ai`, `fastapi`

### Next session — start here

Build order from `RAG_build_brief_v2.md`. **Steps 1–3 cost $0.**

1. **Ingestion**: Docling `HybridChunker` → Voyage → pgvector. `CREATE EXTENSION
   vector`, `documents` + `chunks` tables, `vector(1024)` column.
   ⚠️ First run downloads Docling layout models (~0.5–2 GB) into `.cache/`.
   ⚠️ **Build against ONE pdf first**, verify the round-trip, then run all 14 —
   don't spend 30 throttled minutes on an unproven pipeline.
2. **`search_knowledge_base`**: top-20 vector → cross-encoder rerank → top-5.
3. **`retrieve_full_document`**: plain SQL by title.
4. **Agent wiring** (Pydantic AI) + the hard decline gate. ← first paid step
5. **Test suite**, ~$0.11 per full run.

### Open decisions

- **Reranker is English-trained.** `cross-encoder/ms-marco-MiniLM-L-6-v2` (brief's
  default) vs. a multilingual model on a 100% Japanese corpus. Multilingual
  alternatives are pre-written as commented lines in `.env`. Swap if step 2
  reranking looks poor — one-line change, no code edit.
- **`RERANK_SCORE_THRESHOLD=0.0` is a placeholder, not a real value.**
  Cross-encoder scores are unbounded logits, not 0–1. Calibrate at step 5 using
  the cross-program question as the known-negative anchor.
- **Voyage billing** — deferred by user until the throttle bites.

### Constraints carried forward (from `data/raw/MANIFEST.md`)

- **交付規程 governs over 公募要領** when they conflict — must be in the
  generation system prompt, or answers cite whichever chunk retrieved first.
- **`source_doc` must carry the 枠 name.** Literal overlap between the five
  公募要領 is low (0.5–5.3% Jaccard) but semantic overlap is high.
- **Numeric test questions must target doc #1 or #4.** Docs #2/#3 contain no
  補助率/補助上限額 figures — a numeric question aimed there is an *unintended*
  decline that would contaminate the failure-path test.

### Cleanup when project ends

`neon mcp -y` minted an **account-wide** API key (reaches every org):
```
neon api-keys revoke 3349370
```

---

## Session 2026-09-20 — repo, schema, environment

**Total API spend to date: $0.00**

### Done

**Git / GitHub**
- `git init` (branch `main`), remote → `github.com/Nekta1991/DATARAG` (**public**)
- **Only `README.md` is pushed.** Everything else is local and untracked by
  deliberate choice — corpus PDFs, `PROGRESS.md`, `CLAUDE.md`, the brief,
  `.env.example`, `.gitignore`, `.claude/skills/`, Node files.
- ⚠️ `.gitignore` is itself untracked, so a fresh clone has no ignore rules.
  Commit it alongside whatever goes public next.
- README is Japanese; references `MANIFEST.md`, `PROGRESS.md` and a
  `requirements.txt` that are all dead links on GitHub until pushed.

**Schema — applied and verified on Neon `production`**
- `db/schema.sql` (idempotent), applied by `scripts/apply_schema.py`
- `vector` 0.8.6 extension created
- `documents` — provenance + `full_text` for `retrieve_full_document`.
  `doc_type` is CHECK-constrained to 公募要領 / 交付規程 / 加点項目一覧.
  **`authority_rank` is a GENERATED STORED column** off `doc_type`
  (交付規程=1, 公募要領=2, 加点項目一覧=3) — the 交付規程-governs rule is
  enforced by the schema and cannot drift from `doc_type` by hand-editing.
- `chunks` — `vector(1024)`, HNSW cosine index (m=16, ef_construction=64),
  `UNIQUE (document_id, chunk_index)`, FK cascade from `documents`.
- **`chunk_sources` view** joins the two and emits a single `source_doc`
  label (`program + waku + doc_type`). Provenance is NOT denormalized onto
  `chunks`; the view is what retrieval selects from, so a forgotten join
  cannot drop the 枠 name on the way to the prompt.
- Verified with rolled-back test rows: generated ranks correct, cosine
  round-trip 0.0, all four chunk indexes present. **Tables are empty (0/0).**

**Environment — stack complete, `pip check` clean**
- `requirements.txt` written: 15 pinned direct deps + the cu130 extra index.
- Installed this session: `docling 2.129.0`, `sentence-transformers 6.1.0`,
  `transformers 5.17.0`, `pydantic-ai 2.46.0`, `fastapi 0.141.1`,
  `uvicorn 0.53.0`, `torch 2.14.0+cu130`, `torchvision 0.29.0+cu130`.
  `anthropic 1.7.0` arrived as a pydantic-ai dep.
- All key classes (`DocumentConverter`, `HybridChunker`, `CrossEncoder`,
  `Agent`) import on cp314. **Reranker will run on CUDA.**

**GPU — the wheel-index trap, solved**
- GPU is an **RTX 5060 (Blackwell, sm_120, 8 GB)**, driver 610.88.
- PyPI's default Windows torch wheel is **CPU-only** (`2.14.0+cpu`).
  The index must be chosen by GPU architecture:
  - `cu126` — has 2.14.0 but predates Blackwell, would not run here
  - `cu128` — tops out at torch 2.11.0
  - `cu129` — tops out at torch 2.9.0, no cp314 wheel
  - **`cu130` — 2.14.0+cu130** ← only index with both cp314 and sm_120
- ⚠️ **torch and torchvision must come from the same index.** Installing
  `sentence-transformers` pulled `torchvision 0.29.0+cpu` from PyPI alongside
  the cu130 torch. It imports fine and then dies at runtime:
  `Could not run 'torchvision::nms' with arguments from the 'CUDA' backend`.
  Docling's layout model uses torchvision ops, so this would have surfaced
  mid-ingest. Fixed by reinstalling `torchvision==0.29.0+cu130`; verified
  with a live CUDA NMS call.

### Gotchas worth not rediscovering

- **`load_dotenv()` searches from the script's location.** A helper run out of
  `%TEMP%` silently found no `.env`. Scripts pass `ROOT / ".env"` explicitly.
- **Console is cp932.** Japanese printed to stdout mojibakes even though the
  data is fine. Set `PYTHONIOENCODING=utf-8` for ingestion logging — the DB
  round-trip is verified clean, so any garbling seen is display-only.
- **`keys.txt` is off limits** — do not read, print, or redact it. Read key
  *names* from `.env`; verify keys by calling the provider, not by looking.

### Next

1. **Single-PDF ingest round trip** before all 14. Full ingest is ~30 throttled
   minutes at 3 RPM — do not spend it on an unproven pipeline.
2. `search_knowledge_base` with reranking; switch `RERANKER_MODEL` to
   `BAAI/bge-reranker-v2-m3` before calibrating anything (English MiniLM on a
   100% Japanese corpus makes the rerank stage a no-op, and bge's sigmoid
   output makes `RERANK_SCORE_THRESHOLD` an interpretable number).
3. `retrieve_full_document` — trivial, `documents.full_text` already exists.
4. Agent wiring ← **first paid step**.

### Note for the ingest step

`HybridChunker` takes a tokenizer. The **voyage-3.5 tokenizer is already
cached** in `.cache/huggingface/` — point the chunker at it so `CHUNK_MAX_TOKENS`
counts the same tokens Voyage bills, rather than approximating with a
different model's vocabulary.

Docling's first `DocumentConverter.convert()` downloads layout models
(~0.5-2 GB) into `.cache/`. Budget the time on the first run, not the 15th.

---

## Session 2026-09-20 (cont.) — single-PDF ingest round trip

**Total API spend to date: $0.00.** Voyage tokens used: ~19k of 200M free.

### Pipeline works end to end

`scripts/ingest.py` — Docling -> HybridChunker -> Voyage -> pgvector.
Idempotent per filename (DELETE cascades, then rewrite), so a failed run is
just re-run. `--dry-run` chunks without embedding or writing.

Proved on `04_jisshi_youryou_bekki1_hokkaido.pdf` (10pp, smallest doc):
**32 chunks, 9,075 tokens, all 32 embedded at 1024 dims, stored and queried
back with correct provenance through `chunk_sources`.** Re-ran it to confirm
the overwrite path leaves 1 document / 32 chunks, not duplicates.

`data/corpus_metadata.json` now holds all 14 documents' program / waku /
doc_type / title / source_url, derived from MANIFEST.md.

### Two fixes found by actually looking at the output

**OCR was on and doing nothing.** RapidOCR downloaded models and ran on every
page: **68.3s -> 4.3s per document with `do_ocr=False`**, byte-identical
chunking. Every PDF here has a text layer (MANIFEST confirms), so this was
~40 wasted minutes across 372 pages. Flip back on only if a scanned doc joins
the corpus.

**Docling's default table serializer degenerates on these tables.** It emits
one `row, column = value` triplet per cell; on the audit-criteria tables with
merged headers that produced
`① 事業実施主体の適格性, 審査項目 = ① 事業実施主体の適格性` repeated per
column - 457 tokens of near-noise. Switched to `MarkdownTableSerializer` via
a `ChunkingSerializerProvider`: **35 -> 32 chunks and 10,271 -> 9,075 tokens
for identical content (12% less), with the grid intact.** This matters
specifically because 補助率 and 補助上限額 live in tables.

### Retrieval baseline — measured, and it justifies the architecture

Same gold chunk (the 北海道内の複数総合振興局 clause, 第３ 事業内容), two
phrasings of the same question:

| Query | Gold rank | Gold sim | Top-1 sim |
|---|---|---|---|
| 複数の都道府県にわたり事業を実施する事業実施主体とは | **1** / 32 | 0.5537 | 0.5537 |
| 北海道内で事業を実施する場合の要件 | **13** / 32 | 0.4116 | 0.5309 |

Dense retrieval matches a chunk's *dominant topic*, not a parenthetical clause
inside it. The 北海道 rule is a sub-clause of a 事業内容 paragraph, so a
question phrased around 北海道 ranks it 13th behind generic 要件 sections.

**Consequences, concretely:**
- `RETRIEVAL_TOP_K=20` is vindicated - it catches rank 13. A top-5 or top-10
  vector stage would drop the correct answer before reranking ever sees it.
  **Do not lower TOP_K.**
- This is the clearest argument yet for the cross-encoder stage: the gold chunk
  IS in the candidate set, just badly ordered. That is exactly the failure a
  reranker repairs.
- Worth considering: `pg_trgm` is already available on the branch. A hybrid
  lexical + vector candidate stage would rank an exact term like 北海道 at the
  top trivially. Not in the brief, but it is a one-index change and the
  measurement above is the argument for it.

### Next

1. `search_knowledge_base` — top-20 vector -> cross-encoder -> top-5.
   Switch `RERANKER_MODEL` to `BAAI/bge-reranker-v2-m3` first. Re-run the
   北海道 query as the regression test: **gold must move from 13 to top-5.**
2. Full ingest of the remaining 13 documents (~30 min at the 3 RPM throttle;
   conversion is now ~4s/doc rather than ~70s).
3. `retrieve_full_document` — `documents.full_text` is already populated.
4. Agent wiring <- first paid step.

---

## Living manual

`MANUAL.md` is the standing explanation of the project: purpose, stack choices
with rejected alternatives, DB design, RAG techniques, and a dated decision log
with reasons. EN + JP throughout, Russian summary in section 8.

Web version (same content, editable and commentable):
https://claude.ai/artifact/J1U69gY5YUBYznPztFrYY2

**Rule: update `MANUAL.md` whenever a decision is made or reversed, and mirror
it into the web doc.** PROGRESS.md records what happened; MANUAL.md records why,
in a form that can be defended in an interview. A decision not written down
there is a decision that cannot be defended later.

---

## Session 2026-09-21 — full ingest, both tools, hybrid retrieval

**Total API spend to date: $0.00.** Anthropic calls made: `count_tokens` only (free).
Voyage: 292,840 ingest tokens + a handful of query embeddings, all free tier.

### Done

**Full corpus ingested** — 14 docs / 1,025 chunks, none missing an embedding, no duplicates,
68 min under the 3 RPM throttle, zero retries. Doc #04 reproduced exactly (32 / 9,075).
39 chunks are 513–534 tokens: all tables, overshoot = the heading `contextualize()` adds
after the chunker's check. Accepted; nothing truncates downstream.

**Reranker switched to `BAAI/bge-reranker-v2-m3`** (`.env` + `.env.example`), 2.29 GB in
`.cache/`. Runs on CUDA: ~0.7–1.0 s per 20–30 pairs, 2.5 GiB VRAM. **Cold load 113 s —
FastAPI must load it at startup.** Sigmoid passed explicitly, so scores are 0–1.

**`rag/` package** (query-time code; `scripts/` stays ingest-time):
- `rag/config.py` — loads `.env`, resolves the relative `HF_HOME` against the project root.
- `rag/retrieval.py` — `search_knowledge_base`: (vector top 20 ∪ char-bigram BM25 top 10)
  → bge rerank → top 5. CLI: `python -m rag.retrieval "<q>" --candidates`.
- `rag/documents.py` — `retrieve_full_document(doc_id)` + `document_catalog()`. By catalog
  ID, capped at `FULLDOC_MAX_TOKENS=20000`. CLI: `python -m rag.documents [doc_id]`.

**Hybrid retrieval was forced by a failed regression.** On the full corpus the 北海道 gold
chunk (id 268) fell to vector rank **211** / 1,025 — TOP_K=100 still misses it. Only 4
chunks contain 北海道. With BM25 in the pool: final rank **1**, score 0.8907, next 0.09.
The other phrasing is unchanged (rank 1, 0.9994).

**Full-document cap, verified with real `count_tokens`:** largest payload 18,653 / 20,000.
Uncapped, doc 05 is 64,704 tokens (~$0.13 input per call at Sonnet).

### Gotchas worth not rediscovering

- **Claude/Voyage token ratio is 1.215–1.364** on this corpus, measured text-against-same-
  text. Do not divide by the sum of chunk token counts — chunks repeat headings. `CLAUDE_PER_VOYAGE_TOKEN=1.40`.
- **`voyage-3.5` has no `config.json`.** Offline `AutoTokenizer.from_pretrained(repo_id,
  local_files_only=True)` fails. Load via `snapshot_download(..., local_files_only=True)` → dir.
- **Large Python patches through a bash heredoc broke on quoting** — write the file instead.
- **Docs-connector cell replace inherits the old cell's formatting** — replace cells
  `"as":"markdown"` to set formatting explicitly.

### Open — needs a decision before step 4 calibration

- **The brief's failure-path question is answerable.** 13 of 14 docs prohibit duplicate
  subsidies (DX 交付規程 第10条; smart-agri 第７ 重複申請の制限). "Can the same machine be
  claimed under both?" → grounded "no". Top rerank 0.81. Need a genuinely unanswerable
  question as the known-negative anchor for `RERANK_SCORE_THRESHOLD`.
- **Early threshold signal:** true miss ≤ 0.09; true hits 0.89–0.9994.
- **枠 can't be separated by ranking on numeric questions:** every 公募要領 has the same
  類型比較表, all scoring ~0.998. The generation prompt must pick the 枠 via `source_doc`.
- **Cost estimate in CLAUDE.md omits `retrieve_full_document`:** a question using it costs
  ~$0.04–0.05 (≈4× an ordinary query).

### Next

1. Choose a replacement failure-path question (free to verify: retrieval + rerank only).
2. Step 4 — Pydantic AI agent, both tools, hard decline gate. **First paid step.**
3. Commit: `.gitignore` first, then code. Still only `README.md` in git.

---

## Session 2026-09-21 (cont.) — step 4: agent + two-layer decline gate

**Total API spend: $0.0542** (ledger `data/spend_ledger.jsonl`; console showed $4.98 after run 1).

### Done

**Decline design changed from the brief — measured, not assumed.** Rerank scores measure
topicality, not answerability: off-topic ≤ 0.001, retrieval miss 0.09, on-topic
*unanswerable* 0.55–0.93 (ものづくり補助金 / 2027年度 / IT導入支援事業者), answerable
0.89–0.9994. No threshold separates the last two. Now two gates, both in code:
1. `RERANK_SCORE_THRESHOLD=0.3` before any model call ($0).
2. Structured `Answer` with verbatim quotes + `source_doc`; code checks each quote is in a
   passage a tool returned this run, under that `source_doc` (NFKC + whitespace-normalized).

**`rag/agent.py`** — Pydantic AI, `claude-sonnet-5`, `GENERATION_EFFORT=low`, instructions /
tools / messages prompt-cached. Hard limits per question: 4 requests, 60k in, 4k out
(worst case $0.19). Refuses a run that could push the ledger past `BUDGET_USD=5.00`.
CLI: `python -m rag.agent "<q>"` (PAID) · `--stub` (free) · `--ledger`.

**`scripts/test_gates.py`** — free, FunctionModel plays Claude against live retrieval.
7/7: off-topic → gate 1 (model never called); verbatim quote → answered; one char
changed → declined; real quote under wrong 枠 → declined; answerable=false; no citation;
full-document path.

**Paid runs**

| Question | Result | Req | In (cache read) | Out | Cost |
|---|---|---|---|---|---|
| 北海道 (run 1) | crashed after billing — usage lost | ? | ? | ? | $0.02 (console) |
| 北海道 (retry) | answered, quote verified | 3 | 14,788 (7,930) | 1,360 | $0.0323 |
| 2027年度 schedule | declined, no search | 1 | 2,534 (2,309) | 83 | $0.0018 |

### Gotchas

- **`AgentRunResult.usage` is a property** in pydantic-ai 2.46, not a method. Run 1 died on
  it *after* billing. Ledger now writes immediately after the API returns; unknown usage →
  worst case. Stub tests never touched that line — paid-only code needs a stub check.
- **Bash tool mangles backslashes in heredocs/sed** (Python `" \\n"` never matched).
  Use the Edit tool for anything containing backslashes.
- `PYDANTIC_AI_NO_BANNER=1` is set in `rag/config.py`.

### Decisions (user-approved)

- Accept declines without a search (2027 case: $0.0018 vs ~$0.03 forced).
- Prompt: one citation per claim, nothing uncited (run 1 answer had an uncited paragraph).
- Cross-programme question → answerable set (answer: no, 交付規程 第10条).

### Next

1. **Step 5 test suite** — 8–10 questions, ~$0.35–0.45; announce cost before running.
   Include: 2027 (decline), off-topic (gate 1, $0), cross-programme (answer: no), numeric
   補助率 on doc #01 or #04, a full-document question on a 交付規程, 北海道.
2. Explain the 3rd request in the answered run (expected 2).
3. FastAPI `/query`; load the reranker at startup (113 s cold).
4. Commit: `.gitignore` first. Still only `README.md` in git.

---

## Session 2026-09-21 (cont.) — validation set, dashboard, auth

**Total API spend: $0.0542** (unchanged — everything below ran free or stubbed).

### Done

- `docs/validation_questions.md` — 10 questions with gold facts pulled from the corpus by
  SQL; est. **$0.28–0.36** per full run. Not run.
- **Dashboard mock** (Claude Design canvas): https://claude.ai/artifact/S3ApZuuSALLVkCoBoBLoHW
  — restyled with it-shien.smrj.go.jp's palette (from its CSS), no logos, 「非公式デモ」.
- `docs/dashboard_build_brief.md` — Claude Design prompt, SSE event contract, hosting,
  visual tokens (contrast-checked), auth, how to run.
- **Hosting decided:** round 1 = API on this PC via tunnel (GPU bge); then all-Vercel with
  an API reranker (needs threshold recalibration).
- **Neon Auth enabled** (`auth: true` in `neon.ts`, `neon deploy`). `.env` gained
  `NEON_AUTH_BASE_URL` / `NEON_AUTH_JWKS_URL`; all other lines verified unchanged.
- **`rag/api.py`** — FastAPI: `/api/health`, `/api/status`, `POST /api/query` (SSE). JWT
  verified against JWKS, then `role=admin` required in `neon_auth."user"`. One query at a
  time. Reranker + BM25 warmed at startup (13.6–16.8 s cached).
- `emit(event, data)` threaded through `rag/agent.py` + `rag/retrieval.py`.
- **`web/`** — Next.js 16.3.5 + `@neondatabase/auth` 0.5.0-beta: sign-in page, `proxy.ts`
  guard, dashboard streaming the real console. Stub ($0) is the default; paid needs a checkbox.
  Types, lint and `next build` clean.
- `scripts/create_admin.py` — run by the user; hidden password prompt; sets role=admin in DB.

### Tests (all free)

- `scripts/test_gates.py` 7/7 · `scripts/test_api.py` 10/10.
- Live smoke: no session → 307 to sign-in; wrong password → 401; API without/with junk
  token → 401; CORS allows only localhost:3000.

### Gotchas

- **Open http://localhost:3000, not 127.0.0.1** — Neon Auth `allow_localhost` covers the
  hostname `localhost` only; 127.0.0.1 → `INVALID_ORIGIN` 403.
- Password-reset *links* need custom SMTP (not set up) — hence the admin script.
- Next 16: `proxy.ts` replaces middleware; `create-next-app` writes `web/AGENTS.md`.

### Next

1. **User:** run `scripts/create_admin.py <email>`, then sign in and try stub mode.
2. First paid run from the dashboard (~$0.03) once sign-in works.
3. Step 5 suite (~$0.28–0.36) — confirm before running.
4. Tunnel + Vercel for the round-1 demo; then commit/push (`.gitignore` first) — user said
   "not now, complete the tasks first".

---

## Session 2026-09-22 — Organic redesign (Claude Design → web/)

**Total API spend: $0.0542** (unchanged — nothing paid ran).

### Done

- **Design:** Claude Design project "DATARAG Query Console Design" on the **Organic** design system,
  used natively (terracotta primary, sage = verified, separate red for declines, warm-dark console).
  8 artboards: idle, running, answered, gate-1 decline, gate-2 decline, error, budget guard, 390 mobile,
  plus a console contrast sheet. Exported as a project archive and ported by hand.
- **`web/` restyled:** `globals.css` = Organic tokens verbatim + components; `dashboard.module.css`
  ported from the canvas; fonts via next/font (Caprasimo wordmark, Zen Maru Gothic JP headings,
  Noto Sans JP body, Figtree Latin, IBM Plex Mono). Sign-in page restyled too.
- **Dashboard behaviour added:** two-line question box; idle console lists the pipeline stages;
  decline card explains each reason code (`score_gate | not_answerable | no_citation |
  unverified_quote | limit`) and shows the gate-1 score; error card with 再実行; budget guard notice
  under the input; candidates as a real table with linear 0–1 score bars; one-column layout
  (question → console → answer → candidates) under 1100px via container queries.
- tsc + eslint + `next build` clean. **Not yet seen with a real sign-in.**
- Synced with the parallel session's gate-1 change: the console line now renders `via=named_doc`
  passes as `0.254 < 0.30 · names … → PASS (named_doc)` (it would have printed "≥").
  API restarted 2026-09-22 so it serves the current `agent.py` (strict output + named-doc gate).
  `test_gates.py` 8/8, `test_api.py` 10/10, $0.

### Folder cleanup (user-approved, 2026-09-22)

- **Deleted:** the design preview (`web/app/preview/`, `web/public/preview/` — git did NOT ignore
  them), the superseded stub run `data/validation_runs/20260922-173511_stub.json`, `__pycache__/`,
  `web/tsconfig.tsbuildinfo`. `web/README.md` replaced (was create-next-app boilerplate).
- `web/next.config.ts`: `turbopack.root` set — the root `package-lock.json` (for `neon.ts`)
  caused the "multiple lockfiles" warning. Build clean, no warning.
- **Kept on purpose:** `web/AGENTS.md` + `web/CLAUDE.md` are managed by `next dev` (recreated if
  deleted); root `package.json` / `node_modules` serve `neon.ts`; `.cache/` holds the bge
  reranker, Docling models and the Voyage tokenizer — nothing unused in it.
- Still stale, to update: `docs/dashboard_build_brief.md` §1/§4 (old purple palette/mock),
  the snapshot row "Design mock" above, `MANUAL.md` + web mirror (design-system decision).

### Gotchas

- **Claude in Chrome cannot inject into `localhost` pages** here (even a static JSON times out),
  so localhost visual checks must be done by hand. claude.ai pages work.
- Recorded SSE written through Python text mode on Windows became `\r\r\n` and broke parsing —
  write with `newline=""` or strip `\r`.
- The example chip 「…要件は**何ですか？**」 scores 0.5955 at rerank vs 0.8907 without 何ですか.
  Still passes gate 1; recheck in the validation suite.

### Next

Unchanged from the handoff at the top: create the admin → sign in → stub run → first paid run.
