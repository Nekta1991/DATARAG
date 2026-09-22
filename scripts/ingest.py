"""Ingest PDFs: Docling -> HybridChunker -> Voyage embeddings -> Neon/pgvector.

    python scripts/ingest.py 04_jisshi_youryou_bekki1_hokkaido.pdf
    python scripts/ingest.py --all
    python scripts/ingest.py --all --dry-run     # chunk only, no embeddings, no DB

Idempotent per document: re-ingesting a filename deletes its existing rows
(chunks cascade) and rewrites them, so a failed run can simply be re-run.

Cost: $0. Voyage embeddings bill against the 200M free allowance and this
corpus is ~302k tokens. No Anthropic call happens anywhere in this file.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

import psycopg
import voyageai
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

RAW = ROOT / "data" / "raw"
META = json.loads((ROOT / "data" / "corpus_metadata.json").read_text(encoding="utf-8"))
BY_FILENAME = {d["filename"]: d for d in META}

VOYAGE_MODEL = os.getenv("VOYAGE_MODEL", "voyage-3.5")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
BATCH = int(os.getenv("VOYAGE_BATCH_SIZE", "6"))
MIN_INTERVAL = float(os.getenv("VOYAGE_MIN_REQUEST_INTERVAL_SEC", "21"))
MAX_RETRIES = int(os.getenv("VOYAGE_MAX_RETRIES", "6"))
MAX_TOKENS = int(os.getenv("CHUNK_MAX_TOKENS", "512"))
MERGE_PEERS = os.getenv("CHUNK_MERGE_PEERS", "true").lower() == "true"

# Voyage's own tokenizer, so CHUNK_MAX_TOKENS counts the tokens Voyage bills
# rather than approximating with another model's vocabulary.
TOKENIZER_ID = "voyageai/voyage-3.5"


def log(msg: str) -> None:
    print(msg, flush=True)


# -- chunking ---------------------------------------------------------------

def build_chunker():
    """HybridChunker with markdown table rendering.

    Docling's default table serializer emits one "row, column = value" triplet
    per cell. On these documents' audit-criteria tables - which have merged
    header cells - that degenerates into lines like
    "① 事業実施主体の適格性, 審査項目 = ① 事業実施主体の適格性" repeated for
    every column, spending hundreds of tokens to say nothing. Markdown keeps
    the grid intact, which matters because 補助率 and 補助上限額 live in tables.
    """
    from docling.chunking import HybridChunker
    from docling_core.transforms.chunker.hierarchical_chunker import (
        ChunkingDocSerializer,
        ChunkingSerializerProvider,
    )
    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
    from docling_core.transforms.serializer.markdown import MarkdownTableSerializer
    from transformers import AutoTokenizer

    class MarkdownTableProvider(ChunkingSerializerProvider):
        def get_serializer(self, doc):
            return ChunkingDocSerializer(doc=doc, table_serializer=MarkdownTableSerializer())

    hf = AutoTokenizer.from_pretrained(TOKENIZER_ID)
    tok = HuggingFaceTokenizer(tokenizer=hf, max_tokens=MAX_TOKENS)
    return HybridChunker(
        tokenizer=tok,
        merge_peers=MERGE_PEERS,
        serializer_provider=MarkdownTableProvider(),
    ), hf


def build_converter():
    """Converter with OCR disabled.

    Every PDF in this corpus carries a real text layer (verified in
    MANIFEST.md), so OCR adds nothing but time - it ran ~7s/page on the first
    pass, which is ~40 minutes across 372 pages. If a scanned document is ever
    added to the corpus, this is the switch to flip back on.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = False
    opts.do_table_structure = True          # 補助率/補助上限額 live in tables
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def chunk_pdf(path: pathlib.Path, converter=None, chunker=None, hf=None):
    """Return (full_text, [chunk dicts]) for one PDF."""
    converter = converter or build_converter()
    if chunker is None:
        chunker, hf = build_chunker()

    t0 = time.time()
    doc = converter.convert(str(path)).document
    log(f"  docling convert   {time.time() - t0:5.1f}s")
    out = []
    for i, ch in enumerate(chunker.chunk(dl_doc=doc)):
        headings = getattr(ch.meta, "headings", None) or []
        pages = sorted({
            prov.page_no
            for item in getattr(ch.meta, "doc_items", [])
            for prov in getattr(item, "prov", [])
        })
        # contextualize() prepends the heading path, so an embedded chunk
        # carries its own section context instead of floating free.
        text = chunker.contextualize(chunk=ch)
        out.append({
            "chunk_index": i,
            "content": text,
            "heading": " > ".join(headings) if headings else None,
            "page_no": pages[0] if pages else None,
            # counted with Voyage's own tokenizer, so this is the number
            # Voyage bills and the number the TPM ceiling is measured in
            "token_count": len(hf.encode(text, add_special_tokens=False)),
        })
    return doc.export_to_markdown(), out


