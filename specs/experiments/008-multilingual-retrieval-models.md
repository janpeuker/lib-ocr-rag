# EXP-008 — Multilingual embedder + reranker

**Status:** ⏳ Deferred (measured; English-first models win on cost today)
**Date:** 2026-07-21 · **Related:** feature [028](../028-retrieval-quality/spec.md)

## Goal
Replace the English-first retrieval pair — `BAAI/bge-small-en-v1.5` (33 M, 384-dim) and
`cross-encoder/ms-marco-MiniLM-L-12-v2` (33 M) — with multilingual equivalents
(`BAAI/bge-m3`, 568 M / 1024-dim / 8192-token, and `BAAI/bge-reranker-v2-m3`, 568 M), so
non-English pages in the corpus rank on equal terms with the English ones.

## Why deferred
Two findings from the 028 investigation, in order of importance:

1. **A bigger bi-encoder was not the fix for the failure that motivated the work.** The
   driver query was *relational* (two corpus-frequent terms plus one discriminating term).
   Re-embedding the whole candidate pool at 600-char window grain still ranked the true
   answer 353/539 — because a bi-encoder scores query and passage **independently**, so the
   query collapses to its topic no matter how good the vectors are. The mechanism that fixes
   it is a **cross-encoder**, which reads the pair jointly. Scaling the embedder would have
   cost ~2.3 GB and a full re-embed to fix nothing.
2. **The bi-encoder's remaining job is recall into a candidate pool**, not final ordering.
   With four channels feeding a 200-candidate pool and a reranker on top, the dense channel
   only has to get the page *into* the pool — a much easier task than ranking it first.

So the upgrade is deferred until eval shows the **pool** (not the ordering) is the limit.

## What we shipped instead
The small pair, with the reranker doing the discriminating work at window grain. Both are
single defaults swappable by flag (`--embed-model`, `--rerank-model`) per Principle IV, so
the swap is a one-line change plus a re-embed — nothing is hardcoded to 384 dims.

## Revisit condition
Build it when **either** holds, and prove it with `rag.py eval`, not by assumption:

- Probes over non-English material score materially below English ones. The corpus already
  holds non-English books, so this is a matter of writing probes for them — see
  [EXP-009](009-measurement-gaps.md), which is the blocking prerequisite.
- `rag.py doctor` reports truncation that `WINDOW_CHARS` cannot fix. A char-based window cap
  is script-dependent: at ~4.0 chars/token for English prose a 600-char window is ~150
  tokens, but for scripts that tokenize far denser the same window can exceed a 512-token
  cap. `bge-m3`'s 8192-token context makes the problem structurally impossible.

Cost to weigh at that point: ~2.3 GB per model, a full re-embed (resumable), a 1024-dim
matrix (~4× the BLOB size — still trivial at this corpus scale), and a slower rerank pass.
Measure `reranked` MRR before/after on the **same** probe set; `out/eval_history.jsonl`
already records the before.
