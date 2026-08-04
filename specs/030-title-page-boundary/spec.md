# Feature 030 — Title-page book boundary (coverless books)

**Status:** Proposed (exploration — not implemented) · **Origin:** IMG_8043, Author-G's
*Book-AG* swallowed by Author-AK's *Book-AH*
**Constitution:** IV, V, VI, VIII

Second confirmed instance: **IMG_5148**, Author-C's *Book-E* swallowed by Author-P's
*Book-O* (2026-07-30). It is not a second copy of the same case — it **defeats the
FR-001 prefilter**, so no design in this spec would have caught it. See "IMG_5148: the
prefilter is the binding constraint, not the gate".

## User Scenarios & Testing

### Primary user story
As a user photographing several books in one library sitting, I sometimes open a book
whose **cover I never shoot** — I start at the title page. Nothing in the shot stream
marks the boundary: no session gap (10 minutes), no GPS change (same reading room), no
ISBN, no library call-number change, no folio reset the grouper can see. The book is
silently appended to its predecessor, and every page of it is then cited under the wrong
author for as long as the catalog lives. I want the pipeline to recognise a **title page**
the way a human does — the title is the largest type on an otherwise sparse page — and
open a new book there, without re-introducing the chapter-header false splits that
spec 006 and 024 deliberately guard against.

### Acceptance scenarios
1. **Given** IMG_8043 (Author-G title page: one `Title` box, a facing series list, no
   folio) mid-run inside a cover-anchored book, **when** `batch` groups shots, **then** a
   new book starts at IMG_8043 with title *Book-AG* — with
   no `! IMG_8043` hint present.
2. **Given** a chapter opener whose heading is set large (IMG_4946, IMG_3709, IMG_4454),
   **when** `batch` groups shots, **then** no new book starts — a chapter opener always
   carries a large body block, a title page does not.
3. **Given** a corpus with no title-page shots, **when** `batch` runs, **then** grouping
   output is byte-identical to pre-030 (the detector is additive).
