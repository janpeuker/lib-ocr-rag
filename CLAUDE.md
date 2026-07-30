# CLAUDE.md

Guidance for working in this repo. See `specs/` for the full requirements + rationale
(spec-kit layout; `specs/README.md` is the index, `.specify/memory/constitution.md`
the governing principles) and `README.md` for usage.

## What this is

A bare-bones, single-file local OCR tool: book-page photos → structured Markdown +
plain text, using a prompt-steerable document VLM that keeps **only printed text**
and drops handwritten annotations.

## Hard constraints (do not violate)

- **Python 3** only.
- **Apple Silicon MPS/MLX** — runs on the Mac GPU via mlx-vlm. **No CUDA.**
- **100% offline, no APIs** — no cloud SDKs, no network at inference time. Models
  download once from HuggingFace, then run with `HF_HUB_OFFLINE=1`.
- **Bare-bones, minimal dependencies.** Engine is `mlx-vlm`; `torch`/`torchvision`
  are present only because the transformers Qwen2-VL processor imports them
  (Apple-Silicon CPU/MPS wheels, still no CUDA). Don't add web frameworks, config
  systems, or cloud deps. Dependencies are declared in `pyproject.toml` and managed
  with `uv` (`uv sync` builds `.venv` from `uv.lock`; `uv lock --upgrade` to bump).
- Target machine: Mac M3, 16 GB unified memory.

## Conventions

- Default model is `DEFAULT_MODEL` in `ocr.py` (`mlx-community/dots.mocr-4bit`).
  Switch models via `--model`, never hardcode a second model path.
- The instruction prompt lives in one place: `prompts.py`. Tune it against
  `python ocr.py eval`, not by editing inference code.
- **Do not add new test data** — reuse `test/*.jpeg` + `*_text.txt` as eval fixtures.
- Eval scoring uses stdlib `difflib` only; keep it dependency-free.
- **Merging duplicate books** (a title read across two non-adjacent sittings): the
  grouper only auto-rejoins on an exact ISBN/call or a body-less cover's title twin —
  real duplicates here are often *title-invisible*, so the reliable path is the
  optional `in/merges.txt` allow-list (read by `ocr.py batch`, like the `*.ris` hint;
  absent ⇒ no-op, never touches the cache). `IMG_a + IMG_b` folds whole books;
  `IMG_host += IMG_x` moves a stray shot; `! IMG_x` forces a new book to *start* at that
  shot — the inverse split, for a coverless book opening mid-session with no automatic
  boundary signal (spec 024).
  **`+` is not "add this shot" — it is "add whatever book this shot currently belongs to".**
  Name a shot that turns out to sit in a *different* book and the operator silently imports
  that entire book, and the damage is invisible: the result looks like one large,
  correctly-titled book, with coverage at 100 % and `doctor` green (a `+ IMG_4396` intended
  to reach Mignolo folded in all 73 shots of *Tribal Communities in the Malay World* and went
  unnoticed for a month — spec 013 decision log). **Before writing a `+` line, verify which
  book each named shot is in today** — don't infer it from the shot-number range, which is
  exactly the assumption that fails. When the intent is "attach these specific shots", use
  `+=`; it cannot fold a foreign book. Reserve `+` for joining two *readings of the same
  book*. Cheap audit: a book whose meta shots' `cover_title`s match two different RIS records
  is over-merged (found two real defects across 133 books; spec 029 FR-010).
  **Splitting an over-merge isn't done until the freed book is in Zotero** — without a record
  it fuzzy-matches whatever is nearest and you trade one wrong citation for another
  (*The Natures of Maps* → Harley's *The New Nature of Maps* at 0.878), and a `titles.txt`
  override can't rescue it. Prefer `! IMG_x` over `+=` when the shot you're moving *opened*
  the book: `+=` is applied after segmentation, so it leaves the book's `identity` naming the
  departed shot's book, which then wins a 0.99 `match_ris` tie on RIS file order (spec 013
  FR-009/010).
  `out/merge_candidates.json` is a ranked
  discovery aid for populating it. Re-run `python rag.py index` after to refresh the
  catalog. Rationale + the validated fixture in `specs/013-duplicate-merge/`.
- **Title overrides** (`in/titles.txt`, optional, same no-op-if-absent contract as
  `merges.txt`/`*.ris`): `IMG_xxxx = Some Title` forces a book's title when the OCR
  can't derive it — a title buried in a title-page *list* (a series page) or lost to a
  runaway read, AND not recoverable from the RIS. Last resort; prefer fixing the read or
  the bibliography first. The RIS + title-override hints are in
  `specs/010-bibliography-title-hints/`; base cover-title resolution in
  `specs/007-bibliographic-metadata/`.
