# Feature 005 — Figure / map detection (gated layout dual-pass)

**Status:** Delivered · **Origin:** `library-ocr@124454c`, `259facf` · **Old ref:** §8 / §8.4
**Constitution:** III, IV, V, VI

## User Scenarios & Testing

### Primary user story
As a user OCR'ing pages dominated by maps/figures/diagrams, I do not want the figure's
interior transcribed as garbled body text; I want each figure region **flagged** with a
caption-only placeholder, paying the extra cost only on pages that actually contain a figure.

### Acceptance scenarios
1. **Given** a body page whose text contains a standalone `Map`/`Figure`/`Table`/`Plate`
   caption, **when** the page is figure-suspect, **then** a second **layout-only** pass
   (`LAYOUT_PROMPT`, bbox + category JSON, **no** free text) confirms the region and the
   caption becomes a `> **[Figure — <label and caption>]**` placeholder.
2. **Given** a plain text page with no figure cue, **when** processed, **then** no layout
   pass runs (the gate keeps it cheap).
3. **Given** a 128-image batch, **when** complete, **then** figure detection adds only
   ~+3% wall-time batch-wide (gated to the few figure-suspect pages, ~5/128).
4. **Given** the layout pass output, **when** placeholders are built, **then** layout text
   NEVER enters the transcription body (it would reintroduce the dropped marginalia).

### Edge cases
- The in-prompt figure-placeholder instruction in the transcription `PROMPT` is **not
  reliable** (measured 0 recall) — figure detection MUST come from the layout detector, not
  the transcription prompt.
- A multi-spine shelf shot reads as figure-heavy (several `Picture` boxes, sparse text) — a
  usable cue that it is not a plain `PAGE`.

## Requirements

### Functional
- **FR-001** Figures MUST be detected by a **separate layout-only pass** that returns
  bbox+category JSON over the dots layout categories, kept in `prompts.py` as `LAYOUT_PROMPT`
  and NEVER merged into the transcription `PROMPT`.
- **FR-002** The layout pass MUST reuse the already-loaded model (no new resident weights —
  Principle IV); `--model` still selects the one model.
- **FR-003** The layout pass MUST be **gated**: run only on figure-suspect images (low text
  for pixel area, or a content page whose heuristic emitted no placeholder), not every page.
- **FR-004** `Picture`/`Table` regions MUST be rendered as `> **[Figure — …]**`
  placeholders; the caption text MUST come from the transcription pass, never from layout.
- **FR-005** Detection output MUST NOT re-enter the transcription text under any path.
- **FR-006** The record MUST carry a `figures` field; `PROMPT_VERSION` was bumped to `4`
  for this schema change (cache invalidation — Principle V).

### Key entities
- **Layout element** — `{ bbox:[x1,y1,x2,y2], category }` (text suppressed).
- **Figure placeholder** — the blockquote line emitted into the Markdown body.

## Review & Acceptance Checklist
- [x] Layout detector is separate from transcription; text never crosses over
- [x] Gated (~+3% batch-wide), reuses the single model
- [x] Caption from text pass; bbox from layout pass

## Decision log (non-normative)
- **Experiment (`experiments/detector_experiment.py`, 8 hand-labelled `in/` images,
  throwaway/gitignored; hard cases IMG_4310 multi-spine, IMG_4406 two-map spread):**

  | Detector | shot-type acc | figure recall | figure precision | added time/img | swap Δ |
  |---|---|---|---|---|---|
  | A text heuristic | 0.63 | 0.0 | 0.0 | baseline | 0 |
  | C layout dual-pass | 0.25¹ | 1.0 | 1.0 | +43.6 s (ungated)² | 0 MB |
  | B SigLIP zero-shot | 0.50 | 1.0 | 1.0 | +0.16 s + load | 0 MB |

  ¹ C can't read text (layout-only), so it's a region detector, not a router — that low
  shot-type score is by design. ² On a *triggered* image; gated to ~5/128 ⇒ ≈+1.7 s/img
  (~+3%), under the "<50%" bar.
- **Findings.** The transcription `PROMPT`'s figure line does not work (A recall 0). Both B
  and C detect figure presence perfectly here; C also yields positional bboxes (multiple
  figures/page). Neither pushed the 16 GB M3 into swap.
- **Decision: adopt C (gated layout dual-pass).** It honors the single-model constraint
  (Principle IV) which **B violates** (a second resident model). Keep the text heuristic
  (feature 004) for COVER/IMPRINT/SPREAD/PAGE routing. SigLIP (B) recorded as a ~270× cheaper
  alternative to revisit only if the single-model constraint is relaxed.
- **Open follow-up:** the layout pass returns boxes only, not the text inside them — so words
  printed *inside* a map (place names, legend labels) still leak into body text. Removing
  them needs dots' box-with-text mode + a positional drop; not done.
