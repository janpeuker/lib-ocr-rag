# Feature 011 — Capture-time + geolocation as a per-burst session hint

**Status:** Delivered · **Origin:** `library-ocr@76b43a9` (the soft-hint half of §9) · **Old ref:** §9
**Constitution:** III, V, VIII
**Related:** grouping core → [006](../006-book-grouping/spec.md)

## User Scenarios & Testing

### Primary user story
As a user who photographs one book as a **burst** (a run of shots seconds apart, then a long
gap) and whose batches will eventually span multiple libraries, I want capture **time** and
**GPS** used as a *soft* hint to separate sessions/locations — without it ever overriding the
title evidence or fragmenting a book that I shot across two days.

### Acceptance scenarios
1. **Given** EXIF capture time + GPS read per image (`read_exif`), **when** grouping runs,
   **then** a **large time gap** or a **continent-scale GPS change** starts a new book **only
   when no matching title/call number overrides it**.
2. **Given** consecutive phone shots whose GPS rounds to coordinates ~km apart within one
   session, **when** grouping runs, **then** the small delta is **ignored** (no split).
3. **Given** a book photographed on Dec 10 and again Dec 12 with a matching title, **when**
   grouped, **then** the time gap does **not** split it — title identity wins.
4. **Given** the same dense session (steady ~30–90 s cadence, no lulls), **when** grouped,
   **then** time/GPS do not separate the adjacent books within it — only call number /
   page-reset (feature 006) can.
5. **Given** a grouped book, **when** the report is written, **then** it records the capture
   timespan + duration and the GPS centroid ± radius, so a wide span/radius flags a possible
   mis-grouping (see feature 014).

### Edge cases
- GPS is never a *hard* split: phone GPS jitters km within a session, so only coarse
  (>~0.5°, ~50 km) jumps register, and even those are overridable by title/call number.
- Within one dense session, time/GPS separate **sessions/locations, not books** — the
  intra-session book boundary must come from title/call-number/page-reset evidence.

## Requirements

### Functional
- **FR-001** `read_exif(path)` MUST extract capture datetime + GPS per image and persist them
  in the cached record.
- **FR-002** Capture time and GPS MUST act only as **soft, overridable** session hints: a
  large time gap or coarse GPS jump may start a book **unless** a matching title or call number
  overrides it.
- **FR-003** Small GPS deltas (sub-coarse) MUST be ignored (jitter tolerance), so consecutive
  shots are not split on rounding noise.
- **FR-004** Time/GPS MUST NOT hard-split a book; title identity (feature 006) MUST be able to
  override them in both directions.
- **FR-005** The report MUST surface each book's capture span + duration and GPS centroid +
  radius (`gps_radius`, `book_capture`) as a mis-grouping signal.
- **FR-006** GPS MUST be retained as a forward-looking grouping signal for multi-library
  batches, even though within a single session it does not separate books.

### Key entities
- **EXIF signal** — `{ datetime, gps:{lat,lon} }` per record.
- **Capture span / GPS radius** — per-book aggregates shown in the report.

## Review & Acceptance Checklist
- [x] Time/GPS are soft hints, overridable by title/call number
- [x] GPS jitter tolerated; only coarse jumps register
- [x] A two-day book stays one book
- [x] Span + GPS radius surfaced for audit

## Decision log (non-normative)
- **Why soft, not hard (§9).** The original Pass C hard-fenced on any >30 min gap or any GPS
  change and produced **61 books for 5**: GPS jitter (3rd-decimal drift ≈ km within one
  session) fired on nearly every page, and time fences cut the cross-session "Enemy of All"
  (shot Dec 10 *and* Dec 12) in two. Demoting time/GPS to overridable hints — with title
  identity primary — gave **5 books, correct boundaries**, including that cross-session book.
- **Caveat the user described.** GPS + time separate sessions/locations, not books *within* a
  session: in the dense Jan-2 run the cadence is steady with no lulls, so they only prevent
  cross-session merges; the intra-session split relies on call number / page reset (feature 006).
- Removed constants from the old hard-fence design: `TIME_GAP_S`, `HEADER_SIM` (with
  `_fence`/`_discontinuous`).