- **Cover titles come from the largest font, not reading order**
  (`specs/008-cover-title-largest-font/`). A COVER shot
  gets one extra `dots.mocr` layout+text pass (`COVER_TITLE_PROMPT`); `_pick_cover_title`
  takes the tallest `Title` bbox so a book isn't named after the publisher/author that
  happens to OCR first. The result is cached as `cover_title` and **backfilled** into
  older (pre-largest-font) caches on the next `batch` (one layout pass per cover, resumable;
  `--no-cover-backfill` keeps the fast emit-only path). No `Title` box (a stylized title
  the model folds into the cover image) ⇒ `cover_title` is `""` and the old reading-order
  text heuristic (`_cover_title`) still runs — so don't "fix" the empty case by grabbing
  the next-largest text box (that just resurfaces the author). Tune the selection against
  `batch` cover output, never by editing a second model path.
- **Content dedup runs automatically at `batch` discovery** (`dedup_by_content`,
  `specs/020-content-dedup/`): byte-identical photos (a camera-roll copy like
  `IMG_5097 (1).jpeg` == `IMG_5097.jpeg`) are folded to one canonical file (the clean name)
  before the cache loop, so a duplicate is neither re-OCR'd nor a double hit in RAG. Identity
  is `sha256` of the bytes, **never** the filename — a name-twin with *different* bytes
  (`IMG_4867`: a roll that flipped a `(1)` onto a different shot) is kept and processed. The
  fold map is written to `out/dedup.json` and shown in `index.md`; dedup touches no cache. Not a
  hint file — there's nothing to maintain. Distinct from the `merges.txt` book-merge (§13),
  which folds *different photos of the same book*.
- **Cover/imprint shots can carry real page text** (spec 027). A COVER/IMPRINT shot is
  `role: meta` and read for bibliographic fields, but a title-page verso or imprint page
  often holds prose too. `write_book` emits it as a normal page section when it clears
  `META_BODY_MIN_CHARS` (re-applying the echo/runaway guards, which only run on the body
  path). Emit-time only — it never touches the cache, so a re-`batch` recovers it with no
  re-OCR.
- `IMG_3020` is the diagnostic page: a high score there means handwriting is being
  dropped. Pick the smallest model whose `IMG_3020` score is acceptable.
- **A sparse, colourful mid-book page (a plate caption, a part-title divider) can look
  exactly like a cover and false-split a book** (`detect_type`, spec 024 decision log).
  Guarded by a real, structured folio: a `### Page N` heading with `N >= FOLIO_MIN` (4)
  means dots.mocr read an actual page number off the shot, so it's never classified
  COVER regardless of how short/colourful the text is. Deliberately narrower than "any
  digit near the text" — a bare short digit line can be a publication year or shelf code
  on a genuine cover, and dots.mocr defaults a real sparse cover to a phantom "Page 1"
  (sometimes "Page 1"+"Page 2") even with nothing printed; both were measured false
  positives during tuning. Only guards *future* batches at OCR time — an already-cached
  false split needs its `type`/`role` fields reclassified from the stored `ocr_text`
  (no re-OCR: `detect_type` is a pure function of text already on disk) before the next
  `batch` picks up the new grouping; `merges.txt`/`titles.txt` remain the catch-all for
  cases the folio guard can't foresee.
- **Prefer an ISBN match to a title override when a book won't match Zotero.**
  `match_ris` tries an exact ISBN match (`_book_isbn(book)` vs. each RIS record's `SN`)
  before falling back to fuzzy title comparison. A `titles.txt` title override is just
  one more candidate fed into that *same* fuzzy matcher, so it inherits the same
  false-match risk from a generic/shared main title — worth double-checking the
  bibliography (full-text search, not a narrow grep) for a matching ISBN before writing
  a title override; see the spec 007 decision log for a case that looked unmatched but
  wasn't.
- **Vendored monkeypatch — revisit on every `mlx-vlm` bump.** `load_model()` applies
  `_patch_detokenizer_utf8()`, which works around a strict-UTF-8 decode bug in
  `mlx-vlm`'s (0.6.x, still unfixed as of 0.6.5) `BPEStreamingDetokenizer.add_token` (a stray byte mid-word, e.g.
  `controvert`, would otherwise crash the whole page/batch). When bumping `mlx-vlm`: check
  if upstream fixed `add_token` and **delete the patch if so**; regression-test with
  `python ocr.py run in/IMG_5906.jpeg` (must produce text, not raise). Full rationale and
  revisit checklist in `specs/015-resumability-resilience/`.

## Local RAG (`rag.py`)

A second, separate tool (its deps — `sentence-transformers`, `mcp` — ship in the
same `pyproject.toml`/`uv sync` as the OCR tool):
offline hybrid retrieval over the `out/book_*.md` the OCR tool produces, so Claude
can look up citations without loading whole books. See `specs/016-rag-retrieval-engine/`
(engine), `specs/017-rag-cli/` (CLI), and `specs/018-rag-mcp-integration/` (MCP/Skill).

