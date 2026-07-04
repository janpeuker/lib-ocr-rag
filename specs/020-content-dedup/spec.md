# Feature 020 — Content-checksum image dedup

**Status:** Delivered · **Origin:** duplicate camera-roll exports in a batch run
**Constitution:** III, V, VIII

## User Scenarios & Testing

### Primary user story
As a user whose camera roll exports sometimes contain the **same photo twice under a
name-twin filename** (`IMG_5097 (1).jpeg` alongside `IMG_5097.jpeg`), I want the byte-identical
copy dropped so it is neither OCR'd twice nor returned as a second, redundant hit in RAG search —
**but** I do not want distinct photos discarded just because they collide in name: a camera roll
that "flips a `(1)`" can put a *different* shot under `IMG_4867 (1).jpeg` than under
`IMG_4867.jpeg`, and both must be kept.

### Acceptance scenarios
1. **Given** two files with identical bytes (same `sha256`), **when** `batch` discovers images,
   **then** exactly one canonical file is OCR'd and the other is folded as an alias — never OCR'd,
   never written to `book_*.md`, so `rag.py index` yields a single chunk (no double hit).
2. **Given** two name-twin files with **different** bytes (`IMG_4867.jpeg` 1.86 MB vs
   `IMG_4867 (1).jpeg` 0.43 MB), **when** `batch` runs, **then** both are processed independently
   (distinct content is never folded on filename alone).
3. **Given** a byte-identical group, **when** the canonical is picked, **then** the clean name
   (no ` (N)` copy suffix) wins over the ` (N)` variant, so the page label and `image_path`
   escape-hatch point at the tidy original.
4. **Given** any batch, **when** dedup runs, **then** the fold map is written to `out/dedup.json`
   (canonical → dropped names) for auditability, and a one-line summary is printed to stderr.

### Edge cases
- All folds in the delivering corpus were name-twins, but identity is **content**, not name:
  byte-identical files under unrelated names fold too, and name-twins with differing bytes do not.
- Dedup runs **before** the cache check, so a stale cache for a now-dropped duplicate is simply
  ignored (never deleted); the canonical is OCR'd or served from its own cache as usual.
- Dedup is recomputed from disk every run (pure, deterministic) — it is not a hint file and has
  no allow-list; there is nothing for the user to maintain.

## Requirements

### Functional
- **FR-001** `dedup_by_content(images)` MUST group discovered images by the `sha256` of their
  bytes and, for each group of size > 1, keep one canonical file and return the rest as aliases;
  groups of size 1 pass through unchanged.
- **FR-002** Byte identity MUST be the sole fold criterion. Files whose bytes differ MUST NOT be
  folded, even when their filenames are name-twins.
- **FR-003** The canonical file MUST be chosen by preferring the name without a trailing ` (N)`
  copy suffix, then the shortest stem, then lexicographic order — deterministic across runs.
- **FR-004** Dropped duplicates MUST NOT be OCR'd and MUST NOT appear in any `book_*.md`, so the
  RAG catalog built by `rag.py index` contains each page's text once.
- **FR-005** Dedup MUST run **before** the per-image cache check and MUST NOT delete or rewrite
  any cache entry (Principle V — resumable, cache-safe).
- **FR-006** The fold map (canonical → sorted dropped names) MUST be written to `out/dedup.json`
  each run (empty object when nothing folds), and folded aliases MUST be shown in `index.md`.

### Key entities
- **Content key** — the `sha256` hexdigest of a file's bytes; the fold identity.
- **Alias** — a byte-identical duplicate dropped in favour of its group's canonical file.

## Review & Acceptance Checklist
- [x] Fold is by content checksum, never by filename
- [x] Name-twins with differing bytes both survive
- [x] Canonical prefers the clean name; deterministic
- [x] No re-OCR of dropped files; RAG has one chunk per page
- [x] Cache-safe (dedup precedes the cache check, touches no cache entry)
- [x] `out/dedup.json` + `index.md` alias note for auditability

## Decision log (non-normative)
- **Why checksum, not filename.** Measured on the delivering corpus (2953 images): 267 name-twin
  pairs, of which **196 are byte-identical** (true dupes) and **71 differ** (`IMG_4867`: 1.86 MB
  vs 0.43 MB — a camera roll that flipped a `(1)` onto a different shot). A filename rule would
  wrongly drop the 71; `sha256` splits the two cases exactly and is stdlib-only (Principle III).
- **Why dedup at discovery, not in the RAG DB.** Folding before OCR means the duplicate never
  enters `book_*.md`, so RAG is clean with no `rag.py` change *and* ~196 pages of OCR compute are
  saved — a single source of truth (Principle VIII), versus scrubbing double hits downstream.
- **Why not perceptual / near-dup hashing.** Every true dupe here is byte-identical; perceptual
  hashing would need a new dependency and risks folding genuinely different pages. Deferred as an
  experiment card if re-encoded (not byte-identical) duplicates ever appear.
- **Relation to feature 013.** Orthogonal. 013 merges *different photos of the same book* across
  sittings (human allow-list, title-invisible). 020 folds *the same photo file* appearing twice.
