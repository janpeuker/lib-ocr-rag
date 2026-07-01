# lib-ocr-rag

A tiny, **100% offline** Python tool that turns phone photos of reference-book pages into
clean, structured text — grouped per book — and makes the result searchable for **proper
academic citations**. Snap the pages you're reading, run them through here, and an agent
(or you) can quote and cite them without the books, the cloud, or any retyping.

Under the hood it runs a prompt-steerable document VLM on **Apple Silicon (MLX/Metal)** —
no CUDA, no cloud APIs. The prompt keeps **only printed/typeset text** and **drops every
handwritten annotation** (underlines, circles, margin notes).

- **`ocr.py`** — photos → structured Markdown + plain text, grouped into books.
- **`rag.py`** — an optional local hybrid-search index over that Markdown, exposed as a
  CLI and an optional MCP / Claude Skill, so an agent can pull an exact passage on demand.

Two single-file tools, decoupled — `rag.py` only ever reads what `ocr.py` writes.

---

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # OCR engine (mlx-vlm)
pip install -r requirements-rag.txt      # only if you want the RAG search layer
```

The OCR engine is `mlx-vlm` (mlx, transformers, pillow, numpy). It also pulls
`torch`/`torchvision` — **not** for inference (that's MLX/Metal) but because transformers
eagerly builds the Qwen2-VL *processor*, which imports them. Apple-Silicon CPU/MPS wheels,
**no CUDA**.

The first run downloads the model into the HuggingFace cache (~2 GB). After that, keep
everything offline:

```bash
export HF_HUB_OFFLINE=1
```

(`ocr.py batch` and `rag.py` set this themselves, so the common paths need no extra setup.)

---

## OCR your pages

### A few clean pages — `run`

```bash
python ocr.py run page1.jpeg page2.jpeg --out out/      # → out/<name>.md + .txt
python ocr.py run page.jpeg --model mlx-community/olmOCR-7B-0725-4bit
```

Each image yields `<name>.md` (structured Markdown — `### Page N`, `*italics*`, `>`
blockquotes, footnote superscripts) and `<name>.txt` (flattened: markers stripped, wrapped
lines rejoined into paragraphs).

### A messy folder of phone photos — `batch`

`batch` is for a real folder that mixes body pages, two-page spreads, rotated shots,
cover/spine cataloging shots, and figure/map pages — usually several books at once.

```bash
source .venv/bin/activate
python ocr.py batch                 # OCR in/ → out/, grouped per book
```

`in/` and `out/` are the defaults, so plain `python ocr.py batch` is the whole command. It
uses the default model `mlx-community/dots.mocr-4bit`, runs offline, and is **resumable** —
re-run the same command if it's interrupted and it picks up where it left off.

What it does per image, in brief (each step is a feature spec — see **Design & docs**):

1. **Downscale + orientation auto-correct** — read upright; if a shot is sideways, cheap
   probes rank 0/90/180/270 and one full pass reads the winner.
2. **Adaptive resolution** — read first at a cheap 1280 px; only pages that look
   scrambled/looping are re-read sharper at 1600 px.
3. **Detect the shot type from the OCR text** — `IMPRINT` (ISBN / © / CIP), `COVER` (sparse,
   or colourful behind a library label), or `PAGE` / `SPREAD`. Cover/imprint shots become
   bibliographic metadata; pages/spreads become body text. Handwriting is dropped; printed
   library slips/stamps are kept.
4. **Figures/maps** get a caption-only `> **[Figure — …]**` placeholder via a gated layout pass.
5. **Group into books by title identity** — a book may span several days/sessions; capture
   time and GPS are only *soft* hints. A cover may be shot first or last.
6. **Name each book** — the cover title is read from the **largest type**, not reading order.

```bash
python ocr.py batch in/ --force                                   # ignore cache, recompute
python ocr.py batch in/ --model mlx-community/Qwen2.5-VL-3B-Instruct-4bit
```

### Optional hints (all live in `in/`, all no-ops if absent, none ever touch the cache)

| File | Purpose |
|------|---------|
| `*.ris` | A Zotero/RIS export — corrects each book's title and completes author / publisher / year / ISBN / city when OCR can't read a spine or faint cover. |
| `merges.txt` | Rejoin a book read across non-adjacent sittings. `IMG_a + IMG_b` folds whole books; `IMG_host += IMG_x` moves stray shots. (`out/merge_candidates.json` ranks candidates.) |
| `titles.txt` | `IMG_xxxx = Some Title` — last-resort forced title when it's buried in a series-page list or absent from the RIS. |

All three only enrich output; grouping itself never depends on them.

### What you get in `out/`

| File | What |
|------|------|
| `report.md` | The human entry point — every book with metadata, capture span, GPS centroid ± radius (wide = possible mis-group), key-image provenance, and a linked list of its page shots. Rewritten live as the run proceeds. |
| `book_NN_<slug>.md` / `.txt` | One document per book: YAML metadata header + its pages. |
| `index.md` | One row per image — type, rotation, assigned book, figure count, status (`ok` / `empty` / `no-fields`). Use it to audit grouping. |
| `instrument.jsonl` | One JSON line per image with cost + quality signals; append-only, survives resumes. An avg-per-image summary prints on stderr at the end. |

Progress is one structured line per image on stderr —
`[3/128] IMG_4360 → IMPRINT (book 4) 4.2s` (or `… (cached)` on resume). Every image's
result is cached under `out/cache/`; the cache auto-invalidates when the model or prompt
version changes (`--force` recomputes everything).

---

## Look up citations (local RAG)

