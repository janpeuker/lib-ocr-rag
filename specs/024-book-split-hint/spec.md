# Feature 024 — Book-split hint (`! IMG_x` in merges.txt)

**Status:** Delivered · **Origin:** capture-order grouping (022) fusing a coverless book into its same-session predecessor
**Constitution:** III, V, VIII

## User Scenarios & Testing

### Primary user story
As a user who photographs several books back-to-back in one library sitting, I sometimes
open a book that has **no usable cover shot** right after finishing a cover-anchored one
(Author-O's *Book-N*, pages-only, directly after the second reading of
*Book-I*). No session gap separates them and the anchored-book rule
correctly refuses to split on running headers — so the grouper cannot know a new book
started. I want to state that boundary myself, in the same human-confirmed hint file that
already records merges, so the split survives every re-run without touching the cache.

### Acceptance scenarios
1. **Given** a `merges.txt` line `! IMG_5035`, **when** `batch` groups shots, **then** a
   new book starts at `IMG_5035` even though no gap, cover, or call-number change occurs.
2. **Given** the split hint plus existing merge lines hosting the two halves, **when**
   grouping completes, **then** each half folds into its intended book (splits apply
   during segmentation, merges after — order is deterministic).
3. **Given** no `!` lines, **when** `batch` runs, **then** behaviour is byte-identical to
   pre-024 output (absent hint ⇒ no-op, like every other hint).
4. **Given** a `!` stem that matches no photo, **when** `batch` runs, **then** it is
   silently ignored (the hint file may outlive a renamed corpus).

### Edge cases
- The stem matches exactly (extension optional), like merge stems — `IMG_5035` never
  matches `IMG_5035 (1)`.
- A split at the very first shot of the corpus is a harmless no-op.
- Splits never delete anything: they only force `start = True` at segmentation, so the
  worst possible mistake (a wrong `!` line) is one extra book, trivially reverted.

## Requirements

### Functional
- **FR-001** `load_merges` MUST additionally parse lines of the form `! <stem>` into a
  set of split stems, returned alongside groups and moves.
- **FR-002** During segmentation, a record whose filename stem is in the split set MUST
  start a new book, overriding every keep-together rule.
- **FR-003** Splits MUST apply before manual merges, so a merge line can host either side
  of the boundary.
- **FR-004** An absent file, or a file with no `!` lines, MUST leave grouping unchanged;
  unknown stems MUST be ignored. The hint MUST NOT read or write any cache entry.

### Key entities
- **Split stem** — a photo filename stem at which segmentation must start a new book.

## Review & Acceptance Checklist
- [x] `! IMG_5035` separates Author-O's coverless book from *Book-I*
- [x] No `!` lines ⇒ output unchanged; unknown stems ignored
- [x] Splits precede merges; cache untouched

## Decision log (non-normative)
- **Why a hint, not a heuristic.** The fusing case — coverless book, same session, no
  call-number change — is exactly the signal set the anchored-book rule must ignore to
  avoid splitting on chapter headers (006). Filename-order grouping only "got this right"
  by accident: the interleaved name-twin roll tripped a spurious session gap at the
  boundary. Any header-vote or page-reset heuristic strong enough to re-split it would
  re-introduce chapter-header splits elsewhere; the boundary is human knowledge, so it
  belongs in the human-confirmed hint file (mirror of 013's merge rationale).
- **Why `!` syntax in merges.txt** rather than a third hint file: the split is the exact
  inverse of a merge and is maintained in the same workflow (inspect `report.md`, adjust,
  re-run). One file keeps the grouping story in one place.
- **Folio-number guard against false COVER splits (Jul 2026).** Two real cases
  (Author-D's *Book-F* IMG_8133, Author-Z's *Book-T*
  IMG_5642) got auto-split because `detect_type`'s sparse-text/colour heuristic
  classified a mid-book photo-plate caption and a part-title divider as COVER — each
  had almost no OCR'd text, exactly what a real cover looks like. Both, however, carried
  a printed folio (`### Page 21`, `### Page 15`) that dots.mocr had already read off the
  page — a signal a real cover never produces.
  First cut checked `page_numbers(t)` (the general-purpose folio extractor used for
  citations), which over-fired: 205 already-correct COVER shots flipped, because that
  helper falls back to "any bare digit line ≤4 chars" when no `### Page N` heading is
  present — it caught a publication year printed on a real cover (Author-D's own cover
  misread "1997" as a folio) and other stray digits. Restricting to a literal
  `### Page N` heading cut that to 41, but a second trap remained: dots.mocr defaults a
  genuinely sparse cover/half-title shot to "Page 1" (sometimes "Page 1" + "Page 2" for
  a cover+facing-page spread) even when nothing is printed — real covers for Author-AA,
  Author-AB, Author-AC, Author-AD, and others all carry that phantom `### Page 1`. The
  final guard (`FOLIO_MIN = 4` in `detect_type`) only trusts a folio at or above that
  value; below it, "Page 1/2" is the model's default, not real pagination.
  `detect_type` now refuses to classify a shot with a real folio as COVER, so it falls
  through to PAGE/SPREAD and never trips `group_images`' rule-1 split. Guards *future*
  batches only (`process_image` recomputes `type` on cache miss) — the existing library
  was backfilled separately: every cached record with `type: COVER` had `detect_type`
  re-run against its already-stored `ocr_text` (no re-OCR, since the fix is a
  classification change, not a transcription change) and `type`/`role`/`raw_md`/
  `metadata`/`cover_title`/`running_header`/`page_numbers` patched in place for the 41
  that flipped. Re-running `batch` afterward rejoined both known false splits — and
  several others library-wide — with no `merges.txt` hint needed, so the earlier hint
  lines for these two books were removed as redundant. merges.txt/titles.txt remain the
  catch-all for cases this can't foresee (e.g. a plate page with no printed folio at
  all, or one whose folio happens to be 1-3).
- **The Book-K title override (titles.txt) was a misdiagnosis, not a real gap.** The
  book's ISBN (`<ISBN-1>`) was in the Zotero RIS the whole time — a narrow `grep`
  context window during the original investigation missed the matching record. The
  actual bug: `match_ris` only ever compared titles, so a badly garbled OCR title
  guess (a mangled fragment of the city-name main title) could never fuzzy-match the real RIS title, even
  with an exact ISBN sitting right there in the imprint text. Fixed by trying an exact
  ISBN match first (`_book_isbn(book)` against each RIS record's `SN` field) before
  falling back to fuzzy title comparison — ISBN is immune to both a garbled title guess
  *and* to the false-positive risk fuzzy matching carries for generic/shared main
  titles (the titles.txt override's own first draft, *Book-K* truncated,
  false-matched Author-W's unrelated *Book-S* at 0.99 via the
  main-title-before-colon comparison — a title override can collide with this same
  fuzzy path). With ISBN matching in place the book resolves automatically with full
  author/publisher/year/city from Zotero, so the titles.txt line was removed.
