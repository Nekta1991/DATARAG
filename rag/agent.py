"""The agent: two tools, and a two-layer decline gate enforced in code.

    python -m rag.agent "北海道内で事業を実施する場合の要件は？"          # PAID
    python -m rag.agent "..." --stub      # free: stubbed model, real retrieval
    python -m rag.agent --ledger          # spend so far

Flow for one question:

    1. Score gate ($0). Hybrid retrieval + rerank on the raw question. Top
       score below RERANK_SCORE_THRESHOLD -> decline, no model call at all -
       unless the question names a corpus document (「通常枠の交付規程」):
       whole-document questions match no single chunk well.
    2. Agent run (paid). Claude picks search_knowledge_base and/or
       retrieve_full_document, then returns a structured Answer: answerable,
       the answer, and verbatim quotes with the source_doc each came from.
    3. Evidence gate ($0). Code checks every quote appears, character for
       character, in a passage a tool actually returned this run, under the
       source_doc the citation names. Any miss, or answerable=false -> decline.

Why two layers, measured 2026-09-21: rerank scores separate off-topic
questions (<= 0.001) and retrieval misses (0.09) from everything else, but
on-topic unanswerable questions scored 0.55-0.93 against 0.89-0.9994 for
answerable ones. No threshold separates those. The model proposes evidence;
code decides whether it holds - the decline is never the agent's choice alone.

Known limits of the evidence gate: it proves every citation is real, not that
every sentence is cited - the prompt asks for one citation per claim and
nothing uncited, which narrows that gap without closing it. And the model may
decline without searching when the question's premise (another year, another
programme) is plainly outside the corpus the instructions describe: measured
on the 2027 question, 1 request, $0.0018. Accepted - the corpus is 2026-only.

Every paid run is appended to data/spend_ledger.jsonl, and a run that could
take the ledger past BUDGET_USD is refused before any request is sent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext, Tool, ToolOutput
from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart
from pydantic_ai.usage import UsageLimits

from rag import config
from rag.documents import document_catalog, named_documents, retrieve_full_document
from rag.retrieval import Hit, embed_query, hybrid_candidates, rerank, TOP_N

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
EFFORT = os.getenv("GENERATION_EFFORT", "low")
MAX_OUTPUT = int(os.getenv("GENERATION_MAX_TOKENS", "2048"))
THRESHOLD = float(os.getenv("RERANK_SCORE_THRESHOLD", "0.3"))
BUDGET_USD = float(os.getenv("BUDGET_USD", "5.00"))

DECLINE = "この情報からは判断できません"

# $/token, from CLAUDE.md (cached 2026-09-19). Cache write 1.25x input, read 0.1x.
PRICES = {
    "claude-haiku-4-5-20251001": (1.00e-6, 5.00e-6),
    "claude-sonnet-5": (2.00e-6, 10.00e-6),
    "claude-opus-5": (5.00e-6, 25.00e-6),
}

# Hard ceilings per question. Worst case at Sonnet rates: 60k in ($0.12) +
# 4k out ($0.04) = $0.16. A normal run is ~10x under this.
LIMITS = UsageLimits(request_limit=4, input_tokens_limit=60_000,
                     output_tokens_limit=4_000)
WORST_CASE_USD = (LIMITS.input_tokens_limit * PRICES.get(MODEL, PRICES["claude-opus-5"])[0] * 1.25
                  + LIMITS.output_tokens_limit * PRICES.get(MODEL, PRICES["claude-opus-5"])[1])

LEDGER = config.ROOT / "data" / "spend_ledger.jsonl"


# -- output schema ----------------------------------------------------------

class Citation(BaseModel):
    source_doc: str = Field(description="出典ラベル。ツール結果の「出典:」の値をそのまま写す")
    quote: str = Field(description="根拠となる原文。ツール結果から一字一句そのまま抜き出す（20〜150字）")


class Answer(BaseModel):
    answerable: bool = Field(description="取得した文書だけで質問に答えられる場合のみ true")
    answer: str = Field(description="日本語の回答。answerable が false なら空文字")
    citations: list[Citation] = Field(default_factory=list,
                                      description="回答の各主張を支える引用。主張ごとに1件、最大5件")


# -- per-run state ----------------------------------------------------------

@dataclass
class Deps:
    """Everything the tools returned this run - the evidence gate checks
    quotes against this, and only this. `emit`, if set, receives one
    (event, data) per pipeline step: the dashboard console's feed."""
    evidence: list[tuple[str, str]] = field(default_factory=list)  # (source_doc, text)
    searches: dict[str, list[Hit]] = field(default_factory=dict)
    tool_calls: list[str] = field(default_factory=list)
    emit: object = None

    def say(self, event: str, data: dict) -> None:
        if self.emit:
            self.emit(event, data)


