# DATARAG Project Manual / プロジェクトマニュアル

**Web version (living doc):** https://claude.ai/artifact/J1U69gY5YUBYznPztFrYY2

Last updated: 2026-09-21 · Languages: EN + JP, Russian summary in §8

> A decision log for the デジタル化・AI導入補助金2026 RAG system. Every choice
> recorded with its reasoning, so it can be defended in conversation.
>
> **Maintenance rule:** update this file whenever a decision is made or
> reversed, and mirror the change into the web doc. A decision that is not
> written down here is a decision that cannot be defended later.

---

## 1. Purpose / 目的

DATARAG answers questions about the requirements and procedures of
デジタル化・AI導入補助金2026. It retrieves from the official 公募要領 and 交付規程,
reranks the candidates, and generates a grounded answer. When the evidence is
too weak, it refuses to answer rather than guess.

DATARAG はデジタル化・AI導入補助金2026 の要件・手続きに関する質問に回答します。
公式の公募要領と交付規程から検索し、リランキングを経て根拠に基づく回答を生成します。
根拠が不十分な場合は推測せずに回答を拒否します。

### The two structural problems / 構造上の2つの問題

A naive RAG pipeline breaks on this corpus for two reasons. Both were
measured, not assumed.

**Problem 1 — 枠 ambiguity.** The subsidy is issued as five separate 枠
(funding frames), each with its own 公募要領. Requirements differ but wording is
nearly identical. Measured literal overlap is low (0.5–5.3% Jaccard on
40-character shingles), while semantic overlap is high. An embedding model sees
them as near-duplicates. Retrieval will mix 通常枠 rules into a セキュリティ枠
answer unless every chunk carries its 枠 identity.

本補助金は5つの枠に分かれ、それぞれ別の公募要領を持ちます。要件は異なるのに文言は
酷似しています。字句上の重複は低いものの（Jaccard 0.5〜5.3%）、意味的重複は高く、
埋め込みモデルにはほぼ同一に見えます。

**Problem 2 — authority hierarchy.** 公募要領 explains how to apply. 交付規程 is
the binding regulation covering obligations, 補助対象経費 and 取消・返還条件.
**When the two conflict, 交付規程 governs.** A system that does not know which
tier a passage came from will cite whichever chunk ranked first.

公募要領は「申請方法」を説明する文書です。交付規程は拘束力のある規程です。
**矛盾する場合は交付規程が優先します。**

### Corpus / コーパス

14 public official documents, 372 pages, ~363,000 characters. All carry real
text layers, so no OCR is needed.

| Category | Count | Contents |
|---|---|---|
| 公募要領 | 5 | 通常枠 / インボイス枠（対応類型・電子取引類型）/ セキュリティ対策推進枠 / 複数者連携枠 |
| 交付規程 | 5 | the binding regulation for each of the five 枠 |
| 加点項目一覧 | 1 | scoring criteria, all 枠 |
| スマート農業関連 | 3 | a different programme — included on purpose, see below |

The three スマート農業 documents are **not** part of this subsidy. They were
included so a cross-programme question could test the decline path. Measured
2026-09-21, that premise was wrong: both programmes explicitly prohibit
duplicate subsidies, so the question *is* answerable (see §4 Technique 3). The
documents stay — they now test 枠/programme attribution instead.

Prior-year archives (2025 / 2024 / 2023) are excluded deliberately. Mixing
programme years is the most likely route to a confidently wrong 補助率 or
補助上限額.

---

## 2. Stack / 技術選定

Each component was chosen against a named alternative. The rejected option
matters as much as the chosen one.

| Layer | Choice | Why | Rejected |
|---|---|---|---|
| Chunking | Docling `HybridChunker` | Structure-aware: respects headings, tables, article numbers | Fixed-length splitting — cuts tables and 条 numbers mid-item |
| Embeddings | Voyage AI `voyage-3.5` (1024d) | Strong Japanese performance; 200M free tokens | OpenAI embeddings — the reference repo used them, not adopted |
| Vector store | Neon (Postgres + pgvector) | No weekly auto-pause; SQL metadata beside vectors | Supabase — auto-pauses, bad for an interview-demo schedule |
| Reranking | Cross-encoder, local GPU | Repairs dense-retrieval ordering; costs nothing per query | API reranker — adds latency and per-call cost |
| Agent | Pydantic AI | Abstracts only tool-calling; chunking and retrieval stay visible | LangChain — hides the decisions this project exists to show |
| Generation | Claude API (`claude-sonnet-5`) | The only paid component | — |
| Serving | FastAPI | A `/query` endpoint; Pydantic AI does not replace this | — |

### Why Claude is generation-only / Claude を生成専用とする理由

Anthropic has no embeddings API. Embeddings therefore come from Voyage, and
Claude handles generation only. This is a constraint of the platform, not a
preference.

### Cost model / コスト構造

| Component | Cost | Note |
|---|---|---|
| Voyage embeddings | ¥0 | Corpus is ~302k tokens against a 200M free allowance |
| Neon Postgres | ¥0 | Free tier |
| Cross-encoder reranking | ¥0 | Runs locally on the GPU |
| Claude generation | **$0.032 / answered question** (measured) | `claude-sonnet-5`, 3 requests, 14.8k in (7.9k cache-read) / 1.4k out |
| Claude, early decline | $0.0018 (measured) | 1 request, no tool call |