Once you've produced `out/book_*.md`, `rag.py` builds a small **offline** retrieval index so
you (or Claude) can look up passages **without loading whole books into context** — the point
is *look up, don't load*. It runs both a **dense** (embedding) and a **lexical** (FTS5)
channel and fuses them, so an approximate author *and* a fuzzy concept both work.

```bash
source .venv/bin/activate
python rag.py index                          # chunk + embed out/book_*.md → out/rag.db
python rag.py search "where do sea nomads identify with the sea, not an island"
python rag.py search "Heller-Roazen on pirates as enemies of all mankind" -k 3
python rag.py get-page IMG_3557 --neighbors 1   # the page ± 1 neighbour, in full
```

- `index` is resumable and cache-aware — re-run it after new OCR and it re-embeds only
  new/changed pages (`--force` re-embeds all; `--no-embed` chunks only). It's the one command
  to (re)build everything.
- `search` flags: `-k N`, `--mode hybrid|dense|lexical` (default `hybrid`), `--book <substr>`,
  `--json` (structured, with paste-ready citations — *Author, Title (year) · IMG_x p.N* — and
  an `image_path` back to the source photo).
- `rag.py eval --verbose` scores dense vs lexical vs hybrid (recall@k / MRR) against a
  gitignored `rag_probes.json`.

Paths resolve against the `rag.py` install, not your shell's cwd, so these work from anywhere.

### Use it from another Claude project (`integration/`)

Import one of two independent bundles in [`integration/`](integration/) — both talk to the
same backend:

- **CLI Skill (preferred)** — `integration/skill/library-search/`: a Skill that shells out to
  `rag.py … --json`. Copy it into a target project's `.claude/skills/`. No resident process.
- **MCP server (optional)** — `integration/mcp/library-search.mcp.json`: registers
  `python rag.py serve` (tools `search_library`, `get_page`) over stdio.

See [`integration/README.md`](integration/README.md) for steps. Both assume this repo's
absolute path (edit on install).

---

## Model choice (16 GB M3)

| Model (MLX id) | Params | 4-bit disk | Role |
|---|---|---|---|
| **`mlx-community/dots.mocr-4bit`** | ~3B | ~2 GB | **DEFAULT** — light, fast, Markdown, drops handwriting |
| `mlx-community/olmOCR-7B-0725-4bit` | 7B | ~5 GB | opt-in `--model`; higher fidelity on some pages, slower |
| `mlx-community/Qwen2.5-VL-3B-Instruct-4bit` | 3B | ~2 GB | general-VLM baseline |

A **steerable** VLM is mandatory: classical OCR (Tesseract, EasyOCR) and no-prompt OCR
models transcribe handwriting indiscriminately and can't be told to skip it. Change the
default by editing `DEFAULT_MODEL` in `ocr.py` or pass `--model`.

`ocr.py eval` scores candidate models against `test/` ground truth with `difflib` (zero extra
deps); `IMG_3020` is the handwriting-drop diagnostic. **Rule: pick the smallest model whose
`IMG_3020` score is acceptable.** The measured bake-off (and why bigger lost here) lives in
[`specs/001-single-page-ocr/`](specs/001-single-page-ocr/spec.md) and
[`specs/experiments/003-heavier-ocr-models.md`](specs/experiments/003-heavier-ocr-models.md).

```bash
python ocr.py eval
python ocr.py eval --max-edge 1600,1280,1024     # sweep first-pass resolution
```

---

## Design & docs

- **[`specs/`](specs/README.md)** — the per-feature specifications (what each capability does
  and why, with decision logs). Start at `specs/README.md`. The five title/grouping heuristics
  — largest-font (008) and colour (009) cover titles, bibliography hints (010), time/GPS
  per-burst (011), cover-first-or-last (012) — each have their own story.
- **[`specs/experiments/`](specs/experiments/README.md)** — negative results and deferred work
  (rejected/removed features, future ideas) that aren't obvious from the code.
- **[`ARCHITECTURE.md`](ARCHITECTURE.md)** — the durable shape of the system and the
  cross-cutting design decisions.
- **[`.specify/memory/constitution.md`](.specify/memory/constitution.md)** — the governing
  constraints (offline, Apple-Silicon, bare-bones, single-model, resumable).
- **[`CLAUDE.md`](CLAUDE.md)** — task-level working rules for agents.

---

## Acknowledgements

This tool is a thin orchestration layer over open-source machine-learning work; the heavy
lifting is theirs:

- **[dots.ocr](https://github.com/rednote-hilab/dots.ocr)** (rednote-hilab) — the
  prompt-steerable document VLM that does the actual reading, via
  [`mlx-community/dots.mocr-4bit`](https://huggingface.co/mlx-community/dots.mocr-4bit).
- **[MLX](https://github.com/ml-explore/mlx)** (Apple) and
  **[mlx-vlm](https://github.com/Blaizzy/mlx-vlm)** — the Apple-Silicon inference engine.
- **[Hugging Face Transformers](https://github.com/huggingface/transformers)** and the
  **[Qwen2-VL](https://github.com/QwenLM/Qwen2-VL)** (Alibaba) image processor, plus
  **[PyTorch](https://pytorch.org/)**, **[Pillow](https://python-pillow.org/)**, and
  **[NumPy](https://numpy.org/)**.
- Local RAG (`rag.py`):
  **[sentence-transformers](https://github.com/UKPLab/sentence-transformers)** with
  **[BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5)** embeddings, and
  the **[Model Context Protocol](https://modelcontextprotocol.io/)** (Anthropic) for the
  optional MCP server.

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

This work was primarily AI-generated. AI was used to make new content, such as text, images,
analysis, and ideas. AI was prompted for its contributions, or AI assistance was enabled.
AI-generated content was reviewed and approved.
