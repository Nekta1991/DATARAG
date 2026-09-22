# Validation questions — step 5 (draft)

Status: **DRAFT, not run.** Drafted 2026-09-21. Every expected answer below was
taken from the stored corpus (SQL over `chunk_sources`), not written from memory.

Estimated cost of one full run: **~$0.28–0.36** at `claude-sonnet-5`, `effort=low`.
Q8 costs $0 (gate 1 declines before any model call). Announce and confirm before
running; never re-run the whole suite without asking again.

## How each question is graded

A question passes only when **every** column holds:

- **Status**: `answered` or `declined`, as the system returned it.
- **Decline path**: which gate declined it. That path is part of the result, not a detail.
- **Tool**: the tool the agent should pick. A different tool is a finding, not an
  automatic fail, but it has to be explained.
- **Must contain**: facts that must appear in the answer, checked against the gold
  source below.
- **Must cite**: the `source_doc` a citation has to carry. Gate 2 already proves the
  quote is real; this checks that it is the *right* document.
- **Must not**: the specific wrong answer this question exists to catch.

## Summary

| # | Question | Tests | Expected | Tool | Est. cost |
|---|---|---|---|---|---|
| 1 | 北海道内で事業を実施する場合の要件 | clause inside a paragraph; hybrid retrieval | answered | search | $0.032 |
| 2 | 複数者連携デジタル化・AI導入枠の補助率と補助上限額 | numeric, table content | answered | search | $0.035 |
| 3 | 通常枠の補助額と補助率 | 枠 discrimination | answered | search | $0.035 |
| 4 | 小規模事業者がセキュリティ対策推進枠を使う場合の補助率 | 枠 + 事業者区分 | answered | search | $0.032 |
| 5 | 同じ機械の購入費を両制度に申請できるか | cross-programme; 交付規程 authority | answered: **no** | search | $0.035 |
| 6 | 交付決定が取り消されるのはどのような場合 | 交付規程 as the binding source | answered | search | $0.035 |
| 7 | 通常枠の交付規程の全体構成 | tool selection: whole document | answered | retrieve_full_document | $0.06 |
| 8 | 明日の東京の天気 | off-topic | declined, gate 1 | none | **$0** |
| 9 | ものづくり補助金の補助上限額 | on-topic, not in corpus | declined, gate 2 | search or none | $0.002–0.03 |
| 10 | 2027年度の公募スケジュール | wrong year; the failure-path test | declined, gate 2 | none (measured) | $0.002 |

**Total ≈ $0.28–0.36.** Ledger before the run: $0.054.

---

## Q1 — clause inside a paragraph

**Question:** 北海道内で事業を実施する場合の要件は何ですか？

| | |
|---|---|
| Expected | answered |
| Tool | `search_knowledge_base` |
| Must contain | Inside 北海道, the multi-prefecture requirement is replaced by operating across **複数の総合振興局・振興局** |
| Must cite | スマート農業・農業支援サービス事業 広域型（スマート技術体系転換加速化支援） 公募要領 |
| Must not | Present general 応募要件 as the 北海道 rule |

**Gold:** doc 04, 第３ 事業内容 (chunk 268): 「複数の都道府県にわたり事業を実施する事業実施主体（北海道内で取り組む場合にあっては、北海道内の複数総合振興局・振興局で事業を実施する事業実施主体）」

**Why:** The regression test. The gold chunk sits at vector rank 211 and is found only by BM25. Already answered once, for $0.0323, before the one-citation-per-claim prompt; rerun it to see whether the uncited paragraph is gone.

## Q2 — numeric, table content

**Question:** 複数者連携デジタル化・AI導入枠の補助率と補助上限額を教えてください。

| | |
|---|---|
| Expected | answered |
| Tool | `search_knowledge_base` |
| Must contain | 消費動向等分析経費: 補助率 **２／３以内**, 上限 **５０万円×グループ構成員数**; (1)+(2) 上限 **３，０００万円**; その他経費: 補助率 ２／３以内, 上限 (1)+(2)×10%×2/3 or **２００万円** のいずれか低い方 |
| Must cite | デジタル化・AI導入補助金2026 複数者連携デジタル化・AI導入枠 公募要領 (or its 交付規程) |
| Must not | Use a figure from another 枠 in the same 類型比較表 (e.g. インボイス枠 ３／４) |

**Gold:** doc 01 p3, 類型比較表. **Why:** 補助率 lives in tables, and every 公募要領 carries the same comparison table, which scores about 0.998 for any 補助率 question. Only the prompt and the `source_doc` check keep the 枠 straight.

## Q3 — 枠 discrimination

**Question:** 通常枠の補助額と補助率はいくらですか？

| | |
|---|---|
| Expected | answered |
| Tool | `search_knowledge_base` |
| Must contain | 補助額 **５万円～１５０万円未満** and **１５０万円～４５０万円以下**; 補助率 **１／２以内**, **２／３以内** under the 最低賃金 condition |
| Must cite | デジタル化・AI導入補助金2026 通常枠 (公募要領 or 交付規程) |
| Must not | インボイス枠 ３／４・４／５; セキュリティ枠 figures |

**Gold:** doc 05 【補足２】 and 類型比較表.

## Q4 — 枠 plus 事業者区分

**Question:** 小規模事業者がセキュリティ対策推進枠を利用する場合の補助率はいくらですか？

| | |
|---|---|
| Expected | answered |
| Tool | `search_knowledge_base` |
| Must contain | **２／３以内** for 小規模事業者 (中小企業 is １／２以内); 補助額 ５万円～１５０万円 |
| Must cite | デジタル化・AI導入補助金2026 セキュリティ対策推進枠 (公募要領 or 交付規程) |
| Must not | Answer １／２ without the 小規模事業者 distinction |

