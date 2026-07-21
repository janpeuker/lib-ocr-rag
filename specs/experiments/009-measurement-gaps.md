# EXP-009 — Measurement gaps: probe depth, coverage channel, truncation check

**Status:** ⏳ Deferred (known blind spots in how quality is measured)
**Date:** 2026-07-21 · **Related:** features [028](../028-retrieval-quality/spec.md),
[029](../029-quality-guards/spec.md)

## Goal
Close the three places where the project currently **cannot see** whether it is getting
better or worse. Spec 029 made quality checkable; these are the checks it could not make.

## The gaps

**1. The probe set is too small and too narrow.** Retrieval is scored against 8 probes.
That is enough to catch a regression that breaks everything and far too few to resolve a
2-point MRR difference, to cover non-English material (blocking [EXP-008](008-multilingual-retrieval-models.md)),
or to represent the *kinds* of query that fail differently — relational, proper-noun,
paraphrase, single-rare-term. Each of those needed a different fix in 028, so a probe set
that does not separate them cannot tell you which fix regressed.

**2. The coverage (AND-of-all-terms) channel is unvalidated.** It scores 0.12 against the
current probes — it fires only when every query term co-occurs on one page, which
natural-language probes rarely satisfy. It is retained because it is free when empty and
because it demonstrably rescued the query that motivated 028 (pool rank 8 where the other
channels missed entirely). But "kept on one anecdote" is not the standard the rest of the
system is held to.

**3. The truncation check is char-based and script-dependent.** `doctor` estimates tokens at
4.0 chars/token (measured over 3000 windows, not guessed). That guards `WINDOW_CHARS`
against regression, but it structurally cannot see windows whose script tokenizes far
denser — a residual 0.07 % of windows exceed the cap and the check reports clean.

## Why deferred
All three are *measurement* work whose value depends on the corpus continuing to grow, and
none of them blocks correct behaviour today. Writing good probes is also genuinely
manual: `rag.py probes --scaffold` can draw candidate sentences from the corpus, but a
verbatim sentence flatters the lexical channel, so each one has to be paraphrased into how
a person would actually ask. That is an hour of judgement, not an afternoon of code.

## What we shipped instead
- `rag.py probes --scaffold` to bootstrap a probe set from whatever corpus is present, keyed
  on **image** ids (stable) rather than book numbers (which renumber as the library grows —
  three stale probes once read as total retrieval failure).
- Per-channel eval columns, so a future probe set can attribute a change to a channel.
- `out/eval_history.jsonl` + a reranked-MRR drop warning, so decay shows as **drift** across
  runs rather than needing a golden number.

## Revisit condition
- Grow to ~30 probes spanning the four failure shapes above, including non-English pages.
  Then re-decide the coverage channel on evidence: if it helps, keep it; if it is inert or
  harmful, delete it — do **not** tune its threshold to make it look useful.
- Make the truncation check exact by tokenizing a **sample** of windows with the loaded
  embed model (only when one is already in memory, so `doctor` stays fast enough to run
  unprompted — a slow check gets skipped, which is the failure mode 029 exists to prevent).