3b. **Given** IMG_5148 (Author-C title page + colophon: rotated 90°, phantom `### Page 1`
   folio, title on line 8 under the printer's imprint) mid-run inside *Book-O*,
   **when** `batch` groups shots, **then** a new book starts at IMG_5148 with title
   *Book-E* — with no `! IMG_5148` hint present. **This is the acceptance test the
   current design fails** (FR-014).
4. **Given** an already-cached corpus, **when** `batch` runs after this ships, **then**
   the new field is backfilled one layout pass per *candidate* shot, resumably, with no
   re-transcription (mirrors the 008 `cover_title` backfill).
5. **Given** a `! IMG_x` or `merges.txt` line disagreeing with the detector, **when**
   grouping runs, **then** the human hint wins (VIII).

### Edge cases
- A **journal issue front** (IMG_5098, *Book-AJ*) satisfies the same test. That is
  arguably correct — it *is* a new bibliographic item — but it changes how periodical
  runs group; decide deliberately rather than discovering it in `report.md`.
- An **edited volume** that sets a full title page before every contribution would split
  into one book per chapter. **Measured and real**: IMG_4951 (a contributor's chapter in
  *Book-R*) satisfies FR-003 — its body is broken into an epigraph plus
  several small `Text` boxes, so no body block outgrows the title. FR-009's bibliography
  gate rejects it (0.52); the layout test alone does not.
- A **half-title** page (title only, no author/publisher) may satisfy the test and open
  the book one shot early — harmless if the following pages join it, wrong if the real
  title page then opens a second book.
- The detector sees one shot at a time; a title page photographed as the right half of a
  **spread** with the previous book's last page on the left must not split mid-spread.

## Requirements

### Functional
- **FR-001** A body shot MUST be eligible for title-page detection only via a cheap,
  text-only prefilter computable from the cached record alone: `role == "body"`, no
  parsed folio, a title-like first line, and short text. Measured selectivity on the
  present corpus: **109 of 3493 shots (3.1 %)**.
- **FR-014** The prefilter MUST NOT require the title to be the shot's **first line**, and
  MUST NOT reject a shot for carrying a parsed folio when that folio is the phantom `[1]`
  dots.mocr stamps on sparse pages. Both clauses of FR-001 independently reject IMG_5148,
  a true title page (measured — see the decision log). Candidate replacement: keep the
  short-text and `role == "body"` clauses, and match the RIS gate (FR-009) against **every
  line** of the shot rather than against `page_header` alone. The prefilter must be
  re-measured for selectivity after any such widening; 3.1 % is the budget FR-002 assumes.
- **FR-002** An eligible shot MUST pay at most one extra layout+text pass
  (`COVER_TITLE_PROMPT`, the 008 machinery), reusing the loaded model and the
  already-oriented image. Ineligible shots MUST pay nothing.
- **FR-003** A shot MUST be classified a title page only when the layout response
  contains at least one `Title` box **and** the tallest box on the page (by raw bbox
  height, any category) is a `Title` box. Relative font ratios MUST NOT be the test —
  see the Decision log; they invert on chapter openers.
- **FR-004** The result MUST be cached on the record (a new field, e.g. `title_page` +
  the picked title) so grouping stays a pure function of text already on disk, and MUST
  be backfilled into pre-030 caches on the next `batch` — resumable, one pass per
  candidate, with an opt-out flag (mirroring `--no-cover-backfill`).
- **FR-005** During segmentation, a title-page shot whose title does not match the
  current book's established identity MUST start a new book when that book already has
  body pages — the body-side analogue of rule 1 (a new cover/imprint title).
- **FR-006** `merges.txt` (`+`, `+=`, `!`) MUST continue to override the detector in
  both directions; the detector MUST NOT write to, or depend on, any hint file (VIII).
- **FR-007** A shot the detector splits on MUST record the reason in `coverage.json` /
  `index.md`, so a wrong split is diagnosable without re-running the model.
- **FR-009** A title page MUST additionally match a record in the RIS bibliography
  (`match_ris` scoring, threshold 0.85, types `BOOK`/`CHAP`/`EDBOOK`) before it may start
  a book. A chapter opener is not in the bibliography; a book is. A chapter that *is*
  catalogued (`CHAP`) legitimately passes — citing a chapter as its own unit is allowed.
- **FR-010** With no `*.ris` present the detector MUST be inert, leaving grouping exactly
  as it is today (VIII: absent hint ⇒ no-op). Recall is therefore bounded by catalogue
  coverage, and a book absent from the bibliography MUST fail silently, never guess.
- **FR-011** The RIS gate MUST NOT accept a **containment** match between a section title
  and the book title that contains it. Confirmed against the physical book: IMG_4078
  (a section title containing the book title) and IMG_4089 (a one-word section title) both
  score 0.99 against
  Author-B's *Book-B* / *Book-C* and are both **sections of one correctly
  grouped book**. `sim`'s containment shortcut needs a stricter rule here than `match_ris`
  uses for labelling — a wrong label is cosmetic, a wrong split shatters a book.
- **FR-012** A fired boundary MUST attach the book's preceding **front matter** — the
  folio-less shots between the previous book's last true page and the fire point — rather
  than splitting at the fire point alone. Measured need: *Book-AI* fires at
  IMG_3108 but starts at IMG_3105, stranding IMG_3106–3107.
- **FR-013** Where a book's opening shot is a **multi-book pile/shelf photo** (IMG_3105
  reads six titles at once), the detector MUST NOT attempt to name the book from it. Such
  a shot may anchor a boundary only via the `_fold_key_images` key-image path (012), never
  as a title source.

### Non-functional
- **FR-008** The one-time backfill MUST be interruptible and resume where it stopped
  (V). Budget on the present corpus: ~109 layout passes ≈ 1 min each ≈ 2 h.

### Key entities
- **Title page** — a body shot whose largest layout box is a `Title` box; the opening
  page of a book photographed without a cover.
- **Title-page candidate** — a shot passing the text-only prefilter (FR-001), the only
  shots that pay a layout pass.