## Q5 — cross-programme, 交付規程 authority

**Question:** スマート農業の補助事業とデジタル化・AI導入補助金を併用して、同じ機械の購入費を両方に申請できますか？

| | |
|---|---|
| Expected | answered: **no** |
| Tool | `search_knowledge_base` (possibly twice, once per programme) |
| Must contain | Duplicate subsidy for the same project is prohibited |
| Must cite | A デジタル化・AI導入補助金2026 **交付規程** (第10条 「補助対象としない事業」), and/or smart-agriculture 第７ 重複申請の制限 |
| Must not | Decline; say "yes"; answer from 公募要領 alone when 交付規程 says the same |

**Gold:** DX 交付規程 第10条 「国及び中小機構その他の独立行政法人の他の補助金等と重複する事業については、補助事業の対象として認めないものとする。」; smart-agri 第７ 重複申請の制限. **Why:** This was the brief's failure-path question until the corpus turned out to answer it. The last retrieval run surfaced 申請回数 chunks rather than the 重複 clauses, so this also checks whether the agent searches again with better terms.

## Q6 — 交付規程 as the binding source

**Question:** 補助金の交付決定が取り消されるのはどのような場合ですか？

| | |
|---|---|
| Expected | answered |
| Tool | `search_knowledge_base` |
| Must contain | The 交付決定の取消し conditions, listed as the document lists them |
| Must cite | A **交付規程** (authority_rank 1) |
| Must not | Mix 枠 without saying so. The 交付規程 are near-identical, so an answer citing one should name it |

**Open point:** the question names no 枠. Accepted answers are a clearly attributed answer from one 交付規程, or one that says the rule is shared. Decide which before grading.

**Both rules evaluated, 2026-09-22 ($0).** The 取消し article is character-identical (only layout hyphens differ) in 通常枠, インボイス対応類型, 電子取引類型 and セキュリティ対策推進枠 (all 第27条). **複数者連携 differs:** 第25条, グループ構成員 instead of 補助事業者, plus clauses on IT提供事業者 and 外部専門家 (1,270 vs 904 chars). Q6's top 5 search results are four 交付規程 取消し chunks (09, 10, 12, 11) and one 複数者連携 公募要領 chunk; the 複数者連携 交付規程 is not among them.

- **Rule A (attributed):** passes if the answer names a 枠 whose 交付規程 it cites. No prompt change needed.
- **Rule B (shared):** passes if the answer says the rule is shared and names 2 or more 枠, each cited from its own 交付規程. Needs a prompt line (`CITE_SHARED_RULES=1`), which applies to every question.
- **Both fail** an unqualified 「全ての枠」 claim: that would be false for 複数者連携, and the evidence gate cannot catch it (it checks quotes, not claims about coverage).

`scripts/run_validation.py` grades both on every run (`A ✓/✗ · B ✓/✗`).

## Q7 — tool selection: whole document

**Question:** 通常枠の交付規程は全体としてどのような構成（どのような条項の流れ）になっていますか？

| | |
|---|---|
| Expected | answered |
| Tool | **`retrieve_full_document("09_dx_ai_kitei_tsujyo")`** |
| Must contain | The document's actual sequence of 条 headings |
| Must cite | デジタル化・AI導入補助金2026 通常枠 交付規程 |
| Must not | Reconstruct the structure from 5 search chunks |

**Gate 1 (2026-09-22):** scores 0.254, below the 0.3 threshold, since a structure question matches no single chunk. Gate 1 now also passes a question that names a corpus document (枠 + doc type, e.g. 「通常枠の交付規程」): `named_documents()` in `rag/documents.py`. Q7 is the only suite question this affects.

**Why:** This is the one question built to show tool selection. Doc 09 is 17.9k tokens, under the 20k cap, so it comes back whole. It's the most expensive question (~$0.06).

## Q8 — off-topic

**Question:** 明日の東京の天気を教えてください。

| | |
|---|---|
| Expected | declined, **gate 1** (`score_gate`) |
| Tool | none; the model is never called |
| Cost | **$0** |

**Gold:** measured top score 0.000. **Why:** Shows the gate that costs nothing.

## Q9 — on-topic, not in the corpus

**Question:** ものづくり補助金の補助上限額はいくらですか？

| | |
|---|---|
| Expected | declined, via `not_answerable` or `unverified_quote` |
| Tool | any |
| Must not | Answer with a DX-subsidy figure |

**Gold:** measured top score **0.63**, so gate 1 passes it. Only gate 2 or the model's own `answerable=false` can stop it. **Why:** The case that shows why one threshold was not enough.

## Q10 — wrong year: the failure-path test

**Question:** 2027年度のデジタル化・AI導入補助金の公募スケジュールはいつですか？

| | |
|---|---|
| Expected | declined (`not_answerable`) |
| Tool | none (measured: it declined without searching) |
| Must not | Give 2026 dates as the 2027 schedule |

**Gold:** measured top score 0.55; the 2026 schedule is retrievable. Declined for $0.0018 on 2026-09-21.

---

## After the run

- Record per question: status, reason, tools called, top score, cost, pass/fail per column.
- Recheck `RERANK_SCORE_THRESHOLD=0.3` against all ten top scores.
- Count uncited sentences in each answered response. Pass condition for the per-claim citation prompt: **zero**.
- Explain any run that uses more than 2 requests; the first paid run used 3.
