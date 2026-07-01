# EXP-002 — Hard session/GPS fences for book grouping

**Status:** ❌ Removed (superseded by title-identity grouping)
**Date:** 2026-06-14 · **Related:** feature [006](../006-book-grouping/spec.md), [011](../011-burst-session-hint/spec.md)

## Hypothesis
Books are shot in bursts (a run of photos, then a long gap, often at a different library), so
a **hard fence** — start a new book on any capture-time gap > 30 min or any GPS change — would
recover book boundaries directly from EXIF.

## What we ran
The original Pass C (`_fence`/`_discontinuous`, constants `TIME_GAP_S`/`HEADER_SIM`) on the
real 128-image `in/` batch.

## Result
**61 books for an actual 5.** Three independent failures:
- **GPS jitter** — consecutive phone shots round to coordinates ~km apart *within one session*,
  so `prev.gps != rec.gps` fired on nearly every page.
- **Time gaps split real books** — "The Enemy of All" was shot Dec 10 *and* Dec 12; any time
  fence cut it in two (or, with jitter, one-book-per-page).
- **No per-page identity** — `running_header()` returned the first *body* line, so the
  header/page-reset splitter almost never fired.

## Decision
**Removed.** Replaced by **title-identity** segmentation (feature 006): the running header
defines a book, matching headers stay together across time/GPS gaps, and call-number / page-
reset do the intra-session splits. Time + GPS were demoted to **soft, overridable** session
hints (feature 011). Result on the same 128 records: **5 books, correct boundaries**, including
the cross-session book. Removed: `_fence`, `_discontinuous`, `TIME_GAP_S`, `HEADER_SIM`.

## Lesson
EXIF time/GPS separate **sessions/locations, not books within a session** — they can only ever
be a tie-breaker, never the primary signal. Don't reintroduce a hard fence.
