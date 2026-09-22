# Validation results — stub

Run file `data/validation_runs/20260922-184639_stub.json` · model `stub` · threshold 0.3 · prompt default · **1/2 passed** · run cost **$0.0000** · ledger $0.0542 / $5.00

> Stub run: the model is a script that cites the first passage. Content checks are
> expected to fail; this checks retrieval, both gates, grading and this report.

| # | Status / path | Tools | Top | Req | Cost | status | path | contain | cite | must not | Result |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 6 | answered / answered | search | 0.996 | 0 | $0.0000 | ✓ | ✓ | ✓ | ✓ | — | **PASS** · A ✗ · B ✗ |
| 7 | answered / answered | search | 0.254 | 0 | $0.0000 | ✓ | ✓ | ✗ | ✗ | — | **FAIL** |

## Gate-1 threshold recheck

Threshold 0.3. Lowest score that must pass: 0.2543.

Q7 0.254 · Q6 0.996

## Per question

### Q6 — PASS · A ✗ · B ✗

**Q:** 補助金の交付決定が取り消されるのはどのような場合ですか？

`answered/answered` · top 0.9963 · $0.0000 · 19.2s

- rule A: FAIL — no 枠 named with a matching 交付規程 citation (named none)
- rule B: FAIL — does not say the rule is shared
- Rule A / rule B are both graded until the user picks one. The 取消し article is identical in 通常/インボイス×2/セキュリティ (第27条) but differs in 複数者連携 (第25条), which Q6's top 5 does not retrieve.

**Answer:**

> （スタブ回答）（交付決定の取消し）
> 第２７条 事務局は、補助事業者が次の各号のいずれかに該当するときは、第１６条第１項の規定 に基づく

- [デジタル化・AI導入補助金2026 通常枠 交付規程] 「（交付決定の取消し）
第２７条 事務局は、補助事業者が次の各号のいずれかに該当す」

- [ ] Uncited sentences (read by hand, target 0): __

- req 1: `search_knowledge_base`
- req 2: `final_result`

### Q7 — FAIL

**Q:** 通常枠の交付規程は全体としてどのような構成（どのような条項の流れ）になっていますか？

`answered/answered` · top 0.2543 · $0.0000 · 3.7s

- missing: 4 of 交付の目的/補助対象としない事業/交付申請/交付決定/実績報告/額の確定/取消/返還/財産の管理
- cited ['デジタル化・AI導入補助金2026 セキュリティ対策推進枠 公募要領']
- FINDING tool: expected retrieve_full_document('09_dx_ai_kitei_tsujyo'), got ["search_knowledge_base('通常枠の交付規程は全体としてどのような構成（どのような条項の流れ）になっていますか？')"]

**Answer:**

> （スタブ回答）（１） 交付申請の流れ
> 交付申請の基本的な流れは以下のとおり（このうち、申請者（中小企業・小規模事業者等）が行うアクショ

- [デジタル化・AI導入補助金2026 セキュリティ対策推進枠 公募要領] 「（１） 交付申請の流れ
交付申請の基本的な流れは以下のとおり（このうち、申請者（」

- [ ] Uncited sentences (read by hand, target 0): __

- req 1: `search_knowledge_base`
- req 2: `final_result`

