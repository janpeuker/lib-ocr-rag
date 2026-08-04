# Feature 025 — Imprint-ISBN book boundary

**Status:** Delivered · **Origin:** a title-less imprint gluing four books into *Book-Y* (IMG_8552–8677)
**Constitution:** III, V, VIII

## User Scenarios & Testing

### Primary user story
As a user who photographs books back-to-back in one sitting, I sometimes open the next
book on its **copyright page** (no cover shot, or the cover misreads as a body page).
The imprint parses a year and an ISBN but **no title** — and the existing boundary rule
("a cover/imprint with a *new* title starts a book") never fires without a title. The
whole next book then chains onto its predecessor, and worse, the foreign imprint's
year/ISBN **pollute the wrong book's metadata** (Author-L's 2013 ISBN landed on the
antique *Book-Y*; Author-F's ISBN on an Author-AJ fragment). An ISBN is a
stronger identity signal than a title — it should be a boundary on its own.

### Acceptance scenarios
1. **Given** a body-bearing book followed by an IMPRINT shot whose parsed ISBN is not
   one this book has already shown (IMG_8611: `<ISBN-2>` after the pilot spine),
   **when** `batch` groups shots, **then** a new book starts at that imprint.
2. **Given** a book whose own imprint repeats an ISBN already collected from its cover
   or an earlier meta shot, **when** grouping runs, **then** no split occurs (same ISBN
   = same book).
3. **Given** the normal front-matter flow cover → imprint → pages, **when** the imprint
   is reached, **then** no split occurs — the book has no body yet, so the imprint is
   its own front matter.
4. **Given** an IMPRINT that parses no ISBN (a blank verso or notes page misclassified
   as IMPRINT, e.g. IMG_8614, IMG_4957), **when** grouping runs, **then** the rule stays
   silent — year-only or empty metadata never splits.

### Edge cases
- Only `type == "IMPRINT"` metas participate. A back cover (COVER) that happens to OCR
  a barcode ISBN must not split its own book's tail.
- ISBNs compare digits-only (hyphenation varies between CIP blocks and barcodes).
- A trailing imprint photographed *after* its book's pages starts a spurious one-shot
  meta book in the worst case — which the existing orphan-cover fold reabsorbs; a
  mid-book false split with matching running headers is rejoined by the shared-title
  pass. The rule's failure modes land in existing safety nets.

## Requirements

### Functional
- **FR-001** Segmentation MUST start a new book at a meta record of type IMPRINT whose
  parsed ISBN (digits-only) differs from every ISBN already collected for the current
  book, provided the current book already has body pages.
- **FR-002** Each book MUST accumulate the ISBNs of its joined meta records, so a
  repeated/own ISBN never splits (scenario 2).
- **FR-003** An IMPRINT with no parsed ISBN MUST never trigger this rule.
- **FR-004** The rule MUST be pure over cached records — no new inference, no cache
  reads or writes (Principle V); split hints (024) and manual merges still override.

### Key entities
- **Imprint ISBN** — digits-only normalization of the `isbn:` line in a meta record's
  parsed metadata block.

## Review & Acceptance Checklist
- [x] IMG_8611 (Author-L) and IMG_8677 (Author-F) start their own books with no title parsed
- [x] Cover→imprint→pages flow unchanged; ISBN-less imprints inert
- [x] Pure over cached records; hints still override

## Decision log (non-normative)
- **Why ISBN, not "any imprint after body".** The corpus contains IMPRINT-typed metas
  that are really blank versos (IMG_8614, IMG_8636) and a notes page (IMG_4957) — all
  metadata-empty — plus a false imprint mid-book with a bare year (IMG_8660,
  `year: 1954`, a plate caption). Splitting on every post-body imprint would shatter
  real books; requiring a parsed ISBN restricts the rule to genuine copyright pages.
  Old books without ISBNs simply never fire it — conservative by construction.
- **Why the title rule wasn't enough.** `page_header()` for a meta falls through
  cover_title → `title:` metadata → CIP block → (COVER only) cover-text heuristic. A
  modern US copyright page ("Cataloging-in-Publication", no 'u') misses the CIP regex
  and often parses year+ISBN but no title — exactly the delivering corpus's failure.
- **What stays a hint.** The same sitting also had a cover OCR'd as a body page
  (IMG_8637) and a spine-stack shot OCR'd as an Author-L body page (IMG_8610) — OCR-level
  misreads with no reliable signal; they stay in `merges.txt`/`titles.txt` per the
  spec-024 rationale.