# -- embeddings -------------------------------------------------------------

class Embedder:
    """Voyage client with a client-side throttle for the unpaid 3 RPM ceiling."""

    def __init__(self):
        self.client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
        self.last_call = 0.0
        self.total_tokens = 0

    def _wait(self):
        gap = time.time() - self.last_call
        if self.last_call and gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        n_batches = (len(texts) + BATCH - 1) // BATCH
        for b in range(n_batches):
            batch = texts[b * BATCH:(b + 1) * BATCH]
            for attempt in range(MAX_RETRIES):
                self._wait()
                try:
                    r = self.client.embed(batch, model=VOYAGE_MODEL, input_type="document")
                    self.last_call = time.time()
                    self.total_tokens += r.total_tokens
                    vectors.extend(r.embeddings)
                    log(f"  embed batch {b + 1:>3}/{n_batches}  "
                        f"({len(batch)} chunks, {r.total_tokens} tok)")
                    break
                except Exception as e:
                    self.last_call = time.time()
                    if attempt == MAX_RETRIES - 1:
                        raise
                    backoff = MIN_INTERVAL * (2 ** attempt)
                    log(f"  retry {attempt + 1}/{MAX_RETRIES} after {backoff:.0f}s "
                        f"({type(e).__name__})")
                    time.sleep(backoff)
        return vectors


# -- database ---------------------------------------------------------------

def store(meta: dict, full_text: str, chunks: list[dict], vectors: list[list[float]]):
    dsn = os.getenv("DATABASE_URL_UNPOOLED") or os.environ["DATABASE_URL"]
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM documents WHERE filename = %s", (meta["filename"],))
        cur.execute(
            """INSERT INTO documents
                 (filename, title, source_url, sha256, program, fiscal_year,
                  waku, doc_type, page_count, char_count, full_text)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (meta["filename"], meta["title"], meta["source_url"], meta["sha256_short"],
             meta["program"], meta["fiscal_year"], meta["waku"], meta["doc_type"],
             meta["page_count"], len(full_text), full_text),
        )
        doc_id = cur.fetchone()[0]

        cur.executemany(
            """INSERT INTO chunks
                 (document_id, chunk_index, content, heading, page_no, token_count, embedding)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            [(doc_id, c["chunk_index"], c["content"], c["heading"], c["page_no"],
              c.get("token_count"), "[" + ",".join(f"{x:.8f}" for x in v) + "]")
             for c, v in zip(chunks, vectors)],
        )
        conn.commit()
    return doc_id


# -- driver -----------------------------------------------------------------

def ingest(filename: str, dry_run: bool, embedder, converter=None, chunker=None, hf=None):
    meta = BY_FILENAME.get(filename)
    if meta is None:
        sys.exit(f"{filename} is not in data/corpus_metadata.json")

    label = meta["doc_type"] + (f" / {meta['waku']}" if meta["waku"] else "")
    log(f"\n{filename}  [{label}]")

    full_text, chunks = chunk_pdf(RAW / filename, converter, chunker, hf)
    lens = sorted(len(c["content"]) for c in chunks)
    toks = sorted(c["token_count"] for c in chunks)
    log(f"  chunks            {len(chunks)}  "
        f"(chars min {lens[0]} / median {lens[len(lens) // 2]} / max {lens[-1]})")
    log(f"  tokens            total {sum(toks):,}  "
        f"(min {toks[0]} / median {toks[len(toks) // 2]} / max {toks[-1]}"
        f" / limit {MAX_TOKENS})")
    log(f"  full_text         {len(full_text):,} chars")

    if dry_run:
        log("  dry run - no embeddings, nothing written")
        return chunks

    vectors = embedder.embed([c["content"] for c in chunks])
    assert len(vectors) == len(chunks), f"{len(vectors)} vectors for {len(chunks)} chunks"
    assert len(vectors[0]) == EMBEDDING_DIM, f"got dim {len(vectors[0])}"

    doc_id = store(meta, full_text, chunks, vectors)
    log(f"  stored            document id={doc_id}, {len(chunks)} chunks")
    return chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("filenames", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    targets = [d["filename"] for d in META] if args.all else args.filenames
    if not targets:
        sys.exit("give a filename or --all")

    embedder = None if args.dry_run else Embedder()

    # Build the converter and chunker once. Docling reloads its layout models
    # per DocumentConverter instance, which across 14 documents is minutes.
    log("loading docling models...")
    converter = build_converter()
    chunker, hf = build_chunker()

    t0 = time.time()
    for fn in targets:
        ingest(fn, args.dry_run, embedder, converter, chunker, hf)

    log(f"\ndone in {time.time() - t0:.0f}s")
    if embedder:
        log(f"Voyage tokens used: {embedder.total_tokens:,}  (free allowance: 200,000,000)")


if __name__ == "__main__":
    main()
