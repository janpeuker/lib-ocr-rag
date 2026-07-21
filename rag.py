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

100% offline after the first embed-model download; HF_HUB_OFFLINE=1 is the
default (set HF_HUB_OFFLINE=0 explicitly to allow a new model download).
"""

import argparse
import datetime
import difflib
import hashlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

# Offline by default (spec 021): bge-small is cached locally, and an allowed
# network path can fail spuriously — sentence-transformers probes the Hub for a
# PEFT adapter_config.json even for cached models, and a stale HF token turns
# that probe into a 401 reported as RepositoryNotFoundError. Must precede the
# (lazy) sentence_transformers import; an explicit HF_HUB_OFFLINE in the
# environment wins (needed to download a new --embed-model).
os.environ.setdefault("HF_HUB_OFFLINE", "1")

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

# Cross-encoder reranking (spec 028). A bi-encoder scores query and passage
# separately, so a relational query ("weavers lenders") collapses to its topic
# ("weavers") and the discriminating term is lost. A cross-encoder reads the
# pair jointly and fixes exactly that. Small + English by design: the reranker only
# reorders a pool the cheap channels already found. One default, swap via
# --rerank-model, never a hardcoded second (Principle IV).
DEFAULT_RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-12-v2"
RERANK_WINDOWS = 250  # cap on (query, window) pairs scored per search. Tuned by
                      # eval: 250 and 500 score identically (R@1 .75 / MRR .81 on the
                      # probe set) and 250 halves the latency (~1.2 s vs ~2.4 s).

# Search (IMPLEMENTATION_PLAN.md §12.4; channels extended in spec 028).
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "  # BGE query side
CANDIDATES = 200  # per-channel candidate pool feeding fusion (50 → 200: the corpus
                  # grew 7× since 016, and the pool is the reranker's recall ceiling)
RRF_K0 = 60       # Reciprocal Rank Fusion damping constant
FUZZY_CUTOFF = 0.8   # difflib ratio for expanding a query term to OCR/spelling variants
FUZZY_EXPANSIONS = 4  # max vocabulary variants per query term
FUZZY_MIN_COUNT = 2   # ignore vocabulary terms this rare (OCR garbage, not variants)

# Result shaping (spec 028). Without these, one densely-matching book monopolises
# the page and overlap-split twins waste slots.
PER_BOOK_DEFAULT = 3  # max results from any one book (0 = unlimited)
DUP_RATIO = 0.85      # token-overlap containment above which a result is a near-duplicate

# Health checks (spec 029). This corpus grew 7× (1163 → 9483 chunks) against constants
# tuned at the small size, and nothing said so. `doctor` re-checks the assumptions that
# scale can invalidate. All thresholds are *relative* — no golden numbers — so the
# checks work on any library, which matters because in/ and out/ are gitignored and a
# new adopter has no fixtures at all.
CANDIDATES_TUNED_AT = 9500  # corpus size (chunks) CANDIDATES was last chosen against
SCALE_WARN_FACTOR = 3       # warn once the corpus outgrows that by this much
EVAL_STALE_GROWTH = 0.25    # warn when the corpus grew this fraction since the last eval
EVAL_HISTORY = "eval_history.jsonl"  # appended per eval run, for drift over time
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

# Dual granularity (spec 028). The page-sized chunk stays the *citation* unit — it
# is what makes a returned quote traceable — but it is far too coarse to embed: a
# ~370-token page averaged into one 384-d vector buries the single sentence that
# answers the query, and 8 % of chunks overflowed bge-small's 512-token cap and were
# silently truncated. So each chunk is additionally split into small windows, and
# those carry the vectors; a chunk scores as the best of its windows.
WINDOW_CHARS = 600     # ~150 tokens — well inside any embed model's context
WINDOW_OVERLAP = 120   # carry so a sentence split across windows still matches
WINDOW_MIN_CHARS = 150  # below this a window is folded into its neighbour
# Bump when split_windows' output changes. A chunk's content_sha covers the *page* text,
# so without this a change to the window logic leaves the old windows in place forever —
# the same auto-invalidation the OCR cache gets from PROMPT_VERSION (Principle V).
WINDOW_VERSION = "2"


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


# An image section heading is `## {image_filename}` — and *only* that. A page's own
# OCR'd Markdown routinely contains its own `## Subheading` lines, which would
# otherwise be read as the start of a new image section: the text after them gets
# attributed to a non-existent "image" (measured: 123 chunks under 71 invented labels),
# so those passages carry an uncitable citation, a null image_path, and are lost to
# `get-page` for the real page. Requiring an image extension separates the two cleanly
# (all 2993 real headings end in .jpeg; none of the 76 stray ones did).
_IMAGE_HEADING = re.compile(r"^##\s+(\S.*\.(?:jpe?g|png|tiff?|heic))\s*$", re.IGNORECASE)


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
        # `## IMG_*.jpeg` (but not `### Page`): \s after `##` excludes a third `#`.
        m_img = _IMAGE_HEADING.match(line)
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

def _fold_small(texts, min_chars=MIN_CHARS):
    """Fold any fragment shorter than `min_chars` into an adjacent one (§12.5).

    Merges backwards by default; a too-small leading fragment merges forwards.
    Shared by page-unit merging, split-part cleanup and window splitting (spec 028)
    so none of them leaves a stub.
    """
    out = []
    for t in texts:
        if out and len(t) < min_chars:
            out[-1] += "\n\n" + t
        else:
            out.append(t)
    if len(out) >= 2 and len(out[0]) < min_chars:
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


def split_long(text, max_chars=MAX_CHARS, overlap=OVERLAP_CHARS, min_chars=MIN_CHARS):
    """Split text over `max_chars` on paragraph boundaries, with `overlap` carry.

    Parameterised (spec 028) so page-chunk splitting and the finer window split
    share one implementation — paragraph boundaries, overlap carry and stub folding
    behave identically at both grains.
    """
    if len(text) <= max_chars:
        return [text]
    paras = re.split(r"\n\s*\n", text)
    parts = []
    cur = ""
    for p in paras:
        # Only break when the current part can stand on its own; a tiny `cur`
        # before a huge paragraph must keep accreting, not flush as a stub.
        if len(cur) >= min_chars and len(cur) + len(p) + 2 > max_chars:
            parts.append(cur.strip())
            cur = cur[-overlap:] + "\n\n" + p  # overlap tail into next part
        else:
            cur = (cur + "\n\n" + p) if cur else p
    if cur.strip():
        parts.append(cur.strip())
    # A single paragraph longer than the budget: hard-split with overlap.
    sized = []
    for part in parts:
        if len(part) <= int(max_chars * 1.5):
            sized.append(part)
        else:
            step = max_chars - overlap
            sized.extend(part[i:i + max_chars] for i in range(0, len(part), step))
    return _fold_small(sized, min_chars)


def split_windows(text):
    """Split a chunk's text into the small windows that actually carry the vectors.

    Paragraphs are the first boundary; a paragraph over the window budget is broken
    on sentence ends so a window rarely starts mid-thought (the page split can be
    cruder — it is never embedded directly).
    """
    pieces = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= WINDOW_CHARS:
            pieces.append(para)
            continue
        sents = re.split(r"(?<=[.!?])\s+", para)
        cur = ""
        for s in sents:
            if cur and len(cur) + len(s) + 1 > WINDOW_CHARS:
                pieces.append(cur.strip())
                cur = cur[-WINDOW_OVERLAP:] + " " + s
            else:
                cur = (cur + " " + s) if cur else s
        if cur.strip():
            pieces.append(cur.strip())
    # Re-pack: accrete small paragraphs up to the budget, then fold any stub.
    packed, cur = [], ""
    for p in pieces:
        if cur and len(cur) + len(p) + 2 > WINDOW_CHARS:
            packed.append(cur)
            cur = p
        else:
            cur = (cur + "\n\n" + p) if cur else p
    if cur:
        packed.append(cur)
    # Hard cap. Text with neither paragraph nor sentence boundaries — a table, an index,
    # a reference list — survives both splits above intact and would then be silently
    # truncated by the embedder. Slice it with overlap so no window exceeds the budget.
    sized = []
    for p in packed:
        if len(p) <= int(WINDOW_CHARS * 1.5):
            sized.append(p)
        else:
            step = WINDOW_CHARS - WINDOW_OVERLAP
            sized.extend(p[i:i + WINDOW_CHARS] for i in range(0, len(p), step))
    return _fold_small(sized, WINDOW_MIN_CHARS) or [text]


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
                # Each window carries the same citation header, so a query naming
                # the author/title still matches at window grain (spec 028).
                "windows": [build_embed_text(title, author, u["page"], w)
                            for w in split_windows(part)],
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
-- Dense retrieval unit (spec 028): small windows of a chunk. The chunk stays the
-- citation unit and holds the returned text; the vector lives here.
CREATE TABLE IF NOT EXISTS windows (
  id          TEXT PRIMARY KEY,   -- "{chunk_id}::w{n}"
  chunk_id    TEXT NOT NULL,
  ord         INTEGER NOT NULL,
  text        TEXT NOT NULL,      -- citation header + window body (what gets embedded)
  content_sha TEXT NOT NULL,
  vec         BLOB,
  vec_model   TEXT
);
CREATE INDEX IF NOT EXISTS idx_windows_chunk ON windows(chunk_id);
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
    # Vectors moved from chunks to windows (spec 028). A pre-028 catalog still
    # carries chunk vectors; drop them so they can't be mistaken for current ones
    # (the windows are re-embedded on the next index — the vec_model check drives it).
    if con.execute("SELECT count(*) FROM chunks WHERE vec IS NOT NULL").fetchone()[0]:
        if not con.execute("SELECT count(*) FROM windows").fetchone()[0]:
            con.execute("UPDATE chunks SET vec=NULL, vec_model=NULL")
            con.commit()
    return con


def set_meta(con, key, value):
    con.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _write_windows(con, c):
    """(Re)write one chunk's windows with NULL vectors, so embed_pending picks them up."""
    con.execute("DELETE FROM windows WHERE chunk_id=?", (c["id"],))
    con.executemany(
        "INSERT INTO windows (id, chunk_id, ord, text, content_sha, vec) "
        "VALUES (?,?,?,?,?, NULL)",
        [(f"{c['id']}::w{n}", c["id"], n, w,
          hashlib.sha1(w.encode("utf-8")).hexdigest())
         for n, w in enumerate(c["windows"])])


