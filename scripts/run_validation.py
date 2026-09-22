"""Step 5 validation suite: the 10 questions in docs/validation_questions.md.

    python scripts/run_validation.py                  # free: stubbed model, real retrieval
    python scripts/run_validation.py --only 1,8       # a subset
    python scripts/run_validation.py --paid           # prints the estimate, runs nothing
    python scripts/run_validation.py --paid --yes     # PAID (~$0.28-0.36 for all ten)
    python scripts/run_validation.py --regrade data/validation_runs/<file>.json   # free
    python scripts/run_validation.py --paid --yes --only 6 --shared-rules   # Q6 under rule B

Running and grading are separate on purpose. Every run saves its raw results
to data/validation_runs/, and --regrade re-scores a saved run for $0 - a
grading bug must never cost a second paid run.

The stub is a FunctionModel that searches with the question and cites the
first passage verbatim. It exercises retrieval, both gates, grading and the
report; its answers are not meant to pass the content checks.

Grading (a question passes only if every column holds):
  status / path   the status and decline reason the system returned
  contain         facts the answer must state (NFKC-normalized: ２／３ == 2/3)
  cite            a citation's source_doc must name the right document
  must_not        the specific wrong answer the question exists to catch
  tool            a different tool is a finding to explain, not a fail
Also reported, not graded: requests > 2, and figures in the answer that
appear in no citation quote (a proxy for uncited claims - read those by hand).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import re
import sys
import time
import unicodedata
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart  # noqa: E402
from pydantic_ai.models.function import AgentInfo, FunctionModel  # noqa: E402

from rag.agent import BUDGET_USD, MODEL, THRESHOLD, answer_question, spent_so_far  # noqa: E402

RUNS = ROOT / "data" / "validation_runs"
REPORT = ROOT / "docs" / "validation_results.md"
REPORT_STUB = ROOT / "data" / "validation_results_stub.md"
DECLINED_BY_MODEL = {"not_answerable", "unverified_quote", "no_citation"}
SEARCH, FULL = "search_knowledge_base", "retrieve_full_document"


def any_of(*alts: str) -> tuple[int, tuple[str, ...]]:
    return 1, alts


def k_of(k: int, *alts: str) -> tuple[int, tuple[str, ...]]:
    return k, alts


# Mirrors docs/validation_questions.md. Strings are matched after NFKC,
# whitespace and comma removal, so full-width figures match half-width.
QUESTIONS = [
    dict(id=1, q="北海道内で事業を実施する場合の要件は何ですか？",
         expect="answered", reasons={"answered"}, tool=SEARCH, est=0.032,
         contain=[any_of("総合振興局")],
         cite=[("広域型", "公募要領")]),
    dict(id=2, q="複数者連携デジタル化・AI導入枠の補助率と補助上限額を教えてください。",
         expect="answered", reasons={"answered"}, tool=SEARCH, est=0.035,
         contain=[any_of("2/3"), any_of("50万円"), any_of("3000万円"), any_of("200万円")],
         cite=[("複数者連携",)],
         must_not=["3/4"]),
    dict(id=3, q="通常枠の補助額と補助率はいくらですか？",
         expect="answered", reasons={"answered"}, tool=SEARCH, est=0.035,
         contain=[any_of("5万円"), any_of("150万円"), any_of("450万円"),
                  any_of("1/2"), any_of("2/3"), any_of("最低賃金")],
         cite=[("通常枠",)],
         must_not=["3/4", "4/5"]),
    dict(id=4, q="小規模事業者がセキュリティ対策推進枠を利用する場合の補助率はいくらですか？",
         expect="answered", reasons={"answered"}, tool=SEARCH, est=0.032,
         contain=[any_of("2/3"), any_of("小規模")],
         cite=[("セキュリティ対策推進枠",)]),
    dict(id=5, q="スマート農業の補助事業とデジタル化・AI導入補助金を併用して、同じ機械の購入費を両方に申請できますか？",
         expect="answered", reasons={"answered"}, tool=SEARCH, est=0.035,
         contain=[any_of("重複"),
                  any_of("できません", "できない", "認めない", "認められない", "対象としない", "不可")],
         cite=[("交付規程",), ("スマート農業",)],
         must_not=["申請できます", "併用できます"],
         note="Also check by hand: a 交付規程 is cited where it says the same as a 公募要領."),
    dict(id=6, q="補助金の交付決定が取り消されるのはどのような場合ですか？",
         expect="answered", reasons={"answered"}, tool=SEARCH, est=0.035,
         contain=[any_of("取消", "取り消")],
         cite=[("交付規程",)],
         rules=True,
         note="Rule A / rule B are both graded until the user picks one. The 取消し article "
              "is identical in 通常/インボイス×2/セキュリティ (第27条) but differs in "
              "複数者連携 (第25条), which Q6's top 5 does not retrieve."),
    dict(id=7, q="通常枠の交付規程は全体としてどのような構成（どのような条項の流れ）になっていますか？",
         expect="answered", reasons={"answered"}, tool=f"{FULL}('09_dx_ai_kitei_tsujyo')", est=0.06,
         contain=[k_of(4, "交付の目的", "補助対象としない事業", "交付申請", "交付決定",
                       "実績報告", "額の確定", "取消", "返還", "財産の管理")],
         cite=[("通常枠", "交付規程")]),
    dict(id=8, q="明日の東京の天気を教えてください。",
         expect="declined", reasons={"score_gate"}, tool=None, est=0.0),
    dict(id=9, q="ものづくり補助金の補助上限額はいくらですか？",
         expect="declined", reasons=DECLINED_BY_MODEL, tool="any", est=0.03),
    dict(id=10, q="2027年度のデジタル化・AI導入補助金の公募スケジュールはいつですか？",
         expect="declined", reasons={"not_answerable"}, tool=None, est=0.002,
         note="Measured 2026-09-21: declined without searching. A search first is fine."),
]


def norm(s: str) -> str:
    return re.sub(r"[\s,、]", "", unicodedata.normalize("NFKC", s))


# -- running ---------------------------------------------------------------

def stub_model() -> FunctionModel:
    """Search with the question, then cite the first passage verbatim."""
    def fn(messages, info: AgentInfo) -> ModelResponse:
        seen = next((str(p.content) for m in reversed(messages) for p in getattr(m, "parts", [])
                     if isinstance(p, ToolReturnPart)), None)
        if seen is None:
            q = messages[0].parts[-1].content
            return ModelResponse(parts=[ToolCallPart(SEARCH, {"query": q})])
        src = seen.split("出典: ", 1)[1].split("\n", 1)[0]
        body = seen.split("\n", 2)[2].split("\n\n[", 1)[0]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            "answerable": True, "answer": "（スタブ回答）" + body[:60],
            "citations": [{"source_doc": src, "quote": body[:40]}]})])
    return FunctionModel(fn)


def run(questions: list[dict], paid: bool) -> list[dict]:
    rows = []
    for spec in questions:
        t = time.time()
        print(f"Q{spec['id']:<2} {spec['q']}", flush=True)
        try:
            r = answer_question(spec["q"], None if paid else stub_model())
            row = dataclasses.asdict(r)
            row["citations"] = [c.model_dump() if hasattr(c, "model_dump") else c for c in r.citations]
        except Exception as e:  # the agent already recorded any paid usage
            row = dict(question=spec["q"], status="error", reason=f"{type(e).__name__}: {e}"[:300],
                       answer="", citations=[], tool_calls=[], top_score=0.0, usage={},
                       cost_usd=0.0, rejected_quotes=[], trace=[])
        row["id"], row["seconds"] = spec["id"], round(time.time() - t, 1)
        print(f"    -> {row['status']}/{row['reason']}  ${row['cost_usd']:.4f}  {row['seconds']}s", flush=True)
        rows.append(row)
    return rows


# -- grading ---------------------------------------------------------------

def grade(spec: dict, row: dict) -> dict:
    ans = norm(row["answer"])
    cites = row["citations"]
    checks, notes = {}, []

    checks["status"] = row["status"] == spec["expect"]
    checks["path"] = row["reason"] in spec["reasons"]

    if spec.get("contain") and spec["expect"] == "answered":
        missing = []
        for k, alts in spec["contain"]:
            hits = [a for a in alts if norm(a) in ans]
            if len(hits) < k:
                missing.append(f"{k} of {'/'.join(alts)}" if k > 1 else "/".join(alts))
        checks["contain"] = not missing
        if missing:
            notes.append("missing: " + "; ".join(missing))

    if spec.get("cite") and spec["expect"] == "answered":
        srcs = [c["source_doc"] for c in cites]
        checks["cite"] = any(all(t in s for t in alt) for s in srcs for alt in spec["cite"])
        if not checks["cite"]:
            notes.append(f"cited {srcs or 'nothing'}")

    if spec.get("must_not"):
        bad = [w for w in spec["must_not"] if norm(w) in ans]
        checks["must_not"] = not bad
        if bad:
            notes.append("contains " + ", ".join(bad))

    tool = spec["tool"]
    if tool != "any":
        calls = row["tool_calls"]
        ok = (not calls) if tool is None else any(c.startswith(tool) for c in calls)
        if not ok and row["status"] != "error":
            notes.append(f"FINDING tool: expected {tool or 'none'}, got {calls or 'none'}")

    requests = (row.get("usage") or {}).get("requests")
    if requests and requests > 2:
        notes.append(f"FINDING {requests} requests (expected <= 2); see trace")

    unbacked = unbacked_figures(row)
    if unbacked:
        notes.append("figures in no quote: " + ", ".join(unbacked))
    rules = q6_rules(row) if spec.get("rules") else {}
    for name, (ok, why) in rules.items():
        notes.append(f"rule {name}: {'PASS' if ok else 'FAIL'} — {why}")
    if spec.get("note"):
        notes.append(spec["note"])

    return dict(passed=all(checks.values()), checks=checks, notes=notes, requests=requests,
                rules={k: v[0] for k, v in rules.items()})


# 枠 as an answer names them -> the substring their source_doc carries.
WAKU = {"通常枠": "通常枠", "インボイス": "インボイス", "セキュリティ": "セキュリティ", "複数者連携": "複数者連携"}
UNIVERSAL = ["全ての枠", "すべての枠", "全枠", "各枠共通", "いずれの枠", "どの枠", "全類型", "すべての類型"]
SHARED = ["共通", "同一", "同様", "同じ"]


def q6_rules(row: dict) -> dict[str, tuple[bool, str]]:
    """Q6's two candidate rules. Both fail an unqualified "every 枠" claim:
    複数者連携's 取消し article differs and is not in the evidence.
      A attributed - the answer names the 枠 whose 交付規程 it cites.
      B shared     - it says the rule is shared, and names >= 2 枠, each
                     backed by a citation from that 枠's 交付規程."""
    if row["status"] != "answered":
        return {"A": (False, "not answered"), "B": (False, "not answered")}
    ans = norm(row["answer"])
    kitei = [c["source_doc"] for c in row["citations"] if "交付規程" in c["source_doc"]]
    named = [w for w in WAKU if w in ans]
    backed = [w for w in named if any(WAKU[w] in s for s in kitei)]
    universal = [u for u in UNIVERSAL if norm(u) in ans]
    over = f"claims every 枠 ({universal[0]})" if universal else ""

    a_ok = bool(backed) and not universal
    a_why = over or (f"names {backed} with a matching 交付規程 citation" if backed
                     else f"no 枠 named with a matching 交付規程 citation (named {named or 'none'})")
    says_shared = any(w in ans for w in SHARED)
    b_ok = says_shared and len(backed) >= 2 and not universal
    b_why = over or ("does not say the rule is shared" if not says_shared
                     else f"{len(backed)} 枠 backed by citations {backed} (need 2+)" if len(backed) < 2
                     else f"shared across {backed}, each cited")
    return {"A": (a_ok, a_why), "B": (b_ok, b_why)}


