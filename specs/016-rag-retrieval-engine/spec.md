# Feature 016 — Hybrid retrieval engine & catalog (chunking · embeddings · numpy)

**Status:** Delivered · **Origin:** `library-ocr@8e590d0`, `83366cb`, `1d4298d` · **Old ref:** §12.2–§12.6
**Constitution:** I, II, III, IV, V, VII
**Related:** CLI surface → [017](../017-rag-cli/spec.md); MCP/integration → [018](../018-rag-mcp-integration/spec.md)

This story documents the **engine and data-model decisions** behind RAG retrieval —
why SQLite + `float32` BLOBs (not a vector DB), why brute-force numpy (not an ANN
index), why `bge-small` with an asymmetric query prefix, why page-based chunking, and
why hybrid dense+lexical fused with RRF. It is functional (it defines the catalog
schema and ranking contract) as well as a decision record; the user-facing commands
that drive it are feature 017.

## User Scenarios & Testing

### Primary user story
As the retrieval layer over a **personal-scale** library (~21 books, ~1.2k page-chunks),
I need a durable, portable, offline catalog and a ranking that handles both a paraphrased
concept and an approximate proper noun in one query — without dragging in a vector DB or
ANN index that this scale does not justify.

### Acceptance scenarios
1. **Given** the `out/book_*.md` corpus, **when** it is chunked, **then** the unit is one
   **page** (`### Page N`): a tiny page (<~200 chars) is folded into a neighbour, a long page
   (>~2000 chars) is split on paragraph boundaries with ~200-char overlap, and each chunk's
   `embed_text` is prefixed with a citation header (`{author} — {title} (p.{page}): …`).
2. **Given** chunks, **when** they are embedded, **then** `bge-small-en-v1.5` (384-dim, MPS)
   encodes **passages raw and unit-normalized**, stored as `float32` BLOBs in SQLite; the BGE
   query prefix is applied **only** at query time (feature 017).
3. **Given** a query vector, **when** the numpy backend queries, **then** similarity is a single
   `mat @ qvec` matmul over the in-memory normalized matrix (cosine, since vectors are
   normalized) → top-k — sub-millisecond at this scale, no ANN index.
4. **Given** a paraphrase + proper-noun query, **when** retrieval runs, **then** a **dense**
   channel (numpy) and a **lexical** channel (FTS5 bm25 over `text`+`author`+`book_title`) are
   fused with **Reciprocal Rank Fusion** (`1/(k0+rank)`, k0=60); the sea-nomads probe ranks the
   true passage dense #4, lexical #1, hybrid #1.
5. **Given** the catalog, **when** I inspect it, **then** it is one portable SQLite file
   (`out/rag.db`) with `chunks` (incl. `vec` BLOB, `vec_model`, `content_sha`), a `chunks_fts`
   FTS5 table, and a `meta` table — no `sqlite-vec`/Chroma/FAISS in the default path.

### Edge cases
- The asymmetric BGE detail is a footgun: mixing the query prefix into passages silently
  degrades recall, so it lives in exactly one place each (raw at index, prefixed at query).
- RRF fuses by **rank**, so a cosine in [-1,1] and an open-scale BM25 merge without any fragile
  score normalization.
- Vectors stored as portable BLOBs make rebuilding into another backend free (no re-embed).

## Requirements

### Functional
- **FR-001** The catalog MUST be one SQLite file (`out/rag.db`) and the **single source of
  truth**: `chunks(id, book_file, book_title, author, year, image, page, text, embed_text,
  content_sha, vec BLOB, vec_model)` + an FTS5 table over `text`+`author`+`book_title` + a
  `meta` table (`embed_model`/`embed_dim`).
- **FR-002** Vectors MUST be stored as little-endian `float32` BLOBs
  (`np.asarray(v, np.float32).tobytes()`), read back with `np.frombuffer`. SQLite MUST do no
  vector math; `sqlite-vec`/`sqlite-vss` MUST NOT be used (Principle III).
