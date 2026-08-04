# Feature 023 — Prompt-echo guard

**Status:** Delivered · **Origin:** a book-spine shot cached with the OCR instructions as its "text"
**Constitution:** III, V

## User Scenarios & Testing

### Primary user story
As a user whose corpus contains **near-textless shots** (a leather book spine, a blank
verso, a slip page), I do not want the VLM's failure mode — parroting its own instruction
prompt back as the transcription — to enter a book as page text, because that junk then
pollutes the emitted `book_*.md` and surfaces as absurd RAG hits ("DROP all handwriting:
underlines, circles…" is not a page of an 1811 sailing directory).

### Acceptance scenarios
1. **Given** a shot whose read contains a verbatim line of the instruction prompt,
   **when** the record is built after OCR, **then** its body text is stored empty — the
   shot still exists (type, rotation, grouping) but contributes no text.
2. **Given** a **legacy cache entry** already poisoned with echoed prompt text, **when**
   `batch` emits, **then** the echo is stripped in memory and the junk appears in no
   `book_*.md` — without rewriting or deleting the cache entry.
3. **Given** a page that legitimately discusses handwriting or transcription, **when**
   the guard runs, **then** it is NOT flagged: only a **long verbatim line** of the
   live prompt (≥ 40 chars, straight from `prompts.py`) counts as an echo, not topical
   overlap.
4. **Given** a future edit to `prompts.py`, **when** the guard runs, **then** it tracks
   the new wording automatically — the markers are derived from `PROMPT` at import time,
   never hand-copied strings.

### Edge cases
- Echo detection also blanks the derived `running_header` / `page_numbers` of a flagged
  record, since they were computed from the echoed text (e.g. a fake "Page 1").
- The in-memory heal is idempotent and re-applied every emit; caches stay immutable
  (Principle V) so `--force` re-OCR simply produces a clean record via the same guard.
- A read that merely *starts like* the prompt but diverges is still caught if any single
  prompt line ≥ 40 chars matches verbatim; shorter fragments never trigger.

## Requirements

### Functional
- **FR-001** A predicate MUST flag a read as prompt echo iff it contains, verbatim, at
  least one line of the current `prompts.PROMPT` of length ≥ 40 characters.
- **FR-002** At OCR time, a flagged read MUST be stored as empty body text, so new cache
  entries are never poisoned.
- **FR-003** At emit time, a flagged cached record MUST have its body text, running
  header, and page numbers blanked **in memory only** — cache entries are never modified
  or deleted (Principle V).
- **FR-004** The guard MUST derive its markers from `prompts.py` at import time; no
  duplicated prompt strings (single source of truth, Principle VIII).

### Key entities
- **Echo marker** — a line of `PROMPT` ≥ 40 chars; presence verbatim in a read defines echo.

## Review & Acceptance Checklist
- [x] Spine shot (`IMG_5448`) contributes no text to Author-Y's book
- [x] All legacy poisoned caches healed at emit without cache writes
- [x] Topical mentions of handwriting are not flagged
- [x] Markers derived from `prompts.PROMPT`, not copies

## Decision log (non-normative)
- **Why emit-time healing, not cache repair.** 7 of ~3000 cache entries in the delivering
  corpus were poisoned (`IMG_0580, IMG_0785, IMG_3315, IMG_4340, IMG_5448, IMG_7381,
  IMG_8753`). Deleting them forces re-OCR that deterministically reproduces the echo
  (verified on `IMG_5448`: re-read returned the identical parroted prompt), and hand-editing
  cache JSON breaks the "cache = checkpointed model output" contract. In-memory stripping
  fixes all seven at zero model cost and stays correct for any future poisoned entry.
- **Why ≥ 40-char verbatim lines.** Short fragments ("KEEP all printed text") could in
  principle collide with real content; the long instruction sentences cannot. All seven
  observed echoes contain multiple full prompt lines, so the threshold has margin on both
  sides.
- **Why not fix via prompt engineering.** The echo is a model failure on near-textless
  input, not a prompt defect; tuning the prompt against it risks the eval-tuned handwriting
  behaviour (feature 001) for a case whose correct output is simply "no text".