The pre-build estimate was $0.011/query, assuming one request. The agent makes
two to three — a tool call, then the answer. Project budget is **$5.00 total**,
enforced in code by a spend ledger. Gate 1 saves money directly: an off-topic
query never reaches the model and costs nothing.

### Runtime environment / 実行環境

Python 3.14.2, Windows, RTX 5060 (Blackwell, sm_120, 8 GB). The GPU matters for
one thing only: the cross-encoder reranker runs locally, so reranking is free
and fast.

One trap worth naming. PyPI's default Windows `torch` wheel is CPU-only. A
Blackwell card needs CUDA ≥ 12.8, and only the `cu130` index carries both a
cp314 wheel and sm_120 support. `torch` and `torchvision` must come from the
**same index** — a cu130 torch with a PyPI torchvision imports cleanly and then
fails at runtime with
`Could not run 'torchvision::nms' with arguments from the 'CUDA' backend`.

---

## 3. Database design / データベース設計

Two tables and one view, on Neon Postgres 18.6 with `pgvector` 0.8.6. The
schema lives in `db/schema.sql` and is idempotent.

```
documents (provenance + full_text)
    |  1 to many
    +--> chunks (vector(1024))
    |
    +--> chunk_sources  <-- the joined view retrieval selects from
```

### Decision: `authority_rank` is a GENERATED column / 生成列とした理由

```
authority_rank = 1  交付規程   (binding)
                 2  公募要領   (guidance)
                 3  加点項目一覧
```

Lower rank wins on conflict. Because it is generated from `doc_type` rather
than hand-set, it cannot drift out of sync. The precedence rule is enforced by
the schema, not by discipline.

`doc_type` also carries a CHECK constraint limiting it to the three valid
values, so a bad ingest fails loudly rather than storing nonsense.

交付規程優先のルールを生成列として表現しています。手動設定ではないため、
`doc_type` と不整合を起こすことが原理的にありません。

### Decision: provenance lives in a view, not on `chunks` / 出典情報をビューに置いた理由

The obvious shortcut is to copy `waku` and `doc_type` onto every chunk row.
That was rejected. Retrieval selects from `chunk_sources`, which joins
`documents` and emits one label:

```
source_doc = program + waku + doc_type
```

Two reasons. `documents` stays the single source of truth, so the two copies
cannot disagree. And every retrieved passage arrives already carrying its 枠 —
a forgotten join cannot silently drop the 枠 name on the way to the prompt.
Given Problem 1, that is the exact failure this design prevents.

### Decision: HNSW index — open to challenge / 再検討の余地あり

`chunks.embedding` carries an HNSW index with cosine distance (`m=16`,
`ef_construction=64`). HNSW is **approximate**. At ~1,500 chunks a sequential
scan would be exact *and* fast enough. It was built anyway so the query shape
survives a corpus 100× larger. If demo-time recall matters more than
scalability, dropping it is defensible and reversible.

---

## 4. RAG techniques / 採用する RAG 手法

Three techniques compose into one pipeline: **agentic tool selection**,
**cross-encoder reranking**, and an **unconditional decline gate**.

```
Question
   |
   +--> Gate 1: top rerank score >= 0.3 ?          (code, $0)
   |       no  -> 「この情報からは判断できません」   (no model call)
   |
   +--> Agent picks a tool                            (Claude, paid)
   |       |
   |       +-- search_knowledge_base   (specific fact)
   |       |      vector top 20  ∪  char-bigram BM25 top 10
   |       |      cross-encoder rerank of the union, top 5
   |       |
   |       +-- retrieve_full_document  (spans a document)
   |              by catalog ID, capped at 20k tokens
   |
   +--> Structured answer: answerable + verbatim quotes with source_doc
   |
   +--> Gate 2: every quote found verbatim, under its source_doc,
               in what the tools returned this run ?  (code, $0)
           no  -> 「この情報からは判断できません」
           yes -> answer
```

### Technique 1 — agentic tool selection / ツール選択

`search_knowledge_base` answers specific factual questions.
`retrieve_full_document` fetches a whole document, for questions spanning
most of a document where chunks alone lack context. This is the
distinction between plain RAG and 「RAGやAIエージェント」 work: observable
tool-selection behaviour, not just retrieval.

### Technique 2 — cross-encoder reranking / 再ランキング

Vector search retrieves 20 candidates; a cross-encoder rescores them and
returns the top 5. A bi-encoder embeds query and document separately. A
cross-encoder reads both together, so it judges relevance far more accurately —
at a cost that only makes sense on a short candidate list. §6 Problem C is the
measurement that justifies this stage.

### Decision: `retrieve_full_document` takes an ID and is capped / ID 指定と上限

**By catalog ID, not by title.** The agent paraphrases: 「通常枠の公募要領」
matches no stored title exactly, and a fuzzy match can land on the wrong 枠 —
the exact failure this corpus is built to expose. The tool description lists
all 14 documents with their 枠, and an unknown ID returns the valid list.

