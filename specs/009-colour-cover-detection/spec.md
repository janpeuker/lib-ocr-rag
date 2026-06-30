# Feature 009 — Colour-assisted cover detection

**Status:** Delivered · **Origin:** `lib-ocr-rag@5f90a77` (§17) · **Old ref:** §17
**Constitution:** III, V, VI
**Related:** base shot-type → [004](../004-shot-type-detection/spec.md);
largest-font title → [008](../008-cover-title-largest-font/spec.md)

## User Scenarios & Testing

### Primary user story
As a user who photographs a colourful book cover **in front of a library shelf label** (or a
supply slip), I want it recognised as a COVER even though the label's text pushes the
character count just over the cover threshold — so the cover gets the largest-font title pass
(feature 008) instead of being filed as a stray body page.

### Acceptance scenarios
1. **Given** a "grey-zone" page (`COVER_TEXT_MAX ≤ nchars < COVER_CEILING = 700`), **when**
   `detect_type` is given the image path, **then** it computes a combined score
   `colorfulness × (1 − nchars/COVER_CEILING)` and promotes to COVER when
   `score ≥ COVER_SCORE_MIN (0.30)`.
2. **Given** IMG_9458 (cover + library label, 351 chars, colourfulness 0.645), **when**
   scored, **then** `score = 0.322 → COVER`.
3. **Given** a colourful **figure embedded in body text** (moderate colour, high char count),
   **when** scored, **then** the score is too low and it stays PAGE.
4. **Given** a white, text-only series/map page, **when** scored, **then** colourfulness is
   low and it stays PAGE.

### Edge cases
- The signal only affects images OCR'd **after** the change; an already-cached page keeps its
  stored type until its cache entry is deleted and re-run.
- A title page that is *white* and sparse scores near zero — colour cannot help there; that is
  the positional case in feature 019.

## Requirements

### Functional
- **FR-001** `_image_colorfulness(img_path)` MUST be PIL-only (a 128×128 HSV thumbnail,
  ~2 ms), returning the fraction of pixels with meaningful saturation (S>60) and brightness
  (V>30), excluding near-white and near-black pixels.
- **FR-002** `detect_type` MUST accept an optional `img_path` and, only for grey-zone pages
  (`COVER_TEXT_MAX ≤ nchars < COVER_CEILING`), apply the **multiplicative** score and promote
  to COVER at `≥ COVER_SCORE_MIN`.
- **FR-003** The form MUST trade signals off continuously — more text demands more colour — so
  colourful embedded figures do not false-promote and a real cover behind a label does.
- **FR-004** The signal MUST be cache-additive: existing caches are unaffected until re-OCR'd.
- **FR-005** Constants MUST be tunable against the grey-zone page list:
  `COVER_TEXT_MAX = 280`, `COVER_CEILING = 700`, `COVER_SCORE_MIN = 0.30`; bump `COVER_CEILING`
  only if a genuine cover cannot stay under 700 chars.

### Key entities
- **Colourfulness** — `[0,1]` fraction of meaningfully-coloured pixels.
- **Cover score** — `colorfulness × (1 − nchars/COVER_CEILING)`.

## Review & Acceptance Checklist
- [x] PIL-only, ~2 ms; no new dependency
- [x] Multiplicative trade-off; corpus-calibrated to exactly 2 (correct) promotions
- [x] Cache-additive; only new reads use it

## Decision log (non-normative)
- **Why (§17).** `detect_type` classified COVER solely on `nchars < 280`. A book shot in
  front of "Handling the Collection / Reading Room C" (IMG_9458) had the label's real text
  plus hallucinated fragments at 301 chars — just over — so it was typed PAGE, named a
  standalone book "Handling the Collection", and the §16 largest-font logic never ran.
- **Calibration (full corpus, 116 grey-zone pages — exactly 2 promotions, both correct):**

  | Image | nchars | colorfulness | score | verdict |
  |-------|--------|-------------|-------|---------|
  | IMG_9458 (cover + library label) | 351 | 0.645 | 0.322 | → COVER ✓ |
  | IMG_5537 (BL supply slip) | 313 | 0.718 | 0.397 | → COVER ✓ |
  | IMG_0130 (series page) | 317 | 0.509 | 0.278 | stays PAGE ✓ |
  | IMG_7400 (map page) | 321 | 0.416 | 0.225 | stays PAGE ✓ |
  | IMG_0142 (hallucinated list) | 492 | 0.389 | 0.115 | stays PAGE ✓ |

- **Effective required colourfulness by text count:** 280 → ≥0.50; 350 → ≥0.60; 450 → ≥0.83;
  ≥480 → effectively impossible (would need >100% colourfulness).
- **Cache note.** To get the clean fix for an already-cached page (proper COVER + §16 title
  pass): delete `out/cache/IMG_xxxx.json` and re-run; in the meantime the feature 010
  `merges.txt`/`titles.txt` entries cover it.
