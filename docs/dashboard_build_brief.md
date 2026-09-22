# Dashboard build brief — query console (draft)

Drafted 2026-09-21. **Mock:** https://claude.ai/artifact/S3ApZuuSALLVkCoBoBLoHW
(Claude Design canvas; the example chips replay three recorded runs).

Six parts: **(1)** the prompt for Claude Design, **(2)** the event contract the
console renders (built), **(3)** the path to Vercel via GitHub (hosting decided:
this PC first, then all-Vercel), **(4)** the visual tokens taken from the official
site, **(5)** auth with Neon Auth, **(6)** how to run round 1 locally.

---

## 1. Prompt for Claude Design

Paste the block below into Claude Design with the mock canvas open.

```text
Build the high-fidelity design for DATARAG's query console, starting from the
artboard "Query console — recorded runs" on this canvas. Keep its layout and
visual language; fill in the missing states and the details below.

PRODUCT
DATARAG answers questions about two Japanese subsidy programmes
(デジタル化・AI導入補助金2026, スマート農業・農業支援サービス事業) from 14 official
documents. It either answers with verbatim, code-verified citations or declines
with 「この情報からは判断できません」. The dashboard is a developer console: its job
is to make retrieval and the two decline gates visible, step by step.

AUDIENCE
The developer, and interviewers watching a live demo. UI language: Japanese
labels, English technical identifiers (model names, tool names, event tags).

LAYOUT (desktop 1440 wide; also produce a 390-wide mobile artboard)
- Header: wordmark; corpus chip (14 docs · 1,025 chunks); model chip
  (claude-sonnet-5 · effort low); budget meter (spent / $5.00, thin bar).
- Left column: question input joined to its submit button (one control);
  example-question chips under it; answer card below.
- Right column: dark console window (monospace) streaming events; under it
  the rerank candidates table.
- Mobile: stack question → console (collapsible) → answer → candidates.

STATES — one artboard each
1. Idle: empty input, example chips, console shows "waiting for a question".
2. Running: console streams events one per line; thin indeterminate bar in
   the console header; answer card says results appear after verification.
3. Answered: green badge "ANSWERED · 引用検証済"; answer paragraphs; citations
   list, each with its source label (program + 枠 + doc type) and the verbatim
   quote; a meta line with requests, tokens, cost.
4. Declined by gate 1 (off-topic): red badge "DECLINED · score_gate · $0";
   reason: top score below 0.30, model never called.
5. Declined by gate 2 (on-topic, not answerable or a quote failed
   verification): red badge with the reason code; if quotes were rejected,
   list them struck through with "not found in retrieved text".
6. Error: API or network failure, shown in the console as an ERROR line and
   in the answer card, with a retry button.
7. Budget guard: a run refused because the ledger would pass $5.00; the
   submit button disabled with the reason next to it.

CONSOLE LINES
Grid of three columns: tag (fixed width, colored by tone), message, duration
(right-aligned, only when measured). Tones: info (grey), pass (green),
fail (red), agent (blue), cost (amber). Tags: RUN, EMBED, VECTOR, BM25,
RERANK, GATE 1, AGENT, TOOL, GATE 2, DECLINE, USAGE, COST, ERROR. The console
autoscrolls while running and can be copied as plain text.

CANDIDATES TABLE
Columns: rank, vector rank, BM25 rank ("—" when that stage did not find
it), rerank score with a small bar (0–1), source · heading. A caption states
the pool size and how many candidates only BM25 found.

RULES
- Use only the recorded numbers already on the canvas; anywhere else use
  labelled placeholders like [SCORE]. No invented metrics.
- Real <button>, <input> and <label> elements; visible focus rings; 4.5:1
  text contrast (check grey text on the dark console).
- No gradients, emoji or decorative icons; inline stroke icons only where
  they carry meaning (verified citation, error).
- Colors and element styling follow section 4 of this brief (taken from the
  official IT導入支援 site's CSS): purple #745285 primary, blue #41A6DF
  secondary, Noto Sans JP, 5px button corners, 100px pills, a 40×4px bar
  under section headings. IBM Plex Mono for the console and identifiers.
- Never use the official site's logos, emblems or name, and never suggest the
  tool is official. Keep the 「非公式デモ」 label in the header on every artboard.
```