**Capped at 20,000 tokens.** Full documents measured 11k–65k Claude tokens
(`count_tokens`, 2026-09-21). Uncapped, one call on the 通常枠 公募要領 is
~$0.13 of input at Sonnet rates — ~12× an ordinary query — and is billed again
on every later turn that carries it. Over the cap, whole sections are returned
in order, then the headings of every omitted section with an instruction to
use `search_knowledge_base` for them.

| Document | Uncapped | Returned |
|---|---|---|
| 05 通常枠 公募要領 | 64,704 | 17,511 |
| 01 複数者連携 公募要領 | 44,347 | 18,653 |
| 02 / 03 / 04 / 09 | 11k–18k | whole document |

The cap covers everything the agent reads — header and omitted-section outline
included. See §6 Problem E for how the first version got this wrong.

### Decision: multilingual reranker / 多言語リランカーを選ぶ理由

The common default, `cross-encoder/ms-marco-MiniLM-L-6-v2`, is English-trained.
On a 100% Japanese corpus it would make the rerank stage close to a no-op — and
reranking is the stage this architecture rests on. `BAAI/bge-reranker-v2-m3` is
the choice. Second benefit: bge is conventionally read through a sigmoid into
0–1, which turns the decline threshold into a number that can be reasoned
about. ms-marco emits unbounded logits.

Implemented 2026-09-21. The sigmoid is passed to `CrossEncoder` explicitly
rather than left to the library default, so the threshold stays on a 0–1 scale
for any configured model, including the MiniLM fallback. Measured on the RTX
5060: 20–30 pairs rerank in ~0.7–1.0 s, 2.5 GiB peak VRAM. Cold load took
113 s, so the server must load the model at startup, not on the first query.

### Technique 3 — the decline gate / 回答拒否ゲート

**Decision, 2026-09-21: two gates, both enforced in code.** The brief's design
— one rerank-score threshold before generation — was measured and found
insufficient (below). The replacement:

1. **Score gate, $0.** Hybrid retrieval + rerank on the raw question before
   any model call. Top score below `RERANK_SCORE_THRESHOLD=0.3` → decline.
2. **Evidence gate, $0, inside the same generation call.** Claude returns a
   structured answer: `answerable`, the answer, and verbatim quotes each tagged
   with the `source_doc` it came from. Code then checks every quote appears —
   NFKC- and whitespace-normalized, every character present — in a passage a
   tool returned *this run*, under the `source_doc` the citation names. A miss,
   no citation, or `answerable=false` → decline.

**Neither gate is the agent's decision.** The model proposes evidence; code
decides whether it holds. Instructing a model to stay silent when unsure is a
request, not a guarantee; a check in code is a guarantee.

**Why one threshold cannot work — measured:**

| Question type | Top rerank score | Example |
|---|---|---|
| Off-topic | 0.000–0.001 | 東京の天気, ラーメン屋 |
| Answerable, retrieval missed it | 0.09 | 北海道 question before hybrid retrieval |
| On-topic, not answerable from corpus | **0.55–0.93** | ものづくり補助金, 2027年度, IT導入支援事業者 |
| Answerable | **0.89–0.9994** | 北海道, 補助率 |

The reranker measures *topicality*, not *answerability*. The dangerous cases
overlap the answerable ones, so a threshold that admits real answers admits
them too. The 2027 question retrieves the 2026 schedule at 0.55 — a threshold
gate would pass it to generation with plausible wrong-year evidence.

**What gate 2 catches, tested free with a stubbed model** (`scripts/test_gates.py`,
7/7 pass): a quote with one character changed; a real quote attributed to the
wrong 枠; no citation; `answerable=false`. The wrong-枠 case is the corpus's
core risk — gate 2 turns 枠 attribution from a prompt request into a check.

**Known limits.** Gate 2 proves every *citation* is real, not that every
*sentence* is cited: the first paid answer carried an uncited paragraph. The
prompt now requires one citation per claim and nothing uncited — narrower, not
closed. Second, the model may decline *without searching* when the question's
premise is plainly outside the corpus: the 2027 question declined in 1 request
for $0.0018. Accepted — the corpus is 2026-only, and it is the cheap path.

**Finding, 2026-09-21: the planned known-negative anchor is not a negative.**
The brief's failure-path test is the cross-programme question (DX subsidy ×
smart-agriculture subsidy). But 13 of 14 documents carry an explicit
duplicate-subsidy prohibition — DX 交付規程 第10条 「国及び中小機構その他の独立
行政法人の他の補助金等と重複する事業については、補助事業の対象として認めない」,
smart-agriculture 第７ 重複申請の制限. "Can the same machine be claimed under
both?" is therefore *answerable* (no), and a correct system should answer it,
not decline. Its top rerank score measured 0.81. It moves to the answerable
test set (expected answer: no, per 交付規程 第10条); the failure-path test is now
「2027年度の公募スケジュールは？」.

### Agent and cost control / エージェントとコスト管理

