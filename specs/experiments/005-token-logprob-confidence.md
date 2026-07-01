# EXP-005 — Token-logprob read-confidence (Option A)

**Status:** ⏳ Deferred (phase 2) — shipped the cheap heuristic (Option B) instead
**Date:** 2026-06-14 · **Related:** feature [003](../003-orientation-adaptive-resolution/spec.md)

## Goal
Detect a poor OCR read (to drive the high-res retry and a report confidence hint) using the
**model's own confidence** — the mean per-token log-probability over the generation. This is the
principled signal: it catches uncertainty even when the output *looks* plausible, including the
one failure the shipped heuristic can't see.

## Why deferred
mlx-vlm's non-streaming `generate` carries only the **last** token's logprobs on the returned
`GenerationResult` (`dispatch.py:1372`); the full per-token sequence is available only via
`stream_generate`, which yields `(token, logprobs)` per step (`dispatch.py:1137`). Option A
therefore requires rewriting `ocr_image` to consume the stream and accumulate the sampled-token
logprob each step — more code plus small per-token Python overhead.

## What we shipped instead (Option B)
`text_quality(text)` — a dependency-free 0..1 heuristic (word-vowel plausibility ×
non-repetition via min(line-uniqueness, trigram-uniqueness), gated by the `finish_reason` /
yield signals). Clean reads ≈ 0.997, garble/loops 0.12–0.16; threshold `QUALITY_RETRY = 0.95`.

## Known gap Option A would close
Option B catches garble, loops, and dropout but **cannot** catch *confident-but-wrong character
substitutions* — a low-res `rn`→`m` misread that produces a real word. Only token logprobs see
that.

## Revisit condition
Escalate to A if eval shows B misses real degradations (esp. plausible-looking substitutions on
true 12 MP captures). When built, it graduates into feature 003 as the confidence source.