- **Run** (always `source .venv/bin/activate` first; offline with `HF_HUB_OFFLINE=1`):
  - `python rag.py index` — chunk `out/book_*.md` → embed → build `out/rag.db`.
    **This is also how you re-index after new OCR**: it's cache-aware and resumable,
    re-embedding only new/changed pages (`--force` redoes all; `--no-embed` chunks only).
  - `python rag.py search "<q>" [-k N] [--mode hybrid|dense|lexical] [--book S]
    [--per-book N] [--no-rerank] [--json]`
  - `python rag.py get-page IMG_x [--neighbors N] [--json]`
  - `python rag.py books [--book S] [--json]` — what the catalog holds (and what a
    `--book` scope covers)
  - `python rag.py serve` — optional MCP stdio server (tools `search_library`/`get_page`).
- **Catalog = source of truth.** `out/rag.db` (SQLite): chunks + a `windows` table
  holding the `float32` BLOB vectors + an FTS5 lexical table. No native vector type —
  similarity is a numpy matmul. Don't add `sqlite-vec`/Chroma/FAISS to the default path
  (faiss/duckdb are deferred opt-ins).
- **Chunk = citation unit, window = retrieval unit** (spec 028). A page chunk is what
  gets returned and cited; it is additionally split into ~600-char `windows` which carry
  the vectors, and a page scores as the **max** over its windows (never the average —
  that re-dilutes exactly what windows fix). Don't move vectors back onto `chunks`: a
  ~370-token page averaged into one 384-d vector buries the answering sentence, and 8 %
  of chunks silently truncated at bge-small's 512-token cap.
- **Four channels, then rerank, then shape** (spec 028). `hybrid` fuses dense + BM25-OR
  + an **AND/coverage** channel (all query terms) + a **fuzzy** channel (terms expanded
  against the FTS5 vocabulary via `fts5vocab` + stdlib `difflib`, so an OCR'd "Wilhem Braun"
  answers "Wilhelm Braun") through RRF; then `DEFAULT_RERANK_MODEL`
  (`cross-encoder/ms-marco-MiniLM-L-12-v2`) re-scores **at window grain** — a
  cross-encoder truncates just like a bi-encoder, so never feed it whole pages. Then
  results are shaped: one per page, `--per-book` cap (default 3), near-duplicates dropped
  by token **containment** (not Jaccard — duplicate-book twins split at different lengths
  and measured only 0.78). A bigger embedding model is *not* the fix for a relational
  query and was measured and deferred; see the 028 decision log before reaching for one.
- **`--book` scopes by author/title/file, loosely** (spec 017 FR-009…012). Every word of the
  scope must appear as a case-insensitive substring of `book_file || book_title || author`, so
  `--book ingold` / `"tim ingold"` / `"making anthropology"` all reach the same book — this is
  the operator for "I already know the source". It is pushed *into* each channel (FTS `MATCH` +
  the vector matrix), never applied to their output, and the `--per-book` cap is dropped when
  the scope resolves to one book. Don't add a query-side grammar (`author:X`, `+term`) on top —
  that was considered and rejected in 017's decision log; a second syntax inside the query
  would have to be stripped back out before the dense channel and the reranker see it.
  A scope matching **no** book is reported out-of-band, never by reshaping stdout:
  `search --json` still prints a plain `[]` but writes `scope_miss_message()` to stderr and
  exits `2` (`EXIT_SCOPE_MISS`); MCP `search_library` raises instead of returning `[]`. So an
  empty JSON result with exit 2 means the scope is wrong (misremembered, or the book isn't
  indexed yet) — not that the library is silent on the query (spec 017 FR-013).
- **A missing result may be a coverage hole, not a ranking bug.** Before tuning
  retrieval, check the page is in the catalog at all — `out/coverage.json` (written by
  every `batch`) says, for every shot, either that its text reached a book or a *named
  reason* why not. Anything `UNEXPLAINED` is a hole; cover/imprint shots hid ~295 k chars
  of real prose until spec 027.
- **Quality guards run themselves (spec 029).** `rag.py index` ends with a warn-only
  `doctor` pass; `./library.sh doctor` runs it on demand. It re-checks what corpus growth
  invalidates: uncitable labels, books with no chunks, unembedded/model-stale vectors,
  truncation risk, `CANDIDATES` vs corpus size, probe validity, eval staleness, OCR↔RAG
  page reconciliation, duplicate passages. All checks are **relative to the corpus
  present** — never golden numbers, since `in/`/`out/` are gitignored and another user's
  library shares nothing with this one. When a check fires, fix the cause or re-tune the
  constant and update `CANDIDATES_TUNED_AT`; don't widen the threshold to silence it.
