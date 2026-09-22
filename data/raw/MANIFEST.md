# Corpus manifest — RAG sprint (9/14–9/24)

14 official public PDFs (372 pages, ~360k chars). Downloaded 2026-09-14. Public government documents only;
no internal/company documents in this corpus.

| # | File | Title (as printed on p1) | Source URL | Pages | Bytes | SHA-256 (short) |
|---|------|--------------------------|------------|-------|-------|-----------------|
| 1 | `01_dx_ai_hojokin_2026_koubo_fukusu.pdf` | デジタル化・ＡＩ導入補助金２０２６ 公募要領（複数者連携デジタル化・ＡＩ導入枠） | https://mirasapo-plus.go.jp/wordpress/wp-content/uploads/2026/05/18164046/it2026_koubo_fukusu.pdf | 42 | 1,031,639 | `9d719b21` |
| 2 | `02_smart_nogyo_koubo_bekki1_hashiwatashi.pdf` | スマート農業・農業支援サービス事業加速化総合対策事業（スマート農業技術と産地の橋渡し支援）公募要領（第２次）＝ 別記1 | https://www.maff.go.jp/j/supply/hozyo/nousan/attach/pdf/260805_140-1-6.pdf | 12 | 910,924 | `3a6e8407` |
| 3 | `03_smart_nogyo_koubo_bekki2_service.pdf` | スマート農業・農業支援サービス事業加速化総合対策事業（農業支援サービスの立上げ・事業拡大支援）公募要領（第２次）＝ 別記2 | https://www.maff.go.jp/j/supply/hozyo/nousan/attach/pdf/260805_140-1-3.pdf | 15 | 656,482 | `dad1c74d` |
| 4 | `04_jisshi_youryou_bekki1_hokkaido.pdf` | スマート技術体系転換加速化支援（広域型）公募要領（第２次） | https://www.maff.go.jp/j/supply/hozyo/nousan/attach/pdf/260224_140-1-4.pdf | 10 | 318,416 | `97b1f857` |

Parent page for #2/#3 (links are re-issued periodically):
https://www.maff.go.jp/j/supply/hozyo/nousan/260805_140-1.html

## Verification notes

- All four PDFs carry a real text layer (~1,000 extractable chars/page). **No OCR needed.**
- **#4 title drift**: the brief describes this as 実施要領別記1. The URL resolves to
  a 公募要領（広域型）instead. Content requirement is still satisfied — p1 carries the
  exact clause: 「複数の都道府県にわたり事業を実施する事業実施主体（北海道内で取り組む
  場合にあっては、北海道内の複数総合振興局・振興局で事業を実施する事業実施主体）」.
  The 実施要領別記1 listed on the parent page is an EXCEL form (様式第1-1号), not a
  requirements document, so this PDF is the correct source for the 北海道 rule.
- **#3 size drift**: brief notes ~1,181KB; current issue is 642KB (第２次 re-issue).
  Title matches the brief exactly.
- **Coverage gap affecting the numeric test question**: 補助率 / 補助上限額 appear in
  #1 (pp. 3, 5, 6, 20, 21) and #4 (p. 2) only. #2 and #3 contain no 補助率/補助上限
  figures — those live in separate 実施要領 documents not in this corpus. Design the
  numeric test question against #1 or #4; a 補助率 question aimed at #2/#3 is an
  unintended decline case.

## Batch 2 — デジタル化・AI導入補助金2026, remaining 枠 + authority layer

Added 2026-09-14 from https://it-shien.smrj.go.jp/download/ (base URL
`https://it-shien.smrj.go.jp/pdf/<name>.pdf`). All 公募要領 更新日 2026-08-28;
交付規程 更新日 2026-03-30 (複数者連携: 2026-01-23); 加点項目一覧 2026-06-03.

### 公募要領 — the four 枠 missing from batch 1