FIGURE = re.compile(r"\d+(?:[./]\d+)*(?:万円|円|%|割|以内)|\d+/\d+")


def unbacked_figures(row: dict) -> list[str]:
    """Amounts and rates stated in the answer that no citation quote carries."""
    if row["status"] != "answered":
        return []
    quotes = norm(" ".join(c["quote"] for c in row["citations"]))
    seen = dict.fromkeys(FIGURE.findall(norm(row["answer"])))
    return [f for f in seen if f not in quotes]


# -- report ----------------------------------------------------------------

def mark(v) -> str:
    return "—" if v is None else ("✓" if v else "✗")


def rule_tag(g: dict) -> str:
    return "".join(f" · {k} {'✓' if v else '✗'}" for k, v in g.get("rules", {}).items())


def report(rows: list[dict], mode: str, source: pathlib.Path, shared_rules: bool = False) -> str:
    specs = {s["id"]: s for s in QUESTIONS}
    graded = [(specs[r["id"]], r, grade(specs[r["id"]], r)) for r in rows]
    total = sum(r["cost_usd"] for r in rows)
    passed = sum(g["passed"] for _, _, g in graded)

    out = [f"# Validation results — {mode}", "",
           f"Run file `{source.relative_to(ROOT).as_posix()}` · model `{MODEL if mode == 'paid' else 'stub'}` · "
           f"threshold {THRESHOLD} · prompt {'+ shared-rules (Q6 rule B)' if shared_rules else 'default'} · "
           f"**{passed}/{len(rows)} passed** · run cost **${total:.4f}** · "
           f"ledger ${spent_so_far():.4f} / ${BUDGET_USD:.2f}", ""]
    if mode != "paid":
        out += ["> Stub run: the model is a script that cites the first passage. Content checks are",
                "> expected to fail; this checks retrieval, both gates, grading and this report.", ""]
    out += ["| # | Status / path | Tools | Top | Req | Cost | status | path | contain | cite | must not | Result |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for spec, r, g in graded:
        c = g["checks"]
        tools = ", ".join(t.split("(")[0].replace("search_knowledge_base", "search")
                          .replace("retrieve_full_document", "full_doc") for t in r["tool_calls"]) or "none"
        out.append(f"| {spec['id']} | {r['status']} / {r['reason']} | {tools} | {r['top_score']:.3f} | "
                   f"{g['requests'] or 0} | ${r['cost_usd']:.4f} | {mark(c.get('status'))} | {mark(c.get('path'))} | "
                   f"{mark(c.get('contain'))} | {mark(c.get('cite'))} | {mark(c.get('must_not'))} | "
                   f"**{'PASS' if g['passed'] else 'FAIL'}**{rule_tag(g)} |")

    # Threshold recheck: gate 1 must pass every question the model should see.
    scores = sorted((r["top_score"], spec["id"], r["reason"]) for spec, r, _ in graded)
    should_pass = [s for s, i, _ in scores if specs[i]["reasons"] != {"score_gate"}]
    should_stop = [s for s, i, _ in scores if specs[i]["reasons"] == {"score_gate"}]
    line = f"Threshold {THRESHOLD}."
    if should_pass:
        line += f" Lowest score that must pass: {min(should_pass):.4f}."
    if should_stop:
        line += f" Highest score that must stop: {max(should_stop):.4f}."
    out += ["", "## Gate-1 threshold recheck", "", line, "",
            " · ".join(f"Q{i} {s:.3f}" for s, i, _ in scores), ""]

    out += ["## Per question", ""]
    for spec, r, g in graded:
        out += [f"### Q{spec['id']} — {'PASS' if g['passed'] else 'FAIL'}{rule_tag(g)}", "",
                f"**Q:** {spec['q']}", "",
                f"`{r['status']}/{r['reason']}` · top {r['top_score']:.4f} · ${r['cost_usd']:.4f} · {r.get('seconds', '?')}s", ""]
        for n in g["notes"]:
            out.append(f"- {n}")
        if g["notes"]:
            out.append("")
        if r["status"] == "answered":
            out += ["**Answer:**", "", *[f"> {l}" for l in r["answer"].splitlines() if l.strip()], ""]
            out += [f"- [{c['source_doc']}] 「{c['quote']}」" for c in r["citations"]]
            out += ["", "- [ ] Uncited sentences (read by hand, target 0): __", ""]
        if r.get("trace"):
            out += [f"- req {i}: `{t}`" for i, t in enumerate(r["trace"], 1)] + [""]
    return "\n".join(out) + "\n"


# -- entry point -----------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated question ids, e.g. 1,8")
    ap.add_argument("--paid", action="store_true", help="real Claude calls (costs money)")
    ap.add_argument("--yes", action="store_true", help="confirm a paid run")
    ap.add_argument("--regrade", type=pathlib.Path, help="re-score a saved run, $0")
    ap.add_argument("--shared-rules", action="store_true",
                    help="add the Q6 rule-B prompt line (CITE_SHARED_RULES=1)")
    a = ap.parse_args()

    if a.regrade:
        saved = json.loads(a.regrade.read_text(encoding="utf-8"))
        mode, rows, source = saved["mode"], saved["rows"], a.regrade.resolve()
        shared = saved.get("shared_rules", False)
    else:
        ids = {int(x) for x in a.only.split(",")} if a.only else None
        questions = [q for q in QUESTIONS if ids is None or q["id"] in ids]
        mode = "paid" if a.paid else "stub"
        shared = a.shared_rules
        if shared:
            os.environ["CITE_SHARED_RULES"] = "1"  # read when the agent is first built
        if a.paid:
            est = sum(q["est"] for q in questions)
            print(f"PAID run: {len(questions)} question(s), estimated ${est:.2f} at {MODEL}.")
            print(f"Ledger ${spent_so_far():.4f} / ${BUDGET_USD:.2f}. Per-question hard cap is in rag/agent.py.")
            if not a.yes:
                print("Nothing sent. Re-run with --yes once the cost is approved.")
                return
        rows = run(questions, paid=a.paid)
        RUNS.mkdir(parents=True, exist_ok=True)
        source = RUNS / f"{datetime.now():%Y%m%d-%H%M%S}_{mode}.json"
        source.write_text(json.dumps({"mode": mode, "model": MODEL if a.paid else "stub",
                                      "threshold": THRESHOLD, "shared_rules": shared, "rows": rows},
                                     ensure_ascii=False, indent=1), encoding="utf-8")

    text = report(rows, mode, source, shared)
    dest = REPORT if mode == "paid" else REPORT_STUB
    dest.write_text(text, encoding="utf-8")
    print(f"\nreport -> {dest.relative_to(ROOT).as_posix()}   raw -> {source.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
