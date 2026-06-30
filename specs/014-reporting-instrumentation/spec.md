# Feature 014 — Central report, incremental output, instrumentation

**Status:** Delivered · **Origin:** `library-ocr@afd9849` · **Old ref:** §8.1–§8.2
**Constitution:** III, V

## User Scenarios & Testing

### Primary user story
As a user running a long batch, I want a single human-readable report that fills in **live**
as the run proceeds — every book with its metadata and a linked list of its pages — plus a
flat per-image audit table and a machine-readable instrumentation log, so I can review
progress and spot failures without waiting for the whole run to finish.

### Acceptance scenarios
1. **Given** a batch in progress, **when** each image completes, **then** `out/report.md` is
   re-written: every book with structured metadata (title, author, publisher, year, ISBN,
   call no.), capture timespan + duration, GPS centroid ± radius, key-image provenance,
   cover/title shots, and a linked list of its page shots.
2. **Given** a run, **when** I look at `out/index.md`, **then** I see one row per image
   (filename · type · rotation · book · figure count · status `ok`/`empty`/`no-fields`).
3. **Given** a run, **when** I look at `out/instrument.jsonl`, **then** there is one JSON line
   per processed image with cost + quality signals (`elapsed_s`, `orient_passes`, `type`,
   `rotation`, `book`, `text_score`, `n_chars`, `page_numbers`, `quality`, …), append-only so
   it survives resumes; an avg-per-image summary is printed on stderr at the end.
4. **Given** progress, **when** an image is processed, **then** stderr shows one structured
   line — `[3/128] IMG_4360 → IMPRINT (book 4) 4.2s` (or `… (cached)` on resume).
5. **Given** a low-quality read, **when** the report is written, **then** that page is flagged
   inline (`⚠ low read quality (0.xx)`) so the report doubles as a review queue.

### Edge cases
- A wide capture span or GPS radius in the report flags a possibly mis-grouped book.
- A cover/imprint shot that produced no body MUST still appear (on the Cover/title-shots line),
  never looking "skipped".
- Per-image stdout MUST NOT look like a model "download"; the model loads once and is reused.

## Requirements

### Functional
- **FR-001** `write_report` MUST (re)write `out/report.md` after **each** image (grouping is
  pure and cheap), so `out/` is auditable mid-run.
- **FR-002** `report.md` MUST list, per book: structured biblio fields, capture span +
  duration, GPS centroid + radius, key-image provenance, cover/title shots, and linked page
  shots — and flag low-quality pages inline.
- **FR-003** `out/index.md` MUST carry one row per image with type, rotation, book, figure
  count, and status.
- **FR-004** `out/instrument.jsonl` MUST be append-only, one line per processed image, with
  cost + quality signals; an avg-per-image summary MUST print to stderr at the end.
- **FR-005** Progress MUST be one structured line per image on stderr, marking cached resumes.
- **FR-006** `batch` MUST set `HF_HUB_OFFLINE=1` so nothing is fetched per image (no
  download-looking noise).

### Key entities
- **Report** — `out/report.md`, the human entry point.
- **Index row** — per-image audit record in `out/index.md`.
- **Instrument line** — per-image JSON metrics in `out/instrument.jsonl`.

## Review & Acceptance Checklist
- [x] Report rewritten live; every input image traceable
- [x] Flat index + append-only JSONL + stderr progress
- [x] Low-quality pages flagged as a review queue

## Decision log (non-normative)
- **Why (§8.1–§8.2).** Before this, `out/` looked empty until the very end (per-book files
  need all pages), the per-image stdout looked like a "download" (HF-hub cache-checking, not a
  reload — the model is loaded once via `cmd_batch`'s `if model is None` lazy-load and passed
  down), and all timing/token telemetry was discarded. The report (rewritten per image), the
  structured one-line progress, `HF_HUB_OFFLINE=1`, and `instrument.jsonl` address all three.
- The report is the human entry point to a 128-image run; `index.md` stays as the flat
  per-image audit table.
