# Feature 017 — Title/imprint detection gaps

**Status:** Gap A delivered (`lib-ocr-rag@5f90a77`) · Gap B **Proposed** · **Old ref:** §18
**Constitution:** III, V, VI, VIII

## User Scenarios & Testing

### Primary user story
As a user who sometimes photographs an **imprint embedded in a spread** or a bare **title page
instead of a cover**, I want those recognised so the book is named and described correctly,
rather than the title landing in body text or the book being named after an author.

### Acceptance scenarios — Gap A (IMPRINT embedded in a SPREAD) — delivered
1. **Given** a two-page spread where one page is a series list and the other a title/imprint
   page (e.g. IMG_3104), **when** `detect_type` runs, **then** before returning SPREAD it
   splits on `### Page N` boundaries and, if any page matches `IMPRINT_MARKERS`, returns
   IMPRINT instead — so the title is fed to `parse_metadata`.
2. **Given** a genuine SPREAD whose body merely quotes "all rights reserved", **when**
   detected, **then** a char-count guard (only promote if the matching page is short, e.g.
   < 1500 chars) prevents a false IMPRINT promotion.

### Acceptance scenarios — Gap B (title-only page in place of a cover) — proposed
3. **Given** a book whose first two records contain no COVER/IMPRINT and whose first body
   record is sparse (nchars < ~500), title-like (`_is_title_like`), and consistent with the
   later page headers, **when** the (proposed) grouper post-pass runs, **then** that record is
   promoted to a synthetic IMPRINT (`parse_metadata("IMPRINT", text)`) injected into the book's
   metadata — additive, cache-free.

### Edge cases
- A white, sparse title page scores near zero on colour (feature 009) and carries no imprint
  markers — only **position** (first page of a book session) distinguishes it from a short
  chapter opener, so Gap B is a grouper-level heuristic, not a `detect_type` change.
- The "title-only, no ©" page is rarer than it looks — corpus review found virtually every
  photographed info page has at least one `IMPRINT_MARKERS` hit. **Do not implement Gap B
  without more validated examples.**

## Requirements

### Functional — Gap A (delivered)
- **FR-001** `detect_type` MUST, before returning SPREAD, split the OCR text on `### Page N`
  and return IMPRINT if any page matches `IMPRINT_MARKERS`.
- **FR-002** The promotion MUST be guarded by a char-count limit so a body page quoting
  copyright language is not mis-promoted.

### Functional — Gap B (proposed, not yet built)
- **FR-003** A grouper post-pass SHOULD inspect each book with no COVER/IMPRINT in its first
  two records and promote a sparse, title-like, header-consistent first body record to a
  synthetic IMPRINT injected into the book's metadata.
- **FR-004** The promotion MUST be additive (existing COVER/IMPRINT records untouched) and MUST
  NOT change the cache (a pure grouper transformation — Principle V/VIII).
- **FR-005** Gap B MUST be validated against real corpus examples (books whose title resolves to
  an author name or "Untitled") before implementation.

### Key entities
- **Synthetic IMPRINT** — metadata injected for a book that has only a bare title page.

## Review & Acceptance Checklist
- [x] Gap A: SPREAD→IMPRINT promotion with char-count guard (delivered)
- [ ] Gap B: positional title-page promotion (proposed; needs validated examples)

## Decision log (non-normative)
- **Corpus review (§18).** Six representative info pages — all copyright/imprint pages (©,
  "first published", "all rights reserved", "Library of Congress") already classify IMPRINT;
  cover-only pages (<280 chars) already COVER; IMG_2230 ("no cover, imprint only") is correctly
  IMPRINT with a full CIP block extracted. Only two genuine gaps remain.
- **Gap A (IMG_3104).** A spread of "Also Available" (series list) + "WEAPONIZING MAPS"
  (title/imprint) → `detect_type` sees two folio headers → SPREAD fires before the per-record
  IMPRINT check; the title lands in body text. Fix is a one-liner: per-page `IMPRINT_MARKERS`
  test before the SPREAD return, with a `< 1500 char` guard. **Delivered.**
- **Gap B.** Title-only pages (title + author + publisher, no © block) are white, sparse
  (280–600 chars), markerless → fall through as body PAGE. Colour can't help (white). The only
  reliable signal is **position** (first page of a session). Proposed as a grouper post-pass,
  but explicitly gated on more examples — the class is rarer than expected.
