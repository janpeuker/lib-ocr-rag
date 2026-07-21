# EXP-006 — FAISS / DuckDB vector backends

**Status:** ⏳ Deferred (opt-in; numpy meets the need today)
**Date:** 2026-06-14 · **Related:** feature [016](../016-rag-retrieval-engine/spec.md)

## Goal
Let the user swap the vector search engine — `--backend faiss` (`IndexFlatIP`/`IndexHNSWFlat`)
or `--backend duckdb` (VSS `array_cosine_similarity` / HNSW) — to play with real ANN indexes and
query vectors with SQL.

## Why deferred
The corpus is personal-scale. A brute-force cosine over an in-memory `float32` matrix (the
`numpy` backend) is **sub-millisecond** here, so FAISS/DuckDB buy nothing but a heavy
dependency and an extra moving part — against the bare-bones constraint (Principle III).
FAISS only earns its keep at ~10⁵⁺ vectors.

*Still true after a 7× corpus growth and the move to window-grain vectors
([028](../028-retrieval-quality/spec.md)):* the matrix is now tens of thousands of vectors
rather than ~1.2k, and the dense channel is still ~0.25 s including model load. Search
latency is dominated by loading the models, not by the matmul — an ANN index would not move
the number a user feels.

## What we shipped instead
A `VectorBackend` protocol (`build`/`query`/`save`/`load`) with **`numpy` as the default**, and
the catalog always SQLite — so any backend rebuilds from the cached `vec` BLOBs in seconds with
**no re-embed**. `load_backend()` already raises a clear "step 4" message for `faiss`/`duckdb`.

## Revisit condition
Build the lazy-imported impls when the corpus outgrows brute-force (or for experimentation):
`--backend faiss|duckdb` rebuilds from cached vectors; assert top-k parity with numpy on exact
indexes. Keep them **opt-in and lazy-imported** so the default install stays minimal. The catalog
(Layer 1) stays SQLite regardless — only the search layer swaps.