Pydantic AI agent (`rag/agent.py`), `claude-sonnet-5`, `effort=low` — Sonnet 5
thinks adaptively by default and thinking bills as output, so effort, not
disabled thinking, is the lever. Instructions, tool definitions and message
history are prompt-cached.

**Hard limits in code, per question:** 4 requests, 60k input tokens, 4k output
tokens — worst case $0.19. Every paid run is appended to
`data/spend_ledger.jsonl`; a run that could take the ledger past $5.00 is
refused before any request is sent.

**First paid runs, 2026-09-21:**

| Question | Result | Requests | Input (cache read) | Output | Cost |
|---|---|---|---|---|---|
| 北海道内で事業を実施する場合の要件 | answered, quote verified | 3 | 14,788 (7,930) | 1,360 | $0.0323 |
| 2027年度の…公募スケジュール | declined, no search | 1 | 2,534 (2,309) | 83 | $0.0018 |

The `CLAUDE.md` estimate of $0.011/query assumed one request. The agent makes
two to three — a tool call, then the answer — so an answered question is
~$0.03 and a 10-question suite ~$0.35–0.45. Caching is working: over half the
input of the answered run was billed at 0.1×.

エージェントは Sonnet 5（effort=low）。1問あたりの上限（4リクエスト・入力6万・出力4千トークン、
最大 $0.19）をコードで強制し、全実行を台帳に記録して $5.00 を超える実行は開始前に拒否します。
回答1件の実測は $0.032 でした。

### Decision: hybrid candidate retrieval / ハイブリッド検索

Adopted 2026-09-21; not in the original brief. §6 Problem D is the measurement
that forced it: on the full corpus the 北海道 gold chunk fell to vector rank
211, beyond any pool a reranker can afford.

Stage 1 is now the **union** of vector top 20 and a lexical top 10; the
cross-encoder scores every candidate and picks the final 5. The lexical stage is
**character-bigram BM25**, held in memory:

- Japanese has no spaces and Postgres full-text search has no Japanese
  segmenter, so the unit is the character bigram: 北海道 → 北海, 海道.
- BM25's IDF does the weighting: 事業 / 実施 / 要件 appear in most chunks and
  weigh almost nothing; 北海 appears in four and dominates.
- Text is NFKC-normalized first — the corpus mixes widths (２０２６ / 2026,
  ＡＩ / AI), which would otherwise never match.
- Index build: 2.7 s for 1,025 chunks, once per process. A re-ingest needs a
  server restart to be seen.

**Rejected: `pg_trgm`.** Available on Neon but not installed, and whether it
treats Japanese characters as word characters depends on the database locale —
unverified. Python BM25 had no dependencies and proved the approach first;
moving it into Postgres is an option if it earns its place.

**Rejected: widening `RETRIEVAL_TOP_K`.** Measured: TOP_K=100 still does not
reach rank 211. Reranking hundreds of pairs per query to catch rare terms is
the wrong tool for a lexical problem.

---

## 5. Ingestion results — 2026-09-20 / 取り込み結果

The pipeline was proved end to end on one document before running the corpus.
Building against 14 documents first would have meant spending ~30 throttled
minutes on an unproven pipeline.

**Test document:** `04_jisshi_youryou_bekki1_hokkaido.pdf` — 10 pages, smallest
in the corpus.

| Metric | Result |
|---|---|
| Chunks produced | 32 |
| Total tokens | 9,075 |
| Chunk size (tokens) | min 39 / median 316 / max 507 |
| Chunk limit | 512 |
| Embeddings stored | 32 / 32 at 1024 dimensions |
| Full text extracted | 17,167 characters |
| Docling conversion | 4.2 s |
| Embedding wall time | ~114 s (throttled, 6 batches) |
| Voyage tokens billed | 9,050 of 200,000,000 free |
| Cost | ¥0 |

Ingested twice to confirm the overwrite path: the second run left 1 document
and 32 chunks, not duplicates. `DELETE` on the filename cascades to chunks,
then rewrites, so a failed run can simply be re-run.

### Full corpus — 2026-09-21 / 全文書の取り込み

| Metric | Result |
|---|---|
| Documents | 14 / 14 |
| Chunks | 1,025 (none missing an embedding, no duplicates) |
| Voyage tokens billed | 292,840 of 200,000,000 free (0.15%) |
| Wall time | 68 min under the 3 RPM throttle, zero retries |
| Largest documents | 05 通常枠 公募要領 162 chunks; 06 138; 08 118 |
| Cost | ¥0 |

Doc #04 was re-ingested as part of the run and reproduced exactly: 32 chunks,
9,075 tokens — chunking is deterministic.

**39 chunks exceed the 512-token limit, max 534 — accepted.** All 39 contain a
table, and none exceeds 512 once the prepended heading is removed:
`HybridChunker` enforces the limit on the body, then `contextualize()` adds the
heading path on top. Nothing is truncated anywhere downstream (Voyage accepts
32k tokens, the reranker is configured for 1,024), so re-chunking would cost
another 68-minute ingest for no gain.

### Decision: chunk with Voyage's own tokenizer / Voyage のトークナイザを使う理由

