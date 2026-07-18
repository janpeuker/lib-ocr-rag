# Feature 022 — Capture-order grouping

**Status:** Delivered · **Origin:** interleaved `(1)` name-twin rolls shattering books into singletons
**Constitution:** III, V, VIII

## User Scenarios & Testing

### Primary user story
As a user whose camera roll contains **name-twin files from two different sittings**
(`IMG_5043 (1).jpeg` from a German dissertation shot in June alongside `IMG_5043.jpeg`
from *Indonesian Sea Nomads* shot in May), I want shots grouped in the order they were
**captured**, not the order their filenames happen to sort — because filename order
alternates between the two sittings, every adjacent pair trips the 6-hour session-gap
fence, and both books explode into one-page singleton "books" (287 of 427 books in the
delivering corpus were such singletons).

### Acceptance scenarios
1. **Given** two sittings whose shots interleave in filename order (`a (1), a, b (1), b, …`),
   **when** `batch` groups them, **then** each sitting's shots are contiguous in grouping
   order and chain into their own book — no singleton explosion.
2. **Given** a shot without EXIF capture time (a screenshot, an exported UUID-named file),
   **when** grouping order is computed, **then** it inherits the previous filename-order
   shot's time, staying adjacent to its filename neighbours instead of jumping to either
   end of the corpus.
3. **Given** shots with identical capture times (burst shots), **when** ordered, **then**
   filename order breaks the tie, so ordering stays deterministic across runs.
4. **Given** a corpus with no EXIF at all, **when** grouping runs, **then** the order
   degrades to plain filename order — identical to the pre-022 behaviour.

### Edge cases
- Ordering is applied **inside** `group_images()`, honouring its long-standing docstring
  ("segment the shots *in capture order*") for every caller; the discovery glob stays
  filename-sorted for stable progress logs and cache iteration.
- `_sort_records` already ordered each *merged book's* pages by capture time (feature 013);
  022 extends the same principle to the grouping input. Emitted page order is unchanged
  for books that were already grouped correctly.
- Leading shots with no EXIF (nothing to inherit) sort before all timed shots, preserving
  their mutual filename order.

## Requirements

### Functional
- **FR-001** `group_images()` MUST process records in capture order: primary key EXIF
  `DateTimeOriginal` (already cached as `datetime`), tiebreak original filename order.
- **FR-002** A record with no capture time MUST inherit the nearest preceding record's
  time in filename order (carry-forward), so EXIF-less shots keep their filename position.
- **FR-003** The reordering MUST be a pure, deterministic function of the cached records —
  no new inference, no cache reads or writes (Principle V).
- **FR-004** With no EXIF data present, grouping order MUST equal filename order.

### Key entities
- **Capture order** — records sorted by `(inherited capture time, original index)`.

## Review & Acceptance Checklist
- [x] Interleaved two-sitting corpus groups into two books, not singletons
- [x] EXIF-less shots stay adjacent to filename neighbours
- [x] Deterministic; degrades to filename order without EXIF
- [x] Pure over cached records; cache untouched

## Decision log (non-normative)
- **Why this was a bug, not a feature gap.** The session fences (`SESSION_GAP_S`,
  `GPS_SESSION_DEG`, feature 011) and the `group_images` docstring both assume capture
  order; the pipeline fed filename order (`sorted(in_dir.glob(...))`). The mismatch was
  invisible until content-dedup (020) started keeping name-twins with different bytes —
  which is correct — and those twins interleaved two sittings.
- **Measured effect on the delivering corpus:** 427 books (287 singletons) when the bug
  was diagnosed; 316 after manual `merges.txt` triage of the worst clusters; **125 books
  (3 singletons)** once grouping ran in capture order (together with 023's echo guard and
  024's split hint). Every human-confirmed `merges.txt` book survived; most twin-roll
  merge lines became no-ops (the grouper now finds those joins itself) and are kept as
  documentation and as a regression guard.
- **Known interaction, resolved by 024:** capture order removes the *spurious* session
  gaps that had accidentally been splitting a coverless book from a cover-anchored
  predecessor in the same sitting (Chou after Tribal Communities) and had kept two
  fuzzy-similar titles apart (Chou's "Indonesian Sea Nomads" vs Sopher's "THE SEA
  NOMADS" clears the 0.6 header bar). Both need human boundaries: the `! IMG_x` split
  hint and a `+=` shot-move respectively.
- **Why carry-forward for missing EXIF** rather than sorting unknowns to one end: a
  screenshot or export sits between camera shots of the same sitting in filename order;
  inheriting the neighbour's time keeps it in that sitting. Sorting unknowns to
  `datetime.min` would pile them at the corpus start and regroup them together.
