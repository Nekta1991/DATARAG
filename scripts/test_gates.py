"""Free test of the agent pipeline: real retrieval, stubbed model.

    python scripts/test_gates.py

Cost: $0 - no Anthropic call. A pydantic-ai FunctionModel plays Claude: it
calls the real tools, then returns a scripted Answer, so every path through
both decline gates runs against the live corpus. Uses a few Voyage query
embeddings (free tier; the 3 RPM limit may add waits).
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from rag.agent import answer_question

Q = "北海道内で事業を実施する場合の要件"
GOLD = "北海道内の複数総合振興局"


def _last_tool_return(messages) -> str | None:
    for m in reversed(messages):
        for p in getattr(m, "parts", []):
            if isinstance(p, ToolReturnPart):
                return str(p.content)
    return None


def scripted(tool: str, args: dict, make_answer):
    """Call one tool, then answer with make_answer(tool_output)."""
    def fn(messages, info: AgentInfo) -> ModelResponse:
        seen = _last_tool_return(messages)
        if seen is None:
            return ModelResponse(parts=[ToolCallPart(tool, args)])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, make_answer(seen))])
    return FunctionModel(fn)


def quote_around(text: str, needle: str, n: int = 40) -> tuple[str, str]:
    """(source_doc, verbatim quote) for the block of tool output holding needle."""
    for block in text.split("\n\n["):
        if needle in block:
            src = block.split("出典: ", 1)[1].split("\n", 1)[0]
            body = block.split("\n", 2)[2]
            i = body.index(needle)
            return src, body[max(0, i - 10): i + n]
    raise AssertionError(f"{needle!r} not in tool output")


def answer(ok=True, cites=()):
    return {"answerable": ok, "answer": "（テスト回答）" if ok else "",
            "citations": [{"source_doc": s, "quote": q} for s, q in cites]}


CASES = []


def case(name, question, model, expect_status, expect_reason):
    CASES.append((name, question, model, expect_status, expect_reason))


# 1. off-topic: gate 1 fires, model never called (model=object() would crash if used)
case("off-topic -> score gate", "明日の東京の天気を教えてください",
     FunctionModel(lambda m, i: (_ for _ in ()).throw(AssertionError("model called"))),
     "declined", "score_gate")

# 2. verbatim quote, right source_doc -> answered
case("verbatim quote -> answered", Q,
     scripted("search_knowledge_base", {"query": Q},
              lambda out: answer(True, [quote_around(out, GOLD)])),
     "answered", "answered")

# 3. quote with one character changed -> declined
case("altered quote -> declined", Q,
     scripted("search_knowledge_base", {"query": Q},
              lambda out: answer(True, [(quote_around(out, GOLD)[0],
                                         quote_around(out, GOLD)[1].replace("北海道", "東北"))])),
     "declined", "unverified_quote")

# 4. real quote, attributed to the wrong 枠 -> declined
case("right quote, wrong 枠 -> declined", Q,
     scripted("search_knowledge_base", {"query": Q},
              lambda out: answer(True, [("デジタル化・AI導入補助金2026 通常枠 公募要領",
                                         quote_around(out, GOLD)[1])])),
     "declined", "unverified_quote")

# 5. model says it cannot answer -> declined
case("answerable=false -> declined", Q,
     scripted("search_knowledge_base", {"query": Q}, lambda out: answer(False)),
     "declined", "not_answerable")

# 6. answerable but no citation -> declined
case("no citation -> declined", Q,
     scripted("search_knowledge_base", {"query": Q}, lambda out: answer(True, [])),
     "declined", "no_citation")

# 7. retrieve_full_document path: quote from the whole document -> answered
case("full-document quote -> answered", Q,
     scripted("retrieve_full_document", {"doc_id": "04_jisshi_youryou_bekki1_hokkaido"},
              lambda out: answer(True, [(out.split("出典: ", 1)[1].split("\n", 1)[0],
                                         out[out.index(GOLD) - 10: out.index(GOLD) + 30])])),
     "answered", "answered")

# 8. names a corpus document, scores below the threshold (0.254) -> gate 1 passes anyway
Q7 = "通常枠の交付規程は全体としてどのような構成（どのような条項の流れ）になっていますか？"
case("named document, low score -> answered", Q7,
     scripted("retrieve_full_document", {"doc_id": "09_dx_ai_kitei_tsujyo"},
              lambda out: answer(True, [(out.split("出典: ", 1)[1].split("\n", 1)[0],
                                         out[out.index("（交付決定の取消し）"):][:30])])),
     "answered", "answered")


# 9/10. Table quotes. Q3 of validation run 1 declined because the model
# rewrote a markdown table into prose ("補助額 ５万円～… 補助額 １５０万円～…"),
# stitching one label onto each column. Retrieval was not at fault: chunks 316
# and 871 ranked 1-2 and both carry a clean "| 補助額 | … |" row. The gate must
# keep rejecting the stitched form and accept the row copied as-is.
Q3 = "通常枠 補助額 補助率"
STITCHED = "補助額 ５万円～１５０万円未満 補助額 １５０万円～４５０万円以下"


def table_row(text: str, label: str) -> tuple[str, str]:
    """(source_doc, one markdown table row) from the block holding it."""
    for block in text.split("\n\n["):
        src = block.split("出典: ", 1)[1].split("\n", 1)[0] if "出典: " in block else None
        for line in block.split("\n"):
            if line.startswith(f"| {label}") and src:
                return src, line
    raise AssertionError(f"no '| {label}' row in tool output")


case("table row verbatim -> answered", Q3,
     scripted("search_knowledge_base", {"query": Q3},
              lambda out: answer(True, [table_row(out, "補助額")])),
     "answered", "answered")

case("stitched table cells -> declined", Q3,
     scripted("search_knowledge_base", {"query": Q3},
              lambda out: answer(True, [(table_row(out, "補助額")[0], STITCHED)])),
     "declined", "unverified_quote")


# 11/12. Gate 1 must not kill a question the corpus answers just because it was
# asked in ordinary Japanese. 「どんな企業が応募できますか」 scored 0.0587 raw -
# under any sane threshold - while the corpus answers it well: its
# 中小企業等の定義 table (chunks 328/329, capital and headcount per industry)
# reranks 0.82 for a query phrased in the documents' own words. Stripping the
# interrogative scaffolding lifts it to 0.2191.
#
# The negative is the guard: the same normalization must NOT rescue an
# off-topic question. Measured, it does not - weather stays at 0.0001.
COLLOQUIAL = "どんな企業が応募できますか"

case("colloquial question -> rescued by normalization", COLLOQUIAL,
     scripted("search_knowledge_base", {"query": COLLOQUIAL},
              lambda out: answer(True, [quote_around(out, "応募")])),
     "answered", "answered")

case("normalization does not rescue off-topic", "明日の東京の天気はどうですか",
     FunctionModel(lambda m, i: (_ for _ in ()).throw(AssertionError("model called"))),
     "declined", "score_gate")


def main():
    failed = 0
    for name, q, model, want_status, want_reason in CASES:
        r = answer_question(q, model)
        ok = (r.status, r.reason) == (want_status, want_reason)
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:36} -> {r.status}/{r.reason}  "
              f"top {r.top_score:.3f}  tools {r.tool_calls}")
        if not ok:
            print(f"      expected {want_status}/{want_reason}; rejected={r.rejected_quotes}")
    print(f"\n{len(CASES) - failed}/{len(CASES)} passed, $0 spent")
    sys.exit(failed)


if __name__ == "__main__":
    main()
