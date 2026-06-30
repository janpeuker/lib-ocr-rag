# ARCHITECTURE

How this package is built and *why*. For task-level guidance see `CLAUDE.md`; for the
per-feature requirements, decision logs, and superseded designs see the spec-kit specs
under `specs/` (`specs/README.md` is the index; `.specify/memory/constitution.md` the
governing principles). This document is the durable "shape of the system" overview.

---

## 1. What the package is

Two small, independent command-line tools that share one disk directory (`out/`):

```
                 photos                       Markdown                 questions
                   │                             │                        │
            ┌──────▼───────┐              ┌──────▼───────┐         ┌───────▼───────┐
   in/*.jpeg│   ocr.py     │  out/book_*.md│    rag.py    │ out/rag.db│  Claude /     │
  ──────────►   (VLM OCR)  ├──────────────►  (retrieval) ├──────────►  MCP / CLI    │
            └──────────────┘              └──────────────┘         └───────────────┘
              writes out/                  reads out/, builds        search / get-page
              book_*.md + .txt             out/rag.db (SQLite)       returns citations
```

- **`ocr.py`** turns book-page photos into structured Markdown + plain text, keeping
  only *printed* text and dropping handwritten annotations.
- **`rag.py`** builds an offline hybrid-search catalog over that Markdown so an agent
  can look up a citation without loading whole books into context.

They are deliberately **decoupled**: `rag.py` only ever reads files `ocr.py` wrote,
and has its own dependency set (`requirements-rag.txt` vs `requirements.txt`). You can
run, version, and break one without touching the other. The contract between them is
*files on disk*, not a Python API.

### Governing constraints (the "why" behind almost everything)

| Constraint | Consequence in the code |
|---|---|
| **100% offline, no APIs** | Models download once from HuggingFace, then run under `HF_HUB_OFFLINE=1`. No cloud SDKs, no network at inference time. |
| **Apple Silicon, no CUDA** | Engine is `mlx-vlm` (MLX/Metal). `torch` is present *only* because the Qwen2-VL processor imports it — CPU/MPS wheels. |
| **16 GB unified memory (M3)** | Image downscaling, 4-bit quantized models, single model instance reused across a batch, brute-force vector math instead of a heavyweight index. |
| **Bare-bones, minimal deps** | No web framework, no config system, no vector DB. SQLite + numpy do the retrieval. Two single-file tools. |
| **Resumable batch work** | Every expensive step checkpoints to disk and resumes by default; `--force` recomputes. A run killed by exhausted credits picks up where it stopped. |

---

## 2. `ocr.py` — the OCR pipeline

### 2.1 Why a prompt-steerable VLM, not classical OCR

Tesseract / EasyOCR / PaddleOCR / Surya transcribe *every* glyph and have no
instruction channel — they cannot be told "keep printed text, drop the handwritten
marginalia." The core requirement here (clean printed text out of annotated library
books) needs an *instruction*, so the engine is a **document VLM** steered by a single
prompt in `prompts.py`. Default model: `mlx-community/dots.mocr-4bit` (~3.3 GB, 4-bit).

Model choice is an eval decision, not a hardcode: `python ocr.py eval` scores candidate
models against `test/*` ground truth, and `IMG_3020` (a heavily annotated page) is the
diagnostic — a high score there means handwriting is being dropped. **Rule: pick the
smallest model whose `IMG_3020` score is acceptable.** Swap with `--model`; never
hardcode a second path.

### 2.2 Per-image flow

```
   in/IMG_xxxx.jpeg
        │
        ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │ process_image()                                                   │
   │                                                                   │
   │  1. EXIF read (capture time, GPS) ──────────► metadata for        │
   │                                               grouping later      │
   │  2. ocr_oriented():                                               │
   │        ┌─ full pass @ 0°  (FAST_MAX_EDGE = 1280) ─► text_score    │
   │        │   high enough?  ──yes──► keep                            │
   │        └─ low? ─► cheap rotation PROBES (1024px, 64 tok)          │
   │                   rank 0/90/180/270 by letter yield               │
   │                   ─► one FULL pass at the winning orientation     │
   │  3. quality gate: text_quality < 0.95  ─► re-OCR sharper          │
   │        (escalate FAST_MAX_EDGE → MAX_EDGE = 1600), keep better    │
   │  4. detect_type() from the text (COVER / IMPRINT / PAGE / SPREAD) │
   │  5. parse running header, page numbers, metadata, figures         │
   └───────────────────────────────┬───────────────────────────────────┘
                                    ▼
                         save_cache(out/cache/IMG_xxxx.json)
```