def write_catalog(db_path, chunks, force):
    """Upsert chunk rows, preserving a row's vec when its content is unchanged.

    When content_sha changes (or --force), the row is rewritten with vec=NULL so
    step 2's embedder knows to re-encode it. Rows no longer produced are deleted.
    This makes `index` resume-friendly and ready for the embedding cache (§12.6).
    """
    con = connect(db_path)
    try:
        existing = dict(con.execute("SELECT id, content_sha FROM chunks"))
        # A chunk row can be current while its windows are missing — that is the
        # pre-028 catalog, where vectors hung off the chunk. Such a chunk keeps its
        # row but still needs windows built (and embedded).
        # A window-logic change invalidates every window, regardless of chunk content.
        if get_meta(con, "window_version") != WINDOW_VERSION:
            con.execute("DELETE FROM windows")
            set_meta(con, "window_version", WINDOW_VERSION)
        windowed = {r[0] for r in con.execute("SELECT DISTINCT chunk_id FROM windows")}
        seen, n_new, n_kept = set(), 0, 0
        for c in chunks:
            seen.add(c["id"])
            if not force and existing.get(c["id"]) == c["content_sha"]:
                n_kept += 1
                if c["id"] not in windowed:
                    _write_windows(con, c)
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
            _write_windows(con, c)
            n_new += 1
        stale = [(i,) for i in existing if i not in seen]
        con.executemany("DELETE FROM chunks WHERE id=?", stale)
        con.executemany("DELETE FROM windows WHERE chunk_id=?", stale)
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
    """Encode windows that lack a current-model vector; checkpoint per batch.

    A window is (re)embedded when its vec is NULL (new/changed content — write_catalog
    rewrites windows with a NULL vec) or was produced by a different model. So a normal
    `index` embeds only new pages; switching --embed-model re-embeds all; --force
    re-embeds everything. Returns (n_embedded, dim).
    """
    import numpy as np
    if force:
        rows = con.execute("SELECT id, text FROM windows").fetchall()
    else:
        rows = con.execute(
            "SELECT id, text FROM windows "
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
            "UPDATE windows SET vec=?, vec_model=? WHERE id=?",
            [(v.tobytes(), model_name, wid) for (wid, _), v in zip(batch, vecs)])
        con.commit()  # checkpoint so a kill resumes at the next batch
        done += len(batch)
        print(f"  embedding {done}/{len(rows)} (dim {dim})", end="\r", flush=True)
    print()
    set_meta(con, "embed_model", model_name)
    set_meta(con, "embed_dim", dim)
    # Recorded so `doctor` can flag silent truncation without loading the model
    # (8 % of page-grain chunks used to overflow this cap unnoticed).
    set_meta(con, "embed_max_tokens", getattr(embedder, "max_seq_length", 0) or 0)
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
    """Load cached window vectors from the catalog into the chosen backend.

    Returns (backend, win2chunk) — the backend ranks *windows*, and the map folds a
    window hit back onto the page chunk that owns it (spec 028)."""
    import numpy as np
    rows = con.execute(
        "SELECT id, chunk_id, vec FROM windows WHERE vec IS NOT NULL").fetchall()
    if not rows:
        raise SystemExit("no embeddings — run `python rag.py index` first")
    ids = [r[0] for r in rows]
    win2chunk = {r[0]: r[1] for r in rows}
    mat = np.vstack([np.frombuffer(r[2], np.float32) for r in rows])
    backend = load_backend(name)
    backend.build(ids, mat)
    return backend, win2chunk


