# EXP-003 — Heavier / alternative OCR models

**Status:** ❌ Rejected (kept `dots.mocr-4bit`; olmOCR-7B remains an opt-in `--model`)
**Date:** 2026-06 · **Related:** feature [001](../001-single-page-ocr/spec.md)

## Hypothesis
A bigger or different document VLM would transcribe more faithfully than the small
`dots.mocr-4bit` default — worth the extra memory/time on a 16 GB M3.

## What we ran
`ocr.py eval` over the `test/` fixtures (`difflib` ratio, `IMG_3020` = handwriting-drop
diagnostic):

| Model (MLX id) | IMG_3018 | IMG_3020 | mean |
|---|---|---|---|
| `mlx-community/dots.mocr-4bit` (default) | 0.835 | 0.945 | **0.890** |
| `mlx-community/olmOCR-7B-0725-4bit` | 0.528 | 0.916 | 0.722 |
| `mlx-community/Qwen2.5-VL-3B-Instruct-4bit` | 0.552 | 0.891 | 0.721 |
| `mlx-community/dots.ocr-4bit` | 0.013 | 0.004 | 0.009 |

## Result
- **olmOCR-7B** (the "best-fidelity" candidate) scored **lower**: it hard-wraps lines
  mid-paragraph with trailing spaces and hyphen-break artifacts (`legal- governmental`),
  producing many small mismatches once flattened, while `dots.mocr` emits clean reflowed
  paragraphs. It's also ~5 GB and slower — tight on 16 GB.
- **dots.ocr** (non-multilingual variant) ignores the instruction prompt and emits junk —
  **not steerable**, so it can't satisfy the drop-handwriting requirement at all.
- **Qwen2.5-VL-3B** transcribes but is materially less accurate.

## Decision
**Keep `dots.mocr-4bit` as the default** — smallest *and* highest-scoring here, and it already
drops handwriting on `IMG_3020` (0.945). `olmOCR-7B` stays available via `--model` for anyone
who wants to trade speed for its fidelity on different pages; `dots.ocr`/`Qwen2.5-VL` are not
recommended. This is the evidence behind the rule "pick the smallest model whose `IMG_3020` is
acceptable" (don't assume bigger is better — measure).
