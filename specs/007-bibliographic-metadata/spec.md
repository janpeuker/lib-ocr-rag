# Feature 007 — Bibliographic metadata extraction + cover-title resolution

**Status:** Delivered · **Origin:** `library-ocr@f551263` · **Old ref:** §15
**Constitution:** III, V, VI
**Related:** largest-font title → [008](../008-cover-title-largest-font/spec.md);
bibliography/title hints → [010](../010-bibliography-title-hints/spec.md)

## User Scenarios & Testing

### Primary user story
As a user cataloging books, I want each book named and described from its cover/imprint
shots — title, author, publisher, year, ISBN, call number — parsed straight from the OCR
text, so the per-book document carries a usable citation header without me typing it.

### Acceptance scenarios
1. **Given** a COVER or IMPRINT shot, **when** Pass B routes it, **then** `parse_metadata`
   extracts a small set of fields (`title, author, publisher, year, isbn, call_number`),
   omitting unknowns, and the book is named from it.
2. **Given** dots.mocr prefixes every shot with a `### Page N` folio, **when** the cover
   title is resolved, **then** `_cover_title` skips the leading folio / call-no / ISBN noise
   and joins the first run of consecutive title-like lines, so a wrapped title
   (`THE ECONOMIC HISTORY` / `OF SINGAPORE`) is captured whole — it is never named `Page 1`.
3. **Given** a later short page misclassified COVER (e.g. a `PREFACE` page), **when**
   `book_title` resolves, **then** the **earliest** cover (capture order) wins, generic
   titles are skipped, and a running title repeated on ≥ `COVER_OVERRIDE_VOTES` shots can veto
   a cover it disagrees with.
4. **Given** the resolution runs over old caches, **when** `batch` re-emits, **then** titles
   self-correct from the cached `ocr_text` with **no re-OCR** (pure grouping/emit pass).

### Edge cases
- `_hdr_match` containment requires word-boundary alignment, so `SINGAPORE` does not match
  `Leluhur Singapore's Kampong Gelam`, while `Tribal Communities` still binds to its CIP form.
- A book whose title is buried in a title-page *list* (a series page) or lost to a runaway
  read, and absent from the bibliography, cannot be auto-titled — use feature 010's override.

## Requirements

### Functional
- **FR-001** `parse_metadata(typ, text[, cover_title])` MUST extract
  `{title, author, publisher, year, isbn, call_number}` from cover/imprint OCR text, omitting
  unknown fields, and feed the per-book YAML frontmatter.
- **FR-002** `_cover_title` MUST skip leading folio/call-no/ISBN noise and join the first run
  of consecutive title-like lines; it MUST be used by `parse_metadata`, `page_header`, and
  `book_title`.
- **FR-003** `book_title` MUST prefer the earliest cover in capture order, skip
  `_GENERIC_TITLES`, and allow a running title repeated on ≥ `COVER_OVERRIDE_VOTES` shots to
  veto a misclassified cover.
- **FR-004** Title resolution MUST be a pure re-derivation from cached `ocr_text` (no re-OCR,
  `PROMPT_VERSION` unchanged) so existing caches self-correct.
- **FR-005** Every input image MUST be traceable in the report: cover/imprint shots are listed
  on a **Cover/title shot(s)** line so a metadata shot never looks "skipped" (see feature 014).

### Key entities
- **Book metadata** — `{ title, author, publisher, year, isbn, call_number }`, any subset.

## Review & Acceptance Checklist
- [x] Fields parsed from OCR text; unknowns omitted
- [x] Folio heading no longer swallows the title
- [x] Earliest-cover + generic-title skip + vote veto
- [x] Pure re-derivation; old caches self-correct

## Decision log (non-normative)
- Triggered by a `report.md` review (§15): several books took a wrong/partial title and a
  few title pages looked "missing". Root causes fixed here: `### Page N` swallowed the cover
  title; a stray/interior shot misread as the cover out-ranked the real title page;
  over-eager containment merge; generic-title RIS false match; a runaway-loop blind spot in
  `text_quality` (now also checks trigram diversity — see feature 003); covers invisible in
  the report.
- The **largest-font** refinement (§16) supersedes the reading-order `_cover_title` for shots
  classified COVER — split into feature 008 as its own story.