def _pool_windows(win_hits, win2chunk, n):
    """Fold ranked window hits onto their chunks, keeping each chunk's best window.

    Max-pooling (not averaging) is the point: a page answers a query because *one*
    passage on it does, and averaging would re-dilute exactly what windows fixed."""
    best = {}
    for wid, score in win_hits:
        cid = win2chunk[wid]
        if cid not in best or score > best[cid][1]:
            best[cid] = (wid, score)
    ranked = sorted(best.items(), key=lambda kv: kv[1][1], reverse=True)[:n]
    return [(cid, score) for cid, (_, score) in ranked]


def embed_query(model_name, query):
    """Encode a query with the BGE query-side instruction prefix (§12.4.1)."""
    import numpy as np
    embedder = load_embedder(model_name)
    v = embedder.encode([QUERY_PREFIX + query], normalize_embeddings=True,
                        convert_to_numpy=True)[0]
    return v.astype(np.float32)


def dense_rank(con, query, model_name, backend_name, n):
    backend, win2chunk = build_backend(con, backend_name)
    # Over-fetch windows: several of the top windows belong to the same page, so
    # n windows would pool down to fewer than n chunks.
    hits = backend.query(embed_query(model_name, query), n * 4)
    return _pool_windows(hits, win2chunk, n)  # [(chunk_id, cosine)]


def _query_terms(text):
    """Content tokens of a query — shared by every lexical channel."""
    return [t for t in re.findall(r"[a-z0-9]+", text.lower())
            if len(t) > 2 and t not in STOPWORDS]


def _fts_query(text):
    """Build an FTS5 OR-of-terms query (recall-friendly; bm25 ranks the rest)."""
    return " OR ".join(f'"{t}"' for t in _query_terms(text))


def _fts_rank(con, fts_query, n):
    if not fts_query:
        return []
    rows = con.execute(
        "SELECT id, bm25(chunks_fts) AS s FROM chunks_fts "
        "WHERE chunks_fts MATCH ? ORDER BY s LIMIT ?", (fts_query, n)).fetchall()
    return [(r[0], -float(r[1])) for r in rows]  # negate bm25 so higher = better


def lexical_rank(con, query, n):
    """OR-of-terms BM25 — the recall channel."""
    return _fts_rank(con, _fts_query(query), n)


def coverage_rank(con, query, n):
    """BM25 over pages containing *every* query term (spec 028).

    The OR channel is dominated by whichever terms are frequent in the corpus, so a
    query like "weavers lenders" ranks pages saturated with "weavers" and
    ignores the one discriminating word. Requiring all terms surfaces the page that
    actually joins them. Contributes nothing when no page has them all — the OR
    channel keeps recall, so this can afford to be strict.
    """
    toks = _query_terms(query)
    if len(toks) < 2:
        return []  # identical to the OR channel for a single term
    return _fts_rank(con, " AND ".join(f'"{t}"' for t in toks), n)


def _vocab(con):
    """Indexed terms of the FTS5 table, most frequent first (for fuzzy expansion).

    fts5vocab is a built-in SQLite shadow-table module — no dependency, and the whole
    vocabulary loads in well under a second at this corpus size."""
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.fts_vocab "
                "USING fts5vocab(main, chunks_fts, 'row')")
    return [r[0] for r in con.execute(
        "SELECT term FROM temp.fts_vocab WHERE cnt >= ? ORDER BY cnt DESC",
        (FUZZY_MIN_COUNT,))]


def fuzzy_rank(con, query, n):
    """BM25 over near-spellings of the query terms (spec 028).

    OCR'd proper nouns are the worst case for both other channels: the dense one
    collapses names, and exact FTS5 tokens miss by one character ("Wilhelm Braun" on the
    page reads "Wilhem Braun"). Expanding each term against the actual indexed vocabulary
    with stdlib difflib recovers those, and absorbs OCR spelling noise generally.
    """
    toks = _query_terms(query)
    if not toks:
        return []
    vocab = _vocab(con)
    expanded = []
    for t in toks:
        variants = difflib.get_close_matches(t, vocab, n=FUZZY_EXPANSIONS,
                                             cutoff=FUZZY_CUTOFF)
        expanded.extend(variants or [t])
    if set(expanded) <= set(toks):
        return []  # nothing new — the other channels already cover this query
    return _fts_rank(con, " OR ".join(f'"{t}"' for t in dict.fromkeys(expanded)), n)


def rrf(rankings, k0=RRF_K0):
    """Reciprocal Rank Fusion over best-first (id, score) lists → [(id, score)]."""
    fused = {}
    for ranking in rankings:
        for rank, (cid, _) in enumerate(ranking):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (k0 + rank + 1)
    return sorted(fused.items(), key=lambda kv: kv[1], reverse=True)


_RERANKER = {}  # model_name -> CrossEncoder (loaded once per process; `serve` reuses)