`HybridChunker` needs a tokenizer to enforce `CHUNK_MAX_TOKENS`. Using
`voyageai/voyage-3.5` means the chunker counts the same tokens Voyage bills and
the same tokens the rate limit is measured in. Japanese tokenizes very
differently across models, so an approximation here would be meaningful.

### Decision: contextualize each chunk / チャンクに見出しを付与する

Each chunk is stored as `chunker.contextualize(chunk)`, which prepends the
heading path — e.g. `第５ 応募者及び応募の要件` — before the body text. An
embedded chunk therefore carries its own section context. In a document of
numbered 条 and 項, a passage stripped of its heading is often meaningless.

### Throttle / レート制限

No payment method on the Voyage account → 3 requests/minute, 10,000
tokens/minute. `VOYAGE_BATCH_SIZE=6` with a 21-second client-side interval
keeps within both. The full-corpus ingest was estimated at ~30 minutes and took
68 — the estimate was right about tokens (~300k) and wrong about the request
count. Adding a card raises it to 2,000 RPM, but the throttle has not actually
blocked work: the ingest ran in the background while retrieval was built.

---

## 6. Problems faced and solved / 直面した問題と解決

All six were found by inspecting actual output, not by trusting the pipeline.

### Problem A — OCR was running and doing nothing / 不要な OCR

**Symptom.** Conversion took 68.3s for a 10-page document; RapidOCR was
downloading models and running on every page.

**Cause.** Docling enables OCR by default. Every PDF here has a real text
layer, so OCR re-derived text that was already present.

**Fix.** `PdfPipelineOptions.do_ocr = False`

| | Before | After |
|---|---|---|
| Conversion time | 68.3 s | 4.2 s |
| Chunk output | 35 chunks | identical at this stage |

**Impact.** 16× faster, ~40 minutes saved across 372 pages. Output was
byte-identical, confirming OCR contributed nothing. Flip back on only if a
scanned document joins the corpus.

### Problem B — degenerate table serialization / 表のシリアライズ破綻

**Symptom.** 9 of 35 chunks were dominated by repetitive noise. The worst spent
457 tokens on lines like:

```
① 事業実施主体の適格性, 審査項目 = ① 事業実施主体の適格性.
① 事業実施主体の適格性, 判定基準 = ① 事業実施主体の適格性.
① 事業実施主体の適格性, 判定 = ① 事業実施主体の適格性.
```

**Cause.** Docling's default table serializer emits one `row, column = value`
triplet per cell. These audit-criteria tables use merged header cells, so the
row header repeats as its own value — the output says nothing, at length.

**Why it mattered.** 補助率 and 補助上限額 live in tables. The numeric test
question depends on table content surviving conversion intact.

**Fix.** A `ChunkingSerializerProvider` supplying `MarkdownTableSerializer`.

| | Triplets | Markdown |
|---|---|---|
| Chunks | 35 | 32 |
| Total tokens | 10,271 | 9,075 |

**Impact.** 12% fewer tokens for identical content, grid structure preserved.
Header-row detection is still imperfect — noted as an open item.

### Problem C — dense retrieval misses clauses / ベクトル検索は箇条を見落とす

The most important finding of the session, and a property of the method rather
than a bug.

**Test.** The same gold chunk — the 北海道内の複数総合振興局 clause, which sits
inside 第３ 事業内容 — queried two ways:

| Query | Gold rank | Gold similarity | Top-1 similarity |
|---|---|---|---|
| 複数の都道府県にわたり事業を実施する事業実施主体とは | **1** / 32 | 0.5537 | 0.5537 |
| 北海道内で事業を実施する場合の要件 | **13** / 32 | 0.4116 | 0.5309 |

**Interpretation.** Dense retrieval matches a chunk's *dominant topic*, not a
parenthetical clause inside it. The 北海道 rule is a sub-clause of a 事業内容
paragraph, so a question phrased around 北海道 ranks it 13th, behind generic
要件 sections that are *about* requirements in general.

**Three consequences.**

1. `RETRIEVAL_TOP_K=20` is vindicated. It catches rank 13. A top-5 or top-10
   vector stage would discard the correct answer before reranking saw it.
   **Do not lower it.**
2. Clearest argument for the cross-encoder stage. The gold chunk *is* in the
   candidate set, merely misordered — exactly what a reranker repairs.
3. Concrete case for hybrid search. A lexical stage matching 北海道 would rank
   it first trivially.

**Regression test.** When reranking is built, this query is the acceptance
test: the gold chunk must move from rank 13 into the top 5. Consequence 1 did
not survive the full corpus — see Problem D.

### Problem D — the full corpus buried the clause / 全文書で箇条が埋没

**Symptom.** With all 1,025 chunks loaded, the Problem C regression test
*failed*: the 北海道 gold chunk was not among the 20 candidates at all, so the
reranker never saw it.

**Measurement.**

| | 1 document (32 chunks) | Full corpus (1,025) |
|---|---|---|
| Gold vector rank | 13 | **211** |
| Similarity, gold vs #20 | — | 0.412 vs 0.485 |
| Caught by TOP_K=20 / 50 / 100 | yes | no / no / no |

**Cause.** Problem C at scale. Every other 要件 section in thirteen more
documents is closer to the query's topic than the clause is. A test that passes
on one document says little about 1,025.

