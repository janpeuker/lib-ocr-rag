#!/usr/bin/env python3
"""Local, offline retrieval over the Markdown produced by ocr.py.

Builds a small SQLite catalog of page-sized chunks from out/book_*.md so Claude
can look up citations without loading whole books into context. See
IMPLEMENTATION_PLAN.md §12 for the full design.

This file currently implements STEP 1 only: the chunker + catalog. Embeddings,
hybrid search, and the MCP/Skill surface land in later steps. Vectors are stored
as float32 BLOBs in SQLite (no native vector type — math happens in numpy later);
the `vec` column is left NULL until step 2.

Usage:
    python rag.py index [--src out] [--db out/rag.db] [--force] [--show N]

100% offline; step 1 touches no model and no network.
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

DEFAULT_SRC = "out"
DB_NAME = "rag.db"
# Resolve relative src/db/probes against the rag.py install, not the caller's cwd,
# so the Skill/MCP work when invoked from any other project's directory.
SCRIPT_DIR = Path(__file__).resolve().parent

# Dense embeddings (IMPLEMENTATION_PLAN.md §12.4). Small + fast by default; swap
# via --embed-model. Passages are embedded raw; the query-side BGE instruction
# prefix is applied at search time (step 3), not here.
DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_BATCH = 64  # encode + checkpoint in batches so a killed run resumes

# Search (IMPLEMENTATION_PLAN.md §12.4). Dense + lexical channels fused with RRF.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "  # BGE query side
CANDIDATES = 50   # per-channel candidate pool feeding fusion
RRF_K0 = 60       # Reciprocal Rank Fusion damping constant
STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "at", "by", "for", "and", "or",
    "is", "are", "was", "were", "be", "been", "with", "as", "that", "this", "it",
    "its", "from", "into", "where", "what", "who", "whom", "does", "do", "did",
    "how", "why", "when", "which", "about", "not", "but", "they", "them", "their",
    "there", "here", "you", "we", "his", "her", "say", "said", "says",
}

# Chunking (IMPLEMENTATION_PLAN.md §12.5). One page is the natural unit; merge
# pages too small to retrieve on their own, split pages too large to cite tightly.
MIN_CHARS = 200      # below this a page is merged into a neighbour
MAX_CHARS = 2000     # above this a page is split on paragraph boundaries (~500 tokens)
OVERLAP_CHARS = 200  # carried between split parts so a citation isn't cut mid-thought


# --- Markdown parsing -------------------------------------------------------

def split_frontmatter(raw):
    """Return (meta dict, body) splitting a leading `---` YAML block if present."""
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            return parse_yaml_block(raw[3:end]), raw[end + 4:]
    return {}, raw


def parse_yaml_block(fm):
    """Minimal `key: value` parse — no YAML dep (frontmatter is flat & simple)."""
    meta = {}
    for line in fm.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, val = line.split(":", 1)
        meta[key.strip()] = val.strip().strip('"').strip("'").strip()
    return meta


def _first_h1(body):
    for line in body.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
    return None


def parse_book(path):
    """Parse one book_*.md into (meta, title, [page-unit dicts]).

    A page unit is {image, page, text}: the text under each `### Page N` (or, for
    an image section with no page header, the text directly under `## IMG_*`).
    Everything before the first `## ` (the H1 title + Zotero note) is ignored.
    """
    raw = path.read_text(encoding="utf-8")
    meta, body = split_frontmatter(raw)
    title = meta.get("title") or _first_h1(body) or path.stem

    units = []
    image = None
    page = None
    buf = []

    def flush():
        text = "\n".join(buf).strip()
        if image and text:
            units.append({"image": image, "page": page, "text": text})
        buf.clear()

    for line in body.splitlines():
        # `## IMG_*` (but not `### Page`): \s after `##` excludes a third `#`.
        m_img = re.match(r"^##\s+(\S.*?)\s*$", line)
        m_pg = re.match(r"^###\s+Page\s+(\S+)", line, re.IGNORECASE)
        if m_pg:
            flush()
            page = m_pg.group(1).strip()
        elif m_img:
            flush()
            image = m_img.group(1).strip()
            page = None
        elif line.startswith("# ") and not line.startswith("## "):
            continue  # H1 title line
        elif image is not None:
            buf.append(line)
        # else: preamble before the first image section — skip
    flush()
    return meta, title, units


# --- Chunking ---------------------------------------------------------------

def _fold_small(texts):
    """Fold any fragment shorter than MIN_CHARS into an adjacent one (§12.5).

    Merges backwards by default; a too-small leading fragment merges forwards.
    Shared by page-unit merging and split-part cleanup so neither leaves a stub.
    """
    out = []
    for t in texts:
        if out and len(t) < MIN_CHARS:
            out[-1] += "\n\n" + t
        else:
            out.append(t)
    if len(out) >= 2 and len(out[0]) < MIN_CHARS:
        out[1] = out[0] + "\n\n" + out[1]
        out = out[1:]
    return out


def merge_tiny(units):
    """Fold page units shorter than MIN_CHARS into an adjacent unit, keeping the
    surviving unit's citation (image/page)."""
    out = []
    for u in units:
        if out and len(u["text"]) < MIN_CHARS:
            out[-1]["text"] += "\n\n" + u["text"]
        else:
            out.append(dict(u))
    if len(out) >= 2 and len(out[0]["text"]) < MIN_CHARS:
        out[1]["text"] = out[0]["text"] + "\n\n" + out[1]["text"]
        out = out[1:]
    return out


