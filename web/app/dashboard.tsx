"use client";

import { useRouter } from "next/navigation";
import { Fragment, useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { authClient } from "@/lib/auth/client";
import s from "./dashboard.module.css";

type Tone = "info" | "pass" | "fail" | "agent" | "cost";
type Line = { tag: string; tone: Tone; text: string; dur: string };
type Candidate = {
  rank: number; vector_rank: number | null; lexical_rank: number | null;
  rerank_score: number; source_doc: string; heading: string | null;
};
type Citation = { source_doc: string; quote: string };
type Answer = {
  status: "answered" | "declined"; reason: string; answer: string;
  citations: Citation[]; rejected_quotes: string[];
};
type Status = { documents: number; chunks: number; model: string; effort: string;
  threshold: number; spent_usd: number; budget_usd: number; busy: boolean };
// Union of every field the API's events carry (rag/agent.py, rag/retrieval.py).
type EventData = Partial<{
  question: string; model: string; dims: number; ms: number; k: number;
  best_similarity: number; lexical_only: number; pool: number; pairs: number;
  top_score: number; pass: boolean; threshold: number; effort: string; tool: string;
  args: Record<string, unknown>; error: string; passages: number; cached: boolean;
  doc_id: string; tokens: number; truncated: boolean; answerable: boolean; verified: number;
  citations: number | Citation[]; reason: string; status: string; requests: number;
  input_tokens: number; cache_read_tokens: number; output_tokens: number; cost_usd: number;
  ledger_total_usd: number; budget_usd: number; type: string; message: string; t_ms: number;
  candidates: Candidate[]; answer: string; rejected_quotes: string[];
  via: "score" | "named_doc" | null; named_docs: string[];
}>;

// Real questions from docs/validation_questions.md. A chip fills the input;
// it never starts a run, so a stray click cannot spend money.
const EXAMPLES = [
  "北海道内で事業を実施する場合の要件は何ですか？",
  "通常枠の補助額と補助率はいくらですか？",
  "2027年度のデジタル化・AI導入補助金の公募スケジュールはいつですか？",
  "明日の東京の天気を教えてください。",
];

const WORST_CASE_USD = 0.19; // rag/agent.py per-question hard cap

// Decline reason codes from rag/agent.py (QueryResult.reason) -> explanation.
const REASONS: Record<string, string> = {
  score_gate: "コーパス内に関連する記述がないため、モデルは呼び出していません。",
  not_answerable: "検索は通過しましたが、取得した文書だけではこの質問に答えられないとモデルが判断しました。",
  no_citation: "回答に引用が付いていなかったため、回答は破棄されています。",
  unverified_quote: "引用の逐語照合に失敗しました。以下の引用は取得済みテキスト内に見つからなかったため、回答は破棄されています。",
  limit: "1問あたりの上限（リクエスト数・トークン数）に達したため、回答を中断しました。",
};

const Check = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg>
);
const Cross = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
);
const Warn = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" /></svg>
);
const PIPELINE = ["EMBED", "VECTOR", "BM25", "RERANK", "GATE 1", "AGENT", "GATE 2"];