| # | File | 枠 | Source name | Pages | c/page | SHA-256 |
|---|------|----|-------------|-------|--------|---------|
| 5 | `05_dx_ai_koubo_tsujyo.pdf` | 通常枠 | `it2026_koubo_tsujyo` | 54 | 1084 | `20d95ef6` |
| 6 | `06_dx_ai_koubo_invoice_taiou.pdf` | インボイス枠（インボイス対応類型） | `it2026_koubo_invoice` | 45 | 1043 | `4b99134c` |
| 7 | `07_dx_ai_koubo_invoice_denshi.pdf` | インボイス枠（電子取引類型） | `it2026_koubo_denshi` | 46 | 966 | `716dddf0` |
| 8 | `08_dx_ai_koubo_security.pdf` | セキュリティ対策推進枠 | `it2026_koubo_security` | 41 | 994 | `b8af6f56` |

### 交付規程 — binding regulations (authority layer above 公募要領)

| # | File | 枠 | Source name | Pages | c/page | SHA-256 |
|---|------|----|-------------|-------|--------|---------|
| 9 | `09_dx_ai_kitei_tsujyo.pdf` | 通常枠 | `it2026_kitei_tsujyo` | 21 | 868 | `b425df81` |
| 10 | `10_dx_ai_kitei_invoice_taiou.pdf` | インボイス対応類型 | `it2026_kitei_invoice` | 21 | 829 | `07a01310` |
| 11 | `11_dx_ai_kitei_invoice_denshi.pdf` | 電子取引類型 | `it2026_kitei_denshi` | 21 | 840 | `e72ba0ad` |
| 12 | `12_dx_ai_kitei_security.pdf` | セキュリティ対策推進枠 | `it2026_kitei_security` | 21 | 822 | `23c308d1` |
| 13 | `13_dx_ai_kitei_fukusu.pdf` | 複数者連携枠 | `it2026_kitei_fukusu` | 22 | 833 | `b564fe0e` |

### Scoring criteria

| # | File | Content | Source name | Pages | SHA-256 |
|---|------|---------|-------------|-------|---------|
| 14 | `14_dx_ai_katen_koumoku_list.pdf` | 加点項目一覧 (all 枠, one table) | `it2026_addition_list` | 1 | `c8cd574c` |

### Batch 2 notes

- All 10 verified: correct title on p1, rich text layer (822–1084 chars/page), no OCR needed.
- **交付規程 vs 公募要領**: 公募要領 = how to apply; 交付規程 = binding obligations,
  補助対象経費, 取消・返還条件. When the two appear to conflict, 交付規程 governs —
  the generation prompt should be told this, or the system will cite whichever it
  retrieved first.
- **Cross-枠 retrieval hazard (measured, not assumed)**: literal text overlap between
  the five 公募要領 is low — 0.5–5.3% Jaccard on 40-char shingles, 3.2–18.0% on
  20-char. Highest pairs: 電子取引 vs セキュリティ (18.0%), 通常枠 vs インボイス対応
  (13.3%). So these are genuinely distinct documents, not reskinned boilerplate.
  Semantic overlap still exceeds literal overlap (same concepts, same vocabulary),
  so `source_doc` must carry the 枠 name and answers must state which 枠 each cited
  chunk came from — but this is cheap insurance, not a crisis.

### Deliberately excluded

- **交付申請/実績報告 manuals** (`it2026_manual_*`, 5.7–11.9 MB each): screenshot-heavy
  UI walkthroughs, ~533 chars/page. ~30 MB of mostly-images for low text yield.
- **IT導入支援事業者一覧 (法人/コンソーシアム)**: thousands of company names, pure
  retrieval noise.
- **Flyers / 概要**: thin marketing copy (~636 chars/page). Note both METI-hosted
  links (`chusho.meti.go.jp/koukai/yosan/r8/digital_ai_summary.pdf` and
  `r7/r6_it.pdf`) return **403** to scripted download anyway.
- **Excel 様式** (`.xlsx`): not text documents.
- **ITツール登録要領 / IT導入支援事業者登録要領**: vendor-side audience, not the
  SME/consultant user this demo targets.
- **Prior-year archives (2025 / 2024 / 2023)**: excluded on purpose. Mixing program
  years is the most likely route to a confidently wrong 補助率 or 補助上限額.