def split_long(text):
    """Split text over MAX_CHARS on paragraph boundaries, with OVERLAP_CHARS carry."""
    if len(text) <= MAX_CHARS:
        return [text]
    paras = re.split(r"\n\s*\n", text)
    parts = []
    cur = ""
    for p in paras:
        # Only break when the current part can stand on its own; a tiny `cur`
        # before a huge paragraph must keep accreting, not flush as a stub.
        if len(cur) >= MIN_CHARS and len(cur) + len(p) + 2 > MAX_CHARS:
            parts.append(cur.strip())
            cur = cur[-OVERLAP_CHARS:] + "\n\n" + p  # overlap tail into next part
        else:
            cur = (cur + "\n\n" + p) if cur else p
    if cur.strip():
        parts.append(cur.strip())
    # A single paragraph longer than the budget: hard-split with overlap.
    sized = []
    for part in parts:
        if len(part) <= int(MAX_CHARS * 1.5):
            sized.append(part)
        else:
            step = MAX_CHARS - OVERLAP_CHARS
            sized.extend(part[i:i + MAX_CHARS] for i in range(0, len(part), step))
    return _fold_small(sized)


def build_embed_text(title, author, page, text):
    """Prefix the body with a citation header so a query naming the author/title
    can match even when the page body never repeats them (§12.4.2)."""
    header = " — ".join(b for b in (author, title) if b)
    if page:
        header = f"{header} (p.{page})" if header else f"(p.{page})"
    return f"{header}: {text}" if header else text


def chunk_book(path):
    """Yield catalog rows (dicts) for one book file."""
    meta, title, units = parse_book(path)
    author = meta.get("author") or ""
    year = meta.get("year") or ""
    stem = path.stem
    for u in merge_tiny(units):
        parts = split_long(u["text"])
        for idx, part in enumerate(parts):
            embed_text = build_embed_text(title, author, u["page"], part)
            page_key = u["page"] or "-"
            yield {
                "id": f"{stem}::{u['image']}::{page_key}::{idx}",
                "book_file": path.name,
                "book_title": title,
                "author": author,
                "year": year,
                "image": u["image"],
                "page": u["page"],
                "text": part,
                "embed_text": embed_text,
                "content_sha": hashlib.sha1(embed_text.encode("utf-8")).hexdigest(),
            }


# --- Catalog (SQLite) -------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
  id          TEXT PRIMARY KEY,
  book_file   TEXT NOT NULL,
  book_title  TEXT,
  author      TEXT,
  year        TEXT,
  image       TEXT,
  page        TEXT,
  text        TEXT NOT NULL,
  embed_text  TEXT NOT NULL,
  content_sha TEXT NOT NULL,
  vec         BLOB,
  vec_model   TEXT          -- which embed model produced vec (the cache key)
);
CREATE INDEX IF NOT EXISTS idx_chunks_book  ON chunks(book_file);
CREATE INDEX IF NOT EXISTS idx_chunks_image ON chunks(image);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(db_path):
    """Open the catalog, applying the schema and any column migration."""
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(chunks)")}
    if "vec_model" not in cols:  # migrate a step-1 db in place
        con.execute("ALTER TABLE chunks ADD COLUMN vec_model TEXT")
        con.commit()
    return con