@dataclass
class QueryResult:
    question: str
    status: str                  # answered | declined
    reason: str                  # answered | score_gate | not_answerable | no_citation | unverified_quote | limit
    answer: str
    citations: list[Citation]
    tool_calls: list[str]
    top_score: float
    usage: dict
    cost_usd: float
    rejected_quotes: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)  # per model request, see request_trace


# -- tools ------------------------------------------------------------------

def _format_hits(hits: list[Hit]) -> str:
    blocks = []
    for i, h in enumerate(hits, 1):
        blocks.append(
            f"[{i}] 出典: {h.source_doc}\n"
            f"    authority_rank={h.authority_rank} (1=交付規程が優先) / "
            f"見出し: {h.heading or '-'} / p.{h.page_no}\n{h.content}")
    return "\n\n".join(blocks)


def _search(deps: Deps, query: str) -> list[Hit]:
    if query in deps.searches:
        return deps.searches[query]
    t = time.time()
    qvec = embed_query(query)
    deps.say("embed.done", {"model": "voyage-3.5", "dims": len(qvec),
                            "ms": int((time.time() - t) * 1000)})
    pool = hybrid_candidates(query, qvec, emit=deps.say)
    t = time.time()
    ranked = rerank(query, pool)
    deps.say("rerank.done", {
        "model": os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),
        "pairs": len(pool), "ms": int((time.time() - t) * 1000),
        "top_score": round(ranked[0].rerank_score, 4) if ranked else 0.0,
        "candidates": [{"rank": i, "vector_rank": h.vector_rank, "lexical_rank": h.lexical_rank,
                        "rerank_score": round(h.rerank_score, 4), "similarity": round(h.similarity, 4),
                        "source_doc": h.source_doc, "heading": h.heading, "page_no": h.page_no,
                        "chunk_id": h.chunk_id} for i, h in enumerate(ranked[:TOP_N], 1)]})
    deps.searches[query] = ranked[:TOP_N]
    return deps.searches[query]


def search_knowledge_base(ctx: RunContext[Deps], query: str) -> str:
    """補助金文書を検索し、関連度の高い5件の抜粋を出典付きで返す。
    具体的な要件・金額・期限など、特定の事実を問う質問に使う。"""
    cached = query in ctx.deps.searches
    ctx.deps.say("agent.tool_call", {"tool": "search_knowledge_base", "args": {"query": query}})
    hits = _search(ctx.deps, query)
    ctx.deps.tool_calls.append(f"search_knowledge_base({query!r})")
    ctx.deps.evidence += [(h.source_doc, h.content) for h in hits]
    ctx.deps.say("agent.tool_result", {"tool": "search_knowledge_base",
                                       "passages": len(hits), "cached": cached})
    return _format_hits(hits)


def _retrieve(ctx: RunContext[Deps], doc_id: str) -> str:
    ctx.deps.tool_calls.append(f"retrieve_full_document({doc_id!r})")
    ctx.deps.say("agent.tool_call", {"tool": "retrieve_full_document", "args": {"doc_id": doc_id}})
    try:
        doc = retrieve_full_document(doc_id)
    except KeyError as e:
        ctx.deps.say("agent.tool_result", {"tool": "retrieve_full_document", "error": "unknown doc_id"})
        return f"エラー: {e}"
    ctx.deps.evidence.append((doc.source_doc, doc.text))
    ctx.deps.say("agent.tool_result", {"tool": "retrieve_full_document", "doc_id": doc_id,
                                       "truncated": doc.truncated, "tokens": doc.tokens_returned})
    return f"出典: {doc.source_doc}\n" + doc.as_tool_text()


