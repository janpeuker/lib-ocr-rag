# Feature 006 — Group images into books by title identity

**Status:** Delivered · **Origin:** `library-ocr@76b43a9` · **Old ref:** §9 (core)
**Constitution:** III, V, VIII
**Related:** burst time/GPS hint → [011](../011-burst-session-hint/spec.md);
cover-position folding → [012](../012-cover-position-heuristic/spec.md);
duplicate merge → [013](../013-duplicate-merge/spec.md)

## User Scenarios & Testing

### Primary user story
As a user who never labels which photo belongs to which book, I want the pipeline to infer
book boundaries from the photos themselves — correctly grouping a book even when it was
shot **across several days/sessions**, and correctly splitting two different books shot
back-to-back in one session.

### Acceptance scenarios
1. **Given** cached records in capture order, **when** `group_images` runs, **then** each
   shot's title-like running header (`page_header` + `_is_title_like`) defines a book
   identity, and headers that fuzzy-match (`_hdr_match`, containment + word-boundary aware)
   stay one book — **across time and GPS gaps**.
2. **Given** a book photographed on two different days, **when** grouped, **then** it is one
   book (a matching title overrides the time/GPS gap — see feature 011).
3. **Given** two different books in one session with different library call numbers, **when**
   grouped, **then** a change of call number starts a new book.
4. **Given** a verso/recto alternation of book-title vs chapter-title headers, **when**
   grouped, **then** the run is **not** fragmented — a mid-session header change splits only
   when a **page-number reset** confirms it.
5. **Given** a session-opening shelf/spine overview shot, **when** grouped, **then** it is
   folded into the next book as a recorded `key_image` (provenance), not a standalone book,
   and may name an otherwise-untitled book by exclusion.
6. **Given** adjacent runs whose header sets overlap (one book shot across two days), **when**
   `_merge_shared_title` runs, **then** they are rejoined.

### Edge cases
- A wide capture span / GPS radius in the report flags a possibly mis-grouped book.
- The original hard fences fragmented 5 books into 61 — they MUST NOT return.

## Requirements

### Functional
- **FR-001** Grouping MUST be a pure function of cached records — **no inference, no GPU** —
  recomputed on every run (Principle V).
- **FR-002** The **primary** signal MUST be title identity: a per-shot title-like header;
  `_is_title_like` MUST filter body prose, folios, call numbers, and library-slip fields.
- **FR-003** A library **call number** change MUST start a new book (splits same-session
  adjacent books).
- **FR-004** A mid-session header change MUST split only when a page-number reset confirms it
  (so a verso/recto book-vs-chapter header alternation does not fragment a run).
- **FR-005** Post-passes MUST run in a defined order: `_merge_shared_title` (rejoin adjacent
  runs with overlapping header sets) → `_fold_key_images` (shelf/spine overview → next book's
  provenance) → cover-position folding (feature 012) → duplicate merge (feature 013).
- **FR-006** No hard session/GPS fences; removed helpers/constants (`_fence`,
  `_discontinuous`, `TIME_GAP_S`, `HEADER_SIM`) MUST NOT return. Capture time and GPS are the
  soft, overridable hint specified in feature 011.
- **FR-007** `book_title` MUST never return a body line; titles are resolved per feature 007.

### Key entities
- **Book** — ordered records + identity (title) + optional metadata + `key_images` +
  capture span + GPS centroid/radius.
- **Identity** — the resolved title-like header used for matching.

## Review & Acceptance Checklist
- [x] Title identity over session fences; one book may span days
- [x] Call-number split; page-reset-gated mid-session split
- [x] Pure, GPU-free, recomputed every run
- [x] Time/GPS, cover-position, and merge delegated to features 011 / 012 / 013

## Decision log (non-normative)
- **Why the redesign (§9).** The original Pass C used hard fences (>30 min gap or any GPS
  change → new book) and produced **61 books for 5**: GPS jitter fired on nearly every page;
  time fences cut a two-day book in two; `running_header` returned a body line so the header/
  reset splitter rarely fired. Fix: title-identity segmentation, pure re-grouping over the
  cache (`PROMPT_VERSION` unchanged). Result on 128 records: **5 books, correct boundaries**.
- **Title by exclusion.** `infer_title_by_exclusion` names an untitled book from a shelf
  overview shot by reading each spine block, dropping blocks matching already-identified
  siblings, and keeping the remainder (IMG_4310 → "Leaves of the Same Tree"). Anchored on the
  slip's "User Group:" field; drops authors/publishers/addresses.
- New helpers from the redesign: `page_header`, `_is_title_like`, `_hdr_match`, `call_number`,
  `_page_reset`, `_merge_shared_title`, `_fold_key_images`, `gps_radius`, `book_capture`,
  `book_meta`. Removed: `_fence`, `_discontinuous`, `TIME_GAP_S`, `HEADER_SIM`.
