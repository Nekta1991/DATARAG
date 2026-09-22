# RAG portfolio — project instructions

See `RAG_build_brief_v2.md` for architecture. This file holds operating rules.

## ⚠️ API cost rule (hard requirement)

**Budget: $5.00 total for this entire test project. Do not exceed it.**

Before running ANY action that spends Anthropic API credits, **stop and tell the
user**:

1. What the action is
2. Estimated cost in dollars, with the token math behind it
3. Running total spent so far, if known

Then wait for confirmation. Do not batch-run paid calls "to check something."
Never re-run a paid test suite without asking again — repetition is how a $0.15
suite becomes $3.00.

Prefer the cheapest thing that answers the question:
- Validate keys with `GET /v1/models` — **free**, no tokens billed
- Estimate size with `count_tokens` — **free**
- Test prompt/retrieval logic on stubbed generation before paying for real output
- One representative question beats running all ten

### What costs money vs. what doesn't

| Action | Cost |
|---|---|
| Voyage embeddings (ingest + query) | **$0** — 200M free tokens; corpus is ~302k |
| Neon Postgres (free tier) | **$0** |
| Cross-encoder reranking (local torch) | **$0** — runs on this machine |
| `GET /v1/models`, `count_tokens` | **$0** |
| **Claude API generation** | **the only paid component** |

### Claude API rates (per million tokens, cached 2026-09-19)

| Model | Input | Output |
|---|---|---|
| `claude-haiku-4-5-20251001` | $1.00 | $5.00 |
| `claude-sonnet-5` ← current `.env` | $2.00 | $10.00 |
| `claude-opus-5` | $5.00 | $25.00 |

Prompt caching: cache **write** ≈ 1.25× input rate, cache **read** ≈ 0.1×.

### Measured estimate for this system

One query ≈ 5 reranked chunks (~2.6k tokens) + system prompt + question
≈ **3.1k input / ~0.5k output**.

| | Per query | 10-question suite |
|---|---|---|
| `claude-haiku-4-5-20251001` | ~$0.006 | ~$0.06 |
| `claude-sonnet-5` | ~$0.011 | ~$0.11 |
| `claude-opus-5` | ~$0.028 | ~$0.28 |

Agent tool-selection adds a second call on some questions — budget ~2× the above.
At Sonnet rates the $5 ceiling is roughly **250–450 queries**. Comfortable, but
not unlimited.

**The decline-to-answer gate saves money**: below-threshold queries skip
generation entirely and cost $0. Do not "just let it generate" to see output.

## Model note

`ANTHROPIC_MODEL` in `.env` is `claude-sonnet-5`. If the user wants maximum
quality over cost, `claude-opus-5` is the upgrade; `claude-haiku-4-5-20251001` is the
cheaper step down (bare alias `claude-haiku-4-5` is NOT served on this account). Model choice is the user's decision — surface the cost
difference, don't silently switch.

## Secrets

- Real values live in `.env` (gitignored). `.env.example` holds placeholders only.
- `DATABASE_URL`, `DATABASE_URL_UNPOOLED`, `NEON_BRANCH` are managed by
  `neon link` / `neon deploy` — do not hand-edit those three lines.
- Never print a key, connection string, or token into the transcript.