@lru_cache(maxsize=1)
def _retrieve_tool() -> Tool:
    # The catalog goes into the description, so the agent picks an ID from
    # what exists rather than inventing a title.
    lines = [f"- {d['doc_id']}: {d['source_doc']} (約{d['tokens']:,}トークン)"
             for d in document_catalog()]
    return Tool(
        _retrieve, takes_ctx=True, name="retrieve_full_document",
        description=(
            "文書1件の全文を返す（2万トークンを超える文書は先頭の節のみ、残りは見出し一覧）。"
            "文書全体の構成や、複数の節にまたがる内容を問う質問に使う。"
            "特定の事実だけなら search_knowledge_base を使うこと。"
            "doc_id は次のいずれか:\n" + "\n".join(lines)),
    )


INSTRUCTIONS = """\
あなたは日本の補助金制度（デジタル化・AI導入補助金2026、スマート農業・農業支援サービス事業）の\
公式文書に基づいて回答するアシスタントです。

- 回答はツールが返した文書の内容だけに基づくこと。一般知識や推測で補わない。
- 特定の事実は search_knowledge_base、文書全体にわたる質問は retrieve_full_document を使う。
- 各事実がどの制度・枠・文書種別（出典）に由来するかを明示する。異なる枠の数値を混ぜない。
- 交付規程（authority_rank=1）と公募要領（authority_rank=2）が矛盾する場合は交付規程が優先する。
- 文書は2026年度（令和8年度）のもの。質問が別の年度・別の制度についてで、文書に記載がなければ \
answerable を false にする。近い情報で代用しない。
- 回答の各主張を citations で裏付ける（主張ごとに1件、最大5件）。引用で裏付けられない内容は\
回答に書かない。
- citations には、ツール結果から一字一句そのまま写した引用を入れる。source_doc は\
その引用が載っていた「出典:」の値をそのまま写す。
- 文書から答えられない場合は answerable=false、answer は空文字、citations は空にする。
"""

# Q6 rule B, under evaluation (2026-09-22): say a rule is shared, but only
# across the 枠 whose document was actually retrieved. Off by default. The
# 取消し article is identical in four 交付規程 but differs in 複数者連携
# (第25条, extra グループ clauses), which Q6's top 5 does not retrieve.
SHARED_RULES = """\
- 同じ規定が複数の枠の文書に載っている場合は、ツール結果で確認できた枠を列挙して「共通」と書き、\
枠ごとに1件引用する。確認していない枠まで共通とは書かない（「全ての枠」とは書かない）。
"""


def instructions() -> str:
    return INSTRUCTIONS + (SHARED_RULES if os.getenv("CITE_SHARED_RULES") == "1" else "")


@lru_cache(maxsize=1)
def build_agent() -> Agent[Deps, Answer]:
    return Agent(
        f"anthropic:{MODEL}",
        deps_type=Deps,
        # strict=True: Anthropic enforces the schema on the final_result call.
        # Non-strict (pydantic-ai's default) let Claude send malformed args,
        # which costs a whole extra request as a validation retry - the likely
        # third request on 2026-09-21 (request 3 added ~1.4k tokens: a re-sent
        # answer, not a 5-passage search).
        output_type=ToolOutput(Answer, strict=True),
        instructions=instructions(),
        tools=[Tool(search_knowledge_base, takes_ctx=True), _retrieve_tool()],
        model_settings={
            "max_tokens": MAX_OUTPUT,
            # Adaptive thinking is on by default on Sonnet 5 and bills as
            # output. Lower effort, not disabled thinking, is the lever.
            "anthropic_effort": EFFORT,
            # Instructions + tool definitions are identical every run, and a
            # retrieved document is resent on the follow-up request.
            "anthropic_cache_instructions": True,
            "anthropic_cache_tool_definitions": True,
            "anthropic_cache_messages": True,
        },
    )


# -- evidence gate ----------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s))


