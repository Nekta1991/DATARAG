# Reranking on a host without a GPU — measurements

Reproduce: `python scripts/compare_rerankers.py --report` ($0, reads the cache).
Re-measure: drop `data/rerank_compare.json` and run without `--report` (~15 min,
still $0, rate-limited).

## Why

The dashboard's backend still runs on one PC behind an ngrok tunnel, so the
site is down whenever that machine is off. Moving it to Vercel means giving up
`BAAI/bge-reranker-v2-m3`: it is 2.29 GB of weights plus torch, against a
500 MB standard Python bundle limit, and a Vercel function has no GPU to run it
on anyway. Reranking has to become an API call — Voyage's `rerank-2.5`, free
inside the same 200M-token allowance as the embeddings.

That swap invalidates `RERANK_SCORE_THRESHOLD=0.3`, which was calibrated
against bge's sigmoid output. Gate 1 is the only thing standing between an
off-topic question and a paid Claude call, so the replacement is measured, not
guessed.

## The calibration set

The validation suite has exactly one must-stop question (Q8, off-topic), which
is too thin a basis for a threshold. Four negatives were added, two of them
deliberately *adjacent* — Japanese business questions that sound like they
belong to this corpus and do not — because the easy ones (weather, Python)
score ~0.000 under bge and prove nothing about where to put the line.

Q9 and Q10 are must-**pass**: they are on-topic questions the model is supposed
to decline at gate 2, having seen the evidence. A gate-1 threshold that stopped
them would turn a reasoned 「この情報からは判断できません」 into a silent score
decline.

## Finding 1 — the unpaid rate limit, not the token allowance, is the constraint

Reranking is free. The *rate* limit is what bites, and it is stricter than its
advertised "3 RPM / 10K TPM" suggests. Sending one payload three times, 30 s
apart:

| request size | succeeded |
|---|---|
| 805 tokens | 5/5 (at 25 s spacing — faster than the advertised 3 RPM) |
| 3,000 | 3/3 |
| 5,000 | 3/3 |
| 6,500 | 0/3 |
| 8,000 | 0/3 |

A full candidate pool for this corpus is 2,872–10,262 tokens, so a third of the
questions could not be reranked at all without trimming.

*Caveat, stated rather than hidden:* that table used 30 s spacing, so size and
rate are partly confounded — 6,500 might succeed at 70 s spacing. It does not
change the conclusion, because 5,000 already clears the quality bar below.

Voyage's own accounting also runs above a local token count (8,144 reported for
a 7,948-token pool) and its window is a real 60 s: two 5,000-token requests
41 s apart were refused, 10,253 tokens inside one minute.

## Finding 2 — a character-based token estimate is useless here

The first version of this script estimated the payload as chars/1.4 and
predicted **44,020 tokens for a pool that is really 9,430**. That looked exactly
like "the corpus is too big to rerank" and was wrong.

Chars-per-token runs ~1.3 on dense prose but ~2.8 across the corpus, because the
markdown tables are padded with alignment spaces that cost characters and almost
no tokens. Measured: collapsing every run of spaces removes **38% of the
characters and 2.2% of the tokens** — so that is not an optimization either.

The budget now counts with Voyage's own tokenizer, vendored into `rag/assets/`
so it needs neither `transformers` nor a network call on a cold start.

## Finding 3 — how far the pool can be trimmed

`budget_pool()` keeps the largest prefix that fits, ordered by a candidate's
best position in **either** retrieval stage. That detail is the whole thing:
the 北海道 gold chunk has `vector_rank=None, lexical_rank=1` — found only by
BM25, at vector rank 211 of 1,025 — so ordering by vector rank would sort it
last and the budget would drop precisely the chunk hybrid retrieval exists to
rescue. It survives, and still reranks to #1 at 0.5955.

Top-1 passage against the local reranker over the *full* pool:

| budget | same top-1 | notes |
|---|---|---|
| 5,000 | **12 / 14** | Q7 (clears gate 1 via `named_doc`, not score) and Q5 (0.5203 instead of 0.7573) |
| 3,000 | 8 / 14 | six questions get a different best passage — lost answer quality, not just a lower score |

