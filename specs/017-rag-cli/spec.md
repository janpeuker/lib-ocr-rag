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
- **FR-009** `--book S` MUST scope by what the user *remembers* about the source: every
  whitespace-separated word of `S` MUST appear (case-insensitively, as a substring) in the
  book's `book_file`, `book_title` **or** `author` — so `ingold`, `tim ingold` and
  `making anthropology` all reach the same book without knowing its catalog number.
- **FR-010** The scope MUST be applied *inside* each retrieval channel (the FTS `MATCH` and the
  vector matrix), never as a filter over their output, so a scoped search is not limited to
  what the book won in the library-wide top-`CANDIDATES`.
- **FR-011** When a scope resolves to exactly one book, the `--per-book` cap MUST NOT apply.
- **FR-012** A scope matching **no** book MUST say so distinctly from "no results", and
  `rag.py books [--book S]` MUST list a scope's books, so a scope can be checked before use.
- **FR-013** The distinction in FR-012 MUST reach the *machine* surfaces, not only the human
  one: `search --json` MUST report a scope miss on **stderr** and exit `2` (`EXIT_SCOPE_MISS`)
  while stdout stays a plain JSON list, and the MCP `search_library` tool MUST raise a tool
  error carrying the same wording rather than returning `[]`. One helper
  (`scope_miss_message()`) MUST provide that wording to all three surfaces.

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
- **`--book` matched the filename only (fixed).** The scope ran `book_file LIKE '%S%'`, and a
  `book_file` is `book_86_making-anthropology-…md` — the author never appears in it. So
  `--book ingold` returned **nothing** while `--book book_86` returned the same book's pages:
  the one scoping operator only worked if you already knew the number you were trying to avoid
  looking up. Matching file + title + author, word-by-word, is what users actually mean by "in
  the Ingold book". Deliberately loose (substring, not token or fuzzy-distance): a scope is a
  *filter you can inspect* — `books --book X` shows exactly what it covers — so over-matching
  costs a visible extra book, while under-matching silently returns nothing. Query-side
  operators (`author:X`, `+term`, `"phrase"`) were considered alongside and **rejected**: the
  same intent is already expressible with a flag, and a second grammar inside the query string
  would have to be stripped back out before the dense channel and the reranker ever see it.
- **Scope before ranking, not after.** Post-filtering the channel outputs capped a scoped
  search at whatever the book won in the global top-200 — for a book that is not the corpus's
  loudest voice on a term, a fraction of its real hits, or none. Pushing the scope into the FTS
  `MATCH` (via the UNINDEXED `id` column) and into the vector matrix costs nothing measurable at
  this corpus size and makes the book's pages compete only with each other. Visible effect:
  `--book ingold "correspondence"` went from 3 hits (per-book cap) to the book's full ranking.
- **Eval result.** 5 content-grounded probes: dense MRR 0.77, lexical 0.90, hybrid 0.87 —
  headline is robustness: hybrid is the only mode with R@3=1.00 across both paraphrase and
  proper-noun probes (small set ⇒ MRR deltas noisy).
- **A scope miss is a diagnosis, not an empty result (FR-013).** `--json` returned a bare `[]`
  both when the scope matched no book *and* when it matched a book that had nothing to say —
  and it returned before the human path's "no book matches" line, so the Skill and MCP (the
  callers that always pass `--json`) could not tell the two apart. Symptom in the wild:
  `search "pirate" --book "trocki"` read as "the library has nothing on pirates in Trocki"
  when the real cause was a scope that resolved to zero books. Signalled out-of-band —
  stderr + exit `2` — rather than by reshaping stdout: an object-on-miss (or a uniform
  `{"results": …}` envelope) would make every consumer branch on shape for a case that is
  already fully described by a channel agents can read. MCP has neither stderr nor an exit
  code, so it raises instead; the wording is shared via `scope_miss_message()` so the three
  surfaces can't drift (same motive as FR-008).