def verify_citations(answer: Answer, deps: Deps) -> list[str]:
    """Quotes that do NOT appear in a tool result under the named source_doc.
    Whitespace and width are normalized - Docling line-breaks inside
    sentences - but every character of the quote must be there."""
    bad = []
    for c in answer.citations:
        q, src = _norm(c.quote), _norm(c.source_doc)
        if len(q) < 8 or not any(src == _norm(s) and q in _norm(t) for s, t in deps.evidence):
            bad.append(c.quote)
    return bad


# -- cost & ledger ----------------------------------------------------------

def _cost(u) -> tuple[dict, float]:
    pin, pout = PRICES.get(MODEL, PRICES["claude-opus-5"])
    d = {"requests": u.requests, "input_tokens": u.input_tokens,
         "output_tokens": u.output_tokens,
         "cache_write_tokens": u.cache_write_tokens or 0,
         "cache_read_tokens": u.cache_read_tokens or 0,
         "details": dict(u.details or {})}
    # input_tokens from pydantic-ai includes cached tokens; bill those at
    # their own rates instead of the full input rate.
    plain = d["input_tokens"] - d["cache_write_tokens"] - d["cache_read_tokens"]
    usd = (plain * pin + d["cache_write_tokens"] * pin * 1.25
           + d["cache_read_tokens"] * pin * 0.1 + d["output_tokens"] * pout)
    return d, round(usd, 6)


def request_trace(messages) -> list[str]:
    """One line per model request: the tools it called, and any retry
    pydantic-ai sent back (validation errors show up here). Logged to the
    ledger so an unexpected request count explains itself."""
    trace, retries = [], []
    for m in messages:
        if isinstance(m, ModelRequest):
            retries += [f"retry {p.tool_name or 'text'}: {str(p.content)[:120]}"
                        for p in m.parts if isinstance(p, RetryPromptPart)]
        elif isinstance(m, ModelResponse):
            kinds = [getattr(p, "tool_name", None) or p.part_kind for p in m.parts]
            trace.append(" + ".join(retries + kinds))
            retries = []
    return trace


def spent_so_far() -> float:
    if not LEDGER.exists():
        return 0.0
    return sum(json.loads(l)["cost_usd"] for l in LEDGER.read_text(encoding="utf-8").splitlines() if l)


def _record(r: QueryResult, model: str) -> None:
    LEDGER.parent.mkdir(exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": model, "question": r.question, "status": r.status,
            "reason": r.reason, "usage": r.usage, "cost_usd": r.cost_usd,
            "trace": r.trace,
        }, ensure_ascii=False) + "\n")


# -- entry point ------------------------------------------------------------

def _finish(deps: Deps, r: QueryResult) -> QueryResult:
    deps.say("answer", {"status": r.status, "reason": r.reason, "answer": r.answer,
                        "citations": [c.model_dump() for c in r.citations],
                        "rejected_quotes": r.rejected_quotes, "tool_calls": r.tool_calls})
    deps.say("usage", {**{k: v for k, v in r.usage.items() if k != "details"},
                       "cost_usd": r.cost_usd, "ledger_total_usd": round(spent_so_far(), 4),
                       "budget_usd": BUDGET_USD})
    return r


