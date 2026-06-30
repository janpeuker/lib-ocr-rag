# Feature 012 — Cover position: the title page leads OR trails the burst

**Status:** Delivered · **Origin:** `library-ocr@c465e37` (§9.1) · **Old ref:** §9.1
**Constitution:** III, V, VIII
**Related:** grouping core → [006](../006-book-grouping/spec.md)

## User Scenarios & Testing

### Primary user story
As a user who is inconsistent about **when** I shoot the cover — sometimes first (cover then
pages), sometimes last (pages then cover) — I want the grouper to attach a cover to its book
either way, instead of leaving a trailing cover stranded as a bogus one-shot "book".

### Acceptance scenarios
1. **Given** a cover shot that **leads** its pages, **when** grouping runs, **then** it starts
   the book and the following pages join it (the forward pass already handles this).
2. **Given** a cover shot that **trails** its pages (cover-last, e.g. IMG_2249 after the
   IMG_2230 pages) and whose header differs from the page headers, **when** grouping runs,
   **then** `_fold_orphan_covers` folds the body-less cover into the preceding same-session
   book instead of emitting a standalone meta-only book.
3. **Given** a short caption page misclassified as COVER (a stray `Map`/`Figure`), **when**
   folding runs, **then** it is re-attached to its neighbour for provenance but its `title:`
   line is stripped first, so it can never override the host's real title.
4. **Given** a body-less meta-only book with no same-session neighbour, **when** folding runs,
   **then** it is kept standalone.

### Edge cases
- A cover in the **middle** of a run whose headers also differ still splits — it is not
  body-less (it accretes the following pages) and lead-vs-continue is genuinely ambiguous;
  left as-is, since the workflow is cover-first or cover-last, not mid-run.
- When the cover's header **matches** the page headers, it joins on the title regardless of
  position — this feature only bites when headers differ.

## Requirements

### Functional
- **FR-001** `_fold_orphan_covers` MUST run after `_fold_key_images` and fold each body-less
  meta-only book into the same-session neighbour it belongs to, chosen by (a) title match,
  else (b) the nearer capture-time gap (trailing covers default to the previous book).
- **FR-002** A body-less meta-only book with no same-session neighbour MUST be kept standalone.
- **FR-003** A confident cover title (`_is_real_cover_title`) MAY name the host **only when the
  host has no cover/imprint title of its own** (`book_title` takes the first metadata title).
- **FR-004** A non-cover orphan's `title:` line MUST be stripped (`_strip_title_line`) before
  merging, so a misclassified figure/slip is re-attached for provenance but can never override
  the host title.
- **FR-005** The pass MUST be pure (post-grouping, no re-OCR, `PROMPT_VERSION` unchanged).

### Key entities
- **Orphan cover** — a body-less, meta-only one-shot "book" (genuine trailing cover or a
  misclassified figure/slip).
- **Host** — the same-session neighbour the orphan is folded into.

## Review & Acceptance Checklist
- [x] Cover may lead or trail its pages
- [x] Trailing cover folds into the preceding same-session book
- [x] Misclassified figure/slip re-attached for provenance, never overrides title
- [x] Pure post-pass; no re-OCR

## Decision log (non-normative)
- **Why (§9.1).** The forward pass assumes a cover/imprint *leads* its book, so a leading cover
  always accretes its pages. A **trailing** cover has no following pages to adopt it, so the
  "new title starts a book" rule split it off as a body-less one-shot book — but only when the
  page headers differ from the title (when they match, it joins on the title regardless of
  position). The same split hit short caption pages misclassified as COVER.
- **Validation (existing cache, before/after).** 88 → 80 books, all 9 body-less orphans
  cleared; **no body-bearing book was split** and no host title corrupted (figure/slip orphans
  folded in without changing the host; real covers folded into already-titled hosts).
  Synthetic check: trailing-cover-with-differing-header now yields 1 book (was 2).
- New helpers: `_fold_orphan_covers`, `_choose_orphan_neighbour`, `_absorb_orphan`,
  `_is_real_cover_title`, `_title_matches_book`, `_gap_secs`, `_strip_title_line`.
- **Known limitation:** a mid-run cover with differing headers still splits (see edge cases).
