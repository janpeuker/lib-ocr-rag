# Feature 015 — Resumability, cache contract & crash resilience

**Status:** Delivered · **Origin:** `library-ocr@60a0e59`, `199e680`, `3f1c758` · **Old ref:** §4, §13
**Constitution:** I, II, V

## User Scenarios & Testing

### Primary user story
As a user running a multi-hour batch that may be killed by exhausted credits, sleep, a corrupt
file, or a model quirk, I want the run to resume exactly where it stopped, never re-doing
finished work, and never letting one bad page sink the whole run.

### Acceptance scenarios
1. **Given** a batch interrupted at image 90/128, **when** I re-run the same command, **then**
   it resumes at 91 — images with a complete cache entry are skipped.
2. **Given** a cached image, **when** the `model` or `prompt_version` differs from the entry,
   **then** that image is recomputed; otherwise it is reused.
3. **Given** the in-flight image, **when** a crash occurs, **then** at most that one image is
   lost — the cache is flushed after each image, processed in sorted filename order.
4. **Given** a page that makes the model emit a stray non-UTF-8 byte mid-word (e.g.
   `b' cont\x98rovert'`), **when** OCR runs, **then** the detokenizer monkeypatch drops the bad
   byte and recovers the ASCII text instead of crashing the page (and the batch).
5. **Given** any *other* unrecoverable page (truly corrupt file, OOM), **when** batch runs,
   **then** it is logged to `out/failures.jsonl` and skipped, so an overnight run continues.
6. **Given** an unattended overnight run, **when** I use `run_overnight.sh`, **then** it wraps
   `batch` then `rag.py index` under `caffeinate`, retries each stage (each retry resumes from
   cache), and guards against concurrent runs; GPU memory is bounded.

### Edge cases
- The grouping/emit pass is pure and cheap — always recomputed from the cache, never
  checkpointed.
- The detokenizer patch is keyed to `mlx-vlm==0.6.3` internals and MUST be revisited on every
  `mlx-vlm` bump (delete if upstream fixes `add_token`; the `try/except` resilience stays
  regardless).

## Requirements

### Functional
- **FR-001** Each image's result MUST be a disk checkpoint at `out/cache/<img>.json`
  (`type, rotation, role, raw_md/ocr_text, headers, page_numbers, exif, quality, figures,
  model, prompt_version, …`); a complete entry MUST be skipped on rerun (Principle V).
- **FR-002** `load_cache` MUST invalidate an entry when `model` or `prompt_version` differs.
- **FR-003** `batch` MUST be resume-by-default with `--force` to recompute; images MUST be
  processed in sorted filename order and the cache flushed after each one.
- **FR-004** Grouping (C) and emit (D) MUST be pure and recomputed every run — never
  checkpointed.
- **FR-005** `load_model()` MUST apply the idempotent `_patch_detokenizer_utf8()` monkeypatch
  (flush with `errors="ignore"`, matching the library's own `finalize()`), recovering text
  from a stray byte. It MUST guard on the class attr and silently no-op if the internals change.
- **FR-006** `cmd_batch` MUST wrap each `process_image` in try/except → `out/failures.jsonl`
  and continue, as engine-agnostic resilience independent of the patch.
- **FR-007** `run_overnight.sh` MUST run `batch` → `rag.py index` under `caffeinate`, retrying
  each stage, guarding concurrent runs, and bounding GPU memory.

### Key entities
- **Cache record** — `out/cache/<img>.json`, the unit of resumability.
- **Failure line** — `out/failures.jsonl` entry for a skipped page.

## Review & Acceptance Checklist
- [x] Resume-by-default; per-image flush; model/prompt-version invalidation
- [x] Detokenizer patch recovers stray bytes; flagged for revisit on dep bump
- [x] Per-page try/except → failures.jsonl; overnight wrapper retries from cache

## Decision log (non-normative)
- **Detokenizer bug (§13).** During an 873-image batch, `IMG_5906` crashed the whole run with
  `UnicodeDecodeError: 'utf-8' codec can't decode byte 0x98` — the model generated *"controvert"*
  and emitted a stray byte. Root cause: `mlx-vlm==0.6.3` `BPEStreamingDetokenizer.add_token`
  flushes with a *strict* `.decode("utf-8")` while the same class's `finalize()` already uses
  `errors="ignore"` — the streaming path was simply missed. We only care about printed
  ASCII/Latin text, so dropping an undecodable byte is lossless; `IMG_5906` then OCR'd to 6035
  chars including "controvert". **Regression check on any `mlx-vlm` bump:**
  `python ocr.py run in/IMG_5906.jpeg` must produce text, not raise.
- The two layers are deliberate: the patch fixes this *class* of crash; the `cmd_batch`
  try/except is a backstop for any other unrecoverable page (kept regardless of the patch).
- GPU-memory bounding + concurrent-run guard were added (`199e680`) after memory pressure
  (swap) proved to be the batch's real drag.
