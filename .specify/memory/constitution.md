# lib-ocr-rag Constitution

The non-negotiable principles every feature spec in `specs/` inherits. A spec or
plan that conflicts with a principle here is wrong unless this document is amended
first. Sourced from `CLAUDE.md` (hard constraints + working preferences); kept in
the spec-kit canonical location (`.specify/memory/constitution.md`).

## Core Principles

### I. Offline-only, no APIs (NON-NEGOTIABLE)
The tools MUST run with **no network access at inference/query time**. Models are
downloaded once from HuggingFace, then every run sets `HF_HUB_OFFLINE=1`. No cloud
SDKs, no remote APIs, no telemetry leaving the machine.
*Rationale:* the corpus is a personal research library; privacy and reproducibility
require that nothing depends on a service that can change or disappear.

### II. Apple Silicon / MLX, no CUDA
Inference MUST run on the Mac GPU via `mlx-vlm` (MLX/Metal). `torch`/`torchvision`
are tolerated **only** because the transformers Qwen2-VL processor imports them
(CPU/MPS wheels). No CUDA, ever.
*Rationale:* the target machine is a 16 GB M3; the project is defined by what runs
well there.

### III. Bare-bones, minimal dependencies
No web framework, no config system, no cloud DB, no document-ingestion framework.
The OCR engine is `mlx-vlm`; retrieval is SQLite + numpy + `sentence-transformers`.
A new dependency MUST be justified against this principle and, if optional, MUST be
lazy-imported so the default install stays minimal.
*Rationale:* every dependency is a maintenance and offline-fragility liability.

### IV. Single model per tool, selected by eval (not hardcoded)
Each tool has exactly one default model (`DEFAULT_MODEL` in `ocr.py`,
`DEFAULT_EMBED_MODEL` in `rag.py`), swappable via `--model` / `--embed-model`.
A second model path MUST NOT be hardcoded. Model choice is an **eval decision**:
pick the smallest model whose diagnostic score is acceptable (`IMG_3020` for OCR).
*Rationale:* keeps memory bounded on 16 GB and makes model changes a measured,
reversible knob.

### V. Resumable, checkpointed batch work (NON-NEGOTIABLE)
Any expensive batch MUST checkpoint each unit of work to disk and **resume by
default**; `--force` recomputes. A run killed by exhausted credits or sleep MUST
pick up where it stopped. The cache MUST auto-invalidate when the model or a
`PROMPT_VERSION`/schema changes.
*Rationale:* runs span hundreds of images/hours; restarting from zero is unacceptable.

### VI. Prompts and tunables live in one place; tune by eval
The instruction prompt(s) live only in `prompts.py`; tunables live near a labelled
constant block. Behavior is tuned against `ocr.py eval` / `rag.py eval`, never by
editing inference code ad hoc. Eval scoring stays stdlib-only (`difflib`).
*Rationale:* a single auditable surface for behavior; changes are measurable.

### VII. Two decoupled tools, contract = files on disk
`ocr.py` and `rag.py` are independent single-file tools with separate dependency
sets. `rag.py` only ever **reads** the `out/*.md` that `ocr.py` writes. One may be
versioned, run, or broken without touching the other.
*Rationale:* isolation keeps each tool simple and independently testable.

### VIII. Hints enrich, never gate (the no-op-if-absent contract)
Optional inputs in `in/` (`*.ris`, `merges.txt`, `titles.txt`) MUST be **no-ops when
absent**, MUST only enrich output, and MUST NEVER touch the cache or change grouping
decisions. Grouping is inferred from the photos alone.
*Rationale:* the pipeline must be correct without any human curation; hints are a
last-resort override, not a dependency.

## Test Fixtures & Data Discipline

- **Do not add new OCR test data.** Reuse `test/*.jpeg` + `*_text.txt` as the only
  eval fixtures. New behaviors that can't be auto-scored are verified by inspecting
  `in/` outputs, not by adding fixtures.
- Throwaway probe/label sets (`rag_probes.json`, `experiments/`) are **gitignored**,
  never committed as fixtures.
- No real local absolute paths in tracked files; `integration/` ships a
  `/ABSOLUTE/PATH/TO/lib-ocr-rag` placeholder.

## Development Workflow

- **Main-only development.** Work directly on `main`; no feature branches.
- **Always ask before committing.** Never `git commit`/push without explicit
  per-commit approval.
- **Always activate the venv** (`source .venv/bin/activate`) and run inference with
  `HF_HUB_OFFLINE=1`; the default models are already cached — never re-download.
- Durable guidance is recorded in `CLAUDE.md`; durable design lives in these specs
  (`specs/`) and `ARCHITECTURE.md`, not in scattered notes.
- A vendored monkeypatch (the `mlx-vlm` detokenizer UTF-8 fix) MUST be revisited on
  every `mlx-vlm` bump (see `specs/010-resumability-resilience/`).

## Governance

This constitution supersedes ad-hoc practice. Amendments require updating this file
and reconciling any spec that depended on the old wording. Specs in `specs/` are the
source of truth for *what* the system does and *why*; `ARCHITECTURE.md` describes the
durable *shape*; `CLAUDE.md` carries task-level working rules. Where they disagree,
the constitution wins, then the relevant `spec.md`.

**Version:** 1.0.0 | **Ratified:** 2026-06-30 | **Last amended:** 2026-06-30
