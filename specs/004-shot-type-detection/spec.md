# Feature 004 — Shot-type detection (COVER / IMPRINT / PAGE / SPREAD)

**Status:** Delivered · **Origin:** `library-ocr@7af1cfb` · **Old ref:** §3
**Constitution:** III, IV, VI
**Related:** colour tie-breaker → [009](../009-colour-cover-detection/spec.md);
SPREAD→IMPRINT promotion → [019](../019-title-imprint-gaps/spec.md)

## User Scenarios & Testing

### Primary user story
As the batch pipeline, I need to know what each photo *is* — a book cover, a copyright/
imprint page, an ordinary body page, or a two-page spread — so I can route it to
bibliographic-metadata extraction or to body OCR. The default model is a transcriber, not
a classifier, so this MUST be inferred from the OCR text it reliably produces.

### Acceptance scenarios
1. **Given** the OCR text of a page, **when** `detect_type` runs, **then** it returns one
   of `COVER` · `IMPRINT` · `PAGE` · `SPREAD` · `OTHER` using **text signals only** (no
   classify prompt, no extra model).
2. **Given** a page with ISBN / "first published" / © / CIP / "Library of Congress"
   markers, **when** detected, **then** it is `IMPRINT`.
3. **Given** a sparse page (`nchars < COVER_TEXT_MAX = 280`), **when** detected, **then**
   it is `COVER`.
4. **Given** two `### Page N` headers, **when** detected, **then** it is `SPREAD`.

### Edge cases
- A multi-spine shelf shot (IMG_4310) is still misrouted by the text heuristic alone — a
  known limitation; the figure/layout signal (feature 005) is the proposed fix.
- A page just over `COVER_TEXT_MAX` is not promoted to COVER on text alone; the colour
  tie-breaker (feature 009) handles the genuine grey-zone cover.

## Requirements

### Functional
- **FR-001** `detect_type(text)` MUST classify from OCR text into
  `COVER/IMPRINT/PAGE/SPREAD/OTHER`, with **no separate classify prompt and no second
  model** (Principle IV).
- **FR-002** IMPRINT detection MUST key on an `IMPRINT_MARKERS` regex (ISBN, "first
  published", ©, CIP, "all rights reserved", "Library of Congress").
- **FR-003** COVER detection MUST fire below `COVER_TEXT_MAX` characters of text.
- **FR-004** SPREAD detection MUST fire on two `### Page N` folio headings.
- **FR-005** An `OTHER`/unparseable shot MUST be recorded in the index but contribute no
  body output.
- **FR-006** The result MUST be stored on the image record and drive Pass-B routing
  (cover/imprint → metadata extraction; page/spread → body OCR).

### Key entities
- **Shot type** — the routing label stored on each image record.

## Review & Acceptance Checklist
- [x] Text-only routing (no classify prompt, no second resident model)
- [x] IMPRINT via markers, COVER via sparsity, SPREAD via dual folio
- [x] Colour tie-breaker and SPREAD→IMPRINT promotion delegated to features 009 / 017

## Decision log (non-normative)
- **Why text-based.** `dots.mocr` is a transcriber; it ignores "classify this"
  instructions, so the type is read off the text it *does* produce. IMPRINT detection is
  solid (ISBN); robust COVER/spine detection is the open follow-up.
- **Rejected: SigLIP/CLIP zero-shot classifier.** Accurate on covers (IMG_4310 → COVER 1.00)
  but adds a second resident model against Principle IV; recorded as a revisit-if-relaxed
  option in feature 005.
- The pure character-count COVER rule was later supplemented by a colour signal for
  grey-zone pages — split into its own story (feature 009) because it is a distinct,
  separately-tunable heuristic.