Three cost-control ideas worth calling out:

- **Adaptive resolution.** 12 MP phone photos are downscaled (long edge capped) before
  inference to bound VLM cost and memory. First pass runs at `FAST_MAX_EDGE=1280`
  (eval floor: holds `IMG_3020` at 0.945, same as 1600); a quality gate escalates to
  `MAX_EDGE=1600` only for pages that read poorly. `1024` collapses into a repetition
  loop, so it is the *probe* resolution, never the transcription one.
- **Orientation by probe, not by brute force.** A rotated page reads badly at 0°.
  Rather than pay four full transcriptions, run cheap low-res 64-token *probes* to rank
  the four orientations, then do one full pass at the winner — ~2 full passes worst
  case instead of 4.
- **Read-quality heuristic** (`text_quality`, 0..1, stdlib only) detects the scrambled /
  looping output a too-low resolution produces (clean reads ≈ 0.997, garble < 0.16).
  This is what drives the sharper-retry decision — no second model, no extra deps.

### 2.3 Shot-type detection is text-based, on purpose

`dots.mocr` is a transcriber, not a classifier — it ignores "classify this" instructions
and just OCRs. So `detect_type()` infers COVER / IMPRINT / PAGE / SPREAD from the text the
model *does* produce (e.g. an imprint page has ISBN / copyright lines). Zero new deps,
fully offline. A small zero-shot image classifier (SigLIP/CLIP) was considered and
deferred — it would add weight against the bare-bones constraint for a marginal gain.

### 2.4 Grouping into books is a *pure pass*, never inference

```
   [ cached records, capture-ordered ]
            │
            ▼
   group_images():  start a new book when the *title identity* breaks
            │         ├─ running header no longer matches  (HEADER_MATCH = 0.6)
            │         ├─ page numbers reset
            │         ├─ a >6h capture-time gap with no matching title, or
            │         └─ a coarse (>0.5°, ~50 km) GPS jump
            ▼
   book_01_*.md, book_02_*.md, ...   (+ .txt, report.md, index.md)
```

Grouping reads only cached records — it costs no GPU time, so the per-book files and the
`report.md` are **re-emitted after every image** during a batch, making `out/` auditable
while a long run is still going. The driving heuristic is *title identity over session
fences*: one book may span days/sessions, so a matching running header overrides a time
or GPS gap (and vice-versa).

### 2.5 Resumability & the cache contract

```
   out/cache/IMG_xxxx.json   ── one record per image; the unit of resumability
        keys incl:  model, prompt_version, type, raw_md, ocr_text, quality, …
```

`load_cache` returns a record **only if `model` and `prompt_version` still match** —
change the model or edit the prompt and those pages recompute automatically, everything
else is reused. `python ocr.py batch` is therefore resume-by-default and cache-aware:
re-running after adding photos to `in/` only OCRs the new ones. `--force` ignores the
cache. This is also the re-index path for `rag.py` (chunking keys off `content_sha`).

A thin `run_overnight.sh` wraps `batch` then `rag.py index` under `caffeinate`, retrying
each stage (each retry resumes from cache) — long unattended runs survive sleep and
transient crashes.

### 2.6 One vendored workaround — the detokenizer UTF-8 patch

