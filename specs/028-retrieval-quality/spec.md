# Feature 028 — Retrieval quality at 100+ books: windows · channels · rerank · diversity

**Status:** Delivered · **Constitution:** I, II, III, IV, VI, VII
**Related:** engine → [016](../016-rag-retrieval-engine/spec.md); CLI →
[017](../017-rag-cli/spec.md); MCP/Skill → [018](../018-rag-mcp-integration/spec.md);
the coverage half of the same investigation → [027](../027-meta-shot-text-recovery/spec.md)

Feature 016 was designed and tuned against **21 books / 1163 chunks**. The library is
now **126 books / 9499 chunks** — a 7× growth against an unchanged 50-candidate pool,
unchanged page-grain embedding, and no reranking or result shaping. Three concrete
user-reported failures each turned out to have a **different** cause, which is why this
story changes four things rather than swapping the embedding model.

| Query | Was | Diagnosed cause |
|---|---|---|
| `Wilhelm Braun` | target page absent | Two causes: the page was not in the catalog at all (→ [027](../027-meta-shot-text-recovery/spec.md)), and the page reads "Wilhem Braun", which exact FTS5 tokens cannot match. |
| `weavers lenders` | target absent from top-50 | The dense channel returns the *topic* and drops "lenders"; the OR-of-terms lexical channel lets two corpus-frequent terms swamp the one discriminating term. |
| `quernstone` | 5× one book, better book absent | No per-book cap and no near-duplicate suppression; both books were in the pool all along. |

## User Scenarios & Testing

### Primary user story
As a researcher querying a 126-book library, I need the page that actually *joins* my
query terms to outrank the pages that merely discuss the topic — and I need the top-k to
span the library rather than one densely-matching book.

### Acceptance scenarios
1. **Given** a page chunk, **when** it is indexed, **then** it is additionally split into
   ~600-char **windows**, and the windows — not the page — carry the vectors; a page
   scores as the **max** over its windows.
2. **Given** a multi-term query, **when** retrieval runs, **then** a **coverage** channel
   requiring *every* term contributes alongside the OR channel, and a **fuzzy** channel
   expands each term against the indexed vocabulary so an OCR'd near-spelling still matches.
3. **Given** the fused candidates, **when** reranking is enabled, **then** a small
   cross-encoder re-scores them at window grain and the page takes its best window's score.
4. **Given** the reranked list, **when** results are returned, **then** no page appears
   twice, no book takes more than `--per-book` slots, and a near-duplicate of an
   already-shown passage is dropped.
5. **Given** `rag.py eval`, **when** it runs, **then** every channel gets its own column
   so each addition here is justified by measurement, not argument.

### Edge cases
- The coverage channel returns nothing when no page holds all terms — by design; the OR
  channel keeps recall, so this one can afford to be strict.
- The fuzzy channel must contribute nothing when its expansions add no new terms,
  otherwise it just re-weights the OR channel under a second name.
- A cross-encoder truncates its input exactly as a bi-encoder does; reranking 2000-char
  pages would silently cut their tails, which is why reranking is at window grain.
- Duplicate-book twins split at *different* lengths, so Jaccard understates their overlap
  (measured 0.78 for a near-identical pair). Containment is the correct measure.
- A pre-028 catalog has chunk vectors and no windows; `index` must migrate it without a
  manual `--force`.

## Requirements

### Functional
- **FR-001** The catalog MUST carry a `windows(id, chunk_id, ord, text, content_sha, vec,
  vec_model)` table. The **chunk remains the citation and return unit**; the window is the
  dense-retrieval unit only. Windows MUST be rewritten (with `vec` NULL) whenever their
  chunk's `content_sha` changes, and deleted with their chunk.
- **FR-002** Window text MUST carry the same citation header as the chunk
  (`build_embed_text`), so an author/title query still matches at window grain.
- **FR-003** Embedding MUST operate on `windows` and keep the resumability contract of
  016 FR-003 unchanged (per-batch commit; re-embed only NULL/`vec_model`-mismatched rows).
  A catalog with chunk vectors but no windows MUST self-migrate on the next `index`.
- **FR-004** The dense channel MUST rank windows and **max-pool** to chunks — never
  average, which would re-dilute exactly what windows fix.
- **FR-005** Retrieval MUST add two lexical channels beside the existing OR/BM25 one:
  a **coverage** channel (`AND` of all query terms, skipped for single-term queries) and a
  **fuzzy** channel (query terms expanded against the FTS5 vocabulary). All channels MUST
  fuse through the existing RRF (Principle: rank-based, no score normalisation).
- **FR-006** Fuzzy expansion MUST use SQLite's built-in `fts5vocab` and stdlib `difflib`
  — no new dependency, and no second index to maintain (Principle III).
