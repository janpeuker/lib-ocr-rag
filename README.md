# lib-ocr-rag

A tiny, **100% offline** Python tool — exposed as both a **CLI** and an **MCP / Claude
Skill server**, with minimal dependencies — that turns phone photos of reference-book
pages into clean, structured text and hands them to a **local LLM harness for proper
academic citations**. Snap the pages you're reading, run them through here, and an agent
(or you) can quote and cite them without the books, the cloud, or any retyping.

Under the hood it runs a prompt-steerable document VLM on **Apple Silicon (MLX/Metal)** —
no CUDA, no cloud APIs. The prompt keeps **only printed/typeset text** and **drops every
handwritten annotation** (underlines, circles, margin notes). The OCR pass (`ocr.py`)
groups the shots into books and emits structured Markdown + plain text ready for a citation
manager; an optional local RAG layer (`rag.py`) makes the whole library searchable over
MCP/CLI, so a coding agent can pull an exact passage on demand.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

The engine is `mlx-vlm` (pulls in mlx, transformers, pillow, numpy). It also
needs `torch`/`torchvision` — not for inference (that's MLX/Metal) but because
transformers 5.x eagerly builds the Qwen2-VL *processor*, which imports them.
These are Apple-Silicon CPU/MPS wheels, **no CUDA**.

The first run downloads the model into the HuggingFace cache. After
that, set `HF_HUB_OFFLINE=1` to guarantee no network access:

```bash
export HF_HUB_OFFLINE=1
```

## Usage

```bash
# OCR one or more images → out/<name>.md and out/<name>.txt
python ocr.py run test/IMG_3018.jpeg test/IMG_3020.jpeg --out out/

# Use a different model
python ocr.py run page.jpeg --model mlx-community/olmOCR-7B-0725-4bit

# Score candidate models against the test/ ground truth
python ocr.py eval
python ocr.py eval --models mlx-community/dots.mocr-4bit,mlx-community/olmOCR-7B-0725-4bit --out out/

# Sweep first-pass resolution (downscales fixtures like the batch pipeline does)
python ocr.py eval --max-edge 1600,1280,1024
```

Each image yields two files:
- `<name>.md` — structured Markdown from the model (`### Page N` headings,
  `*italics*`, `>` blockquotes, footnote superscripts).
- `<name>.txt` — flattened plain text: Markdown markers stripped, wrapped lines
  joined into paragraphs.

## Batch a folder, grouped per book

`run` treats every image as a clean page. `batch` is for a messy folder of phone
photos that mixes body pages, two-page spreads, rotated shots, cover/spine
cataloging shots, and figure/map pages — typically several books in one folder.

```bash
source .venv/bin/activate          # always activate the venv first
python ocr.py batch                # OCR in/ → out/, grouped per book
```

`in/` and `out/` are the defaults, so plain `python ocr.py batch` is the whole
command — equivalent to `python ocr.py batch in/ --out out/`. It uses the default
model **`mlx-community/dots.mocr-4bit`** (already cached locally; pass `--model` to
change it) and sets `HF_HUB_OFFLINE=1` itself, so no network and no extra env setup.
The run is resumable (see below), so it's safe to just re-run if interrupted.

For each image: downscale, **OCR with orientation auto-correction** (read at 0°;
if the result looks degenerate — a sign the shot is sideways — retry 90/270/180 and
keep the orientation that yields the most text). The chosen orientation reads first at
a cheap **`FAST_MAX_EDGE` (1280 px)** resolution; if that read scores poorly on a
text-quality check (scrambled / looping output → likely too-low resolution), it's
re-OCR'd once at the sharper **`MAX_EDGE` (1600 px)** and the better read is kept —
so most pages stay cheap and only hard pages pay for full resolution (the 1280 floor
is eval-backed; see *Eval / optimization* below). Then **detect the shot type from
that OCR text** — `IMPRINT` (has ISBN / "first published" / © / CIP data), `COVER`
(very little text), or `PAGE`/`SPREAD`. Imprint/cover shots become bibliographic
metadata (publisher, year, ISBN, title) parsed straight from the text; pages/spreads
become body text. Handwriting is dropped; printed library slips/stamps are kept.

> Type detection is text-based on purpose: the default `dots.mocr` is a *transcriber*
> that ignores "classify this" instructions, so we read the type off the text it
> reliably produces. Imprint detection is solid; robust cover/spine detection (a small
> zero-shot image classifier) is a planned follow-up.

**Figures/maps** are flagged with a gated *layout* dual-pass: `dots.mocr` ignores the
in-prompt figure instruction when transcribing, so for any body page that has a
standalone `Map`/`Figure`/`Table`/`Plate` caption, a second **layout-only** pass
(bbox+category, never fed back into the text) confirms the region and the caption line
becomes a `> **[Figure — …]**` placeholder. Only figure-suspect pages pay the extra
pass (~+3% batch-wide). See `IMPLEMENTATION_PLAN.md §8.4` for the experiment that chose
this over a separate image classifier.

Images are then grouped into books by **title identity**: each shot's running
header / cover-or-imprint title defines a book, and shots whose titles match stay
together — *across* capture-time and GPS gaps (one book may be shot over several
days). A library call number changing, or a confirmed page-number reset, splits
adjacent books; a shelf/spine overview shot is folded into the next book as a
recorded "key image" and can name an otherwise-untitled book by elimination.
Capture-time and GPS are kept only as *soft, overridable* session hints (phone GPS
jitters km within a session, so it never hard-splits). See `IMPLEMENTATION_PLAN.md §9`.

**Optional hints (all live in `in/`, all no-ops if absent, none ever touch the cache).**
- **Bibliography** (`*.ris`): drop a Zotero/RIS export in and `batch` matches each book
  to it to **correct the title** and **complete author / publisher / year / ISBN / city**
  — OCR can't always read a spine or a faint cover. Matching compares the main title
  (before any subtitle), so a shared subtitle suffix won't cause a false match.
- **Duplicate merges** (`merges.txt`): rejoin a book read across non-adjacent sittings
  that the grouper left split. `IMG_a + IMG_b` folds whole books; `IMG_host += IMG_x …`
  moves stray shots. `out/merge_candidates.json` is a ranked discovery aid.
- **Title overrides** (`titles.txt`): `IMG_xxxx = Some Title` forces a book's title as a
  last resort — when the title is buried in a title-page *list* (a series page) or lost
  to a runaway read, and isn't in the RIS either.

All three only enrich output; grouping itself never depends on them.

Output in `out/`:
- `report.md` — the human entry point: every book with structured metadata (title,
  author, publisher, year, ISBN, call no.), its capture timespan, GPS centroid ±
  radius (a wide radius flags a possible mis-grouping), key-image provenance, the
  cover/title shots read for metadata, and a linked list of its page shots. Rewritten
  after each image, so it fills in live.
- `book_NN_<slug>.md` / `.txt` — one document per book: YAML metadata header
  (from the RIS match if any, else its imprint/cover shot) followed by its pages.
- `index.md` — every image with its detected type, rotation, assigned book, figure
  count, and status (`ok` / `empty` / `no-fields`) — use it to audit the grouping.
- `instrument.jsonl` — one JSON line per processed image with cost + quality
  signals (`elapsed_s`, `orient_passes`, `type`, `rotation`, `book`, `text_score`,
  `n_chars`, `page_numbers`, …) for reviewing the run; an avg-per-image summary is
  printed on stderr at the end. Append-only, so it survives resumes.

Progress is one structured line per image on stderr —
`[3/128] IMG_4360 → IMPRINT (book 4) 4.2s` (or `… (cached)` on resume). The batch
sets `HF_HUB_OFFLINE=1` itself so nothing is fetched per image.

**Resumable.** Every image's result is cached under `out/cache/`, so a run
interrupted partway (e.g. usage credits run out) resumes where it left off — just
re-run the same command. The cache auto-invalidates when the model or the prompt
version changes. Use `--force` to ignore the cache and recompute everything.

```bash
python ocr.py batch in/ --force        # recompute from scratch
python ocr.py batch in/ --model mlx-community/Qwen2.5-VL-3B-Instruct-4bit
```

### Planned improvements

- **Robust cover/spine detection** — the multi-spine shelf photo (e.g. IMG_4310) is
  still misrouted by the text heuristic. The §8.4 experiment showed the layout pass can
  flag it (sparse text + several `Picture` regions); wiring that into shot-type routing
  is the open follow-up. (A separate SigLIP classifier nailed it but was rejected: it
  adds a second resident model against the single-model constraint — see §8.4.)
- **Remove the text printed inside a figure** — a figure/map is now *flagged* with a
  placeholder, but the words printed *inside* the map (place names, legend labels) still
  show up as ordinary text in the output. To delete them we'd switch the layout pass to
  dots.ocr's other mode, which returns each region's bounding box **together with the
  text inside it** (today's pass returns only the boxes). Then any transcribed text whose
  position falls inside a `Picture` box could be dropped. Not done yet.

The central report, structured progress logging, offline enforcement, JSONL
instrumentation, and gated figure detection described above are now implemented.

See `IMPLEMENTATION_PLAN.md` §8 for the remaining detection work.

## Look up citations (local RAG)

Once you've produced `out/book_*.md`, `rag.py` builds a small **offline** retrieval
index over them so you (or Claude) can look up passages and citations **without
loading whole books into context** — the point is *look up, don't load*.

It's a separate, still bare-bones tool: a SQLite catalog at `out/rag.db` of
page-sized chunks, **dense embeddings** (`BAAI/bge-small-en-v1.5` on MPS) stored as
float32 BLOBs, plus a **SQLite FTS5** lexical index. A query runs both channels and
fuses them with Reciprocal Rank Fusion — dense handles paraphrase, lexical catches
exact names/terms, so an approximate author + a fuzzy concept both work. See
`IMPLEMENTATION_PLAN.md §12`.

```bash
source .venv/bin/activate
pip install -r requirements-rag.txt          # adds sentence-transformers (one-time)

python rag.py index                          # chunk + embed out/book_*.md → out/rag.db
HF_HUB_OFFLINE=1 python rag.py search "where do sea nomads identify with the sea, not an island"
```

`index` is resumable and cache-aware: re-running embeds only new/changed pages
(`--force` re-embeds all; `--no-embed` chunks only). The embedding model downloads
once, then `HF_HUB_OFFLINE=1` keeps everything offline.

`search` flags: `-k N` (results), `--mode hybrid|dense|lexical` (default `hybrid`),
`--book <substr>` (restrict to one book), `--json` (structured output). Fetch more
context around a hit with `get-page`:

```bash
python rag.py search "Heller-Roazen on pirates as enemies of all mankind" -k 3
python rag.py get-page IMG_3557 --neighbors 1      # the page ± 1 neighbour, in full
```

Results come back with paste-ready citations — *Author, Title (year) · IMG_x p.N*.

Paths resolve against the `rag.py` install (not your shell's working directory), so
these commands work from anywhere.

### Re-index after new OCR

`rag.py index` is the one command to (re)build everything. It's cache-aware, so after
OCR'ing more books just run it again — only new/changed pages are re-embedded:

```bash
python rag.py index            # incremental; embeds only what changed
python rag.py index --force    # rebuild + re-embed everything
python rag.py index --no-embed # chunk into the catalog only (no model load)
```

### Eval (optional)

`rag.py eval` scores `dense` vs `lexical` vs `hybrid` retrieval (recall@1/3/5 + MRR)
against a small probe set `rag_probes.json` (a gitignored, throwaway list of
`{"query", "book"/"image"/"page"}` matchers — `query` required, the rest optional).
Use it to sanity-check a model/prompt change or to justify hybrid as the default:

```bash
python rag.py eval --verbose
```

### Use it from other Claude projects (`integration/`)

To query this library *from another Claude project*, import one of the two bundles
in [`integration/`](integration/) — they talk to the same backend and are
independent, so pick either or both:

- **CLI Skill (preferred)** — `integration/skill/library-search/`: a Skill that
  shells out to `rag.py … --json`. Copy it into a target project's `.claude/skills/`.
  No resident process; the non-brittle replacement for lazy-loading book context.
- **MCP server (optional)** — `integration/mcp/library-search.mcp.json`: registers
  `python rag.py serve` (tools `search_library`, `get_page`) over stdio. Add it via
  `claude mcp add` or the target's `.mcp.json`.

See [`integration/README.md`](integration/README.md) for import steps. Both assume
this repo's absolute path (edit if you move it).

## Model choice (16 GB M3)

| Model (MLX id) | Params | 4-bit disk | Steerable | Role |
|---|---|---|---|---|
| **`mlx-community/dots.mocr-4bit`** | ~3B | ~2 GB | yes (auto-prompted) | **DEFAULT** — light, fast, Markdown |
| `mlx-community/olmOCR-7B-0725-4bit` | 7B | ~5 GB | yes | opt-in `--model`, best fidelity, slower |
| `mlx-community/Qwen2.5-VL-3B-Instruct-4bit` | 3B | ~2 GB | yes | general-VLM baseline |

A steerable VLM is mandatory here: classical OCR (Tesseract, EasyOCR) and
no-prompt OCR models transcribe handwriting indiscriminately and can't be told
to skip it. Change the default by editing `DEFAULT_MODEL` in `ocr.py` or pass
`--model`.

## Eval / optimization

`ocr.py eval` flattens both prediction and ground truth (lowercase, strip
Markdown, collapse whitespace) and reports `difflib.SequenceMatcher.ratio()` —
zero extra dependencies. `IMG_3020` is the diagnostic page: a high score there
means the handwriting is actually being dropped. **Decision rule:** pick the
smallest model whose `IMG_3020` score is acceptable.

### Measured results (M3, 16 GB, this repo's prompt)

```
model                                      IMG_3018   IMG_3020   mean
mlx-community/dots.mocr-4bit                 0.835      0.945     0.890   # DEFAULT
mlx-community/olmOCR-7B-0725-4bit            0.528      0.916     0.722   # opt-in 7B — worse here, see below
mlx-community/Qwen2.5-VL-3B-Instruct-4bit    0.552      0.891     0.721
mlx-community/dots.ocr-4bit                  0.013      0.004     0.009   # echoes prompt — wrong variant, not steerable here
```

`dots.mocr-4bit` wins decisively and already drops the handwriting on IMG_3020
(0.945), so it's the default. Notable, data-driven finding: the larger
`olmOCR-7B` (the plan's "best-fidelity" opt-in) actually scored **lower** on
these pages — it hard-wraps lines mid-paragraph with trailing spaces and
hyphen-break artifacts (`legal- governmental`), producing many small
mismatches once flattened, whereas dots.mocr emits clean reflowed paragraphs.
The non-multilingual `dots.ocr` variant ignores the instruction prompt and
emits junk; `Qwen2.5-VL-3B` transcribes but is far less accurate. The residual
gap on IMG_3018 for every model is mostly ground-truth idiosyncrasy (the truth
drops one footnote superscript and keeps a running header) rather than OCR
error. **Conclusion: keep the small, fast `dots.mocr-4bit` as the default** —
on your own pages it beats the heavier alternatives.

### Resolution sweep → first-pass speedup

`eval --max-edge 1600,1280,1024` downscales each fixture through the batch
pipeline's own preprocessing before OCR, so the scores reflect the resolution the
model actually sees. On `dots.mocr-4bit`:

```
max_edge   IMG_3018   IMG_3020   mean
1600        0.835      0.945     0.890
1280        0.835      0.945     0.890   # FAST_MAX_EDGE — adopted
1024        0.842      0.469     0.656   # IMG_3020 collapses (repetition loop)
```

`IMG_3020` (the handwriting-drop diagnostic) holds at 1280 but **collapses at 1024**,
where the model falls into a verbatim repetition loop — the too-low-resolution
runaway the quality gate is built to catch. So `FAST_MAX_EDGE` is set to **1280** as
the eval floor and `MAX_EDGE` stays 1600 as the quality-gated escalation target.
*Caveat:* the `test/` fixtures are only 1280 px on the long edge, so the sweep proves
1280 is non-lossy down to fixture resolution and that 1024 breaks — it can't directly
prove 1280 ≈ 1600 on full 12 MP captures; the escalation backstops that residual risk.

See `IMPLEMENTATION_PLAN.md` for the full design rationale.

## Acknowledgements

This tool is a thin orchestration layer over open-source machine-learning work; the
heavy lifting is theirs:

- **[dots.ocr](https://github.com/rednote-hilab/dots.ocr)** (rednote-hilab) — the
  prompt-steerable document VLM that does the actual reading, run via the
  [`mlx-community/dots.mocr-4bit`](https://huggingface.co/mlx-community/dots.mocr-4bit)
  conversion.
- **[MLX](https://github.com/ml-explore/mlx)** (Apple) and
  **[mlx-vlm](https://github.com/Blaizzy/mlx-vlm)** — the Apple-Silicon inference engine.
- **[Hugging Face Transformers](https://github.com/huggingface/transformers)** and the
  **[Qwen2-VL](https://github.com/QwenLM/Qwen2-VL)** (Alibaba) image processor, plus
  **[PyTorch](https://pytorch.org/)**, **[Pillow](https://python-pillow.org/)**, and
  **[NumPy](https://numpy.org/)**.
- Local RAG (`rag.py`):
  **[sentence-transformers](https://github.com/UKPLab/sentence-transformers)** with
  **[BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5)** embeddings,
  and the **[Model Context Protocol](https://modelcontextprotocol.io/)** (Anthropic) for
  the optional MCP server.

## License

Released under the **MIT License** — © 2026 Jan Peuker, Goldsmiths, University of London.
See [`LICENSE`](LICENSE) for the full text. The models and libraries listed under
*Acknowledgements* are distributed under their own respective licenses.

## Citation

If you use this software in academic work, please cite it:

```bibtex
@software{peuker_lib_ocr_rag_2026,
  title        = {lib-ocr-rag: Offline document OCR for photographed book pages},
  author       = {Peuker, Jan},
  organization = {Goldsmiths, University of London},
  year         = {2026},
  url          = {https://github.com/janpeuker/lib-ocr-rag}
}
```

## AI Usage Attribution

This work was primarily AI-generated. AI was used to make new content, such as text,
images, analysis, and ideas. AI was prompted for its contributions, or AI assistance was
enabled. AI-generated content was reviewed and approved.
