# Feature 008 — Cover title by largest font (layout dual-pass)

**Status:** Delivered · **Origin:** `lib-ocr-rag@441d58a` (parent `library-ocr` §16) · **Old ref:** §16
**Constitution:** III, IV, V, VI
**Related:** base title resolution → [007](../007-bibliographic-metadata/spec.md)

## User Scenarios & Testing

### Primary user story
As a user cataloging books, I want a cover named after the **book**, not after the publisher
or author that happens to OCR first. On a cover, the title is the **largest type**, not the
first line in reading order — so the tool should pick the title by font size.

### Acceptance scenarios
1. **Given** a COVER shot, **when** it is processed, **then** one extra layout pass
   (`COVER_TITLE_PROMPT`, layout **with** text) is run, and `_pick_cover_title` takes the
   tallest `Title` bbox (height = font-size proxy) as the title.
2. **Given** a wrapped title set in one size (`Tribal Communities` / `in the Malay World`),
   **when** picked, **then** boxes within `_COVER_TITLE_FONT_RATIO` (0.55) of the tallest are
   joined into the whole title, and a front-cover/spine repeat is de-duped.
3. **Given** a title with a subtitle set smaller (`Singapore` + `Wealth, Power and the Culture
   of Control`), **when** picked, **then** a *hugging* subtitle (top within
   `_COVER_SUBTITLE_GAP_RATIO` of its own height below the title) is absorbed, while an author
   byline set farther down (`_looks_like_byline`) is not.
4. **Given** a cover with no `Title` box at all, **when** picked, **then** `cover_title` is
   `""` and the feature 007 reading-order heuristic still runs (no regression).
5. **Given** a pre-§16 cache lacking `cover_title`, **when** `batch` runs, **then** it
   **backfills** one layout pass per cover (`backfill_cover_title`), resumable
   (the field — even `""` — is the checkpoint); `--no-cover-backfill` keeps the fast path.

### Edge cases
- The layout pass is non-deterministic on MPS for a few busy/back covers (subtitle box
  flickers, scrambled JSON) — those are pinned with the feature 010 hints, not by the heuristic.
- A title page mis-classified as body PAGE does not get the font heuristic (it only fires on
  COVER); such residual cases use the feature 010 title override.

## Requirements

### Functional
- **FR-001** A COVER shot MUST get one extra layout pass using `COVER_TITLE_PROMPT` (layout
  **with** text), reusing the single loaded model (Principle IV); its text MUST NEVER enter
  the transcription body.
- **FR-002** `_pick_cover_title` MUST select the tallest `Title` bbox, join same-font title
  boxes within `_COVER_TITLE_FONT_RATIO`, de-dupe cover/spine repeats, and absorb a hugging
  subtitle while stopping at a byline (`_looks_like_byline`).
- **FR-003** With no usable `Title` box, `cover_title` MUST be `""` and the reading-order
  heuristic (feature 007) MUST still apply — never grab the next-largest box (that resurfaces
  the author).
- **FR-004** `cover_title` MUST be cached and preferred by `parse_metadata`, `book_title`,
  `page_header` over the text heuristic.
- **FR-005** The pass MUST NOT bump `PROMPT_VERSION` (cover-only, additive); pre-§16 caches
  MUST be **backfilled** resumably, with `--no-cover-backfill` to skip.
- **FR-006** The layout decode budget MUST cap at `COVER_LAYOUT_MAX_TOKENS` (1536) and, on
  `finish_reason=="length"`, redo at the full budget (truncated JSON scrambles boxes). The
  pass MUST run at full `MAX_EDGE` (1024 silently misses small-on-cover titles).
- **FR-007** A byline running header MUST NOT veto a real cover title: `book_title` skips the
  `COVER_OVERRIDE_VOTES` veto when `_looks_like_byline(header)`.

### Key entities
- **`cover_title`** — the largest-font title string cached per COVER record (may be `""`).

## Review & Acceptance Checklist
- [x] Title = tallest type, not reading order
- [x] Wrapped title joined; subtitle absorbed; byline excluded
- [x] Single-model reuse; layout text never in body
- [x] No version bump; resumable backfill of old caches

## Decision log (non-normative)
- **Why (§16).** `_cover_title` (feature 007) takes the *first* run of title-like lines in
  dots.mocr reading order, which is arbitrary w.r.t. type size — the publisher/author imprint
  OCRs first as often as not. Confirmed across the batch:

  | Book | Shot | OCR'd as (reading order) | Largest type (true title) |
  |------|------|--------------------------|---------------------------|
  | 27 | IMG_3036 | The Guilford Press, New York London | Rethinking the Power of Maps |
  | 32 | IMG_4358 | Geoffrey Benjamin | Tribal Communities in the Malay World |
  | 38 | IMG_4798 | British Library | Secret Maps |
  | 43 | IMG_5026 | Geoffrey Benjamin & Cynthia Chou | Tribal Communities in the Malay World |
  | 59 | IMG_5922 | Bloomsbury | Singapore: A Modern History |

- ~one extra layout pass per COVER (~150 in the batch), ~50 s/cover at 1600 px. Resolution is
  not negotiable — 1024 px is faster but silently misses small-on-cover titles.
- **Spine-stamp leak (book 30).** A runaway-truncated library stamp ("GENERAL REFERENCE
  LIBRARY" → "GENERAL REF") was used as a title; `_spine_titles` now requires the `User Group:`
  anchor and `_SPINE_STOP` catches `general ref`/`state librar`. The book was rejoined to its
  later cover sitting via the feature 010 merge hint (`IMG_4310 + IMG_4893`).
