# EXP-001 — SigLIP/CLIP zero-shot shot-type classifier

**Status:** ❌ Rejected (revisit only if the single-model constraint is relaxed)
**Date:** 2026-06-14 · **Related:** feature [005](../005-figure-detection/spec.md), [004](../004-shot-type-detection/spec.md)
**Constitution conflict:** IV (single model per tool)

## Hypothesis
A small zero-shot image classifier (SigLIP/CLIP, ~400 MB) run *before* OCR could label
each photo (cover / imprint / page / spines-on-table / figure-heavy) more reliably than
the text heuristic — fixing the multi-spine shelf shot (IMG_4310) the text path misroutes.

## What we ran
`experiments/detector_experiment.py` over 8 hand-labelled `in/` images (Candidate **B** =
`google/siglip-base-patch16-224` zero-shot), against the text-heuristic baseline (A) and the
dots.mocr layout dual-pass (C). Measured shot-type accuracy, figure recall/precision, added
wall-time, and peak memory / swap delta.

## Result
- B nailed the hard multi-spine cover (IMG_4310 → COVER 1.00) and detected figure presence
  perfectly, but confused IMPRINT/COVER/SPREAD elsewhere → overall shot-type accuracy only
  **0.50**. Add ~370 MB resident; ~0.16 s/img + load. Did **not** push the 16 GB M3 into swap.

## Decision
**Rejected as the default.** It adds a **second resident model**, violating the single-model
constraint (Principle IV) — which the chosen Candidate C (layout dual-pass, feature 005)
honors by reusing the one loaded model. C also yields positional figure bboxes that B can't.

## Revisit condition
Reconsider only if Principle IV is relaxed, or if gated Candidate C's wall-time proves too
high in practice. B is recorded as a strong, ~270× cheaper *figure-presence* signal and a
good cover detector — its accuracy edge is real, just not worth a second model today.
