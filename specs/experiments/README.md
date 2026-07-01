# Experiment cards (negative results + deferred work)

The `experiments/` **code** directory is gitignored (throwaway harnesses, hand-label
files, scratch output — never committed as fixtures, per the constitution). These
**cards** preserve what those experiments *taught us* so the learning isn't lost with
the code: features we tried and **rejected/removed**, and work we deliberately
**deferred** to the future.

A card is lighter than a feature `spec.md`: a hypothesis, what we ran, the result, and
the decision (or the revisit condition). When a deferred card is eventually built, it
graduates into a numbered feature spec under `specs/` and the card is updated to point
at it.

| # | Experiment | Status | Related spec |
|---|------------|--------|--------------|
| 001 | [SigLIP/CLIP zero-shot shot-type classifier](001-siglip-shot-classifier.md) | ❌ Rejected | [005](../005-figure-detection/spec.md) |
| 002 | [Hard session/GPS fences for grouping](002-hard-session-gps-fences.md) | ❌ Removed (superseded) | [006](../006-book-grouping/spec.md), [011](../011-burst-session-hint/spec.md) |
| 003 | [Heavier OCR models (olmOCR-7B, dots.ocr, Qwen2.5-VL)](003-heavier-ocr-models.md) | ❌ Rejected | [001](../001-single-page-ocr/spec.md) |
| 004 | [In-prompt figure placeholder](004-in-prompt-figure-placeholder.md) | ❌ Removed (0 recall) | [005](../005-figure-detection/spec.md) |
| 005 | [Token-logprob read-confidence (Option A)](005-token-logprob-confidence.md) | ⏳ Deferred | [003](../003-orientation-adaptive-resolution/spec.md) |
| 006 | [FAISS / DuckDB vector backends](006-faiss-duckdb-backends.md) | ⏳ Deferred (opt-in) | [016](../016-rag-retrieval-engine/spec.md) |
| 007 | [Drop text printed inside a figure](007-figure-interior-text-removal.md) | ⏳ Deferred | [005](../005-figure-detection/spec.md) |

Legend: ❌ tried and not adopted (negative result / removed feature) · ⏳ understood,
intentionally not built yet.

See also feature [019 (title/imprint gaps)](../019-title-imprint-gaps/spec.md), whose
**Gap B** is a proposed-but-unbuilt detector tracked as a full spec rather than a card.
