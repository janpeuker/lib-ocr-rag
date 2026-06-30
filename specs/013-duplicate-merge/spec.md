# Feature 013 — Library-wide duplicate-book merge

**Status:** Delivered · **Origin:** `library-ocr@ab7a77b` (§14) · **Old ref:** §14
**Constitution:** III, V, VIII

## User Scenarios & Testing

### Primary user story
As a user who sometimes reads the **same book in two non-adjacent sittings** (other books
photographed in between), I want those two runs recognised as one book — and I want a safe,
human-confirmable way to do it, because the real duplicates here are often *title-invisible*
(the two runs share no readable title).

### Acceptance scenarios
1. **Given** a body-less meta-only book (a lone cover) whose confident title
   (`_is_real_cover_title`) matches a body-bearing book anywhere, **when** the auto pass runs
   (Tier 1), **then** it is folded in (a bare cover has no pages to lose).
2. **Given** two body-bearing books sharing an **exact** ISBN/call number, **when** the auto
   pass runs (Tier 2), **then** they merge; a **differing** key is a hard negative — different
   editions stay apart (Wood's *Power of Maps* Routledge-1993 vs Guilford-2010 stay separate).
3. **Given** an `in/merges.txt` allow-list, **when** `batch` runs, **then** `IMG_a + IMG_b`
   folds the whole books containing those shots into one, and `IMG_host += IMG_x` moves
   individual stray shots into the host book; merged records are re-sorted into capture order.
4. **Given** a merged book, **when** emitted, **then** provenance is recorded as `merged_from`
   and rendered as "Assembled from N shots across multiple readings".
5. **Given** the corpus, **when** `merge_candidates` runs, **then** `out/merge_candidates.json`
   ranks same-title page+page pairs as a discovery aid for populating `merges.txt`.

### Edge cases
- Distinct books share generic titles (`SINGAPORE`/Bloomsbury vs `SINGAPORE`/Oxford) — title
  identity alone is unsafe, so the auto pass relies on a bare cover (Tier 1) or an exact key
  (Tier 2), never on a shared generic title.
- Title-invisible duplicates won't surface in `merge_candidates.json` — those are found by eye
  and listed directly in `merges.txt`.
- Merge does not resolve titles — a merged book is only as well-named as `book_title` makes it;
  correct via feature 010 (RIS / `titles.txt`).

## Requirements

### Functional
- **FR-001** `group_images(records, merges=None)` MUST run the normal pipeline, then
  `_merge_library_duplicates` with two **conservative auto passes**: Tier 1 (bare-cover title
  match into a body-bearing book) and Tier 2 (exact ISBN/call match between body-bearing books).
- **FR-002** A **differing** strong key MUST be treated as a hard negative (different editions
  stay separate).
- **FR-003** `_apply_manual_merges` MUST be the **primary** mechanism via an optional
  `in/merges.txt` allow-list honouring the no-op-if-absent contract (Principle VIII), with two
  operators: `IMG_a + IMG_b [+ …]` (fold whole books) and `IMG_host += IMG_x [IMG_y …]` (move
  stray shots).
- **FR-004** Merged records MUST be re-sorted into capture order so each reading's pages stay
  contiguous; provenance MUST be recorded (`merged_from`) and shown in the book file.
- **FR-005** `merge_candidates(books)` MUST emit a ranked `out/merge_candidates.json` (exact
  title +3, one-sided key +2, long specific title +1, generic title −3, differing strong keys
  −100) as a discovery aid — **not** auto-applied.
- **FR-006** The merge pass MUST be pure (no re-OCR) and MUST NOT touch the cache.

### Key entities
- **Merge directive** — a line from `in/merges.txt` (`+` fold or `+=` move).
- **Merge candidate** — a scored same-title pair in `merge_candidates.json`.

## Review & Acceptance Checklist
- [x] Auto merge is conservative (bare cover OR exact key); differing key = hard negative
- [x] `merges.txt` is primary, no-op-if-absent, cache-free
- [x] Provenance recorded; candidates ranked but not auto-applied

## Decision log (non-normative)
- **Why auto-merge can't be the whole answer (111-book run).** Bibliographic keys are sparse
  (ISBN 18/111, publisher 12, year 21, call number 3, author 0); title identity alone is unsafe
  (generic-title collisions); and the real duplicates are **title-invisible** — Ingold reads as
  `MAKING` and `It's utility as a relationality`; Gusinde as `Page 14-3: The Body Painted
  Shoort` and `Kawësqar woman.` (no shared resolved title). Pure title matching would miss
  exactly the cases the user cares about → the human allow-list is primary.
- **Verification (2026-06-25 fixture).** `merges.txt` (Ingold `2818+2927`, Gusinde `2881+2973`,
  move `2881 += 2985`): 111 → 109 books; Ingold spans 2818–2938 across both readings; Gusinde
  spans 2881–2985 incl. the rescued stray cover; Weizman no longer holds 2985; `SINGAPORE`×2 and
  the Wood editions stay separate; `merge_candidates.json` ranks the real `intertidal` duplicate
  first (+6).
- **Known limitation:** merge does not resolve titles (deferred to feature 010).
