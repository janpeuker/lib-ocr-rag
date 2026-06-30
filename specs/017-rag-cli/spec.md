# Feature 017 — RAG CLI surface (index · search · get-page · eval)

**Status:** Delivered · **Origin:** `library-ocr@1d4298d`, `0558341`, `29e81dd`, `56ef31e` · **Old ref:** §12.7, §12.9
**Constitution:** I, III, IV, V, VII
**Related:** engine → [016](../016-rag-retrieval-engine/spec.md); MCP/integration → [018](../018-rag-mcp-integration/spec.md)

## User Scenarios & Testing

### Primary user story
As a researcher (or an agent shelling out), I want a fast, offline command-line surface to
(re)build the index and look up citation-stamped page snippets — *look up, don't load* — that
works from any working directory and that re-embeds only what changed.

### Acceptance scenarios
1. **Given** new/changed `out/book_*.md`, **when** I run `rag.py index`, **then** it chunks →
   embeds (cached) → builds the FTS5 table, re-embedding only new/changed pages;
   `--force` re-embeds all, `--no-embed` chunks only, a killed run resumes.
2. **Given** a question, **when** I run `rag.py search "…" [-k N] [--mode hybrid|dense|lexical]
   [--book S] [--json]`, **then** I get the top-k pages; default output is compact Markdown,
   `--json` emits `[{score, citation, book, author, year, image, page, book_file, text,
   image_path}]` with full chunk text.
3. **Given** a hit, **when** I run `rag.py get-page IMG_x [--neighbors N] [--json]`, **then** I
   get that page ± neighbours in full, without loading the book.
4. **Given** any result, **when** I read `citation`, **then** it is paste-ready —
   *"Andaya, Leaves of the Same Tree (2008), IMG_4894 p.3"*.
5. **Given** the tool is invoked from another project's directory, **when** any command runs,
   **then** `--src`/`--db`/`--probes` resolve against the install dir (`SCRIPT_DIR`), not the
   caller's cwd.
6. **Given** `rag.py eval [--verbose]`, **when** run, **then** it scores dense/lexical/hybrid by
   recall@1/3/5 + MRR against a **gitignored** `rag_probes.json`, stdlib-only.

### Edge cases
- `search`/`get-page` must be **fast to invoke** (cold start matters for a per-call CLI): the
  embedding model is lazy-loaded only for dense/hybrid; the numpy load-blobs-and-matmul path is
  warm-start-free.
- Every JSON result carries `image_path` (absolute path to the source photo in `in/`, or
  `null`); some `image` labels are section headings, not filenames → `null`; `test/` fixtures
  are never exposed.
- The probe set MUST NOT be committed as a fixture (mirrors `experiments/`).

## Requirements

### Functional
- **FR-001** `rag.py index [--src out/] [--embed-model …] [--no-embed] [--force]` MUST chunk →
  embed (cached, keyed on `(content_sha, embed_model)`) → build FTS5; resume-by-default; this is
  also the re-index path after new OCR (Principle V).
- **FR-002** `rag.py search "Q" [-k 5] [--mode hybrid] [--book S] [--json]` MUST be the primary
  retrieval path; default compact Markdown, `--json` the structured array with full text.
- **FR-003** `rag.py get-page IMAGE_ID [--neighbors N] [--json]` MUST fetch a page ± neighbours.
- **FR-004** Each `--json` result MUST include `image_path` resolved via `_source_image_path()`
  against `SCRIPT_DIR/in` (or `null`).
- **FR-005** `rag.py eval` MUST score dense/lexical/hybrid (recall@k/MRR) against a gitignored
  `rag_probes.json`; stdlib-only.
- **FR-006** All relative paths MUST resolve against `SCRIPT_DIR` (the install), never the
  caller's cwd, so the tool works from any project.
- **FR-007** Every command MUST run offline (`HF_HUB_OFFLINE=1`) with the embedding model
  lazy-loaded only when a dense/hybrid query needs it.
- **FR-008** Shared helpers `result_dict()` + `citation()` MUST back both the CLI and the MCP
  server (feature 018), so the two surfaces never drift.

### Key entities
- **Search result** — `{ score, citation, book, author, year, image, page, book_file, text,
  image_path }`.
- **Probe** — `{ query, book?/image?/page? }` matcher in `rag_probes.json`.

## Review & Acceptance Checklist
- [x] index/search/get-page/eval; resumable, cache-aware
- [x] `--json` with paste-ready citation + full text + `image_path`
- [x] Paths via `SCRIPT_DIR`; offline; lazy model load for fast invocation
- [x] Probe set gitignored, not a committed fixture

## Decision log (non-normative)
- **CLI-first (not server-first).** The primary interface is the CLI, invoked on demand (by a
  Skill or a human); nothing stays resident, no server lifecycle, composes with `ocr.py`
  ergonomics. The MCP server (feature 018) is a thin optional wrapper, no longer the main path.
- **`image_path` escape hatch.** Bitmaps are deliberately not in the catalog (unsearchable
  bloat); the path lets an agent `Read` the original photo to verify garbled OCR, inspect
  figures/tables, or recover the handwriting the Markdown dropped (the photo is the only place
  it survives).
- **Path-resolution bug fixed (step 6).** Relative `--src/--db/--probes` originally resolved
  against the caller's cwd, so a Skill run from another project failed with "no catalog at
  out/rag.db"; now resolved against `SCRIPT_DIR`. (`get-page` human output bug also fixed: it
  must call `citation(row)`, not `row['citation']`.)
- **Eval result.** 5 content-grounded probes: dense MRR 0.77, lexical 0.90, hybrid 0.87 —
  headline is robustness: hybrid is the only mode with R@3=1.00 across both paraphrase and
  proper-noun probes (small set ⇒ MRR deltas noisy).