All five must-stop negatives stay at ~0.000 at every budget, so the decline gate
is unharmed either way. It is answer quality that degrades.

**5,000 is where reliability and quality meet**, and it is the default.

## Finding 4 — the threshold

Both backends scored over the identical 5,000-token budgeted pool, so the
columns are comparable. **The bge column is therefore not the production local
configuration**, which reranks the full pool and has no budget — it is bge
restricted to Voyage's pool so the comparison means something.

| id | expect | bge | voyage | note |
|---|---|---|---|---|
| 1 | must_pass | 0.5955 | 0.6953 | clause inside a paragraph |
| 2 | must_pass | — | — | not measured; see below |
| 3 | must_pass | 0.9798 | 0.8828 | 枠 discrimination |
| 4 | must_pass | 0.9949 | 0.9297 | 枠 + 事業者区分 |
| 5 | must_pass | 0.5203 | 0.5938 | cross-programme |
| 6 | must_pass | 0.9963 | 0.9375 | 交付規程 binding |
| 7 | must_pass | 0.1246 | 0.4727 | named doc — clears gate 1 via `named_doc`, not score |
| 8 | must_stop | 0.0000 | 0.3438 | off-topic |
| 9 | must_pass | 0.5618 | 0.6172 | on-topic, absent → model declines at gate 2 |
| 10 | must_pass | 0.5069 | 0.5547 | wrong year → model declines at gate 2 |
| 101 | must_stop | 0.0000 | 0.3340 | off-topic, navigation |
| 102 | must_stop | 0.0001 | 0.2773 | off-topic, programming |
| 103 | must_stop | 0.0033 | **0.3613** | adjacent: corporate tax |
| 104 | must_stop | 0.0015 | **0.3652** | adjacent: social insurance |

**Voyage separates, and a threshold exists.**

| | lowest must-pass | highest must-stop | gap |
|---|---|---|---|
| voyage | 0.4727 (Q7) | 0.3652 | **+0.1074** |
| voyage, excluding Q7 | 0.5547 (Q10) | 0.3652 | **+0.1895** |

**Recommended: `RERANK_SCORE_THRESHOLD=0.42` when `RERANK_BACKEND=voyage`.**
That is the midpoint of the usable range and leaves ~0.055 of margin on each
side. It also clears Q7 on score alone, so the `named_doc` pass stops being
load-bearing for that question rather than merely redundant.

The two *adjacent* negatives earned their place: at 0.3613 and 0.3652 they are
the highest must-stops, above the off-topic ones (0.2773–0.3438). Calibrating
on weather and Python alone would have suggested a threshold near 0.35 with
apparently comfortable headroom, and the first plausible-sounding tax question
would have gone through as a paid call.

**Q2 is not measured.** Its rerank request was refused repeatedly, including
after 120 s and 200 s of deliberate idle, so this is recorded as unmeasured
rather than worked around. It does not change the boundary: Q2 is a must-pass,
and the two questions most like it — Q3 and Q4, the other numeric 補助率 ones —
score 0.8828 and 0.9297, far above the 0.4727 floor.

### The trap this file exists to prevent

Carrying `RERANK_SCORE_THRESHOLD=0.3` across backends would **not** fail loudly.
Voyage's floor is ~0.28–0.37, so every off-topic question clears 0.3 and reaches
Claude as a **paid** call. Gate 1 would still log, still print a score, and still
say `pass` — it would simply have stopped being a gate.

## What this does not decide

The ceiling above belongs to Voyage's *unpaid* tier, not to `rerank-2.5`. With
a payment method on the account the rate limits lift and the full pool fits with
no trimming and no quality loss — reranking stays free either way, inside the
200M allowance. Staying unpaid costs two things: the Q5-class quality loss
above, and a serving rate of roughly one query per minute.

Whether that trade is worth making, or whether the backend should simply stay
on the GPU machine, is a decision for the project owner, not for this file.
