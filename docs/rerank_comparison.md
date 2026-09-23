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

*(measurement in progress — `scripts/compare_rerankers.py --report` prints the
current table, and this section is filled in from it)*

The shape is already clear from the cases measured: Voyage's scale is
compressed with a high floor. It scores an off-topic question ~0.33 where bge
scores 0.0000, so **carrying `RERANK_SCORE_THRESHOLD=0.3` across would turn
gate 1 into a pass-through** and bill the paid model for 明日の東京の天気.

## What this does not decide

The ceiling above belongs to Voyage's *unpaid* tier, not to `rerank-2.5`. With
a payment method on the account the rate limits lift and the full pool fits with
no trimming and no quality loss — reranking stays free either way, inside the
200M allowance. Staying unpaid costs two things: the Q5-class quality loss
above, and a serving rate of roughly one query per minute.

Whether that trade is worth making, or whether the backend should simply stay
on the GPU machine, is a decision for the project owner, not for this file.
