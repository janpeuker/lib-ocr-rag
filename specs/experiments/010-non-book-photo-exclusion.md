# EXP-010 — Excluding a photo that isn't a book page

**Status:** ⏳ Deferred (rare; the cost is one spurious catalog entry)
**Date:** 2026-07-21 · **Related:** features [006](../006-book-grouping/spec.md),
[029](../029-quality-guards/spec.md)

## Goal
Give the pipeline a way to be told "this photo is not a book page", for the occasional shot
in `in/` that is a screenshot, a slide, a poster, or a phone-UI capture. Today every photo
becomes a page, and a text-bearing non-book shot with no neighbours becomes a whole **book**
whose title is whatever line the OCR read largest.

## Why it matters (and why it's small)
`rag.py doctor`'s `empty-books` check surfaces the symptom: a book file that holds no
searchable text. Observed instances were (a) a genuine book cover photographed alone, with
no pages — legitimately empty, nothing to fix — and (b) a screenshot of a presentation
slide, which is the real false positive. Both are cheap: they add a catalog entry that
returns nothing, not a wrong answer. That is why this is deferred rather than built.

## Options considered
- **A `skip.txt` hint in `in/`** — mirrors the existing `merges.txt` / `titles.txt` /
  `*.ris` contract exactly: optional, no-op if absent, enriches only, never touches the
  cache (Principle VIII). Cheapest to build and consistent with everything else.
- **Detect it** — a screenshot has tell-tale properties (device aspect ratio, UI chrome,
  EXIF with no camera model). Tempting, but it is a new classifier on the OCR path for a
  rare case, and a false positive **silently deletes a real page** — a far worse failure
  than the one it fixes. Rejected on that asymmetry.
- **Do nothing; let the user delete the file.** What we do today. The photo is the user's
  data, and removing it from `in/` is a one-line shell command they can reason about.

## Revisit condition
Build the `skip.txt` variant if non-book shots become common enough to be annoying — say,
`empty-books` regularly lists entries that are neither a cover-only book nor a real one.
Until then the manual path is honest and the `doctor` warning makes the situation visible
rather than silent, which was the actual gap.

**Do not** build the detector without a way to review what it dropped; if it is ever built,
it must mark shots as skipped in `out/coverage.json` with a named reason, never discard them
silently — the whole point of the coverage audit is that nothing disappears unexplained.
