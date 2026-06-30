# Feature 003 — Orientation correction + adaptive resolution + telemetry

**Status:** Delivered · **Origin:** `library-ocr@6daa5ec`, `b1fa172` · **Old ref:** §10, §11
**Constitution:** II, III, V, VI

## User Scenarios & Testing

### Primary user story
As a user who shoots pages at arbitrary angles and resolutions, I want each photo read
**upright** and **sharply enough to be correct** without paying full-resolution,
four-orientation cost on every page — most pages should be cheap, only hard pages should
pay more.

### Acceptance scenarios
1. **Given** an upright page, **when** it is OCR'd, **then** it reads in a single full
   pass at the cheap `FAST_MAX_EDGE` (1280 px) and is trusted when its text-yield score
   clears `MIN_TEXT_SCORE` — no rotation work.
2. **Given** a sideways/upside-down page, **when** the 0° read scores poorly, **then** the
   tool ranks all four orientations with cheap **probes** (`PROBE_MAX_EDGE`=1024,
   `PROBE_TOKENS`=64) and does **one** full pass at the winning orientation — ~2 full
   passes worst case, not 4.
3. **Given** a page whose first read is scrambled/looping (a too-low-resolution symptom),
   **when** `text_quality` falls below `QUALITY_RETRY` (0.95), **then** that orientation is
   re-OCR'd once at the sharper `MAX_EDGE` (1600) and the higher-quality read is kept.
4. **Given** any processed image, **when** the run finishes, **then** per-pass telemetry
   (`seconds, prompt_tokens, generation_tokens, prompt_tps, generation_tps, finish_reason`,
   plus `orient_passes`/`orient_probes`) is recorded to `instrument.jsonl` and the console
   line, with `RUNAWAY` flagged when `finish_reason == "length"`.

### Edge cases
- A sparse-but-upright page where 0° wins the probe ranking keeps the original 0° read
  (no needless re-pass).
- A runaway with **no line breaks** (one giant repeated paragraph) is still caught because
  `text_quality` also measures trigram diversity, not just line uniqueness.
- 1024 px collapses `IMG_3020` into a repetition loop, so 1024 is the *probe* resolution
  only — never a transcription resolution.

## Requirements

### Functional
- **FR-001** OCR MUST auto-correct orientation by **text yield**: trust 0° when its
  `text_score ≥ MIN_TEXT_SCORE`; otherwise probe 0/90/180/270 cheaply and do one full pass
  at the winner. Classical-CV rotation detection MUST NOT be used (it cannot resolve 180°).
- **FR-002** The chosen-orientation full pass MUST run first at `FAST_MAX_EDGE`; if
  `text_quality(text) < QUALITY_RETRY`, it MUST escalate to `MAX_EDGE` and keep the better
  read. `prep_image` MUST accept a `max_edge` parameter.
- **FR-003** `text_quality(text) -> float` MUST be pure, stdlib-only, in `[0,1]`, combining
  **word plausibility** (fraction of word tokens containing a vowel), **non-repetition**
  (`min(unique-line ratio, unique-trigram ratio)`), and the `finish_reason=="length"` /
  text-yield signals.
- **FR-004** `ocr_image` MUST return `(text, stats)` with the per-pass telemetry, threaded
  into the record and surfaced in `instrument.jsonl` + the console line.
- **FR-005** `FAST_MAX_EDGE` MUST be eval-backed: the `ocr.py eval --max-edge "…"` sweep
  downscales fixtures through the real `prep_image` path before OCR, and the floor is the
  smallest edge that does not regress the `IMG_3020` diagnostic.
- **FR-006** Tunables (`FAST_MAX_EDGE`, `MAX_EDGE`, `PROBE_MAX_EDGE`, `PROBE_TOKENS`,
  `MIN_TEXT_SCORE`, `QUALITY_RETRY`) MUST live near the labelled tuning block and be tuned
  via eval, not ad hoc (Principle VI).

### Key entities
- **Pass stats** — `{ seconds, prompt_tokens, generation_tokens, prompt_tps,
  generation_tps, finish_reason }`.
- **Quality** — the `[0,1]` `text_quality` score stored per record and shown in the report.

## Review & Acceptance Checklist
- [x] Upright fast path; probe-then-one-pass for rotated; ≤ ~2 full passes
- [x] Adaptive 1280 → 1600 only on a quality gate
- [x] `text_quality` catches garble, line-loops, and inline trigram-loops
- [x] `FAST_MAX_EDGE` floor is eval-gated, not guessed

## Decision log (non-normative)
- **Original cost.** `ocr_oriented` ran a *full* transcription at 0/90/270/180 and kept the
  letter-richest — a rotated page paid ~4× (observed ~350 s for `IMG_4808`); a dense upright
  page took 131.5 s and all mlx-vlm timing/token telemetry was discarded, hiding the
  bottleneck. Probes + telemetry fix both.
- **`--max-edge` sweep (`dots.mocr-4bit`, `test/`):** 1600 → 0.835/0.945/0.890; 1280 →
  0.835/0.945/0.890 (**adopted floor**); 1024 → 0.842/**0.469**/0.656 (`IMG_3020` collapses
  into a verbatim repetition loop). So `FAST_MAX_EDGE=1280`, `MAX_EDGE=1600`, and the quality
  gate backstops any regression.
- **Caveat:** `test/` fixtures are only 1280 px on the long edge, so the sweep proves 1280 is
  non-lossy *down to fixture resolution* and that 1024 breaks — it cannot directly prove
  1280 ≈ 1600 on true 12 MP captures; quality-gated escalation covers that residual risk.
- **`QUALITY_RETRY=0.95`:** clean fixtures score 0.997–1.000, garble/looping 0.12–0.16 — a
  wide gap, so 0.95 flags real degradation without false-flagging good pages.
- **Option A deferred.** Mean token-logprob (the principled confidence signal) needs
  `stream_generate` and per-token accumulation; deferred to phase 2. `text_quality` (B) is
  the cheap 80% solution; it cannot catch confident-but-wrong character substitutions
  (low-res `rn`→`m` producing a real word) — only logprobs can.