- **FR-003** Chunking MUST be page-based with tiny-page merge (`MIN_CHARS`), long-page split on
  paragraph boundaries with overlap, and a citation-header `embed_text`; `content_sha` over the
  chunk text MUST be the embedding cache key.
- **FR-004** Embeddings MUST default to `DEFAULT_EMBED_MODEL` (`bge-small-en-v1.5`), swappable
  via `--embed-model`, never a hardcoded second (Principle IV). Passages MUST be embedded **raw
  and normalized**; the query prefix is applied only in search.
- **FR-005** The vector engine MUST be a `VectorBackend` protocol (`build`/`query`/`save`/`load`)
  with `numpy` as the **default** (in-memory matmul on normalized BLOBs). FAISS/DuckDB are
  **deferred opt-in** backends behind one `--backend` switch, lazy-imported with a clear
  install message; the catalog stays SQLite regardless and any backend rebuilds from it in
  seconds with no re-embed.
- **FR-006** Retrieval MUST be hybrid: dense top-N + FTS5 lexical top-N fused with RRF (k0=60),
  stdlib arithmetic only. `--mode {hybrid,dense,lexical}` exposes each channel; hybrid is
  default because the motivating query needs both halves.
- **FR-007** The engine MUST run offline on MPS (Principles I, II) and only **read** `out/*.md`
  (Principle VII).

### Key entities
- **Chunk row** — `{ id, book_file, book_title, author, year, image, page, text, embed_text,
  vec, content_sha, vec_model }`.
- **VectorBackend** — `build(ids, vecs)`, `query(qvec, k) -> [(id, score)]`, `save`, `load`.

## Review & Acceptance Checklist
- [x] Page-chunking with merge/split/overlap + citation-header embed_text
- [x] SQLite-only catalog; vectors as portable BLOBs; numpy matmul default
- [x] bge-small, asymmetric prefix (raw passages / prefixed query)
- [x] Hybrid dense+lexical fused with RRF; rank-based, no score normalization

## Decision log (non-normative)
- **Why build vs adopt.** Chroma (onnxruntime + server/store model), FAISS (only earns its keep
  at 10⁵⁺ vectors), mcp-local-rag (weight is in ingestion, which the OCR tool already solved),
  niazarifin/rag_local (hash embeddings can't do semantic match) — all rejected as the default.
  FAISS/DuckDB kept as deferred opt-in backends behind one `--backend` switch (step 4, optional,
  not yet built — `load_backend()` already raises a clear "step 4" message for them).
- **Why SQLite + BLOB, not a vector DB.** Personal-scale corpus (thousands of chunks, not
  millions) ⇒ brute-force cosine over an in-memory matrix is milliseconds, so a dedicated index
  buys nothing but a heavy dep and an extra moving part. SQLite gives one portable file,
  transactions (a killed index run leaves a consistent DB), built-in FTS5, and no service.
- **Why hybrid + RRF.** The driver query has two halves with opposite needs: a paraphrased
  concept (→ dense embeddings) and an approximate proper noun "James C. Scott" (the single worst
  case for dense — name embeddings collapse → a lexical channel). Author-enriched `embed_text`
  also lets the author match semantically even when the body never repeats it.
- **Why bge-small (384-dim).** Small, fast, strong English retrieval (MTEB), runs on CPU/MPS,
  already cached; 384 dims keeps the BLOB matrix tiny so the whole library fits in memory for the
  matmul. The "(a) swap store vs (b) swap search" asks are only fully orthogonal for the numpy
  backend; for FAISS/DuckDB the index *is* both — so one `--backend` switch is the right grain.
- **Chunker bug found+fixed (step 1):** `split_long` was flushing a short paragraph alone before
  a huge one; the tiny-page merge + long-page split now share a `_fold_small` helper (no
  sub-`MIN_CHARS` stubs). 21 books → 1163 chunks (203–2658 chars), idempotent re-runs.
