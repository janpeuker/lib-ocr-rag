# EXP-004 — In-prompt figure placeholder

**Status:** ❌ Removed as a *relied-upon* mechanism (line kept as a hint; real detection is the layout pass)
**Date:** 2026-06-14 · **Related:** feature [005](../005-figure-detection/spec.md)

## Hypothesis
We could get figure/map regions flagged for free by *instructing the transcription prompt* to
emit a placeholder instead of transcribing a figure's interior:

```
For figures, maps, or diagrams: do NOT transcribe the interior … emit exactly one
placeholder line:  > **[Figure — <label and caption>]**
```

## What we ran
Measured figure recall/precision of the text heuristic (Candidate A, transcription prompt only)
against hand-labelled figure pages in `experiments/detector_experiment.py`, including the
two-map spread IMG_4406.

## Result
**Figure recall 0.0.** `dots.mocr` in transcription mode **ignores** the placeholder
instruction and transcribes the figure interior as ordinary text anyway (and even
false-positived elsewhere). The instruction is not honoured.

## Decision
Stop relying on the prompt line for detection. Figures are detected by a **separate
layout-only pass** (`LAYOUT_PROMPT`, bbox+category JSON, gated to figure-suspect pages — feature
005); the caption text still comes from the transcription pass. The placeholder wording remains
in `PROMPT` only as a harmless steer, **never** as the detection path.

## Lesson
A transcriber-mode VLM follows "transcribe" and drops "classify/skip this region" instructions —
structural/layout decisions need the model's *layout* mode, not a plea inside the text prompt.