`mlx-vlm==0.6.3`'s streaming BPE detokenizer flushes its buffer with a *strict*
`.decode("utf-8")`, so a single stray byte the model emits mid-word (e.g.
`b' cont\x98rovert'`) raises `UnicodeDecodeError` and kills the whole page — and, without
a guard, the whole batch. `load_model()` applies an idempotent monkeypatch
(`_patch_detokenizer_utf8`) that makes the flush tolerant (`errors="ignore"`, matching the
library's own `finalize()`), so the bad byte is dropped and the ASCII text is recovered
intact. `cmd_batch` additionally wraps each page in try/except → `out/failures.jsonl`, so
any *other* unrecoverable page can't sink an overnight run. **Both are flagged for revisit
on every `mlx-vlm` bump** (`CLAUDE.md`, `specs/015-resumability-resilience/`).

---

## 3. `rag.py` — offline hybrid retrieval

### 3.1 Goal & non-goals

Goal: given a natural-language question, return the *handful of pages* that answer it,
with citations — so an agent spends hundreds of tokens, not a whole book. Non-goals: no
server, no re-ranking model, no GPU at query time, no cloud vector DB.

### 3.2 The catalog: SQLite as the single source of truth

```
   out/rag.db  (one SQLite file)
   ┌──────────────────────────────────────────────────────────────┐
   │ chunks                                                         │
   │   id PK, book_file, book_title, author, year, image, page,    │
   │   text, embed_text, content_sha,                              │
   │   vec BLOB  ◄── float32[384] little-endian, unit-normalized   │
   │   vec_model ◄── cache key: which model produced vec           │
   │ chunks_fts   (FTS5 virtual table: text, author, book_title)   │
   │ meta         (key→value: embed_model, …)                      │
   └──────────────────────────────────────────────────────────────┘
```

**Why SQLite + a BLOB, not a vector database.** The corpus is a personal library
(thousands of chunks, not millions). At that scale a brute-force cosine over an
in-memory matrix is *milliseconds*, so a dedicated index (FAISS/Chroma/sqlite-vec) buys
nothing but a heavy dependency and an extra moving part — against the bare-bones,
offline constraints. SQLite gives us one portable file, transactions (so a killed index
run leaves a consistent DB), a built-in lexical engine (FTS5), and zero service to run.
There is no native vector type, so vectors live as `float32` BLOBs and similarity is a
numpy matmul. FAISS/DuckDB remain *deferred opt-in* backends behind the same
`build`/`query` contract — not the default path.

### 3.3 Embeddings: `BAAI/bge-small-en-v1.5`

- **Why bge-small (384-dim).** Small and fast, strong on English retrieval (MTEB), runs
  comfortably on CPU/MPS, and is already cached locally. 384 dims keeps the BLOB matrix
  tiny — the whole library's vectors fit in memory for the matmul. Swap via
  `--embed-model`; never hardcode a second.
- **The asymmetric BGE detail.** BGE is trained with an instruction on the *query* side
  only. So **passages are embedded raw** at index time, and the prefix
  `"Represent this sentence for searching relevant passages: "` is prepended **only to the
  query** at search time. Mixing this up silently degrades recall, so it lives in exactly
  one place each.
- **Vectors are unit-normalized at encode time** (`normalize_embeddings=True`), which is
  what lets the backend treat a dot product *as* cosine — no per-query normalization.

### 3.4 Chunking: the page is the unit

```
   book_*.md ──parse──► per-page units
        merge_tiny()  : page < 200 chars folded into a neighbour
        split_long()  : page > 2000 chars split on paragraph breaks,
                        200-char overlap carried so a citation isn't cut mid-thought
        build_embed_text(): prepend title/author/page so a bare page is self-describing
   ► chunk rows (id, text, embed_text, content_sha)
```

A page is the natural citation unit. `content_sha` over the chunk text is the
embedding cache key — re-indexing only re-embeds new or *changed* pages.

### 3.5 Search: hybrid dense + lexical, fused with RRF

```
   query "where does Scott discuss legibility at sea"
        │
        ├───────────────► DENSE channel
        │                   embed_query() ─► NumpyBackend.query()
        │                   scores = mat @ qvec   (one matmul = cosine, all rows)
        │                   top-50 (id, cosine)
        │
        └───────────────► LEXICAL channel
                            FTS5 MATCH ─► bm25(chunks_fts)
                            top-50 (id, bm25)
                  │
                  ▼
        rrf(): score(id) = Σ 1/(k0 + rank)   over both rankings   (k0 = 60)
                  │                            rank-based, so the two
                  ▼                            incomparable score scales never clash
        top-k fused chunks ─► citation() ─► page snippet + book/author/page
```

**Why hybrid + RRF.** Dense retrieval catches paraphrase and concept ("legibility at
sea" ≈ "maritime legibility"); lexical (BM25 via FTS5) nails exact names, rare terms,
and call numbers that embeddings smear. **Reciprocal Rank Fusion** combines them using
only *ranks*, not scores — so a cosine in `[-1, 1]` and a BM25 score on an open scale can
be merged without any fragile normalization. `--mode dense|lexical|hybrid` exposes each
channel; `--book` filters the candidate pool to one title before fusion.

### 3.6 The numpy backend in full

```python
class NumpyBackend:
    def build(self, ids, mat):  self.ids, self.mat = ids, mat      # hold matrix in RAM
    def query(self, qvec, k):
        scores = self.mat @ qvec                # (N,384) @ (384,) → (N,)  one BLAS call
        order  = np.argsort(-scores)[:k]
        return [(self.ids[i], float(scores[i])) for i in order]
```

That is the entire "vector engine." Vectors are pulled from the BLOB column
(`np.frombuffer(... , float32)`), stacked once, and every query is a single matmul. The
FAISS and DuckDB backends (deferred) must implement the same two-method contract.

### 3.7 Surfaces: CLI first, MCP optional

```
   rag.py index      chunk → embed → build FTS5      (resumable, cache-aware)
   rag.py search     hybrid retrieval, --json / --book / -k / --mode
   rag.py get-page   fetch IMG_x ± neighbours (read a citation in context)
   rag.py serve      optional MCP stdio server: tools search_library / get_page
   rag.py eval       stdlib recall@k / MRR over rag_probes.json (throwaway)
```

The CLI is primary; MCP is a thin wrapper exposing the same two operations to an agent.
**Path resolution is against the install dir (`SCRIPT_DIR`), not the caller's cwd**, so
the tool works when invoked from any other project. The Skill/MCP integration is *not*
wired into this repo — it lives in `integration/` as a portable bundle other Claude
projects import (using this repo's absolute path).

---

## 4. Cross-cutting design decisions (summary table)

| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| OCR engine | prompt-steerable VLM (`dots.mocr-4bit` via mlx-vlm) | Tesseract/Surya/PaddleOCR | need an instruction channel to drop handwriting; must run on Metal, offline |
| Model selection | eval-driven, smallest-that-passes `IMG_3020` | hardcode biggest | fits 16 GB; keeps cost down |
| Orientation | cheap probes then one full pass | 4 full passes | ~2× the work, not 4× |
| Resolution | adaptive 1280 → 1600 on quality gate | fixed 1600 | speed by default, sharpness only when needed |
| Resumability unit | per-image JSON cache keyed on model+prompt | re-run from scratch | survives killed/credit-exhausted runs |
| Catalog store | one SQLite file (BLOB vectors + FTS5) | FAISS/Chroma/sqlite-vec | personal-scale corpus; portable, transactional, no service |
| Vector math | brute-force numpy matmul on normalized vecs | ANN index | ms at this scale; one fewer dep |
| Embeddings | `bge-small-en-v1.5` (384-d), query-prefix only | larger models / symmetric use | fast on CPU/MPS, cached, strong English recall |
| Retrieval | dense + lexical fused with RRF | dense-only | embeddings miss exact names/call numbers; RRF fuses without score normalization |
| Surface | CLI primary, MCP optional, paths via `SCRIPT_DIR` | server-first | bare-bones; usable from any project dir |

---

## 5. File map

```
ocr.py                  OCR pipeline + eval + batch (single file)
prompts.py              the one instruction prompt (+ PROMPT_VERSION, LAYOUT_PROMPT)
rag.py                  chunker + catalog + hybrid search + MCP serve (single file)
requirements.txt        mlx-vlm engine (+ torch/torchvision the processor imports)
requirements-rag.txt    rag-only deps (sentence-transformers, numpy)
run_overnight.sh        caffeinated, retrying batch→index wrapper
integration/            portable Skill + MCP bundle for *other* projects
test/                   eval fixtures (*.jpeg + *_text.txt) — reused, not added to
out/                    book_*.md/.txt, cache/, report.md, index.md, rag.db, *.log
CLAUDE.md               task-level working rules
specs/                   spec-kit feature specs (per-story requirements + decision logs)
.specify/memory/         constitution.md — governing principles
IMPLEMENTATION_PLAN.md  retired — §-number → spec redirect map
ARCHITECTURE.md         this document
```
