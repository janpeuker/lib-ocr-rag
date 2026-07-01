# Feature 002 — Batch a folder, grouped per book

**Status:** Delivered · **Origin:** `library-ocr@eb26153` · **Old ref:** §1–§7
**Constitution:** I, II, III, V, VII

## User Scenarios & Testing

### Primary user story
As a researcher with a folder of ~hundreds of heterogeneous iPhone photos (body pages,
two-page spreads, rotated shots, cover/spine cataloging shots, figure/map pages —
several books mixed together, taken across multiple sessions), I want one command that
turns the whole folder into clean Markdown **grouped per book**, without me labelling
which photo belongs to which book.

### Acceptance scenarios
1. **Given** photos in `in/`, **when** I run `python ocr.py batch` (defaults `in/` → `out/`),
   **then** each image is preprocessed and OCR'd once, and the results are emitted as
   per-book documents — with no manual per-photo labelling.
2. **Given** the folder mixes pages, spreads, covers, and figures, **when** batch runs,
   **then** each image is routed by its detected role (cover/imprint → metadata;
   page/spread → body) and grouped into the correct book.
3. **Given** a 12 MP photo, **when** it is processed, **then** it is downscaled (long
   edge capped) before inference to bound VLM cost/memory; preprocessing uses Pillow only
   and never writes into `in/`.
4. **Given** the same `batch` command is re-run, **when** nothing changed, **then** it
   resumes from cache and re-OCRs nothing (see feature 010).
5. **Given** a completed run, **when** I inspect `out/`, **then** I find per-book
   `book_NN_<slug>.md` + `.txt`, plus `index.md` (one row per image: type, rotation, book,
   status) for auditing.

### Edge cases
- A blurry/empty page is recorded in `index.md` with an `empty`/`no-fields` status rather
  than silently dropped.
- An image OCR'd as `OTHER` is recorded in the index but contributes no body output.
- The default `dots.mocr` ignores "classify this" instructions, so routing is inferred
  from the produced **text**, not from a classify prompt (see feature 004).

## Requirements

### Functional
- **FR-001** The tool MUST expose `batch [DIR] [--model M] [--out DIR] [--force]` with
  defaults `DIR=in/`, `out=out/`, mirroring `run`'s load-model-once-then-loop.
- **FR-002** Each image MUST be processed in a per-image flow: EXIF read (capture
  time + GPS) → orientation-correct OCR (feature 003) → role detection (feature 004) →
  capture of grouping signals (running header, printed page numbers) and, for cover/
  imprint, bibliographic metadata (feature 007).
- **FR-003** Preprocessing MUST use **Pillow only** (no OpenCV/new deps), MUST apply EXIF
  transpose + the detected rotation + a long-edge downscale, and MUST NOT modify files in
  `in/` (write prepped copies to a temp dir, auto-cleaned).
- **FR-004** Grouping into books MUST be a **pure, non-inference pass** over the cached
  records (feature 006), so it can be recomputed cheaply on every run.
- **FR-005** The model MUST be loaded once and reused across the whole batch (no per-image
  reload); a single `--model` serves all passes (Principle IV).
- **FR-006** `batch` MUST set `HF_HUB_OFFLINE=1` itself so no network is used per image.
- **FR-007** Output MUST include, per book, `book_NN_<slug>.md` (YAML frontmatter +
  `## <source img>` body sections) and `book_NN_<slug>.txt` (flattened), plus a flat
  `out/index.md` audit table (filename · type · rotation · book · figure count · status).
- **FR-008** `run` and `eval` (feature 001) MUST keep working unchanged.

### Key entities
- **Image record** (cached) — `{ image, type, rotation, role, raw_md/ocr_text,
  running_header/page_header, page_numbers, exif:{dt,gps}, figures, quality, model,
  prompt_version, … }`. The unit of resumability (feature 010).
- **Book** — an ordered set of image records sharing one title identity (feature 006),
  carrying optional cover/imprint metadata.

## Review & Acceptance Checklist
- [x] No manual per-photo labelling required
- [x] Two inference passes (orient+read, route) + one pure grouping pass + emit
- [x] Pillow-only preprocessing; `in/` never mutated
- [x] Resume-by-default; `--force` recomputes
- [x] `run`/`eval` unaffected

## Decision log (non-normative)
- **Why a batch redesign.** The original tool assumed clean, pre-cropped single pages.
  Real input is 128+ heterogeneous phone photos across four sessions (Dec 2025 → Jan 2026):
  body pages, landscape spreads (with inserted blanks, a finger, bleed-through), imprint
  identity pages, cover/spine cataloging shots, rotated captures (rotation in the pixels,
  EXIF already orient=1), figure/map pages, and blurry slips.
- **Confirmed user requirements:** cover/imprint → bibliographic metadata not body OCR;
  keep printed library slips/stamps, drop only handwriting; output grouped per book
  (grouping inferred, not labelled); straighten rotated photos; flag figures/maps with a
  caption placeholder rather than extracting; keep GPS as a (soft) grouping signal.
- **Architecture:** per image — **(A)** classify+orient → **(B)** route to metadata or
  body OCR — then **(C)** pure grouping over all records, then **(D)** emit per-book files.
  Reuses `load_model`, `ocr_image`, `md_to_text`, `normalize`/`similarity`.
- `MAX_EDGE` lowered 2200 → 1600 (`d9e8553`) to cut VLM prefill cost; further tuned in
  feature 003.