## Review & Acceptance Checklist
- [ ] IMG_8043 splits automatically; `! IMG_8043` can be retired from `merges.txt`
- [ ] IMG_5148 splits automatically (FR-014); `! IMG_5148` + `IMG_5148 + IMG_5165` retired
- [ ] Prefilter selectivity re-measured after FR-014 widens it (3.1 % is FR-002's budget)
- [ ] IMG_4946 / IMG_3709 / IMG_4454 / IMG_8639 / IMG_8044 / IMG_4800 / IMG_8079 do not split
- [ ] Precision measured over all 109 candidates before wiring the grouper rule
- [ ] Edited-volume behaviour decided (per-chapter title pages)
- [ ] IMG_4078 / IMG_4089 do **not** split Author-B's *Book-B* (FR-011)
- [ ] *Book-AI* starts at IMG_3105, not IMG_3108 (FR-012/FR-013)
- [ ] Backfill resumable; `batch` on an unchanged corpus is a no-op
- [ ] Hints still win

## Decision log (non-normative)

### The failure (measured, 2026-07-30)
`page_header(IMG_8043)` already returns Book-AG's exact title — the
title was never lost. The gap is in `group_images`: every title-driven boundary rule
(rule 1, rule 1b) requires `role == "meta"`, and the one body-side rule (rule 4) requires
`_page_reset`, which returns `False` because a title page carries **no folio at all**.
The reset only becomes visible two shots later (IMG_8045, `[1, 2]` after IMG_8041's
`150`), by which point the title page is already absorbed. Boundary signals available at
IMG_8041 → IMG_8043: capture gap 594 s, identical GPS, no ISBN, no call number.

### Text-only heuristics: measured and rejected
Each run over the full 3493-shot cache:

| Signal | Fires | Why rejected |
|---|---|---|
| Folio reset (prev ≥ 20 → `[1]`) | **243** | dots.mocr emits a phantom "Page 1" constantly; this is why `_page_reset` is gated in the first place |
| Title-like header ≠ current book + no folio | 110 | overwhelmingly chapter openers |
| … + publisher name in text | 7 | 6 of 7 are **bibliographies** (IMG_4348, IMG_7467) — pages dense with "Oxford University Press" |
| … + capture gap ≥ 5 min | 5 | 3 are mid-book pauses (IMG_3709, IMG_4800, IMG_3217) |

Median inter-shot gap is 17 s and the 594 s pause is a genuine outlier, but only 11
transitions corpus-wide exceed 5 min and most are mid-book breaks. No cross-product of
these signals reaches usable precision. **The evidence a human uses here is visual, and
none of it survives into the transcribed text.**

### Why "largest font" must be the *category*, not a ratio
The intuition ("the title is bigger than the other text") does not survive measurement —
it **inverts**. Layout passes over the target and its eight nearest confusables:

| Shot | What it is | Tallest box | Title box? | Naive dominance ratio |
|---|---|---|---|---|
| **IMG_8043** | **Author-G title page** | **Title, h96** | **yes** | **2.0** |
| IMG_5098 | *Book-AJ* journal front | Title, h175 | yes | 7.6 |
| IMG_4946 | chapter opener | Text, h463 | yes | 4.3 |
| IMG_3709 | chapter opener | Text, h993 | no | 2.9 |
| IMG_8639 | contents | Table, h756 | no | 4.2 |
| IMG_8044 | Introduction + table | Table, h862 | no | 16.3 |
| IMG_4800 | body | Text, h627 | no | 1.3 |
| IMG_4454 | chapter opener | Text, h458 | no | 2.4 |
| IMG_8079 | body spread | Text, h399 | no | 1.6 |

A per-line font ratio ranks the true title page **below** three false positives. What
separates them is structural: a title page has **no large body block**, so its `Title`
box is the tallest box on the page; a chapter opener always has one. On this sample the
rule is exact — IMG_8043 and IMG_5098 pass, the other seven fail. Note that dots.mocr's
own `Title`/`Section-header` distinction does most of the work: chapter openers get
`Section-header`, which is why trusting only `Title` (the 008 lesson) carries over here.

### Full-corpus sweep result (complete, 2026-07-30)
All **109** prefilter candidates were run through the layout pass (~1 min/shot, ~2 h).
23 carried a `Title` box; **3 satisfied FR-003**:

| Shot | What it is | Verdict |
|---|---|---|
| IMG_8043 | Author-G title page | true positive (the target) |
| IMG_4951 | chapter in *Book-R* | **false positive** |
| IMG_5098 | *Book-AJ* journal front, inside another book | **false positive** |

**1 hit / 2 false positives for ~2 GPU-hours.** The bibliography gate (below) keeps the
hit and rejects both misses for free, which is why FR-009 leads the design and FR-003 is
demoted to a corroborating vote. Do not wire FR-003 in on its own.

Sweep output lived in `titlepage_sweep.jsonl` (scratchpad, throwaway — not committed as a
fixture, per the test-data discipline rule). Note its `picked` column reflects
`_pick_cover_title` *before* the gutter fix below, so IMG_8043 shows `""` there.

### The bibliography gate — measured 2026-07-30, and it changes the design
User proposal: only let a title page start a book if that title is **in the Zotero
bibliography**, since a chapter would not be. Measured over the full 3493-shot cache with
`match_ris` scoring (stdlib `difflib`, **no GPU, no layout pass, whole corpus in seconds**):

| Shot | OCR title | RIS score | Outcome |
|---|---|---|---|
| IMG_8043 | *Book-AG* | **0.99** | the target — fires |
| IMG_7606 | *Book-AK* | **0.99** | independently reproduces the existing `! IMG_7606` hint |
| IMG_3108 | *Book-AI* | 0.99 | **true boundary, 3 shots late** — see "boundary placement" below |
| IMG_4078 / IMG_4089 | two section titles | 0.99 | **FALSE POSITIVES** (user-confirmed): both are sections *inside* Author-B's *Book-B*, a correctly grouped and correctly titled book. This is FR-011's containment hazard, confirmed |
| IMG_4951 | a chapter title | **0.52** | the layout route's false positive — **rejected** |
| IMG_4946 | a chapter title | 0.61 | chapter — rejected |
| IMG_5098 | *Book-AJ* | 0.47 | journal front — rejected (resolves that edge case too) |
| IMG_3656, IMG_3012, IMG_6661, IMG_6677 | — | 0.99 | suppressed by FR-005: title already equals the book's own identity |

Nine raw fires, four suppressed by the identity check, **zero chapter-opener false
positives of the edited-volume kind**, and it rediscovers both `!` hints a human had to
write by hand. It still outperforms the layout route on cost and corpus coverage, and it
makes FR-002/FR-003's layout pass a **corroborating second vote at best, not the primary
detector**. But after user verification the honest score on the three "new" fires is
**1 true (late) / 2 false** — the gate is a large improvement, not a solved problem.

### IMG_5148: the prefilter is the binding constraint, not the gate (measured 2026-07-30)
Author-C's *Book-E* (17 shots, IMG_5148–5164, incl. its whole table of contents) had
been filed under Author-P's *Book-O* since January. The boundary signals are the same
null set as IMG_8043 — 6.5 min gap, identical GPS, no ISBN (a 1965 book), no call-number
change — so this spec's premise holds. What it adds is that **the RIS gate was never the
problem here; the prefilter was.**

IMG_5148 is a combined title page + colophon, shot at 90° (4 orient passes, 416 chars):

```
### Page 1
Printed by Lim Bian Han, Government Printer, Singapore
1965
Price: $5 or 12s. 6d.
Published by Authority
...
<BOOK-E TITLE>
<its long descriptive subtitle>
<AUTHOR-C, with post-nominals>
<series/museum memoir line>, 1965
```

Measured against the shipped code:

| FR-001 clause | IMG_5148 | Verdict |
|---|---|---|
| `role == "body"` | `body` | passes |
| short text | 416 chars | passes |
| **no parsed folio** | `page_numbers: [1]` | **rejects** — phantom folio |
| **title-like first line** | `page_header()` returns `''` | **rejects** — first line is `### Page 1`; the title is on line 8 |

And yet FR-009 would have fired cleanly *if it had ever been reached*: `match_ris` scores
Book-E's cover title → its full RIS title (with subtitle)
at **0.99** (main↔main), and the full run-on line scores 0.99 too. The gate was ready; the
shot never became a candidate.

Two lessons, both narrowing the design:
- **The phantom `[1]` cuts both ways.** The decision log above cites it as the reason
  `_page_reset` is gated (243 spurious fires). Here the *same* phantom disqualifies a true
  title page. A folio clause can be a weak positive signal or a hard negative filter, not
  both — FR-014 drops it as a filter.
- **`page_header` is the wrong input to the RIS gate.** It reads the most common running
  header / first line, which on a title page is whatever the printer set at the top —
  here the colophon. The gate is cheap enough (stdlib `difflib`, whole corpus in seconds)
  to run against **every line** of a short shot, which is what FR-014 proposes. This
  costs nothing in GPU and reopens the selectivity question the sweep already answered
  once; re-measure before wiring.

Retired to `merges.txt` as `! IMG_5148` + `IMG_5148 + IMG_5165` in the meantime — the
existing Book-E merge line seeded the book one shot late, at the IMG_5165 cover.

### Boundary placement — the detector fires *after* the true boundary
The RIS gate finds the first page that *names* the book, which is not the page where the
book starts. *Book-AI* really begins at **IMG_3105**, a pile-of-spines photo
reading six books at once (*Book-AD*, *Book-D*, *Book-AA*, a fifth title, and a
truncated author byline) —
928 chars, so `detect_type` rightly calls it PAGE, not COVER. The detector instead fires
at IMG_3108, leaving IMG_3106 (narrative TOC) and IMG_3107 (Chapter 5) filed under Author-Q.
A split-at-the-fire-point rule is therefore **right about the book and wrong about three
pages**. Any shipped version needs the retroactive front-matter attachment of option 3,
or it trades one mis-attribution for a smaller one.

### Adjacent bug found while verifying (candidate for its own spec)
Author-B's *Book-B* has **two** meta shots, both correctly typed COVER:

```
IMG_4072  spine       cover_title='<publisher> Author-B <two stray cover words>'  (garbled)
IMG_4073  title page  cover_title='<Book-B title + subtitle>'                       (clean)
```

`book_title` step 1 takes the **earliest** cover by capture time (`ocr.py:1736`) — a
deliberate guard so a mid-book page misread as COVER cannot outrank the real cover. When
the user photographs the **spine first**, that guard picks the garbled read. Here
`identity` still resolved correctly, the Zotero match rescued the emitted title
(`book_96_…`), **and the user had already paid
for it by hand** — `titles.txt:12`, `IMG_4075 = <Book-B title> # cover OCR'd as the
garbled spine string`. A manual override standing in for a
ranking rule is the measure of the cost. So nothing is visibly broken —
but an uncatalogued book in this shape would be filed under its garbled spine. The user's
framing is right ("a spine should count as good as a cover if there is no cover"); the
missing piece is **ranking** among a book's meta shots — prefer the one that matches the
bibliography, or the more title-like read — rather than first-wins. Belongs to 007/008,
not to this spec's boundary detection.

Three limits, all recorded rather than papered over:
- **The folio gate the proposal pairs with is already implemented** — it is FR-001's
  `not page_numbers` clause, and it is precisely how IMG_4951 became a candidate
  (`page_numbers: []`). dots.mocr routinely misses a chapter opener's printed folio, so
  the gate is much weaker than the intuition suggests. Keep it (free), don't rely on it.
- **Containment is the residual false-positive channel** (FR-011). A chapter titled after
  its own book scores 0.99 through `sim`'s containment shortcut.
- **Recall is bounded by catalogue coverage** — 144 `BOOK`/`CHAP`/`EDBOOK` records
  against ~150 books. Uncatalogued books stay invisible (FR-010).

On Constitution VIII: this makes segmentation depend on a hint file, but `merges.txt`
already drives segmentation through `!` (024), and absent-RIS reduces to today's
behaviour. Consistent with the no-op-if-absent contract, not an exception to it.

### Exploration options, roughly by cost
0. **Bibliography-gated detector, no layout pass at all** (new front-runner — see the
   measurement above). Stdlib only, whole corpus, 5 actionable fires, 0 chapter false
   positives. Start here; treat the options below as refinements on top of it.
1. **Ship the detector as diagnosis only** (cheapest, safest). Cache `title_page` and
   surface candidates in `report.md` / a `doctor` check — "these shots look like title
   pages inside another book" — and let the user write `! IMG_x`. Keeps grouping
   untouched, turns an invisible failure into a listed one, and *builds the ground-truth
   set* that would justify step 2. This is the recommended first increment.
2. **Detector + grouper rule** (FR-005), gated on the 109-shot precision number.
3. **Corroborate with the deferred page-reset.** Instead of splitting on the title page
   alone, split only when a folio reset follows within ~3 shots (IMG_8045 restarts at
   `[1, 2]`). Higher precision, but needs the boundary to be applied *retroactively* to
   the folio-less front matter above it — a structural change to a single forward pass.
4. **Corroborate with the capture-gap outlier**, scored relative to the current run's own
   median rather than a global constant (17 s median vs. 594 s here). Cheap, no new
   passes; alone far too weak (§ table above), plausible as a second vote.
5. **Ask the model directly.** A short VLM question ("is this a book title page?") on the
   same candidate set, instead of parsing layout geometry. Untested; would need eval
   against the same 109 shots, and dots.mocr is a transcriber that historically ignores
   classification instructions (the 004 lesson) — likely a dead end, recorded so it is
   not re-proposed.
6. **Cluster-level identity instead of a forward pass.** Group shots by title evidence
   across the whole sitting rather than segmenting left-to-right. Correct in principle,
   a rewrite of 006/022 in practice; out of scope, noted so the incremental patches
   stay honest about what they are.

### Delivered alongside (not part of this spec)
While measuring, `_pick_cover_title` was found to discard a perfectly good title on any
two-page shot: the subtitle scan was vertical-only, so on IMG_8043 (title at x 996–1411,
facing series list at x 216–778) it absorbed the facing page, blew past
`_COVER_TITLE_MAXLEN`, and returned `""`. Fixed with a `_shares_column` guard requiring
horizontal overlap with the title block. This also affects real **COVER** shots
photographed as cover + facing page — it is a 008 bug, fixed there, and is why FR-003 can
rely on the picked title being usable.