- **FR-007** A cross-encoder reranker MUST re-score the fused pool at **window** grain,
  max-pooling to the chunk, bounded by `RERANK_WINDOWS`. Candidates whose windows fall
  outside the cap MUST keep their fused order below the reranked ones, never be dropped.
  `DEFAULT_RERANK_MODEL` is the single default, swappable via `--rerank-model`
  (Principle IV); `--no-rerank` restores the pure-RRF path.
- **FR-008** Results MUST be shaped before truncation to `k`: at most one result per
  page, at most `--per-book` per book (0 = off), and near-duplicates dropped by token
  **containment** ≥ `DUP_RATIO`.
- **FR-009** `rag.py eval` MUST report `dense | lexical | coverage | fuzzy | hybrid |
  reranked` separately (Principle VI).
- **FR-010** `rag.py books` MUST list the catalog's books (file, title, author, year,
  page/chunk counts) so an agent can scope `--book` without opening a book file.
- **FR-011** All of the above MUST run offline on MPS after a one-time reranker download
  (Principles I, II).

### Key entities
- **Window row** — `{ id, chunk_id, ord, text, content_sha, vec, vec_model }`.
- **Channel** — a best-first `[(chunk_id, score)]` list; four of them now feed RRF.

## Review & Acceptance Checklist
- [x] 33 872 windows over 9499 chunks; all vectorized; index resumable and self-migrating
- [x] `quernstone` → Book A #1–#2 (was 5× Book B, Book A absent); Book B capped at 3
- [x] `weavers lenders` → the page reading "the weavers also acted as lenders to
      travelling merchants" is #1 (was absent from top-50)
- [x] `Wilhelm Braun` → the two pages spelling it exactly rank #1–#2; the "Wilhem Braun" page
      (recovered by 027, found via the fuzzy channel) follows at #6
- [x] Duplicate-book twins (`IMG_2001`/`IMG_2002 p.178`) no longer both returned
- [x] eval: reranked **R@1 .75 / R@3 .88 / MRR .81** vs hybrid .62/.75/.69 (8 probes)
- [x] `RERANK_WINDOWS` 250 vs 500 scores identically → 250 chosen, halving latency

## Decision log (non-normative)
- **Why not simply swap the embedding model.** The obvious move — a bigger/multilingual
  bi-encoder — was measured and rejected as the *first* step. Re-embedding the candidate
  pool at 600-char windows still ranked the "weavers lenders" target 353/539: the
  failure is that a bi-encoder scores query and passage independently, so a relational
  query collapses to its topic. A cross-encoder is the mechanism that fixes it. The
  embedder swap stays deferred until eval shows the *pool* (not the ordering) is the limit.
- **Why Model2Vec / distilled static embeddings were rejected.** They are speed
  distillations — strictly weaker semantically than `bge-small`, which is already the
  weak link. They would trade the wrong axis.
- **Why the reranker is small and English-only.** `ms-marco-MiniLM-L-12-v2` is 33 M
  params and only re-orders a pool the cheap channels already found, so its cost is
  bounded and its failure mode is graceful (`--no-rerank`). The corpus does hold German
  and Portuguese material; a multilingual reranker (`bge-reranker-v2-m3`, 568 M) is the
  documented upgrade path if eval ever shows non-English probes losing.
- **Why windows rather than smaller chunks.** Shrinking the chunk would shrink the
  *citation*, and a citation you cannot locate on the page is worth less than a slightly
  diluted one. Splitting the retrieval unit away from the citation unit gets the precision
  without paying that cost, and incidentally ends the silent 512-token truncation that hit
  8 % of chunks.
- **Why containment, not Jaccard, for near-duplicates.** Measured directly on the
  unmerged duplicate pair: Jaccard 0.78 (below any usable threshold) because the twins
  split at 1614 vs 2000 chars, while containment is ~0.98. Jaccard punishes length
  differences that are irrelevant to "does this add anything new".
- **Why one result per page.** `get-page` already exists to expand a hit, so a second
  chunk of the same page under the same citation buys nothing and costs a slot.
- **Coverage channel is unvalidated by the probe set.** It scores 0.12 on the 8 probes —
  natural-language probes rarely have every term co-occurring. It is kept because it is
  free when empty and because it demonstrably rescued the motivating query (`weavers
  lenders`, pool rank 8). If a future probe set shows it *hurting*, drop it; do not tune it.
- **Stale probes found.** Three of the five original probes matched on `book_13`/`book_18`/
  `book_12`, which now name entirely different books — book numbers shift as the library
  grows. They read as total retrieval failure until corrected. **Probe matchers should
  prefer `image` over `book`**, which is stable.