---

## 2. Event contract (backend → console)

The console renders a **Server-Sent Events** stream (`text/event-stream`) from
`POST /api/query`. Each event is one console line. **Built 2026-09-21:** an
`emit` callback threads through `rag/agent.py` and `rag/retrieval.py`. The tool
events come from inside the tool functions, and every event carries `t_ms`
since the run started.

| Event | Payload | Console line |
|---|---|---|
| `run.start` | `question` | RUN 質問を受信 — … |
| `embed.done` | `model`, `dims`, `ms` | EMBED voyage-3.5 · 1024 dims |
| `vector.done` | `k`, `ms`, `best_similarity` | VECTOR pgvector top 20 · best 0.532 |
| `bm25.done` | `k`, `ms`, `lexical_only`, `pool` | BM25 … 4 not in vector set → pool 24 |
| `rerank.done` | `model`, `pairs`, `ms`, `top_score`, `candidates[]` | RERANK … (and fills the table) |
| `gate1` | `top_score`, `threshold`, `pass`, `via` (`score` / `named_doc` / null), `named_docs[]` | GATE 1 0.8907 ≥ 0.30 → PASS · or 0.254 < 0.30, names 09_dx_ai_kitei_tsujyo → PASS |
| `agent.start` | `model`, `effort` | AGENT claude-sonnet-5 · effort low |
| `agent.tool_call` | `tool`, `args` | TOOL search_knowledge_base("…") |
| `agent.tool_result` | `tool`, then `passages`, `cached` (search) or `doc_id`, `truncated`, `tokens` (full doc) or `error` | TOOL … 5 passages |
| `gate2` | `answerable`, `citations`, `verified`, `rejected[]`, `pass`, `reason` | GATE 2 1/1 quotes verified → PASS |
| `answer` | `status`, `reason`, `answer`, `citations[]` (`source_doc`, `quote`), `rejected_quotes[]`, `tool_calls[]` | fills the answer card |
| `usage` | `requests`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cost_usd`, `ledger_total_usd`, `budget_usd` — token fields absent on a gate-1 decline or stub run | USAGE / COST |
| `error` | `type`, `message` | ERROR |
| `done` | `ok`, `error?` | closes the stream |

`candidates[]` items: `rank`, `vector_rank` (null if BM25-only), `lexical_rank`
(null if vector-only), `rerank_score`, `similarity`, `source_doc`, `heading`,
`page_no`, `chunk_id`.

**Checked against the code 2026-09-22** (`rag/agent.py`, `rag/retrieval.py`, `rag/api.py`).
The rest of the contract a redesign must keep:

- **Event order.** Answered: `run.start → embed.done → vector.done → bm25.done → rerank.done →
  gate1 → agent.start → (agent.tool_call → [embed/vector/bm25/rerank for a new query] →
  agent.tool_result)* → gate2 → answer → usage → done`. Gate-1 decline stops after `gate1`
  with `answer → usage → done`. `error` can appear anywhere before `done`. A repeated search
  query sends `agent.tool_result` with `cached: true` and no retrieval events.
- **Framing.** sse-starlette: `event:` and `data:` on consecutive lines (`\r\n`), a blank
  line between events. A blank line *inside* an event splits it: the parser then sees
  unnamed `message` events (happened in the preview fixture, fixed 2026-09-22).
- **Request.** `POST /api/query`, `Authorization: Bearer <Neon Auth JWT>`, JSON
  `{"question": 1–500 chars, "stub": bool}`. `stub: true` is free (no model call).
  401 bad/missing token · 403 not admin · 429 a query is already running.
- **`GET /api/status`** (admin): `documents`, `chunks`, `model`, `effort`, `threshold`,
  `spent_usd`, `budget_usd`, `busy`, `user`. **`GET /api/health`** (open): `ok`, `warm`,
  `warm_seconds`.
- **Preview.** `web/app/preview` (only with `DESIGN_PREVIEW=1`) replays
  `web/public/preview/*.sse` through the real dashboard with a fake session. Temporary:
  delete it before any push.

---

## 3. Deploying to Vercel via GitHub

### What moves as-is

- **Frontend:** Next.js (App Router) on Vercel, built from the GitHub repo
  (`github.com/Nekta1991/DATARAG`, public).
- **Secrets** (`VOYAGE_API_KEY`, `ANTHROPIC_API_KEY`, `DATABASE_URL`) go in Vercel
  environment variables, never in the repo. `.gitignore` already excludes
  `.env` and `keys.txt`.

### Decision (2026-09-21): B for the first demo round, then A

The first round runs the backend on this PC (option B), keeping the measured bge
setup. After that the plan is to move to option A, all on Vercel with an API
reranker, which needs recalibration first. The query path needs the **bge
reranker: 2.29 GB of weights, run on this machine's GPU**, and Vercel
functions have no GPU. The options as weighed:

| Option | How | For | Against |
|---|---|---|---|
| **A. All on Vercel, API reranker** | FastAPI as a Vercel Python function; replace local bge with Voyage's rerank API | One platform; no torch in the bundle; nothing to keep running | Reverses the local-reranker decision. `RERANK_SCORE_THRESHOLD` must be recalibrated and the regression tests re-run ($0). Adds a Voyage request per query against the 3 RPM limit. Check that the free token allowance covers rerank models |
| **B. Vercel UI + this PC** | FastAPI runs here on the GPU; Vercel reaches it through a tunnel (e.g. Cloudflare Tunnel) | Keeps everything measured so far; no code change to retrieval | Works only while this PC is on; the tunnel is one more thing to run for a demo |
| **C. Vercel UI + a hosted backend** | Container on a GPU or CPU host outside Vercel | Always on | New platform and possibly a new bill; CPU reranking speed unmeasured |
| **D. bge on Vercel CPU** | Large Python function bundling torch and the 2.29 GB model | Single platform, same reranker | Cold start loads 2.29 GB; CPU rerank latency unmeasured and likely slow |

Recommendation: **A** if the demo must run with nothing local, **B** if it will be
shown from this laptop. Either way, retrieval is re-verified with the free tests
before the UI is wired.

### Required whatever the option

1. **Protect the deployment.** A public URL calling the Anthropic key lets anyone
   spend the $5 budget. Turn on Vercel Deployment Protection (or an access token
   on `/api/query`) before the first deploy.
2. **Move the spend ledger into Neon.** `data/spend_ledger.jsonl` is a local file.
   On serverless the filesystem is per-instance and ephemeral, so the $5 guard
   would reset silently. A `spend_ledger` table keeps it enforced.
3. **Load the reranker (if kept) and the BM25 index at startup.** Measured: 113 s
   cold reranker load and 2.7 s BM25 build. Neither should land on a user's first query.
4. **Commit order:** `.gitignore` first (now also ignoring `data/spend_ledger.jsonl`),
   then the code. Only `README.md` is in git today.

---

## 4. Visual design — taken from the official site

Source: the CSS of https://it-shien.smrj.go.jp/ (fetched 2026-09-21; colors ranked
by frequency across its 13 stylesheets). **Adopted:** palette, typeface, element
shapes. **Not adopted:** logos, emblems, the programme name as branding, or
anything implying the tool is official. The header carries 「非公式デモ」.

| Token | Value | Official use | Dashboard use |
|---|---|---|---|
| Primary | `#745285` | brand button, heading bar | submit button, active chip, heading bars, score bars |
| Primary hover | `#A267BF` | button hover | submit hover |
| Primary dark | `#341C4E` | deep accents | wordmark, decline headline; console ground darkened to `#221431` |
| Primary tint | `#ECE5F1` | lavender section | chip hover, score-bar track |
| Secondary | `#41A6DF` | "primary-strong" blue | candidates heading bar, focus ring, answered progress bar |
| Secondary dark | `#1479B2` → **`#11689A`** | deep-blue button | answered badge, citation label (darkened: 4.40:1 → 5.59:1 on `#F0F7FC`) |
| Secondary tint | `#F0F7FC` | menu hover | citation block, answered badge |
| Accent | `#FF587E` / `#FFEEF2` | pink highlights | decline badge ground; text `#B3264A` (5.70:1) |
| Text | `#424242` | body text | body text |
| Muted | `#6D6D6D` | button hover grey | secondary text (5.17:1 on white) |
| Ground | `#F8F8F8` / `#FFFFFF` | section backgrounds | page / cards |
| Line | `#E5E5E5` | dividers | card borders, dividers |

Elements copied in shape: buttons with 5px corners, weight 700, a soft tinted
shadow (`2px 4px 15px #7452854D`) and 30/40/50px heights; pills at 100px radius;
section headings with a 40×4px bar underneath. Typeface: **Noto Sans JP** (the
site's only webfont).

Every text/ground pair was measured against WCAG 4.5:1. The console tones
(pale purple, blue, peach and taupe on `#221431`) all measure 7.5:1 or higher.

---

## 5. Auth — Neon Auth (Managed Better Auth)

**Yes, the database can hold user registrations, like Supabase Auth.** Neon Auth
keeps users, sessions and auth config in a `neon_auth` schema in the same
Postgres. It branches with the database, and it's queryable with SQL.

- **Available here:** the project is on AWS (`aws-ap-southeast-1`) with no IP
  allow list, which Managed Auth requires. Enabled with `auth: true` in `neon.ts`,
  then `neon deploy`.
- **Login methods:** email + password, Google / GitHub / Vercel OAuth, email OTP,
  magic link, and an admin plugin. MFA and passkeys are not offered on the
  managed version.
- **Emails:** verification and OTP mail go out through Neon's shared sender for
  development. Production needs custom SMTP.
- **Backend check:** the frontend gets a JWT (Ed25519, 15-minute expiry); FastAPI
  verifies it against `NEON_AUTH_JWKS_URL`. This works for option B too, because
  the tunnel-reached backend verifies the same token.
- **Client:** `@neondatabase/auth` (Better Auth methods), optional
  `@neondatabase/auth-ui` for ready-made screens.

**The catch for this project: open sign-up would spend the budget.** Anyone who
registers could run paid queries. Registration has to be closed: invite-only
through the admin plugin, or an allowlist table the backend checks before a paid
run. Because users live in the same database, the spend ledger (moving to Neon
anyway) can then be kept **per user**, with a per-user cap under the $5 total.

Differences from Supabase Auth: no phone-first or SAML sign-in, and no MFA on the
managed version. Nothing is being migrated here, so neither matters.

---

## 6. Running round 1 locally (built 2026-09-21)

```bash
# 1. once: create the admin (password typed at a hidden prompt; sets role=admin in neon_auth."user")
.venv/Scripts/python.exe scripts/create_admin.py <admin email>

# 2. API on this PC (warms reranker + BM25 at startup, ~15 s when cached)
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m uvicorn rag.api:app --host 127.0.0.1 --port 8000

# 3. frontend
cd web && npm run build && npx next start -p 3000
```

Open **http://localhost:3000**. Not `127.0.0.1`: Neon Auth trusts the hostname
`localhost` only, and sign-in from `127.0.0.1` fails with `INVALID_ORIGIN` (403).

- Stub mode is the default: real retrieval and gate 1, stubbed model, **$0**. The
  「本番モード」 checkbox makes a run paid (~$0.03, capped at $0.19).
- Free tests: `scripts/test_gates.py` (7) and `scripts/test_api.py` (10).
- Before the tunnel (round 1 demo): add the tunnel's origin to `API_ALLOWED_ORIGINS`,
  set `NEXT_PUBLIC_API_URL` to the tunnel URL, and add the Vercel origin to Neon Auth
  trusted domains (`neon neon-auth domain add https://<app>.vercel.app`).