def set_meta(con, key, value):
    con.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def write_catalog(db_path, chunks, force):
    """Upsert chunk rows, preserving a row's vec when its content is unchanged.

    When content_sha changes (or --force), the row is rewritten with vec=NULL so
    step 2's embedder knows to re-encode it. Rows no longer produced are deleted.
    This makes `index` resume-friendly and ready for the embedding cache (§12.6).
    """
    con = connect(db_path)
    try:
        existing = dict(con.execute("SELECT id, content_sha FROM chunks"))
        seen, n_new, n_kept = set(), 0, 0
        for c in chunks:
            seen.add(c["id"])
            if not force and existing.get(c["id"]) == c["content_sha"]:
                n_kept += 1
                continue
            con.execute(
                """INSERT INTO chunks
                     (id, book_file, book_title, author, year, image, page,
                      text, embed_text, content_sha, vec)
                   VALUES (?,?,?,?,?,?,?,?,?,?, NULL)
                   ON CONFLICT(id) DO UPDATE SET
                     book_file=excluded.book_file, book_title=excluded.book_title,
                     author=excluded.author, year=excluded.year,
                     image=excluded.image, page=excluded.page, text=excluded.text,
                     embed_text=excluded.embed_text,
                     content_sha=excluded.content_sha, vec=NULL""",
                (c["id"], c["book_file"], c["book_title"], c["author"], c["year"],
                 c["image"], c["page"], c["text"], c["embed_text"], c["content_sha"]),
            )
            n_new += 1
        stale = [(i,) for i in existing if i not in seen]
        con.executemany("DELETE FROM chunks WHERE id=?", stale)
        con.commit()
        return n_new, n_kept, len(stale)
    finally:
        con.close()


def build_fts(con):
    """(Re)build the FTS5 lexical index over the current chunks (§12.4.3).

    Rebuilt wholesale on each index — cheap at this corpus size, and it keeps the
    standalone FTS table trivially in sync with the chunks table (no triggers).
    """
    con.execute("DROP TABLE IF EXISTS chunks_fts")
    con.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5("
                "id UNINDEXED, text, author, book_title)")
    con.execute("INSERT INTO chunks_fts(id, text, author, book_title) "
                "SELECT id, text, author, book_title FROM chunks")
    con.commit()


# --- Embeddings (step 2) ----------------------------------------------------

def load_embedder(model_name):
    """Load the sentence-transformers model on MPS (offline after first download)."""
    from sentence_transformers import SentenceTransformer
    import torch
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    return SentenceTransformer(model_name, device=device)


def embed_pending(con, model_name, force):
    """Encode chunks that lack a current-model vector; checkpoint per batch.

    A chunk is (re)embedded when its vec is NULL (new/changed content — write_catalog
    nulls it) or was produced by a different model. So a normal `index` embeds only
    new pages; switching --embed-model re-embeds all; --force re-embeds everything.
    Returns (n_embedded, dim).
    """
    import numpy as np
    if force:
        rows = con.execute("SELECT id, embed_text FROM chunks").fetchall()
    else:
        rows = con.execute(
            "SELECT id, embed_text FROM chunks "
            "WHERE vec IS NULL OR vec_model IS NULL OR vec_model != ?",
            (model_name,)).fetchall()
    if not rows:
        return 0, int(get_meta(con, "embed_dim", 0))

    embedder = load_embedder(model_name)
    dim = None
    done = 0
    for start in range(0, len(rows), EMBED_BATCH):
        batch = rows[start:start + EMBED_BATCH]
        vecs = embedder.encode([t for _, t in batch], normalize_embeddings=True,
                               convert_to_numpy=True, show_progress_bar=False)
        vecs = vecs.astype(np.float32)
        dim = vecs.shape[1]
        con.executemany(
            "UPDATE chunks SET vec=?, vec_model=? WHERE id=?",
            [(v.tobytes(), model_name, cid) for (cid, _), v in zip(batch, vecs)])
        con.commit()  # checkpoint so a kill resumes at the next batch
        done += len(batch)
        print(f"  embedding {done}/{len(rows)} (dim {dim})", end="\r", flush=True)
    print()
    set_meta(con, "embed_model", model_name)
    set_meta(con, "embed_dim", dim)
    con.commit()
    return len(rows), dim