- **Bump `WINDOW_VERSION` whenever `split_windows` changes.** A chunk's `content_sha`
  covers the page text only, so without the bump a window-logic fix leaves the old
  windows in place forever (the same role `PROMPT_VERSION` plays for the OCR cache).
- **`rag.py eval` records history.** Each run appends corpus size + per-channel scores to
  `out/eval_history.jsonl` and flags a reranked-MRR drop since the last comparable run —
  decay shows up as drift, which a single run cannot show. Bootstrap a probe set for a
  fresh corpus with `rag.py probes --scaffold` (then paraphrase the generated sentences;
  verbatim ones flatter the lexical channel).
- **`image_path` = escape hatch to the original page.** Every `search`/`get-page` JSON
  result carries `image_path`: the absolute path to the source photo in `in/` (or `null`).
  Bitmaps are deliberately **not** in the catalog (unsearchable, just bloat) — the path
  lets an agent `Read` the original to verify garbled OCR, inspect figures/tables, or
  recover the handwriting the Markdown dropped (the photo is the only place it survives).
  Resolved via `_source_image_path()` against `SCRIPT_DIR/in`; `test/` fixtures are not
  exposed. The stored `image` label always includes the `.jpeg` suffix. **Every chunk must
  resolve to a real page file** — a label that isn't a filename is a bug, not a quirk:
  `parse_book` used to accept any `## ` line as an image heading, so a page's own Markdown
  subheading opened a bogus section (123 chunks under 71 invented labels, all with broken
  citations). `_IMAGE_HEADING` now requires an image extension, and `rag.py doctor` checks
  it. This was documented here as expected behaviour for months — don't re-rationalize it.
- **Default embed model** is `DEFAULT_EMBED_MODEL` in `rag.py` (`BAAI/bge-small-en-v1.5`);
  switch via `--embed-model`, never hardcode a second. `bge-small` is cached locally —
  don't re-download. Passages are embedded raw; the BGE query prefix is applied in `search`.
- **Paths resolve against the install dir** (`SCRIPT_DIR`), not the caller's cwd, so the
  Skill/MCP work from any other project's directory.
- **Skill/MCP are NOT wired into this repo.** They live in `integration/` as a portable
  bundle for *other* Claude projects to import (`integration/README.md` has steps). To
  "create the skill" in another project: copy `integration/skill/library-search/` into
  that project's `.claude/skills/`. CLI Skill is primary; MCP optional. The bundle ships
  a `/ABSOLUTE/PATH/TO/lib-ocr-rag` **placeholder** (never a real local path — that must
  not leak into the public repo); substitute the real clone path on install, per
  `integration/README.md`.
- **Eval** is stdlib-only (`rag.py eval`, recall@k/MRR per channel) against
  `rag_probes.json` (gitignored throwaway, like `experiments/`). Don't commit the probe
  set as a fixture. **Prefer `image` matchers over `book`** — book numbers renumber as
  the library grows, and three stale `book_NN` probes read as total retrieval failure
  until corrected.

## Working preferences

- **Record durable guidance here in `CLAUDE.md`, not in the memory system.**
- Persist durable design as **spec-kit feature specs** under `specs/<NNN>-<slug>/spec.md`
  (User Scenarios + numbered `FR-###` requirements + a non-normative Decision log), with the
  governing principles in `.specify/memory/constitution.md`. Add a new story the spec-kit way
  — write the requirements before the code — and index it in `specs/README.md`. Don't keep a
  single chronological plan doc; record superseded designs in the relevant spec's Decision log,
  or as a card in `specs/experiments/` for rejected/deferred work, rather than deleting them.
- Plans must be split into discrete, independently-testable steps, and long batch
  pipelines must be **resumable** — checkpoint expensive work to disk (per-item cache,
  resume-by-default, `--force` to recompute) so a session killed by exhausted usage
  credits picks up where it left off.
- **Main-only development** — work directly on `main`, do not create feature branches.
- **Always ask before committing.** Never `git commit` (or push) without explicit
  per-commit approval.
- **Always activate the venv** before running anything (`source .venv/bin/activate`;
  it's uv-managed — recreate/repair with `uv sync`, never `pip install`)
  so the already-downloaded, offline model is used — or use `./library.sh`, which does
  venv + offline + dispatch in one (`update` = batch + index; `search`/`page`; `ocr`/`rag`
  passthrough; spec 021). Both tools set `HF_HUB_OFFLINE=1` themselves at import time
  (`setdefault` — an explicit `HF_HUB_OFFLINE=0` wins, needed only to download a *new*
  model); the `dots.mocr-4bit` model is already cached locally (~3.3 GB) — never
  re-download it.