def load_reranker(model_name):
    """Load the cross-encoder (offline after first download); cached per process."""
    if model_name not in _RERANKER:
        from sentence_transformers import CrossEncoder
        import torch
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        _RERANKER[model_name] = CrossEncoder(model_name, device=device)
    return _RERANKER[model_name]


def rerank(con, query, ranked, model_name, n):
    """Re-score fused candidates with the cross-encoder, at window grain (spec 028).

    Windows (~150 tokens) are scored rather than whole pages because a cross-encoder
    truncates its input just as a bi-encoder does — feeding it 2000-char pages would
    silently cut the tail. A page takes its best window's score, for the same reason
    the dense channel max-pools.
    """
    if not ranked:
        return ranked
    order = {cid: i for i, (cid, _) in enumerate(ranked)}
    qs = ",".join("?" * len(order))
    rows = con.execute(
        f"SELECT id, chunk_id, text FROM windows WHERE chunk_id IN ({qs})",
        list(order)).fetchall()
    # Keep the pool bounded by taking windows of the best-fused pages first.
    rows.sort(key=lambda r: (order[r[1]], r[0]))
    rows = rows[:RERANK_WINDOWS]
    if not rows:
        return ranked
    scores = load_reranker(model_name).predict(
        [(query, r[2]) for r in rows], show_progress_bar=False)
    best = {}
    for (_, cid, _), s in zip(rows, scores):
        best[cid] = max(best.get(cid, float("-inf")), float(s))
    # Pages whose windows were cut off by the cap keep their fused order, below the
    # reranked ones — never dropped, just not promoted.
    scored = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    rest = [(cid, s) for cid, s in ranked if cid not in best]
    return (scored + rest)[:n]


def _shingles(text):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def diversify(con, ranked, k, per_book=PER_BOOK_DEFAULT, dup_ratio=DUP_RATIO):
    """Trim to k, capping any one book's share and dropping near-duplicates (spec 028).

    Three distinct effects, all observed in the raw ranking: a book that happens to
    discuss the query term densely takes every slot (5× Book B for "quernstone"); a long
    page split into several chunks returns the same page twice under one citation;
    and overlap twins plus unmerged duplicate books return the same prose twice.

    A page appears at most once — `get-page` is how you expand it, so a second chunk
    of the same page buys nothing and costs a slot.
    """
    out, per, kept, pages = [], {}, [], set()
    for cid, score in ranked:
        row = con.execute("SELECT book_file, image, text FROM chunks WHERE id=?",
                          (cid,)).fetchone()
        if row is None:
            continue
        book_file, image, text = row
        if (book_file, image) in pages:
            continue
        if per_book and per.get(book_file, 0) >= per_book:
            continue
        # Containment, not Jaccard: the duplicate-book twins split at different
        # lengths (1614 vs 2000 chars), which drags Jaccard under any usable
        # threshold even when one chunk's text is almost entirely inside the other.
        sh = _shingles(text)
        if sh and any(len(sh & prev) / min(len(sh), len(prev)) >= dup_ratio
                      for prev in kept if prev):
            continue
        out.append((cid, score))
        kept.append(sh)
        pages.add((book_file, image))
        per[book_file] = per.get(book_file, 0) + 1
        if len(out) >= k:
            break
    return out


def search(con, query, mode, backend_name, k, book=None, rerank_model=None,
           per_book=PER_BOOK_DEFAULT):
    """Return [(id, score)] for the top-k chunks under the chosen retrieval mode.

    Pipeline (spec 028): channels → RRF fusion → cross-encoder rerank → diversify.
    `rerank_model=None` skips reranking (the pure-RRF path, and what `--mode dense`
    or `lexical` means when you want to inspect one channel).
    """
    model_name = get_meta(con, "embed_model") or DEFAULT_EMBED_MODEL
    rankings = []
    if mode in ("dense", "hybrid"):
        rankings.append(dense_rank(con, query, model_name, backend_name, CANDIDATES))
    if mode in ("lexical", "hybrid"):
        rankings.append(lexical_rank(con, query, CANDIDATES))
    if mode == "hybrid":  # the precision channels only make sense alongside the others
        rankings.append(coverage_rank(con, query, CANDIDATES))
        rankings.append(fuzzy_rank(con, query, CANDIDATES))
    rankings = [r for r in rankings if r]
    if book:
        allowed = {r[0] for r in con.execute(
            "SELECT id FROM chunks WHERE book_file LIKE ?", (f"%{book}%",))}
        rankings = [[(i, s) for i, s in r if i in allowed] for r in rankings]
    if len(rankings) > 1:
        ranked = rrf(rankings)
    else:
        ranked = rankings[0] if rankings else []
    if rerank_model:
        ranked = rerank(con, query, ranked[:CANDIDATES], rerank_model, CANDIDATES)
    return diversify(con, ranked, k, per_book)


