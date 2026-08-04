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
   editions stay apart (Author-Q's *Book-AD*, 1993 vs 2010 printings, stay separate).
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
- **`+` folds whole books, so one misattributed shot in the line folds a whole foreign book.**
  A `+` term is not "add this shot" — it is "add whatever book this shot currently belongs to".
  If the author's mental model of which book a shot sits in is wrong, the operator silently
  imports every page of the other book, and the result is *invisible in the output*: it looks
  like one large, correctly-titled book. See FR-007 and the 2026-07-30 entry in the decision log.

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
- **FR-007** An **over-merge** — a book holding two or more meta shots whose `cover_title`s
  match *different* RIS records — MUST be reported (spec 029's `doctor`), not silently
  emitted. `merges.txt` is human-authored and `+` is transitive over whole books, so a
  wrong line is both easy to write and impossible to see in `report.md`. Reporting only:
  the allow-list stays authoritative (VIII), and a book legitimately holding two
  bibliographic identities (a bound-together volume) must remain expressible.
- **FR-008** Where a `+` line's intent is "attach these specific shots", the author SHOULD
  use `+=`, which moves shots and cannot fold a foreign book. `+` MUST remain the operator
  for joining two *readings of the same book*, which is what it was introduced for (FR-003).
- **FR-009** A `+=` move that removes the shot a book's `identity` was seeded from MUST
  invalidate that `identity`. `_apply_manual_merges` runs after segmentation and currently
  leaves it pointing at the departed shot's book, where `match_ris` then scores it as a
  title query (measured — see the decision log). Until fixed, `! IMG_x` is the workaround,
  since a split applied *during* segmentation seeds `identity` correctly.
- **FR-010** `match_ris` MUST NOT resolve a tie between two equally-scoring RIS records by
  bibliography file order. Two records with near-identical main titles (*Book-P* /
  *Book-J*, 0.878 against each other) both reach 0.99 against the
  right query, and today the earlier line in the `.ris` wins silently.

### Key entities
- **Merge directive** — a line from `in/merges.txt` (`+` fold or `+=` move).
- **Merge candidate** — a scored same-title pair in `merge_candidates.json`.
- **Over-merge** — one emitted book whose shots are really two or more distinct works; the
  inverse of the duplicate this feature exists to fix, and the failure mode its own primary
  operator introduces.

## Review & Acceptance Checklist
- [x] Auto merge is conservative (bare cover OR exact key); differing key = hard negative
- [x] `merges.txt` is primary, no-op-if-absent, cache-free
- [x] Provenance recorded; candidates ranked but not auto-applied
- [ ] Over-merge reported by `doctor` (FR-007); measured false-positive rate recorded
- [ ] `+=` invalidates a stale `identity` (FR-009); `! IMG_2848` workaround retired
- [ ] `match_ris` ties no longer resolved by RIS file order (FR-010)

## Decision log (non-normative)
- **Why auto-merge can't be the whole answer (111-book run).** Bibliographic keys are sparse
  (ISBN 18/111, publisher 12, year 21, call number 3, author 0); title identity alone is unsafe
  (generic-title collisions); and the real duplicates are **title-invisible** — Book-G reads as
  a bare one-word fragment in one sitting and a mid-sentence clause in the other; Author-I's
  plate book as a plate number in one and a specimen caption in the other (no shared resolved
  title). Pure title matching would miss
  exactly the cases the user cares about → the human allow-list is primary.
- **Verification (2026-06-25 fixture).** `merges.txt` (Book-G `2818+2927`, Author-I `2881+2973`,
  move `2881 += 2985`): 111 → 109 books; Book-G spans 2818–2938 across both readings; Author-I
  spans 2881–2985 incl. the rescued stray cover; Author-B no longer holds 2985; the two books
  sharing a bare city-name title and the two Author-Q editions stay separate;
  `merge_candidates.json` ranks the real duplicate first (+6).
- **Known limitation:** merge does not resolve titles (deferred to feature 010).
- **The feature's own operator is now a leading source of over-merges (2026-07-30).** This
  line stood in `merges.txt` for a month:

  ```
  IMG_4446 + IMG_4396 + IMG_4396 (1)   # Author-A, Book-A —
                                       # the main run (4361-4498) + the '(1)' fragment
  ```

  The comment's premise was wrong. IMG_4396 is p.310 of a contributor's chapter in
  *Book-I* (an edited volume) — a different book that
  happened to occupy the shot range the author believed was Author-A's. Because `+` folds
  **whole books**, that one term imported all 73 shots of *Book-I* (both sittings,
  already joined to each other by a separate `IMG_4358 + IMG_5026` line) into Book-A. Result:
  a 130-shot "Book-A" whose first emitted page is *Book-I*'s imprint, carrying *Book-I*'s
  call number in its own frontmatter, with 65 pages of the wrong book citable
  under Author-A's name. Nothing in `report.md`, `coverage.json` or `doctor` flagged it — every
  page was accounted for, just to the wrong book. Removing the middle term was the whole fix:
  a session fence already sat between the two runs (Jan 2 → Jun 28), and IMG_4445 is a clean
  COVER reading *Book-A*'s real title.

  Three things this establishes, none of which the original design anticipated:
  1. **The dangerous direction is the one this feature exists to encourage.** Every guard here
     (Tier 1/Tier 2 conservatism, differing-key hard negative, candidates not auto-applied)
     protects against *auto*-merging too eagerly. The manual operator has no guard at all, and
     it is the primary mechanism by FR-003.
  2. **Over-merge is invisible in a way under-merge is not.** A shattered book shows up as
     several oddly-titled books a human notices; an over-merged book looks like one big
     correctly-titled book. Coverage accounting cannot see it — spec 029's `UNEXPLAINED` check
     is about text that reached *no* book, not text that reached the *wrong* one.
  3. **The signal is already on disk.** See the sweep below.

- **Measured detector for FR-007 (2026-07-30, whole corpus).** For each book, collect the
  `cover_title` of every meta shot, score each against the RIS with `match_ris`, and flag books
  where two or more *distinct* records match. Stdlib only, no GPU, whole corpus in seconds.
  Over 133 books it fired **twice**, and both fires were real defects:

  | Book | Foreign cover found | Verdict |
  |---|---|---|
  | Author-C, *Book-E* | IMG_5684 → *Book-N* (Author-O) | **real** — the `IMG_5035 += IMG_5685…5689` line moved Author-O's five body pages out of Book-E but left the sitting's COVER behind. Fixed by adding IMG_5684 to that line. |
  | Author-Q, *Book-AD* | IMG_2848 (*Book-P*'s cover) → *Book-J* | **real, and a third instance of the same `+` failure** (user-confirmed). Resolved below. |

  Zero clean false positives. Notably the obvious candidate for one — Author-B's
  *Book-B*, which legitimately holds two COVER shots (a garbled spine and a clean title
  page, see spec 030's decision log) — does **not** fire, because the garbled spine matches no
  RIS record. That is the property that makes the check usable: it needs two *confident*
  bibliographic identities, not merely two disagreeing OCR strings.

  Recall is bounded the same way FR-010 of spec 030 is bounded: a foreign book absent from the
  bibliography is invisible. The Book-A case itself only becomes detectable once
  *Book-I* has a `TI` record in Zotero — at the time of the fix it
  existed only as `T2` on two chapter records, so the check that found two other bugs would
  **not** have found the one that motivated it. Recorded rather than papered over.

- **Third instance, and the check's own fire resolved (2026-07-30, user-confirmed).**
  `IMG_2849 + IMG_3007  # Author-Q, Book-AD — two sittings` was wrong the same way
  the Book-A line was: two *different* Author-Q & Author-R books, not two sittings of one.
  IMG_2849 is the imprint of **Book-P** (2008) and its body 2850–2880
  is that book's argument (its central propositions and worked cases); IMG_3007 is the
  1993 edition and 3006–3035 really is *Book-AD*.
  Three confirmed instances now, all from the same author error — believing a shot
  range belongs to a book it does not — which is the evidence for FR-008's "prefer `+=`".

  Two things had to be true before the split produced a *correct* book, and neither was
  obvious from the merge semantics:

  1. **The bibliography must contain the foreign book, or the split makes attribution
     worse.** *Book-P*'s title fuzzy-matches Author-J's near-identically titled *Book-J* at
     **0.878**, over the 0.85 threshold. Splitting without a record would have moved 33
     pages from one wrong citation (Author-Q 1992) to another (Author-J 2001). A `titles.txt`
     override cannot rescue this — an override is just one more candidate fed to the same
     matcher (§10). The record had to be added first. Generalisation: **an over-merge fix
     is not complete until the freed book has a bibliographic identity of its own**, which
     is why FR-011 of spec 029 (unattributed books) and FR-007 belong to the same workflow.
  2. **`+=` runs after segmentation, so moving a book's *first* shot out leaves a stale
     `identity`.** `group_images` seeds `cur["identity"]` from the shot that opens a book
     (`ocr.py:1140`); `_apply_manual_merges` runs later and never revisits it. Moving the
     stray IMG_2847 cover away left the 2848 book claiming `identity == <Book-AD's
     title>`, which `match_ris` scores as a query — tying the real record at 0.99 and losing
     the tie on **RIS file order**. Worked around with `! IMG_2848`, which splits during
     segmentation so `identity` seeds correctly. The underlying defect stands: a `+=` that
     removes a book's seed shot SHOULD invalidate that book's `identity`, and `match_ris`
     resolving a 0.99 tie by file order is a latent hazard wherever two records share a
     near-identical main title. Both are candidates for their own fix; recorded here rather
     than patched blind.