const ms = (v?: number) => (typeof v === "number" ? (v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${v}ms`) : "");
const num = (v?: number) => (typeof v === "number" ? v.toLocaleString("en-US") : "?");

// One SSE event -> one console line (docs/dashboard_build_brief.md §2).
function toLine(ev: string, d: EventData): Line | null {
  switch (ev) {
    case "run.start": return { tag: "RUN", tone: "info", text: `質問を受信 — ${d.question}`, dur: "" };
    case "embed.done": return { tag: "EMBED", tone: "info", text: `${d.model} · ${d.dims} dims`, dur: ms(d.ms) };
    case "vector.done": return { tag: "VECTOR", tone: "info", text: `pgvector top ${d.k} · best similarity ${d.best_similarity}`, dur: ms(d.ms) };
    case "bm25.done": return { tag: "BM25", tone: "info", text: `char-bigram top ${d.k} · ${d.lexical_only} not in vector set → pool ${d.pool}`, dur: ms(d.ms) };
    case "rerank.done": return { tag: "RERANK", tone: "info", text: `${String(d.model).split("/").pop()} · ${d.pairs} pairs · top ${d.top_score}`, dur: ms(d.ms) };
    case "gate1": {
      const cmp = `${d.top_score} ${Number(d.top_score) >= Number(d.threshold) ? "≥" : "<"} ${Number(d.threshold).toFixed(2)}`;
      // via=named_doc: the question names a corpus document, so gate 1 passes
      // whatever the score (rag/documents.py named_documents).
      const text = !d.pass ? `${cmp} → DECLINE (score_gate)`
        : d.via === "named_doc" ? `${cmp} · names ${(d.named_docs ?? []).join(", ")} → PASS (named_doc)`
        : `${cmp} → PASS`;
      return { tag: "GATE 1", tone: d.pass ? "pass" : "fail", text, dur: "" };
    }
    case "agent.start": return { tag: "AGENT", tone: "agent", text: `${d.model} · effort ${d.effort}`, dur: "" };
    case "agent.tool_call": return { tag: "TOOL", tone: "agent", text: `${d.tool}(${JSON.stringify(Object.values(d.args ?? {})[0] ?? "")})`, dur: "" };
    case "agent.tool_result":
      if (d.error) return { tag: "TOOL", tone: "fail", text: `${d.tool} → ${d.error}`, dur: "" };
      return { tag: "TOOL", tone: "agent", dur: "",
        text: d.tool === "search_knowledge_base"
          ? `→ ${d.passages} passages${d.cached ? " (cached from gate 1)" : ""}`
          : `→ ${d.doc_id} · ${num(d.tokens)} tok${d.truncated ? " · truncated" : ""}` };
    case "gate2": return { tag: "GATE 2", tone: d.pass ? "pass" : "fail", dur: "",
      text: d.answerable === false ? "answerable=false → DECLINE"
        : `${d.verified}/${d.citations} quotes found verbatim under their source_doc → ${d.pass ? "PASS" : `DECLINE (${d.reason})`}` };
    case "answer": return { tag: d.status === "answered" ? "ANSWER" : "DECLINE", tone: d.status === "answered" ? "pass" : "fail",
      text: d.status === "answered"
        ? `answer emitted · ${Array.isArray(d.citations) ? d.citations.length : 0} citation${Array.isArray(d.citations) && d.citations.length === 1 ? "" : "s"}`
        : `「この情報からは判断できません」 · ${d.reason}`, dur: "" };
    case "usage":
      return { tag: "COST", tone: "cost", dur: "",
        text: d.requests
          ? `${d.requests} req · in ${num(d.input_tokens)} (cache read ${num(d.cache_read_tokens)}) · out ${num(d.output_tokens)} · $${Number(d.cost_usd).toFixed(4)} · ledger $${Number(d.ledger_total_usd).toFixed(4)} / $${Number(d.budget_usd).toFixed(2)}`
          : `$${Number(d.cost_usd).toFixed(4)} · ledger $${Number(d.ledger_total_usd).toFixed(4)} / $${Number(d.budget_usd).toFixed(2)}` };
    case "error": return { tag: "ERROR", tone: "fail", text: `${d.type}: ${d.message}`, dur: "" };
    default: return null;
  }
}

export default function Dashboard({ email, apiUrl }: { email: string; apiUrl: string }) {
  const router = useRouter();
  const [question, setQuestion] = useState(EXAMPLES[0]);
  const [paid, setPaid] = useState(false);
  const [running, setRunning] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [pool, setPool] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [meta, setMeta] = useState("");
  const [elapsed, setElapsed] = useState("");
  const [status, setStatus] = useState<Status | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [gate, setGate] = useState<{ top: number; threshold: number } | null>(null);
  const [cost, setCost] = useState<number | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const token = useCallback(async () => {
    const { data, error } = await authClient.token();
    if (error || !data?.token) throw new Error("セッションが切れました。再度サインインしてください。");
    return data.token;
  }, []);

  // Fetch returns a result; callers apply it. Keeps setState out of the
  // synchronous body of the effect below.
  const fetchStatus = useCallback(async (): Promise<{ status?: Status; error?: string }> => {
    try {
      const r = await fetch(`${apiUrl}/api/status`, { headers: { Authorization: `Bearer ${await token()}` } });
      if (r.status === 403) return { error: "このアカウントには管理者権限がありません。" };
      if (!r.ok) return { error: `API error ${r.status}` };
      return { status: await r.json() };
    } catch (e) {
      return { error: e instanceof Error ? e.message : String(e) };
    }
  }, [apiUrl, token]);

  const applyStatus = (r: { status?: Status; error?: string }) => {
    if (r.status) setStatus(r.status);
    setApiError(r.error ?? null);
  };

  useEffect(() => {
    let alive = true;
    fetchStatus().then((r) => { if (alive) applyStatus(r); });
    return () => { alive = false; };
  }, [fetchStatus]);
  useEffect(() => { endRef.current?.scrollIntoView({ block: "nearest" }); }, [lines]);

  const overBudget = !!status && status.spent_usd + WORST_CASE_USD > status.budget_usd;

  async function run(e?: FormEvent) {
    e?.preventDefault();
    if (running || !question.trim()) return;
    setRunning(true);
    setLines([]); setCandidates([]); setAnswer(null); setMeta(""); setPool(""); setElapsed("");
    setGate(null); setCost(null); setRunError(null);
    try {
      const r = await fetch(`${apiUrl}/api/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${await token()}` },
        body: JSON.stringify({ question: question.trim(), stub: !paid }),
      });
      if (!r.ok || !r.body) {
        const msg = r.status === 429 ? "別の質問を処理中です。" : r.status === 403 ? "管理者権限がありません。" : `API error ${r.status}`;
        throw new Error(msg);
      }
      const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += value;
        let cut: number;
        while ((cut = buf.search(/\r?\n\r?\n/)) >= 0) {
          const block = buf.slice(0, cut);
          buf = buf.slice(cut).replace(/^\r?\n\r?\n/, "");
          let ev = "message"; let data = "";
          for (const ln of block.split(/\r?\n/)) {
            if (ln.startsWith("event:")) ev = ln.slice(6).trim();
            else if (ln.startsWith("data:")) data += ln.slice(5).trim();
          }
          if (!data) continue;
          const d = JSON.parse(data) as EventData;
          if (ev === "rerank.done") setCandidates(d.candidates ?? []);
          if (ev === "bm25.done") setPool(`pool ${d.pool} 件 · BM25 のみが発見 ${d.lexical_only} 件`);
          if (ev === "answer") setAnswer(d as unknown as Answer);
          if (ev === "gate1") setGate({ top: Number(d.top_score), threshold: Number(d.threshold) });
          if (ev === "usage") setCost(Number(d.cost_usd));
          if (ev === "error") setRunError(`${d.type}: ${d.message}`);
          if (ev === "usage") setMeta(d.requests
            ? `${d.requests} requests · in ${num(d.input_tokens)} (cache read ${num(d.cache_read_tokens)}) · out ${num(d.output_tokens)} · $${Number(d.cost_usd).toFixed(4)}`
            : `0 requests · $${Number(d.cost_usd).toFixed(4)}`);
          if (typeof d.t_ms === "number") setElapsed(ms(d.t_ms));
          const line = toLine(ev, d);
          if (line) setLines((xs) => [...xs, line]);
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setLines((xs) => [...xs, { tag: "ERROR", tone: "fail", text: msg, dur: "" }]);
      setRunError(msg);
    } finally {
      setRunning(false);
      applyStatus(await fetchStatus());
    }
  }

  async function signOut() {
    await authClient.signOut();
    router.replace("/auth/sign-in");
    router.refresh();
  }

  function copyConsole() {
    navigator.clipboard.writeText(lines.map((l) => `${l.tag.padEnd(8)} ${l.text}${l.dur ? `  ${l.dur}` : ""}`).join("\n"));
  }

  const done = !running && lines.length > 0;
  const answered = answer?.status === "answered";
  const failed = done && !answer && !!runError;
  const blockedByBudget = paid && overBudget;

  return (
    <div className={s.page}>
      <header className={s.header}>
        <div className={s.brand}>
          <span className={s.wordmark}>DATARAG</span>
          <span className={s.subtitle}>デジタル化・AI導入補助金2026 ／ スマート農業 RAG</span>
          <span className="tag tag-accent">非公式デモ</span>
        </div>
        <div className={s.meta}>
          {status && <span className="chip">{status.documents} docs · {num(status.chunks)} chunks</span>}
          {status && <span className="chip">{status.model} · effort {status.effort}</span>}
          {status && (
            <span className={s.budget}>
              spent ${status.spent_usd.toFixed(3)} / ${status.budget_usd.toFixed(2)}
              <span className={`${s.bar} ${overBudget ? s.over : ""}`}>
                <i style={{ width: `${Math.min(100, (status.spent_usd / status.budget_usd) * 100)}%` }} />
              </span>
            </span>
          )}
          <span className={s.email}>{email}</span>
          <button type="button" className="btn btn-secondary" onClick={signOut}>サインアウト</button>
        </div>
      </header>

      {apiError && <p role="alert" className={`blocked ${s.apiError}`}><Warn />API に接続できません: {apiError}（{apiUrl}）</p>}

      <div className={s.grid}>
        <div className={s.col}>
          <section className={`card ${s.qCard}`}>
            <h2 className="ct"><label htmlFor="q">質問</label></h2>
            <div className="ct-rule" />
            <form className={s.qjoin} onSubmit={run}>
              <input id="q" className={s.qin} value={question} maxLength={500} placeholder="補助金について質問してください"
                     onChange={(e) => setQuestion(e.target.value)} disabled={running} />
              <button type="submit" className={`btn btn-primary ${s.qsub}`}
                      disabled={running || !question.trim() || blockedByBudget || !!apiError}>
                {running ? "実行中…" : "検索して回答"}
              </button>
            </form>
            {blockedByBudget && (
              <p role="alert" className="blocked">
                <Warn />予算上限 ${status?.budget_usd.toFixed(2)} を超える可能性があるため実行できません（最悪ケース ${WORST_CASE_USD.toFixed(2)}／問）。本番モードをオフにするとスタブで無料実行できます。
              </p>
            )}
            <div className={s.chips}>
              {EXAMPLES.map((q) => (
                <button key={q} type="button" className={s.exchip} disabled={running} onClick={() => setQuestion(q)} title={q}>
                  {q.length > 26 ? `${q.slice(0, 26)}…` : q}
                </button>
              ))}
            </div>
            <label className={s.chk}>
              <input type="checkbox" checked={paid} onChange={(e) => setPaid(e.target.checked)} disabled={running} />
              <span className={s.box}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg></span>
              <span className={s.chkTxt}>
                本番モード（Claude API・課金 約$0.03／問、上限 ${WORST_CASE_USD.toFixed(2)}）
                <em>{paid ? "オン: 実際に課金されます" : "オフ: スタブで無料実行（検索とゲート1は実データ）"}</em>
              </span>
            </label>
          </section>

          <section className={`card ${s.aCard}`} aria-live="polite">
            <div className={s.answerHead}>
              <h2 className="ct">回答</h2>
              {done && answer && (
                <span className={`badge ${answered ? "ok" : "no"}`}>
                  {answered ? "ANSWERED · 引用検証済" : `DECLINED · ${answer.reason}${cost === 0 ? " · $0" : ""}`}
                </span>
              )}
              {failed && <span className="badge no">ERROR</span>}
            </div>
            <div className="ct-rule" />
            {!answer && !running && !failed && <p className={s.muted}>質問を入力して実行してください。進行状況は右のコンソールに表示されます。</p>}
            {running && <p className={s.muted}>検索と引用検証が終わるまで回答は表示されません。</p>}
            {answer && answered && (
              <>
                <div className={s.answerText}>{answer.answer.split(/\n+/).map((p, i) => <p key={i}>{p}</p>)}</div>
                <h3 className={`jph ${s.citeHead}`}>引用（{answer.citations.length}件・検証済）</h3>
                {answer.citations.map((c, i) => (
                  <div key={i} className={s.cite}>
                    <span className={s.citeIc}><Check /></span>
                    <span>
                      <span className={s.citeSrc}>{c.source_doc}</span>
                      <span className={s.citeQ}>「{c.quote}」</span>
                    </span>
                  </div>
                ))}
              </>
            )}
            {answer && !answered && (
              <>
                <div className={s.answerText}><p>「この情報からは判断できません」</p></div>
                <p className={s.reason}>
                  {answer.reason === "score_gate" && gate
                    ? `リランク最高スコア ${gate.top.toFixed(4)} がしきい値 ${gate.threshold.toFixed(2)} を下回りました。`
                    : ""}
                  {REASONS[answer.reason] ?? `理由コード: ${answer.reason}`}
                </p>
                {answer.rejected_quotes.map((q, i) => (
                  <div key={i} className={`${s.cite} ${s.bad}`}>
                    <span className={s.citeIc}><Cross /></span>
                    <span>
                      <span className={s.citeQ}>「{q}」</span>
                      <span className={s.citeFlag}>not found in retrieved text</span>
                    </span>
                  </div>
                ))}
              </>
            )}
            {failed && (
              <>
                <p role="alert" className="blocked"><Warn /><span>{runError}。回答は生成されていません。</span></p>
                <div className={s.retry}>
                  <button type="button" className="btn btn-primary" onClick={() => run()}
                          disabled={!question.trim() || blockedByBudget || !!apiError}>再実行</button>
                </div>
              </>
            )}
            {done && meta && <div className={s.metaLine}>{meta}</div>}
          </section>
        </div>

        <div className={s.col}>
          <section aria-label="console" className={s.console}>
            <div className={s.consoleHead}>
              <span className={s.consoleTitle}>console</span>
              <span className={s.consoleCount}>{lines.length} events</span>
              {running && <span className={`${s.state} ${s.running}`}>running<span className={s.ind}><i /></span></span>}
              {done && (
                <span className={s.state}>
                  done · {elapsed}<span className={`${s.ind} ${answered ? s.done : s.fail}`}><i /></span>
                </span>
              )}
              <button type="button" className={s.copy} onClick={copyConsole} disabled={!lines.length}>copy</button>
            </div>
            <div className={s.consoleBody}>
              {!lines.length && (
                <>
                  <div className={s.idleNote}>waiting for a question — pipeline stages</div>
                  <div className={s.pipe}>
                    {PIPELINE.map((p, i) => <Fragment key={p}>{i > 0 && <i>→</i>}<span>{p}</span></Fragment>)}
                  </div>
                </>
              )}
              {!!lines.length && (
                <div className={s.rows}>
                  {lines.map((l, i) => (
                    <div key={i} className={s.row}>
                      <span className={`${s.tg} ${s[`t_${l.tone}`]}`}>{l.tag}</span>
                      <span className={l.tone === "fail" ? `${s.ms} ${s.msFail}` : s.ms}>{l.text}</span>
                      <span className={s.du}>{l.dur}</span>
                    </div>
                  ))}
                </div>
              )}
              <div ref={endRef} />
            </div>
          </section>

          <section className={`card ${s.cand}`}>
            <h2 className="ct">検索候補（リランク後 上位）</h2>
            <div className="ct-rule sage" />
            <table className={s.tbl}>
              <thead>
                <tr><th className={s.num}>rank</th><th className={`${s.num} ${s.vc}`}>vector</th><th className={s.num}>bm25</th><th className={s.sc}>rerank</th><th>source · heading</th></tr>
              </thead>
              <tbody>
                {!candidates.length && (
                  <tr><td className={s.num}>—</td><td className={`${s.num} ${s.vc}`}>—</td><td className={s.num}>—</td><td className={s.sc}>—</td><td className={s.src}>検索を実行すると候補が表示されます</td></tr>
                )}
                {candidates.map((c) => (
                  <tr key={c.rank}>
                    <td className={`${s.num} ${c.rank === 1 ? s.r1 : ""}`}>{c.rank}</td>
                    <td className={`${s.num} ${s.vc}`}>{c.vector_rank ?? "—"}</td>
                    <td className={s.num}>{c.lexical_rank ?? "—"}</td>
                    <td className={s.sc}>
                      <span className={s.scV}>
                        <span className={c.rank === 1 ? "" : s.dim}>{c.rerank_score.toFixed(4)}</span>
                        <span className={s.scB}><i style={{ width: `${Math.round(c.rerank_score * 1000) / 10}%` }} /></span>
                      </span>
                    </td>
                    <td className={s.src}>{c.source_doc} · <span className={s.hd}>{c.heading ?? "—"}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className={s.cap}>{pool || "pool — · bm25-only —"}</div>
          </section>
        </div>
      </div>
    </div>
  );
}