def excerpt(text, query, width=240):
    """Return the ~`width`-char span of `text` that best matches `query`.

    A page is up to 2000 chars but only a sentence or two of it answered the query,
    and showing the page's opening instead makes a correct hit look wrong — the term
    you searched for is simply off-screen. Picks the densest run of query terms;
    falls back to the head when nothing matches (e.g. a purely semantic hit).
    """
    flat = re.sub(r"\s+", " ", text).strip()
    terms = _query_terms(query)
    if not terms:
        return flat[:width]
    # Score every term occurrence, then take the window covering the most *distinct*
    # terms — density, not raw count, so one repeated word can't win over a real match.
    hits = []
    for t in terms:
        for m in re.finditer(re.escape(t), flat, re.IGNORECASE):
            hits.append((m.start(), t))
    if not hits:
        return flat[:width]
    hits.sort()
    best_i, best_score = hits[0][0], 0
    for i, (pos, _) in enumerate(hits):
        seen = {t for p, t in hits[i:] if p < pos + width}
        if len(seen) > best_score:
            best_score, best_i = len(seen), pos
    start = max(0, best_i - width // 4)
    out = flat[start:start + width]
    return ("…" if start else "") + out + ("…" if start + width < len(flat) else "")


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
        n_win, n_vec = con.execute(
            "SELECT count(*), count(vec) FROM windows").fetchone()
    finally:
        con.close()
    if n_emb:
        print(f"  embedded {n_emb} windows with {args.embed_model} (dim {dim}); "
              f"{n_vec}/{n_win} windows vectorized ({len(rows)} chunks)")
    else:
        print(f"  embeddings up to date ({n_vec}/{n_win} windows vectorized, "
              f"{args.embed_model})")

    # Warn-only health check on the everyday path (spec 029). A guard you have to
    # remember to run is a guard that does not run: the drift this catches accumulated
    # over 100 books precisely because nothing checked unprompted. Never blocks.
    if not args.no_check:
        probes_path = SCRIPT_DIR / "rag_probes.json"
        con = connect(db_path)
        try:
            print("\nhealth check (`rag.py doctor` for detail):")
            n_warn = run_doctor(con, src, probes_path, verbose=False)
        finally:
            con.close()
        if not n_warn:
            print("  ok  all checks passed")


def cmd_search(args):
    src, db_path = _resolve_db(args)
    if not db_path.exists():
        sys.exit(f"no catalog at {db_path} — run `python rag.py index` first")
    con = connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        hits = search(con, args.query, args.mode, args.backend, args.k,
                      args.book or None,
                      None if args.no_rerank else args.rerank_model,
                      args.per_book)
        results = []
        for cid, score in hits:
            if args.min_score is not None and score < args.min_score:
                continue  # `-k` is a cap, not a quota — don't pad with non-answers
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
            print(f"\n{rank}. [{r['score']:.3f}] {r['citation']}")
            print(f"   {excerpt(r['text'], args.query)}")
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


def cmd_books(args):
    """List the catalog's books — so an agent can scope a --book filter (or just see
    what the library actually holds) without opening any book file."""
    src, db_path = _resolve_db(args)
    if not db_path.exists():
        sys.exit(f"no catalog at {db_path} — run `python rag.py index` first")
    con = connect(db_path)
    try:
        rows = con.execute(
            "SELECT book_file, book_title, author, year, count(*) AS chunks, "
            "       count(DISTINCT image) AS pages "
            "FROM chunks GROUP BY book_file ORDER BY book_file").fetchall()
        if args.json:
            print(json.dumps([{"book_file": b, "book": t, "author": a or None,
                               "year": y or None, "chunks": c, "pages": p}
                              for b, t, a, y, c, p in rows],
                             ensure_ascii=False, indent=2))
            return
        print(f"{len(rows)} books in {db_path}")
        for b, t, a, y, c, p in rows:
            bits = " · ".join(x for x in (a, y) if x)
            print(f"  {b:52} {p:>4}p {c:>5}ch  {t}" + (f"  [{bits}]" if bits else ""))
    finally:
        con.close()


# --- health checks (spec 029) -----------------------------------------------

def _checks(con, src, probes_path):
    """Yield (level, name, message) for each catalog invariant.

    Every check is relative to the corpus in front of it — no committed fixture, no
    golden number — because in/ and out/ are gitignored, so a second user of this repo
    has an entirely different library and no baseline to compare against. Each check
    exists because its failure mode actually happened here and went unnoticed.
    """
    n_chunks, n_books = con.execute(
        "SELECT count(*), count(DISTINCT book_file) FROM chunks").fetchone()
    yield "info", "corpus", f"{n_books} books · {n_chunks} chunks"

    # 1. Uncitable image labels. A chunk whose image is not a real page file has a
    # broken citation, a null image_path and is unreachable by get-page. This is how
    # a Markdown-heading-vs-image-label confusion hid 123 chunks under 71 fake images.
    bad = con.execute(
        "SELECT image, count(*) c FROM chunks WHERE image NOT LIKE '%.jpeg' "
        "GROUP BY 1 ORDER BY c DESC").fetchall()
    if bad:
        n = sum(c for _, c in bad)
        yield ("warn", "uncitable-labels",
               f"{n} chunks under {len(bad)} non-filename image labels "
               f"(e.g. {bad[0][0][:40]!r}) — these cannot be cited or re-opened")
    else:
        yield "ok", "uncitable-labels", "every chunk cites a real page file"

    # 2. Pages present as text but absent from the catalog: a book file that produced
    # no chunks at all is silently unsearchable.
    empty = [p.name for p in sorted(Path(src).glob("book_*.md"))
             if not any(True for _ in chunk_book(p))]
    if empty:
        yield ("warn", "empty-books",
               f"{len(empty)} book file(s) produced no chunks: {', '.join(empty[:3])}"
               + (" …" if len(empty) > 3 else ""))
    else:
        yield "ok", "empty-books", "every book file contributes chunks"

    # 3. Vector health: unembedded windows, or vectors left behind by a model swap.
    n_win, n_vec = con.execute("SELECT count(*), count(vec) FROM windows").fetchone()
    model = get_meta(con, "embed_model") or DEFAULT_EMBED_MODEL
    stale = con.execute("SELECT count(*) FROM windows WHERE vec IS NOT NULL "
                        "AND vec_model IS NOT NULL AND vec_model != ?", (model,)).fetchone()[0]
    if n_vec < n_win:
        yield "warn", "vectors", f"{n_win - n_vec}/{n_win} windows unembedded — run `rag.py index`"
    elif stale:
        yield "warn", "vectors", f"{stale} windows embedded by a different model than {model}"
    else:
        yield "ok", "vectors", f"{n_vec} windows vectorized with {model}"

    # 4. Silent truncation. The embedder drops whatever exceeds its context, which is
    # invisible in every output — it just quietly stops matching.
    cap = int(get_meta(con, "embed_max_tokens", 0) or 0)
    if not cap:
        yield ("info", "truncation",
               "embed context cap not recorded yet — set on the next embedding pass")
    else:
        # 4.0 chars/token, measured over 3000 windows of this corpus. A looser estimate
        # cries wolf (3.5 flagged 469 windows where only 147 truly overflowed), and a
        # check that warns about nothing gets ignored — which is how drift survives.
        long_chars = int(cap * 4.0)
        over = con.execute("SELECT count(*) FROM windows WHERE length(text) > ?",
                           (long_chars,)).fetchone()[0]
        if over:
            yield ("warn", "truncation",
                   f"{over}/{n_win} windows may exceed the {cap}-token embed cap "
                   f"(>{long_chars} chars) and be silently cut")
        else:
            yield "ok", "truncation", f"all windows fit the {cap}-token embed cap"

    # 5. Scale drift: a candidate pool tuned at one corpus size is the reranker's
    # recall ceiling at every later size, and nothing recomputes it.
    if n_chunks > CANDIDATES_TUNED_AT * SCALE_WARN_FACTOR:
        yield ("warn", "scale",
               f"corpus is {n_chunks/CANDIDATES_TUNED_AT:.1f}× the size CANDIDATES="
               f"{CANDIDATES} was tuned at ({CANDIDATES_TUNED_AT} chunks) — re-tune "
               f"against `rag.py eval` and update CANDIDATES_TUNED_AT")
    else:
        yield "ok", "scale", f"CANDIDATES={CANDIDATES} still sized for {n_chunks} chunks"

    # 6. Probe validity. A matcher pointing at nothing scores 0 and reads as a
    # retrieval failure; three probes here matched book numbers that had renumbered.
    if probes_path.exists():
        probes = json.loads(probes_path.read_text(encoding="utf-8"))
        dead = []
        for p in probes:
            where, args = [], []
            if p.get("book"):
                where.append("book_file LIKE ?"); args.append(f"%{p['book']}%")
            if p.get("image"):
                where.append("image LIKE ?"); args.append(f"{p['image']}%")
            if p.get("page"):
                where.append("page = ?"); args.append(str(p["page"]))
            if not where:
                continue
            if not con.execute("SELECT 1 FROM chunks WHERE " + " AND ".join(where)
                               + " LIMIT 1", args).fetchone():
                dead.append(p["query"][:40])
        if dead:
            yield ("warn", "probes",
                   f"{len(dead)}/{len(probes)} probes match nothing in the catalog "
                   f"(e.g. {dead[0]!r}) — stale matcher, not a retrieval failure. "
                   f"Prefer `image` over `book`: book numbers renumber as the library grows")
        else:
            yield "ok", "probes", f"all {len(probes)} probe matchers resolve"
    else:
        yield ("warn", "probes",
               f"no probe set at {probes_path.name} — retrieval quality is unmeasured; "
               f"run `rag.py probes --scaffold` to bootstrap one from this corpus")

    # 7. Eval staleness: quality is only known as of the last measurement.
    at = int(get_meta(con, "eval_chunks", 0) or 0)
    if not at:
        yield "warn", "eval", "never evaluated — run `rag.py eval`"
    elif n_chunks > at * (1 + EVAL_STALE_GROWTH):
        yield ("warn", "eval",
               f"corpus grew {100*(n_chunks-at)/at:.0f}% since the last eval "
               f"({at} → {n_chunks} chunks) — re-run `rag.py eval`")
    else:
        yield "ok", "eval", f"last evaluated at {at} chunks"

    # 8. Cross-tool reconciliation. ocr.py records what it emitted in coverage.json;
    # this is the only place the two tools' views of the same corpus are compared, and
    # it is where a whole class of "the text exists but isn't searchable" bug lands.
    # A small gap is expected: a page of only tiny fragments is folded into its
    # neighbour by merge_tiny and keeps that neighbour's citation.
    cov = Path(src) / "coverage.json"
    if cov.exists():
        data = json.loads(cov.read_text(encoding="utf-8"))
        emitted = sum(data["summary"].values()) - len(data["not_emitted"])
        n_img = con.execute("SELECT count(DISTINCT image) FROM chunks").fetchone()[0]
        gap = emitted - n_img
        if gap > max(5, emitted * 0.02):
            yield ("warn", "ocr-rag-gap",
                   f"{gap} pages were written to book files but produced no chunk of "
                   f"their own — too many to be merge_tiny folding; re-run `rag.py index`")
        else:
            yield ("ok", "ocr-rag-gap",
                   f"{n_img} pages indexed of {emitted} emitted ({gap} folded into neighbours)")

    # 9. Duplicate books. Reported as info, not a warning: re-reading a book you forgot
    # you already read is normal use, and spec 028's near-duplicate suppression already
    # stops the twins from taking two result slots. Worth surfacing only so the count is
    # visible if it ever looks wrong — `in/merges.txt` folds them if you care to.
    dups = con.execute(
        "SELECT count(*) FROM (SELECT substr(text,1,200) p, count(DISTINCT image) n "
        "FROM chunks GROUP BY p HAVING n > 1)").fetchone()[0]
    if dups:
        yield ("info", "duplicates",
               f"{dups} passages appear under 2+ images (a book read twice — expected; "
               f"deduped at search time, fold with in/merges.txt if you want)")


def run_doctor(con, src, probes_path, verbose=True):
    """Run every check; return the number of warnings."""
    results = list(_checks(con, src, probes_path))
    n_warn = sum(1 for lvl, _, _ in results if lvl == "warn")
    for lvl, name, msg in results:
        if lvl == "ok" and not verbose:
            continue
        mark = {"ok": "  ok  ", "warn": " WARN ", "info": "      "}[lvl]
        print(f"{mark}{name:18} {msg}")
    return n_warn


def cmd_doctor(args):
    """Check the catalog's invariants — the assumptions that corpus growth invalidates."""
    src, db_path = _resolve_db(args)
    if not db_path.exists():
        sys.exit(f"no catalog at {db_path} — run `python rag.py index` first")
    probes_path = Path(args.probes)
    if not probes_path.is_absolute():
        probes_path = SCRIPT_DIR / probes_path
    con = connect(db_path)
    try:
        n_warn = run_doctor(con, src, probes_path, verbose=not args.quiet)
    finally:
        con.close()
    print(f"\n{n_warn} warning(s)." if n_warn else "\nall checks passed.")
    if n_warn and args.strict:
        sys.exit(1)


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


def cmd_probes(args):
    """Bootstrap a probe set from whatever corpus is present (spec 029).

    The probe set can't be a committed fixture — it is inherently corpus-specific, and
    a second user of this repo has an entirely different library — so instead of
    shipping probes we ship the means to generate them. Each probe is a distinctive
    sentence lifted from a page, matched back to that page by **image** (stable) rather
    than book number (which renumbers as the library grows).
    """
    src, db_path = _resolve_db(args)
    if not db_path.exists():
        sys.exit(f"no catalog at {db_path} — run `python rag.py index` first")
    out = Path(args.out)
    if not out.is_absolute():
        out = SCRIPT_DIR / out
    if out.exists() and not args.force:
        sys.exit(f"{out} exists — pass --force to overwrite (it is a throwaway file, "
                 f"but overwriting loses hand-tuned probes)")

    con = connect(db_path)
    try:
        # Term rarity over the indexed vocabulary: a good probe quotes a sentence that
        # few other pages could answer.
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.fts_vocab "
                    "USING fts5vocab(main, chunks_fts, 'row')")
        cnt = dict(con.execute("SELECT term, cnt FROM temp.fts_vocab"))
        # One page per book, spread across the library.
        rows = con.execute(
            "SELECT image, text, book_file FROM chunks WHERE image LIKE '%.jpeg' "
            "AND length(text) > 600 GROUP BY book_file ORDER BY random()").fetchall()
        probes = []
        for image, text, book_file in rows:
            best, best_score = None, 0.0
            for sent in re.split(r"(?<=[.!?])\s+", text):
                toks = _query_terms(sent)
                if not 6 <= len(toks) <= 25:
                    continue
                # Rarest-terms-per-word: favours a sentence with specific vocabulary.
                score = sum(1.0 / (1 + cnt.get(t, 0)) for t in toks) / len(toks)
                if score > best_score:
                    best, best_score = sent, score
            if best:
                probes.append({"query": re.sub(r"\s+", " ", best).strip()[:160],
                               "image": image.rsplit(".", 1)[0]})
            if len(probes) >= args.n:
                break
    finally:
        con.close()

    out.write_text(json.dumps(probes, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(f"wrote {len(probes)} probes → {out}")
    print("These are verbatim sentences, so they flatter the lexical channel. Edit them "
          "into paraphrases (how you would actually ask) for a meaningful score, and "
          "delete any that are boilerplate. Then: python rag.py eval --verbose")


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
                 '  [{"query":"...","book":"book_01","image":"IMG_1234","page":"135"}]\n'
                 "  query is required; book/image/page are optional matchers.")
    probes = json.loads(probes_path.read_text(encoding="utf-8"))

    con = connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        model_name = get_meta(con, "embed_model") or DEFAULT_EMBED_MODEL
        backend, win2chunk = build_backend(con, args.backend)
        embedder = load_embedder(model_name)
        depth = 5

        def dense(q, n):
            import numpy as np
            v = embedder.encode([QUERY_PREFIX + q], normalize_embeddings=True,
                                convert_to_numpy=True)[0].astype(np.float32)
            return _pool_windows(backend.query(v, n * 4), win2chunk, n)

        # Every channel gets its own column, so each addition in spec 028 has to earn
        # its place against the probe set rather than on argument (Principle VI).
        modes = ("dense", "lexical", "coverage", "fuzzy", "hybrid", "reranked")
        ranks = {m: [] for m in modes}
        for p in probes:
            q = p["query"]
            d = dense(q, CANDIDATES)
            lex = lexical_rank(con, q, CANDIDATES)
            cov = coverage_rank(con, q, CANDIDATES)
            fuz = fuzzy_rank(con, q, CANDIDATES)
            fused = rrf([r for r in (d, lex, cov, fuz) if r])
            channels = {
                "dense": d[:depth], "lexical": lex[:depth],
                "coverage": cov[:depth], "fuzzy": fuz[:depth],
                "hybrid": diversify(con, fused, depth, args.per_book),
                "reranked": diversify(
                    con, rerank(con, q, fused[:CANDIDATES], args.rerank_model, CANDIDATES),
                    depth, args.per_book),
            }
            for m in modes:
                ranks[m].append(_first_hit_rank(con, channels[m], p, depth))
            if args.verbose:
                rr = "  ".join(f"{m}={ranks[m][-1]}" for m in modes)
                print(f"  {q[:44]:44}  {rr}")

        n = len(probes)
        recall = lambda rs, k: sum(1 for r in rs if r and r <= k) / n
        mrr = lambda rs: sum(1.0 / r for r in rs if r) / n

        # Record when quality was last measured, and against how much corpus, so
        # `doctor` can say "you grew 40 % since this number was true" (spec 029).
        n_chunks = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
        set_meta(con, "eval_chunks", n_chunks)
        set_meta(con, "eval_at", datetime.datetime.now().isoformat(timespec="seconds"))
        con.commit()
        hist = Path(src) / EVAL_HISTORY
        with hist.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "at": get_meta(con, "eval_at"),
                "chunks": n_chunks,
                "books": con.execute(
                    "SELECT count(DISTINCT book_file) FROM chunks").fetchone()[0],
                "probes": n,
                "embed_model": model_name,
                "rerank_model": args.rerank_model,
                "scores": {m: {"r1": round(recall(ranks[m], 1), 3),
                               "r3": round(recall(ranks[m], 3), 3),
                               "r5": round(recall(ranks[m], 5), 3),
                               "mrr": round(mrr(ranks[m]), 3)} for m in modes},
            }, ensure_ascii=False) + "\n")
        print(f"\n{n} probes · depth {depth} · backend {args.backend} "
              f"· rerank {args.rerank_model}")
        print(f"{'mode':9} {'R@1':>6} {'R@3':>6} {'R@5':>6} {'MRR':>6}")
        for m in modes:
            rs = ranks[m]
            print(f"{m:9} {recall(rs,1):6.2f} {recall(rs,3):6.2f} "
                  f"{recall(rs,5):6.2f} {mrr(rs):6.2f}")
        # Drift is the signal quality decay actually shows up as; a single run can't
        # show it, so compare against the previous entry in the history log.
        prev = [json.loads(l) for l in hist.read_text(encoding="utf-8").splitlines() if l]
        if len(prev) > 1 and prev[-2]["probes"] == n:
            was, now = prev[-2]["scores"]["reranked"]["mrr"], prev[-1]["scores"]["reranked"]["mrr"]
            if now < was - 0.02:
                print(f"\n  ⚠ reranked MRR fell {was:.2f} → {now:.2f} since "
                      f"{prev[-2]['at']} ({prev[-2]['chunks']} → {n_chunks} chunks)")
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
                       mode: str = "hybrid", per_book: int = PER_BOOK_DEFAULT) -> list:
        """Search the OCR'd book library for passages relevant to a query.

        Returns up to k results, each with a paste-ready `citation`, the book /
        author / image / page, and the full chunk `text` (quote it directly —
        no need to open the book file). `mode` is hybrid|dense|lexical; `book`
        restricts to a book_file substring; `per_book` caps how many results any
        one book may take (0 = no cap).
        """
        con = connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            hits = search(con, query, mode, backend, k, book or None,
                          args.rerank_model, per_book)
            return [result_dict(con.execute("SELECT * FROM chunks WHERE id=?", (cid,)).fetchone(),
                                score) for cid, score in hits]
        finally:
            con.close()

    @server.tool()
    def get_page(image_id: str, neighbors: int = 0) -> list:
        """Fetch a page (by image id, e.g. IMG_1234) in full, optionally with
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
    p_index.add_argument("--no-check", dest="no_check", action="store_true",
                         help="skip the post-index health check")
    p_index.set_defaults(func=cmd_index)

    p_search = sub.add_parser("search", help="retrieve citation-stamped passages")
    p_search.add_argument("query", help="natural-language query")
    p_search.add_argument("-k", type=int, default=5, help="results to return (default 5)")
    p_search.add_argument("--mode", choices=["hybrid", "dense", "lexical"],
                          default="hybrid", help="retrieval mode (default hybrid)")
    p_search.add_argument("--backend", default="numpy",
                          help="vector backend (numpy; faiss/duckdb in step 4)")
    p_search.add_argument("--book", default="", help="restrict to book_file containing this")
    p_search.add_argument("--per-book", dest="per_book", type=int, default=PER_BOOK_DEFAULT,
                          help=f"max results from one book (default {PER_BOOK_DEFAULT}; 0 = no cap)")
    p_search.add_argument("--no-rerank", dest="no_rerank", action="store_true",
                          help="skip the cross-encoder rerank (faster, less precise)")
    p_search.add_argument("--min-score", dest="min_score", type=float, default=None,
                          help="drop results below this score. With the default reranker "
                               "the scores are logits: >0 is a real answer, <0 is a term "
                               "that merely appears. `--min-score 0` trims the tail.")
    p_search.add_argument("--rerank-model", dest="rerank_model", default=DEFAULT_RERANK_MODEL,
                          help=f"cross-encoder model (default: {DEFAULT_RERANK_MODEL})")
    p_search.add_argument("--json", action="store_true", help="emit JSON (for the Skill)")
    p_search.add_argument("--src", default=DEFAULT_SRC, help="dir holding rag.db (default: out)")
    p_search.add_argument("--db", default="", help="catalog path (default: <src>/rag.db)")
    p_search.set_defaults(func=cmd_search)

    p_page = sub.add_parser("get-page", help="print a page (± neighbours) in full")
    p_page.add_argument("image_id", help="image id, e.g. IMG_1234 or IMG_1234.jpeg")
    p_page.add_argument("--neighbors", type=int, default=0,
                        help="also include N pages on each side (default 0)")
    p_page.add_argument("--json", action="store_true", help="emit JSON (for the Skill)")
    p_page.add_argument("--src", default=DEFAULT_SRC, help="dir holding rag.db (default: out)")
    p_page.add_argument("--db", default="", help="catalog path (default: <src>/rag.db)")
    p_page.set_defaults(func=cmd_get_page)

    p_books = sub.add_parser("books", help="list the books held in the catalog")
    p_books.add_argument("--json", action="store_true", help="emit JSON (for the Skill)")
    p_books.add_argument("--src", default=DEFAULT_SRC, help="dir holding rag.db (default: out)")
    p_books.add_argument("--db", default="", help="catalog path (default: <src>/rag.db)")
    p_books.set_defaults(func=cmd_books)

    p_doc = sub.add_parser("doctor", help="check catalog invariants (coverage, scale, staleness)")
    p_doc.add_argument("--probes", default="rag_probes.json", help="probe set to validate")
    p_doc.add_argument("--quiet", action="store_true", help="print warnings only")
    p_doc.add_argument("--strict", action="store_true", help="exit non-zero if any check warns")
    p_doc.add_argument("--src", default=DEFAULT_SRC, help="dir holding rag.db (default: out)")
    p_doc.add_argument("--db", default="", help="catalog path (default: <src>/rag.db)")
    p_doc.set_defaults(func=cmd_doctor)

    p_probes = sub.add_parser("probes", help="bootstrap a probe set from this corpus")
    p_probes.add_argument("--scaffold", action="store_true",
                          help="generate probes (currently the only mode)")
    p_probes.add_argument("-n", type=int, default=20, help="how many probes (default 20)")
    p_probes.add_argument("--out", default="rag_probes.json", help="where to write")
    p_probes.add_argument("--force", action="store_true", help="overwrite an existing probe set")
    p_probes.add_argument("--src", default=DEFAULT_SRC, help="dir holding rag.db (default: out)")
    p_probes.add_argument("--db", default="", help="catalog path (default: <src>/rag.db)")
    p_probes.set_defaults(func=cmd_probes)

    p_eval = sub.add_parser("eval", help="score dense/lexical/hybrid vs rag_probes.json")
    p_eval.add_argument("--probes", default="rag_probes.json", help="probe set (JSON list)")
    p_eval.add_argument("--backend", default="numpy", help="vector backend (default numpy)")
    p_eval.add_argument("--rerank-model", dest="rerank_model", default=DEFAULT_RERANK_MODEL,
                        help=f"cross-encoder model (default: {DEFAULT_RERANK_MODEL})")
    p_eval.add_argument("--per-book", dest="per_book", type=int, default=PER_BOOK_DEFAULT,
                        help=f"max results from one book (default {PER_BOOK_DEFAULT}; 0 = no cap)")
    p_eval.add_argument("--verbose", action="store_true", help="print per-probe ranks")
    p_eval.add_argument("--src", default=DEFAULT_SRC, help="dir holding rag.db (default: out)")
    p_eval.add_argument("--db", default="", help="catalog path (default: <src>/rag.db)")
    p_eval.set_defaults(func=cmd_eval)

    p_serve = sub.add_parser("serve", help="run the optional MCP stdio server")
    p_serve.add_argument("--backend", default="numpy", help="vector backend (default numpy)")
    p_serve.add_argument("--rerank-model", dest="rerank_model", default=DEFAULT_RERANK_MODEL,
                         help=f"cross-encoder model (default: {DEFAULT_RERANK_MODEL})")
    p_serve.add_argument("--src", default=DEFAULT_SRC, help="dir holding rag.db (default: out)")
    p_serve.add_argument("--db", default="", help="catalog path (default: <src>/rag.db)")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