# --- Search: backends + hybrid retrieval (step 3) ---------------------------

class NumpyBackend:
    """Default vector backend: brute-force cosine via one matmul (§12.3).

    Vectors are unit-normalized at index time, so a dot product *is* cosine.
    `build` just holds the matrix in memory; `query` ranks every row. The faiss
    and duckdb backends (step 4) implement the same build/query contract.
    """
    name = "numpy"

    def __init__(self):
        self.ids = []
        self.mat = None

    def build(self, ids, mat):
        self.ids, self.mat = ids, mat

    def query(self, qvec, k):
        import numpy as np
        scores = self.mat @ qvec
        order = np.argsort(-scores)[:k]
        return [(self.ids[i], float(scores[i])) for i in order]


def load_backend(name):
    if name == "numpy":
        return NumpyBackend()
    raise SystemExit(f"backend '{name}' arrives in step 4 — use --backend numpy for now")


def build_backend(con, name):
    """Load cached vectors from the catalog into the chosen backend."""
    import numpy as np
    rows = con.execute("SELECT id, vec FROM chunks WHERE vec IS NOT NULL").fetchall()
    if not rows:
        raise SystemExit("no embeddings — run `python rag.py index` first")
    ids = [r[0] for r in rows]
    mat = np.vstack([np.frombuffer(r[1], np.float32) for r in rows])
    backend = load_backend(name)
    backend.build(ids, mat)
    return backend


def embed_query(model_name, query):
    """Encode a query with the BGE query-side instruction prefix (§12.4.1)."""
    import numpy as np
    embedder = load_embedder(model_name)
    v = embedder.encode([QUERY_PREFIX + query], normalize_embeddings=True,
                        convert_to_numpy=True)[0]
    return v.astype(np.float32)


def dense_rank(con, query, model_name, backend_name, n):
    backend = build_backend(con, backend_name)
    return backend.query(embed_query(model_name, query), n)  # [(id, cosine)]


def _fts_query(text):
    """Build an FTS5 OR-of-terms query (recall-friendly; bm25 ranks the rest)."""
    toks = [t for t in re.findall(r"[a-z0-9]+", text.lower())
            if len(t) > 2 and t not in STOPWORDS]
    return " OR ".join(f'"{t}"' for t in toks)


def lexical_rank(con, query, n):
    q = _fts_query(query)
    if not q:
        return []
    rows = con.execute(
        "SELECT id, bm25(chunks_fts) AS s FROM chunks_fts "
        "WHERE chunks_fts MATCH ? ORDER BY s LIMIT ?", (q, n)).fetchall()
    return [(r[0], -float(r[1])) for r in rows]  # negate bm25 so higher = better


def rrf(rankings, k0=RRF_K0):
    """Reciprocal Rank Fusion over best-first (id, score) lists → [(id, score)]."""
    fused = {}
    for ranking in rankings:
        for rank, (cid, _) in enumerate(ranking):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (k0 + rank + 1)
    return sorted(fused.items(), key=lambda kv: kv[1], reverse=True)


def search(con, query, mode, backend_name, k, book=None):
    """Return [(id, score)] for the top-k chunks under the chosen retrieval mode."""
    model_name = get_meta(con, "embed_model") or DEFAULT_EMBED_MODEL
    rankings = []
    if mode in ("dense", "hybrid"):
        rankings.append(dense_rank(con, query, model_name, backend_name, CANDIDATES))
    if mode in ("lexical", "hybrid"):
        rankings.append(lexical_rank(con, query, CANDIDATES))
    if book:
        allowed = {r[0] for r in con.execute(
            "SELECT id FROM chunks WHERE book_file LIKE ?", (f"%{book}%",))}
        rankings = [[(i, s) for i, s in r if i in allowed] for r in rankings]
    if len(rankings) > 1:
        ranked = rrf(rankings)
    else:
        ranked = rankings[0] if rankings else []
    return ranked[:k]


