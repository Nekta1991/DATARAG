"""retrieve_full_document: one whole source document, by catalog ID, capped.

    python -m rag.documents                       # the catalog the agent sees
    python -m rag.documents 02_smart_nogyo_koubo_bekki1_hashiwatashi

Cost: $0 here - plain SQL plus a local tokenizer. But what this returns is
pasted into a paid prompt, which is why it is capped.

Why an ID, not a title: the agent paraphrases. 「通常枠の公募要領」 matches
no stored title exactly, and a fuzzy match can land on the wrong 枠 - the
failure this corpus is built to expose. The agent picks from a closed list.

Why a cap: full documents run 11k-65k Claude tokens (count_tokens, measured).
Uncapped, one call on the 通常枠 公募要領 costs ~$0.13 of input at Sonnet
rates, ~12x an ordinary query, and is re-billed on every later turn that
carries it. At the 20k cap the worst measured payload is 18.7k tokens. Over the cap,
whole sections are returned in order, then the headings of what was left out,
so the agent can target the rest with search_knowledge_base.
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

import psycopg

from rag import config  # noqa: F401  - loads .env, pins HF_HOME
from rag.retrieval import voyage_tokens

MAX_TOKENS = int(os.getenv("FULLDOC_MAX_TOKENS", "20000"))
# Budgeting is done in Voyage tokens (tokenizer cached locally, no network),
# converted to Claude tokens. Measured with count_tokens on docs 01-08, whole
# documents and capped prefixes: Claude/Voyage = 1.215-1.364. 1.40 leaves a
# margin over the worst case. Characters are no proxy - 1.59 to 2.82 per token.
CLAUDE_PER_VOYAGE = float(os.getenv("CLAUDE_PER_VOYAGE_TOKEN", "1.40"))

_SECTION = re.compile(r"(?m)^(?=## )")


@dataclass
class FullDocument:
    doc_id: str
    source_doc: str
    authority_rank: int
    title: str
    text: str
    truncated: bool
    tokens_returned: int      # est. Claude tokens of as_tool_text(), header included
    tokens_total: int         # the same, had the whole document been returned
    omitted_headings: list[str] = field(default_factory=list)

    def as_tool_text(self) -> str:
        """What the agent actually reads."""
        head = (f"[{self.source_doc}] {self.title}\n"
                f"authority_rank={self.authority_rank} (1=交付規程 is binding)\n")
        if not self.truncated:
            return head + "\n" + self.text
        return (head
                + f"※ 文書が長いため先頭 約{self.tokens_returned:,} / "
                  f"{self.tokens_total:,} トークンのみ返却。\n\n"
                + self.text
                + "\n\n--- 以下の節は省略。必要なら search_knowledge_base で検索: ---\n"
                + "\n".join(f"- {h}" for h in self.omitted_headings))


def _claude_tokens(text: str) -> int:
    # Counts with Voyage's tokenizer and scales, rather than Claude's own:
    # measured 1.215-1.364 Claude tokens per Voyage token on this corpus, and
    # Anthropic's count_tokens is a network round trip per call.
    #
    # Shares rag.retrieval's vendored tokenizer instead of loading its own
    # through transformers. This runs on the serving path - retrieve_full_
    # document caps its output by token count - so a transformers import here
    # would pull the whole library into a serverless bundle that otherwise
    # needs none of it.
    return int(voyage_tokens(text) * CLAUDE_PER_VOYAGE)


def _heading(section: str) -> str | None:
    line = section.split("\n", 1)[0].lstrip("#").strip()
    # Docling splits some headings into fragments like 「２」 or 「改訂後）」;
    # they carry no information in an outline.
    return line if len(line) > 2 else None


def _dsn() -> str:
    return os.environ["DATABASE_URL"]


def _doc_id(filename: str) -> str:
    return filename.removesuffix(".pdf")


def document_catalog() -> list[dict]:
    """Every retrievable document. Built into the tool description, so the
    agent chooses from what exists instead of guessing a title."""
    with psycopg.connect(_dsn()) as conn:
        rows = conn.execute(
            """SELECT filename, program || COALESCE(' ' || waku, '') || ' ' || doc_type,
                      authority_rank, title, full_text
                 FROM documents ORDER BY filename""").fetchall()
    return [{"doc_id": _doc_id(fn), "source_doc": src, "authority_rank": rank,
             "title": title, "tokens": _claude_tokens(txt)}
            for fn, src, rank, title, txt in rows]


@lru_cache(maxsize=1)
def _name_keys() -> list[tuple[str, str, list[str]]]:
    """(doc_id, doc_type, 枠 names) per document. A 枠 with a parenthesised
    類型 gives both: 「インボイス枠（電子取引類型）」 -> インボイス枠, 電子取引類型."""
    with psycopg.connect(_dsn()) as conn:
        rows = conn.execute("SELECT filename, waku, doc_type FROM documents ORDER BY filename").fetchall()
    return [(_doc_id(fn), _nfkc(dt), [_nfkc(w) for w in re.split(r"[（）]", waku or "") if w])
            for fn, waku, dt in rows]


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def named_documents(question: str) -> list[str]:
    """doc_ids the question names: its doc_type plus one of its 枠 names
    (「通常枠の交付規程」), or a doc_type unique enough to stand alone
    (「加点項目一覧」). Gate 1 passes these whatever the rerank score - a
    question about a whole document's structure matches no single chunk
    well (Q7 scored 0.254 on 2026-09-22). Gate 2 still applies."""
    q = _nfkc(question)
    return [doc_id for doc_id, doc_type, waku in _name_keys()
            if doc_type in q and (any(w in q for w in waku) if waku else True)]


def retrieve_full_document(doc_id: str, max_tokens: int = MAX_TOKENS) -> FullDocument:
    with psycopg.connect(_dsn()) as conn:
        row = conn.execute(
            """SELECT filename, program || COALESCE(' ' || waku, '') || ' ' || doc_type,
                      authority_rank, title, full_text
                 FROM documents WHERE filename = %s""", (doc_id + ".pdf",)).fetchone()
        if row is None:
            valid = [_doc_id(r[0]) for r in
                     conn.execute("SELECT filename FROM documents ORDER BY 1")]
            raise KeyError(f"unknown doc_id {doc_id!r}; valid: {', '.join(valid)}")
    fn, src, rank, title, text = row
    whole = FullDocument(_doc_id(fn), src, rank, title, text, False, 0, 0)
    total = _claude_tokens(whole.as_tool_text())
    if total <= max_tokens:
        whole.tokens_returned = whole.tokens_total = total
        return whole

    # The cap covers what the agent reads: header, truncation notice and the
    # outline of omitted sections too - on doc 01 the outline alone is ~1k
    # tokens. Reserve for the outline of *every* heading, an upper bound on
    # whatever ends up omitted, then fill the rest with whole sections.
    sections = [s for s in _SECTION.split(text) if s.strip()]
    frame = FullDocument(whole.doc_id, src, rank, title, "", True, 0, total,
                         [h for h in map(_heading, sections) if h])
    budget = max_tokens - _claude_tokens(frame.as_tool_text())
    kept, used = [], 0
    for i, sec in enumerate(sections):
        n = _claude_tokens(sec)
        if used + n > budget:
            break
        kept.append(sec)
        used += n
    else:
        i = len(sections)
    if not kept:
        # A single opening section over the cap: cut it at a line boundary.
        lines, buf = sections[0].split("\n"), []
        for ln in lines:
            if _claude_tokens("\n".join(buf + [ln])) > budget:
                break
            buf.append(ln)
        kept, used, i = ["\n".join(buf)], _claude_tokens("\n".join(buf)), 1
    omitted = [h for h in map(_heading, sections[i:]) if h]
    doc = FullDocument(whole.doc_id, src, rank, title, "".join(kept).rstrip(),
                       True, 0, total, omitted)
    doc.tokens_returned = _claude_tokens(doc.as_tool_text())
    return doc


def _main():
    if len(sys.argv) < 2:
        for d in document_catalog():
            fits = "full" if d["tokens"] <= MAX_TOKENS else "capped"
            print(f"{d['doc_id']:46} {d['tokens']:>7,} tok  {fits:6}  {d['source_doc']}")
        return
    doc = retrieve_full_document(sys.argv[1])
    out = doc.as_tool_text()
    print(f"truncated={doc.truncated}  returned~{doc.tokens_returned:,} / "
          f"total~{doc.tokens_total:,} Claude tok  omitted sections={len(doc.omitted_headings)}")
    print(out[:600] + "\n...\n" + out[-600:])


if __name__ == "__main__":
    _main()
