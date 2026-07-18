# Feature 024 — Book-split hint (`! IMG_x` in merges.txt)

**Status:** Delivered · **Origin:** capture-order grouping (022) fusing a coverless book into its same-session predecessor
**Constitution:** III, V, VIII

## User Scenarios & Testing

### Primary user story
As a user who photographs several books back-to-back in one library sitting, I sometimes
open a book that has **no usable cover shot** right after finishing a cover-anchored one
(Chou's *Indonesian Sea Nomads*, pages-only, directly after the second reading of *Tribal
Communities in the Malay World*). No session gap separates them and the anchored-book rule
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
- [x] `! IMG_5035` separates Chou's coverless book from Tribal Communities
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