def _page(row):
    return row["page"] if row["page"] and row["page"] != "-" else None


def citation(row):
    """Paste-ready citation: 'Author, Title (year) · IMG_x p.N'."""
    bits = []
    if row["author"]:
        bits.append(row["author"])
    title = row["book_title"] or row["book_file"]
    bits.append(f"{title} ({row['year']})" if row["year"] else title)
    loc = row["image"] or ""
    if _page(row):
        loc += f" p.{row['page']}"
    return ", ".join(bits) + (f" · {loc}" if loc else "")


def _source_image_path(image_label):
    """Absolute path to the original page bitmap in in/, or None if absent.

    The OCR tool keys each page on its source filename, so the image label is that
    filename (`IMG_x.jpeg`) and maps directly to in/IMG_x.jpeg. RAG can't search the
    bitmap (retrieval is over the OCR'd text), but handing back the path lets an agent
    `Read` the original to verify garbled OCR, inspect figures/tables, or recover
    dropped handwriting — the page image is the only place that marginalia still
    exists. Resolved against SCRIPT_DIR so it works from any caller's cwd. in/ only:
    eval fixtures under test/ are deliberately not exposed.

    Some labels are section headings rather than filenames; those simply won't
    resolve to a file and return None."""
    if not image_label:
        return None
    name = image_label if image_label.lower().endswith(".jpeg") else f"{image_label}.jpeg"
    p = SCRIPT_DIR / "in" / name
    return str(p) if p.exists() else None


def result_dict(row, score=None):
    """Structured result for --json — full chunk text so it's quotable without
    loading the book; that's the token-saving payoff (§12.1)."""
    d = {
        "citation": citation(row),
        "book": row["book_title"] or row["book_file"],
        "author": row["author"] or None,
        "year": row["year"] or None,
        "image": row["image"],
        "image_path": _source_image_path(row["image"]),
        "page": _page(row),
        "book_file": row["book_file"],
        "text": row["text"],
    }
    if score is not None:
        d = {"score": round(float(score), 4), **d}
    return d


# --- CLI --------------------------------------------------------------------

def _resolve_db(args):
    """Resolve (src, db) paths against the install dir for relative inputs, so the
    catalog is found no matter what working directory rag.py is invoked from."""
    src = Path(args.src)
    if not src.is_absolute():
        src = SCRIPT_DIR / src
    db = Path(args.db) if args.db else src / DB_NAME
    if not db.is_absolute():
        db = SCRIPT_DIR / db
    return src, db

def cmd_index(args):
    src, db_path = _resolve_db(args)
    books = sorted(src.glob("book_*.md"))
    if not books:
        sys.exit(f"no book_*.md files in {src}/ — run `python ocr.py batch` first")

    rows, per_book = [], []
    for path in books:
        bc = list(chunk_book(path))
        rows.extend(bc)
        per_book.append((path.name, len(bc)))

    n_new, n_kept, n_stale = write_catalog(db_path, rows, args.force)

    # Summary — the step-1 acceptance check (row counts, citation fields).
    n_authored = sum(1 for r in rows if r["author"])
    n_paged = sum(1 for r in rows if r["page"])
    avg_len = sum(len(r["text"]) for r in rows) // max(1, len(rows))
    print(f"indexed {len(books)} books → {len(rows)} chunks  ({db_path})")
    print(f"  written/updated {n_new}, unchanged {n_kept}, removed {n_stale}")
    print(f"  {n_authored}/{len(rows)} chunks carry an author, "
          f"{n_paged}/{len(rows)} carry a page number, avg {avg_len} chars/chunk")
    print(f"  chars/chunk: min {min(len(r['text']) for r in rows)}, "
          f"max {max(len(r['text']) for r in rows)}")
    for name, n in per_book:
        print(f"    {name}: {n} chunks")
    if args.show:
        print(f"\n  sample embed_text prefixes (first {args.show}):")
        for r in rows[:args.show]:
            print(f"    [{r['id']}]\n      {r['embed_text'][:160]!r}")

    # Rebuild the FTS5 lexical index (step 3) + embed (step 2, cached).
    con = connect(db_path)
    try:
        build_fts(con)
        print(f"  built FTS5 lexical index over {len(rows)} chunks")
        if args.no_embed:
            return
        n_emb, dim = embed_pending(con, args.embed_model, args.force)
        n_total = con.execute("SELECT count(*) FROM chunks WHERE vec IS NOT NULL").fetchone()[0]
    finally:
        con.close()
    if n_emb:
        print(f"  embedded {n_emb} chunks with {args.embed_model} (dim {dim}); "
              f"{n_total}/{len(rows)} now vectorized")
    else:
        print(f"  embeddings up to date ({n_total}/{len(rows)} vectorized, {args.embed_model})")