def answer_question(question: str, model=None, emit=None) -> QueryResult:
    """model=None -> the real, paid Claude model. Pass a pydantic-ai test
    model to exercise the whole pipeline for free. `emit(event, data)`
    receives each step as it happens (the dashboard console)."""
    deps = Deps(emit=emit)
    deps.say("run.start", {"question": question})

    # Layer 1: score gate, before any model call.
    pre = _search(deps, question)
    top = pre[0].rerank_score if pre else 0.0
    named = named_documents(question)
    passed = top >= THRESHOLD or bool(named)
    deps.say("gate1", {"top_score": round(top, 4), "threshold": THRESHOLD, "pass": passed,
                       "via": "score" if top >= THRESHOLD else "named_doc" if named else None,
                       "named_docs": named})
    if not passed:
        return _finish(deps, QueryResult(question, "declined", "score_gate", DECLINE,
                                         [], [], top, {}, 0.0))

    paid = model is None
    if paid and spent_so_far() + WORST_CASE_USD > BUDGET_USD:
        msg = (f"budget guard: ${spent_so_far():.4f} spent + worst case "
               f"${WORST_CASE_USD:.2f} would exceed ${BUDGET_USD:.2f}")
        deps.say("error", {"type": "BudgetGuard", "message": msg})
        raise RuntimeError(msg)

    agent = build_agent()
    deps.say("agent.start", {"model": MODEL if paid else "stub", "effort": EFFORT})
    try:
        run = agent.run_sync(question, deps=deps, usage_limits=LIMITS,
                             **({"model": model} if model is not None else {}))
    except Exception as e:  # UsageLimitExceeded and friends: decline, don't guess
        # Requests may have been billed, and the exception carries no usage:
        # record the worst case. Over-counting is the safe error for a budget.
        r = QueryResult(question, "declined", f"error: {type(e).__name__}", DECLINE,
                        [], deps.tool_calls, top, {"note": "usage unknown, worst case"},
                        WORST_CASE_USD if paid else 0.0)
        if paid:
            _record(r, MODEL)
        deps.say("error", {"type": type(e).__name__, "message": str(e)[:300]})
        _finish(deps, r)
        raise

    # The request is billed by now. Record it before anything else can fail -
    # a crash here once lost a paid run's usage (2026-09-21).
    if paid:
        try:
            usage, cost = _cost(run.usage)
        except Exception as e:
            usage, cost = {"note": f"usage unreadable ({e!r}), worst case"}, WORST_CASE_USD
    else:
        usage, cost = {}, 0.0
    try:
        trace = request_trace(run.all_messages())
    except Exception as e:
        trace = [f"trace unreadable ({e!r})"]
    try:
        out: Answer = run.output
    except Exception as e:
        r = QueryResult(question, "declined", f"error: {type(e).__name__}", DECLINE,
                        [], deps.tool_calls, top, usage, cost, trace=trace)
        if paid:
            _record(r, MODEL)
        deps.say("error", {"type": type(e).__name__, "message": str(e)[:300]})
        _finish(deps, r)
        raise

    # Layer 2: evidence gate.
    if not out.answerable:
        reason, bad = "not_answerable", []
    elif not out.citations:
        reason, bad = "no_citation", []
    else:
        bad = verify_citations(out, deps)
        reason = "unverified_quote" if bad else "answered"
    deps.say("gate2", {"answerable": out.answerable, "citations": len(out.citations),
                       "verified": len(out.citations) - len(bad), "rejected": bad,
                       "pass": reason == "answered", "reason": reason})

    ok = reason == "answered"
    r = QueryResult(question, "answered" if ok else "declined", reason,
                    out.answer if ok else DECLINE, out.citations if ok else [],
                    deps.tool_calls, top, usage, cost, bad, trace)
    if paid:
        _record(r, MODEL)
    return _finish(deps, r)


def _print(r: QueryResult) -> None:
    print(f"Q        {r.question}")
    print(f"status   {r.status} ({r.reason})   top rerank {r.top_score:.4f}")
    print(f"tools    {' -> '.join(r.tool_calls) or '(none)'}")
    print(f"answer   {r.answer}")
    for c in r.citations:
        print(f"  cite   [{c.source_doc}] {c.quote}")
    for q in r.rejected_quotes:
        print(f"  REJECT {q}")
    for i, t in enumerate(r.trace, 1):
        print(f"req {i}    {t}")
    if r.usage:
        print(f"usage    {r.usage}")
    print(f"cost     ${r.cost_usd:.4f}   ledger total ${spent_so_far():.4f} / ${BUDGET_USD:.2f}")


def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?")
    ap.add_argument("--stub", action="store_true", help="free: pydantic-ai TestModel")
    ap.add_argument("--ledger", action="store_true")
    a = ap.parse_args()
    if a.ledger or not a.question:
        print(f"spent ${spent_so_far():.4f} of ${BUDGET_USD:.2f}  ({LEDGER})")
        return
    model = None
    if a.stub:
        from pydantic_ai.models.test import TestModel
        model = TestModel()
    t = time.time()
    r = answer_question(a.question, model)
    _print(r)
    print(f"time     {time.time() - t:.1f}s")


if __name__ == "__main__":
    _main()