**The reranker still behaved correctly.** Its best score over the wrong pool was
0.09 — it correctly reported that nothing relevant had been retrieved, so the
decline gate would have fired. Safe, but an answerable question refused.

**Fix.** Hybrid candidate retrieval (§4). Only 4 chunks in the corpus contain
北海道; the gold chunk is one of them.

| Query | Vector rank | BM25 rank | Final rank | Score |
|---|---|---|---|---|
| 北海道内で事業を実施する場合の要件 | 211 (not in pool) | **1** | **1** | 0.8907 |
| 複数の都道府県にわたり…事業実施主体とは | 1 | 1 | 1 | 0.9994 |

The regression passes, found by the lexical stage alone, and the other phrasing
is unchanged. Next best after the gold chunk: 0.09 — a clean separation.

### Problem E — the token cap leaked / トークン上限の超過

**Symptom.** The first `retrieve_full_document` returned 20,420 and 21,188
real Claude tokens against a 20,000 cap — caught by checking the output with
`count_tokens` rather than trusting the estimate.

**Two causes.**

1. *Wrong baseline for the ratio.* Budgeting uses the local Voyage tokenizer,
   converted to Claude tokens. The first ratio (1.30) divided Claude's count of
   `full_text` by the *sum of chunk* token counts — which include a heading
   repeated on every chunk and so overstate the Voyage side. Measured
   correctly, text against the same text: **1.215–1.364**. Now 1.40.
2. *The frame was left out.* The cap was applied to the body, but the agent
   also reads the header and the list of omitted sections — ~1k tokens on
   doc 01. The cap now applies to the rendered tool output as a whole.

**Result.** All 9 stored documents then checked: largest real payload 18,653;
the estimate now overshoots the real count every time, as a cap should.

**Also found.** The tokenizer was contacting the Hugging Face Hub on every
load. `voyage-3.5` ships no `config.json`, and offline `AutoTokenizer` insists
on one before reading the tokenizer files it does have — so it is now loaded
from the cached snapshot directory and runs with `HF_HUB_OFFLINE=1`.

### Problem F — a paid run lost its own receipt / 課金記録の欠落

**Symptom.** The first paid call reached Claude, was billed, and then crashed
on `run.usage()` — in this Pydantic AI version `usage` is a property, not a
method. The answer and the exact usage were lost; the ledger had no entry.

**Why the free tests missed it.** The stubbed-model tests skip cost accounting,
so the only line that differed on the paid path was never executed before
money was spent on it.

**Fix.** The ledger entry is now written the moment a paid request returns,
before any later step can fail. If exact usage cannot be read, the run is
recorded at the worst-case cost ($0.19) — over-counting is the safe error for
a budget. The lost run was first logged at $0.19, then reconciled to $0.02
from the Anthropic console (balance $5.00 → $4.98).

**Lesson.** Code that only runs on the paid path should be exercised against a
stub's usage object before the first paid call — now done in the free test.

---

## 7. Decision log / 意思決定の記録

Newest first. Quick-reference version of §2–§6.

