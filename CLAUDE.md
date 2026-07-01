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
  systems, or cloud deps.
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
  `IMG_host += IMG_x` moves a stray shot. `out/merge_candidates.json` is a ranked
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
- `IMG_3020` is the diagnostic page: a high score there means handwriting is being
  dropped. Pick the smallest model whose `IMG_3020` score is acceptable.
- **Vendored monkeypatch — revisit on every `mlx-vlm` bump.** `load_model()` applies
  `_patch_detokenizer_utf8()`, which works around a strict-UTF-8 decode bug in
  `mlx-vlm==0.6.3`'s `BPEStreamingDetokenizer.add_token` (a stray byte mid-word, e.g.
  `controvert`, would otherwise crash the whole page/batch). When bumping `mlx-vlm`: check
  if upstream fixed `add_token` and **delete the patch if so**; regression-test with
  `python ocr.py run in/IMG_5906.jpeg` (must produce text, not raise). Full rationale and
  revisit checklist in `specs/015-resumability-resilience/`.

## Local RAG (`rag.py`)

A second, separate tool (deps in `requirements-rag.txt`, not `requirements.txt`):
offline hybrid retrieval over the `out/book_*.md` the OCR tool produces, so Claude
can look up citations without loading whole books. See `specs/016-rag-retrieval-engine/`
(engine), `specs/017-rag-cli/` (CLI), and `specs/018-rag-mcp-integration/` (MCP/Skill).

- **Run** (always `source .venv/bin/activate` first; offline with `HF_HUB_OFFLINE=1`):
  - `python rag.py index` — chunk `out/book_*.md` → embed → build `out/rag.db`.
    **This is also how you re-index after new OCR**: it's cache-aware and resumable,
    re-embedding only new/changed pages (`--force` redoes all; `--no-embed` chunks only).
  - `python rag.py search "<q>" [-k N] [--mode hybrid|dense|lexical] [--book S] [--json]`
  - `python rag.py get-page IMG_x [--neighbors N] [--json]`
  - `python rag.py serve` — optional MCP stdio server (tools `search_library`/`get_page`).
- **Catalog = source of truth.** `out/rag.db` (SQLite): chunks + `float32` BLOB vectors
  + an FTS5 lexical table. No native vector type — similarity is a numpy matmul. Don't
  add `sqlite-vec`/Chroma/FAISS to the default path (faiss/duckdb are deferred opt-ins).
- **`image_path` = escape hatch to the original page.** Every `search`/`get-page` JSON
  result carries `image_path`: the absolute path to the source photo in `in/` (or `null`).
  Bitmaps are deliberately **not** in the catalog (unsearchable, just bloat) — the path
  lets an agent `Read` the original to verify garbled OCR, inspect figures/tables, or
  recover the handwriting the Markdown dropped (the photo is the only place it survives).
  Resolved via `_source_image_path()` against `SCRIPT_DIR/in`; `test/` fixtures are not
  exposed. The stored `image` label already includes the `.jpeg` suffix, and some labels
  are section headings, not filenames — those resolve to `null`.
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
- **Eval** is stdlib-only (`rag.py eval`, recall@k/MRR) against `rag_probes.json`
  (gitignored throwaway, like `experiments/`). Don't commit the probe set as a fixture.

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
- **Always activate the venv** before running anything (`source .venv/bin/activate`)
  so the already-downloaded, offline model is used. Run inference with
  `HF_HUB_OFFLINE=1`; the `dots.mocr-4bit` model is already cached locally (~3.3 GB) —
  never re-download it.