def cmd_search(args):
    src, db_path = _resolve_db(args)
    if not db_path.exists():
        sys.exit(f"no catalog at {db_path} — run `python rag.py index` first")
    con = connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        hits = search(con, args.query, args.mode, args.backend, args.k,
                      args.book or None)
        results = []
        for cid, score in hits:
            row = con.execute("SELECT * FROM chunks WHERE id=?", (cid,)).fetchone()
            results.append(result_dict(row, score))
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return
        head = f'query: "{args.query}"  ·  mode={args.mode}  backend={args.backend}'
        head += f"  ·  book~{args.book}" if args.book else ""
        print(head)
        if not results:
            print("  (no results)")
            return
        for rank, r in enumerate(results, 1):
            snippet = re.sub(r"\s+", " ", r["text"]).strip()[:240]
            print(f"\n{rank}. [{r['score']:.3f}] {r['citation']}")
            print(f"   {snippet}…")
    finally:
        con.close()


def cmd_get_page(args):
    """Fetch a hit's full page (± neighbouring pages) for more context — without
    loading the whole book."""
    src, db_path = _resolve_db(args)
    if not db_path.exists():
        sys.exit(f"no catalog at {db_path} — run `python rag.py index` first")
    con = connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        img = args.image_id
        match = con.execute(
            "SELECT image, book_file FROM chunks WHERE image=? OR image LIKE ? LIMIT 1",
            (img, img + "%")).fetchone()
        if not match:
            sys.exit(f"no chunks for image '{img}'")
        image_label, book_file = match["image"], match["book_file"]

        images = [image_label]
        if args.neighbors:
            all_imgs = [r["image"] for r in con.execute(
                "SELECT DISTINCT image FROM chunks WHERE book_file=? ORDER BY image",
                (book_file,))]
            i = all_imgs.index(image_label)
            lo, hi = max(0, i - args.neighbors), i + args.neighbors + 1
            images = all_imgs[lo:hi]

        rows = []
        for im in images:
            rows.extend(con.execute(
                "SELECT * FROM chunks WHERE book_file=? AND image=? ORDER BY rowid",
                (book_file, im)).fetchall())

        if args.json:
            print(json.dumps([result_dict(r) for r in rows],
                             ensure_ascii=False, indent=2))
            return
        for r in rows:
            print(f"\n## {citation(r)}\n")
            print(r["text"])
    finally:
        con.close()


def _probe_match(probe, row):
    """A result row satisfies a probe if every specified matcher matches."""
    if probe.get("book") and probe["book"] not in (row["book_file"] or ""):
        return False
    if probe.get("image") and not (row["image"] or "").startswith(probe["image"]):
        return False
    if probe.get("page") and str(row["page"]) != str(probe["page"]):
        return False
    return True


def _first_hit_rank(con, ranked, probe, depth):
    for i, (cid, _) in enumerate(ranked[:depth], 1):
        row = con.execute("SELECT book_file, image, page FROM chunks WHERE id=?",
                          (cid,)).fetchone()
        if _probe_match(probe, row):
            return i
    return None