| Date | Decision | Reason | Rejected alternative |
|---|---|---|---|
| 2026-09-21 | Auth: Neon Auth with one admin account; the API verifies the JWT against JWKS and requires role=admin in `neon_auth."user"` | Users live in the same database; open sign-up grants nothing without the role; no password passes through any transcript | A hand-rolled admin table; a shared API key |
| 2026-09-21 | Stub mode is the dashboard default; a paid run needs an explicit checkbox | A stray click must not spend the budget | Paid by default |
| 2026-09-21 | Dashboard hosting: backend on this PC via tunnel first, then all-Vercel with an API reranker | Keeps the measured GPU bge setup for the first demo; Vercel has no GPU | CPU bge on Vercel (2.29 GB cold load, unmeasured latency); a third hosting platform |
| 2026-09-21 | Dashboard palette and element shapes from it-shien.smrj.go.jp CSS; no logos; 「非公式デモ」 label | Familiar to the audience; a tool about this subsidy must not pass as official | Copying the site's branding; a generic palette |
| 2026-09-21 | Two-layer decline gate: score ≥ 0.3, then verbatim-quote check under source_doc | Off-topic ≤ 0.001 but on-topic unanswerable 0.55–0.93 overlaps answerable 0.89+; no single threshold separates them | One rerank-score threshold, as the brief states |
| 2026-09-21 | Failure-path test → 「2027年度の公募スケジュール」 | Cross-programme question is answerable (交付規程 第10条) | The brief's cross-programme question |
| 2026-09-21 | `effort=low` on Sonnet 5, thinking left adaptive | Thinking bills as output; lower effort cuts it without the disabled-thinking failure modes | Disabling thinking |
| 2026-09-21 | Spend ledger + per-run hard limits in code | $5 budget enforced by the program, not by memory | Relying on announcing costs alone |
| 2026-09-21 | Accept declines without a search | 2027 question declined in 1 request for $0.0018; corpus is 2026-only | Forcing a search before every decline (~15× the cost) |
| 2026-09-21 | Hybrid candidates: vector top 20 ∪ char-bigram BM25 top 10 | Full corpus pushed a gold clause to vector rank 211; BM25 finds it at rank 1 | Widening TOP_K (100 still misses); `pg_trgm` (locale behaviour unverified) |
| 2026-09-21 | `retrieve_full_document` by catalog ID | Agent paraphrases titles; fuzzy match can pick the wrong 枠 | Title lookup, exact or fuzzy |
| 2026-09-21 | Full-document cap 20k tokens | Uncapped doc 05 is ~$0.13 input per call, re-billed per turn | Returning full text as the brief states |
| 2026-09-21 | Accept 39 chunks at 513–534 tokens | Overshoot is the prepended heading; nothing truncates downstream | Re-ingesting at a lower limit (68 min, no gain) |
| 2026-09-21 | Sigmoid passed explicitly to the reranker | Threshold stays 0–1 for any configured model | Relying on the library default |
| 2026-09-20 | Markdown table serializer | Default triplets degenerate on merged-header tables; 12% fewer tokens | Docling default `row, col = value` triplets |
| 2026-09-20 | `do_ocr = False` | All PDFs have text layers; 68.3s → 4.2s | Leaving Docling's default on |
| 2026-09-20 | Keep `RETRIEVAL_TOP_K=20` | Measured: gold chunk ranked 13th for one phrasing | Lowering to 5 or 10 for speed |
| 2026-09-20 | Voyage tokenizer for chunking | Counts the tokens actually billed and rate-limited | A generic or default tokenizer |
| 2026-09-20 | Contextualize chunks with headings | 条/項 text is meaningless stripped of its heading | Raw chunk text |
| 2026-09-20 | `authority_rank` GENERATED | 交付規程 precedence enforced by schema, cannot drift | Hand-set column, or prompt-only rule |
| 2026-09-20 | Provenance via `chunk_sources` view | Single source of truth; forgotten join cannot drop 枠 | Denormalizing 枠 onto every chunk |
| 2026-09-20 | HNSW index (open to challenge) | Query shape that scales | Sequential scan, exact at this size |
| 2026-09-20 | `cu130` torch, matched torchvision | Only index with cp314 + Blackwell sm_120 | PyPI default (CPU-only) |
| 2026-09-19 | `BAAI/bge-reranker-v2-m3` | English MiniLM is a no-op on Japanese; sigmoid output is calibratable | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| 2026-09-19 | Defer Voyage billing | 3 RPM throttle has not blocked work | Adding a payment method |
| 2026-09-19 | `claude-sonnet-5` | Balance of quality and cost within a $5 budget | Opus 5 (2.5×), Haiku 4.5 (0.5×) |
| 2026-09-19 | Exclude prior-year archives | Mixing years is the likeliest route to a wrong 補助率 | Including 2025/2024/2023 for volume |
| 2026-09-19 | Include 3 農業 documents | Enables a genuine no-answer question for the decline test | A corpus with only answerable questions |
| 2026-09-19 | Neon over Supabase | No weekly auto-pause | Supabase |
| 2026-09-19 | Pydantic AI over LangChain | Abstracts only orchestration, not retrieval decisions | LangChain |
| 2026-09-19 | Docling over fixed-length chunking | Structure-aware; tables and 条 numbers survive | Hand-rolled splitting |

### Open items / 未決事項

| Item | Status |
|---|---|
| Test suite (step 5) | 8–10 questions, ~$0.35–0.45. Includes 2027 (decline), off-topic (gate 1), cross-programme (answer: no), a numeric 補助率 question, a full-document question |
| `RERANK_SCORE_THRESHOLD=0.3` | Set from 7 data points; recheck against the suite |
| Uncited claims | Gate 2 verifies citations, not sentences. Prompt now demands one citation per claim; measure in step 5 |
| Third request per answered question | First paid run used 3 requests where 2 were expected; not yet explained |
| 枠 discrimination on numeric questions | Every 公募要領 carries the same 類型比較表, so all 枠 tables score ~0.998 for a 補助率 question. Ranking cannot separate 枠 here — the generation prompt must, via `source_doc` |
| HNSW vs exact scan | Built, but reversible if demo recall matters more |
| Table header detection | Markdown output still mis-detects some header rows |

---

## 8. Резюме на русском

**Что это.** DATARAG — RAG-система по японской субсидии
デジタル化・AI導入補助金2026. Отвечает на вопросы о требованиях и процедурах,
опираясь на официальные документы. Если оснований недостаточно — отказывается
отвечать, а не догадывается.

**Главная сложность.** Субсидия выходит в пяти рамках (枠), у каждой свой
документ. Требования разные, формулировки почти одинаковые — для
эмбеддинг-модели они почти неразличимы. Плюс иерархия: 交付規程 (обязательный
регламент) имеет приоритет над 公募要領 (инструкцией) при противоречии.

**Стек.** Docling → Voyage AI (voyage-3.5) → Neon Postgres + pgvector →
cross-encoder → Pydantic AI → Claude API → FastAPI. Платный только последний
этап — генерация, около $0.011 за запрос. Бюджет проекта — $5.00.

**Три ключевых решения в базе данных.**