def cmd_eval(args):
    """Score dense vs lexical vs hybrid retrieval against rag_probes.json (§12.9 step 6).

    Each probe is {"query": ..., optional "book"/"image"/"page" matchers}. Reports
    recall@1/3/5 (a probe counts if a matching result is in the top-k) and MRR.
    Stdlib-only scoring; loads the embedder + backend once for the whole run.
    """
    src, db_path = _resolve_db(args)
    if not db_path.exists():
        sys.exit(f"no catalog at {db_path} — run `python rag.py index` first")
    probes_path = Path(args.probes)
    if not probes_path.is_absolute():
        probes_path = SCRIPT_DIR / probes_path
    if not probes_path.exists():
        sys.exit(f"no probes file at {probes_path}. Create a JSON list like:\n"
                 '  [{"query":"...","book":"book_01","image":"IMG_3557","page":"135"}]\n'
                 "  query is required; book/image/page are optional matchers.")
    probes = json.loads(probes_path.read_text(encoding="utf-8"))

    con = connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        model_name = get_meta(con, "embed_model") or DEFAULT_EMBED_MODEL
        backend = build_backend(con, args.backend)
        embedder = load_embedder(model_name)
        depth = 5

        def dense(q):
            import numpy as np
            v = embedder.encode([QUERY_PREFIX + q], normalize_embeddings=True,
                                convert_to_numpy=True)[0].astype(np.float32)
            return backend.query(v, depth)

        modes = ("dense", "lexical", "hybrid")
        ranks = {m: [] for m in modes}
        for p in probes:
            d = dense(p["query"])
            lex = lexical_rank(con, p["query"], depth)
            channels = {"dense": d, "lexical": lex, "hybrid": rrf([d, lex])[:depth]}
            for m in modes:
                ranks[m].append(_first_hit_rank(con, channels[m], p, depth))
            if args.verbose:
                rr = {m: ranks[m][-1] for m in modes}
                print(f"  {p['query'][:52]:52}  dense={rr['dense']}  "
                      f"lex={rr['lexical']}  hybrid={rr['hybrid']}")

        n = len(probes)
        recall = lambda rs, k: sum(1 for r in rs if r and r <= k) / n
        mrr = lambda rs: sum(1.0 / r for r in rs if r) / n
        print(f"\n{n} probes · depth {depth} · backend {args.backend}")
        print(f"{'mode':8} {'R@1':>6} {'R@3':>6} {'R@5':>6} {'MRR':>6}")
        for m in modes:
            rs = ranks[m]
            print(f"{m:8} {recall(rs,1):6.2f} {recall(rs,3):6.2f} "
                  f"{recall(rs,5):6.2f} {mrr(rs):6.2f}")
    finally:
        con.close()


def cmd_serve(args):
    """Optional MCP stdio server exposing the same search/get-page over the catalog.

    Thin wrapper over the CLI functions for projects that prefer a registered MCP
    tool. The Skill/CLI path needs none of this. See integration/ for how another
    project imports this server.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        sys.exit("MCP server needs the 'mcp' package: pip install -r requirements-rag.txt")

    src, db_path = _resolve_db(args)
    if not db_path.exists():
        sys.exit(f"no catalog at {db_path} — run `python rag.py index` first")
    backend = args.backend
    server = FastMCP("library-search")

    @server.tool()
    def search_library(query: str, k: int = 5, book: str = "",
                       mode: str = "hybrid") -> list:
        """Search the OCR'd book library for passages relevant to a query.

        Returns up to k results, each with a paste-ready `citation`, the book /
        author / image / page, and the full chunk `text` (quote it directly —
        no need to open the book file). `mode` is hybrid|dense|lexical; `book`
        restricts to a book_file substring.
        """
        con = connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            hits = search(con, query, mode, backend, k, book or None)
            return [result_dict(con.execute("SELECT * FROM chunks WHERE id=?", (cid,)).fetchone(),
                                score) for cid, score in hits]
        finally:
            con.close()

    @server.tool()
    def get_page(image_id: str, neighbors: int = 0) -> list:
        """Fetch a page (by image id, e.g. IMG_3557) in full, optionally with
        `neighbors` pages on each side, for more context around a search hit."""
        con = connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            m = con.execute("SELECT image, book_file FROM chunks WHERE image=? OR image LIKE ? LIMIT 1",
                            (image_id, image_id + "%")).fetchone()
            if not m:
                return []
            image_label, book_file = m["image"], m["book_file"]
            images = [image_label]
            if neighbors:
                alli = [r["image"] for r in con.execute(
                    "SELECT DISTINCT image FROM chunks WHERE book_file=? ORDER BY image",
                    (book_file,))]
                i = alli.index(image_label)
                images = alli[max(0, i - neighbors): i + neighbors + 1]
            out = []
            for im in images:
                out.extend(result_dict(r) for r in con.execute(
                    "SELECT * FROM chunks WHERE book_file=? AND image=? ORDER BY rowid",
                    (book_file, im)).fetchall())
            return out
        finally:
            con.close()

    server.run()  # stdio transport


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_index = sub.add_parser("index", help="chunk out/book_*.md into the SQLite catalog")
    p_index.add_argument("--src", default=DEFAULT_SRC, help="dir of book_*.md (default: out)")
    p_index.add_argument("--db", default="", help="catalog path (default: <src>/rag.db)")
    p_index.add_argument("--embed-model", dest="embed_model", default=DEFAULT_EMBED_MODEL,
                         help=f"sentence-transformers model (default: {DEFAULT_EMBED_MODEL})")
    p_index.add_argument("--no-embed", dest="no_embed", action="store_true",
                         help="chunk into the catalog only; skip embedding")
    p_index.add_argument("--force", action="store_true",
                         help="rewrite every chunk and re-embed all")
    p_index.add_argument("--show", type=int, default=0, help="print N sample chunks")
    p_index.set_defaults(func=cmd_index)

    p_search = sub.add_parser("search", help="retrieve citation-stamped passages")
    p_search.add_argument("query", help="natural-language query")
    p_search.add_argument("-k", type=int, default=5, help="results to return (default 5)")
    p_search.add_argument("--mode", choices=["hybrid", "dense", "lexical"],
                          default="hybrid", help="retrieval mode (default hybrid)")
    p_search.add_argument("--backend", default="numpy",
                          help="vector backend (numpy; faiss/duckdb in step 4)")
    p_search.add_argument("--book", default="", help="restrict to book_file containing this")
    p_search.add_argument("--json", action="store_true", help="emit JSON (for the Skill)")
    p_search.add_argument("--src", default=DEFAULT_SRC, help="dir holding rag.db (default: out)")
    p_search.add_argument("--db", default="", help="catalog path (default: <src>/rag.db)")
    p_search.set_defaults(func=cmd_search)

    p_page = sub.add_parser("get-page", help="print a page (± neighbours) in full")
    p_page.add_argument("image_id", help="image id, e.g. IMG_3557 or IMG_3557.jpeg")
    p_page.add_argument("--neighbors", type=int, default=0,
                        help="also include N pages on each side (default 0)")
    p_page.add_argument("--json", action="store_true", help="emit JSON (for the Skill)")
    p_page.add_argument("--src", default=DEFAULT_SRC, help="dir holding rag.db (default: out)")
    p_page.add_argument("--db", default="", help="catalog path (default: <src>/rag.db)")
    p_page.set_defaults(func=cmd_get_page)

    p_eval = sub.add_parser("eval", help="score dense/lexical/hybrid vs rag_probes.json")
    p_eval.add_argument("--probes", default="rag_probes.json", help="probe set (JSON list)")
    p_eval.add_argument("--backend", default="numpy", help="vector backend (default numpy)")
    p_eval.add_argument("--verbose", action="store_true", help="print per-probe ranks")
    p_eval.add_argument("--src", default=DEFAULT_SRC, help="dir holding rag.db (default: out)")
    p_eval.add_argument("--db", default="", help="catalog path (default: <src>/rag.db)")
    p_eval.set_defaults(func=cmd_eval)

    p_serve = sub.add_parser("serve", help="run the optional MCP stdio server")
    p_serve.add_argument("--backend", default="numpy", help="vector backend (default numpy)")
    p_serve.add_argument("--src", default=DEFAULT_SRC, help="dir holding rag.db (default: out)")
    p_serve.add_argument("--db", default="", help="catalog path (default: <src>/rag.db)")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