1. `authority_rank` — вычисляемый столбец, а не ручное поле. Правило приоритета
   зашито в схему и не может рассогласоваться с типом документа.
2. Источник хранится в представлении `chunk_sources`, а не дублируется в каждом
   чанке. Забытый JOIN физически не может потерять название рамки.
3. Защита от ответа без оснований — жёсткий порог в коде, а не инструкция в
   промпте. Просьба к модели — это не гарантия.

**Результаты 20.09.2026.** Пайплайн проверен на одном документе: 32 чанка,
9 075 токенов, все эмбеддинги сохранены и читаются обратно. Затраты — ¥0.

**Три найденные проблемы.**

1. *OCR работал впустую.* У всех PDF есть текстовый слой. Отключение:
   68,3 с → 4,2 с на документ, результат идентичен.
2. *Таблицы разворачивались в мусор.* Стандартный сериализатор Docling повторял
   заголовок строки как значение. Переход на markdown дал −12% токенов при том
   же содержании. Важно, потому что ставки и лимиты субсидии живут именно в
   таблицах.
3. *Векторный поиск теряет пункты внутри абзаца.* Один и тот же фрагмент получил
   1-е место при одной формулировке и 13-е при другой. Это не баг, а свойство
   метода.

**Почему третья находка важнее всего.** Она одновременно обосновывает три вещи:
брать 20 кандидатов, а не 5; обязательно ставить cross-encoder; и рассмотреть
гибридный поиск. Тот же запрос станет регрессионным тестом: после реранкинга
фрагмент обязан подняться с 13-го места в топ-5.

**Результаты 21.09.2026.** Загружены все 14 документов: 1 025 чанков,
292 840 токенов Voyage (0,15% бесплатного лимита), 68 минут под ограничением
3 запроса в минуту. Затраты — $0.

**Регрессионный тест провалился — и это главная находка дня.** На полном корпусе
фрагмент про 北海道 опустился с 13-го места на **211-е** из 1 025 и вообще не
попал в 20 кандидатов. Реранкер не может поднять то, чего ему не дали.
Расширение пула до 100 не помогает. При этом реранкер повёл себя честно: лучшая
оценка по неверному пулу — 0,09, то есть система отказалась бы отвечать, а не
выдумала ответ.

**Решение — гибридный поиск.** Кандидаты теперь — объединение топ-20 по векторам
и топ-10 по BM25 на символьных биграммах (в японском нет пробелов: 北海道 →
北海, 海道). Слово 北海道 встречается всего в 4 чанках из 1 025, поэтому
лексический поиск находит нужный фрагмент первым. Итог: 1-е место, оценка 0,89;
следующий кандидат — 0,09. Вторая формулировка по-прежнему на 1-м месте (0,9994).

**`retrieve_full_document` — по ID и с лимитом 20 000 токенов.** Полные документы —
от 11 до 65 тысяч токенов Claude; без лимита один вызов стоил бы до ~$0,13.
Первая версия лимит превышала (до 21 188 токенов): коэффициент пересчёта был
посчитан от неверной базы, а заголовок и список пропущенных разделов не входили
в бюджет. Исправлено и проверено через бесплатный `count_tokens`: максимум
18 653.

**Открытый вопрос.** Контрольный вопрос «на отказ» из брифа (совмещение
двух программ субсидий) на самом деле *имеет ответ*: в 13 из 14 документов прямо
запрещено получать субсидию на то же самое из другой госпрограммы. Правильная
система должна ответить «нельзя», а не отказываться. Нужен другой вопрос — такой,
на который в корпусе действительно нет ответа, — иначе порог отказа не
откалибровать.

**Агент и двухслойный отказ (21.09.2026).** Проверка трёх вопросов без ответа в
корпусе показала: оценка реранкера измеряет *тематичность*, а не *наличие ответа*.
Вопросы не по теме получают ≤ 0,001, но тематические вопросы без ответа — 0,55–0,93,
а вопросы с ответом — 0,89–0,9994. Один порог их не разделит. Поэтому два слоя,
оба в коде: (1) порог 0,3 до вызова модели — отсекает посторонние вопросы за $0;
(2) Claude возвращает цитаты дословно, а код проверяет, что каждая цитата есть в
найденных фрагментах *под тем же источником (枠)*. Иначе — отказ. Модель
предлагает доказательства, код решает.

**Первые платные запуски.** Вопрос про 北海道 — верный ответ с проверенной цитатой,
$0,032. Вопрос про 2027 год — корректный отказ за $0,0018 (модель отказала без
поиска: корпус только за 2026 год). Смета $0,011 на запрос была занижена — агент
делает 2–3 запроса. Весь набор тестов — около $0,35–0,45.

**Инцидент.** Первый платный вызов упал *после* оплаты из-за ошибки в коде
(`run.usage()` вместо `run.usage`) — запись о расходе потерялась. Теперь расход
пишется в журнал сразу после ответа API, а при неизвестном расходе записывается
худший случай. Потерянный вызов сверен с консолью Anthropic: $0,02.

**Потрачено: $0,054 из $5,00.**

**Что дальше.** Набор тестов (8–10 вопросов, ~$0,40), затем FastAPI.
