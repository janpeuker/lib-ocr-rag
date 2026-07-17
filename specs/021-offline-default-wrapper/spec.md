# Feature 021 — Offline-by-default + everyday wrapper script

**Status:** Delivered · **Origin:** a `rag.py search` run without `HF_HUB_OFFLINE=1` hit the
network, and a stale HuggingFace OAuth token turned that into a misleading
`RepositoryNotFoundError` (401) for a public, fully-cached model
**Constitution:** I, III, VII

## User Scenarios & Testing

### Primary user story
As the library's user I want both tools to be **offline by default** — forgetting to type
`HF_HUB_OFFLINE=1` must never cause a network call, let alone a spurious failure while the
model sits fully cached on disk. And I want **one wrapper script** that runs the everyday
OCR → index → search flow with the correct settings (venv, offline, repo cwd) so I don't
have to remember them.

### Acceptance scenarios
1. **Given** the embed model cached locally and no `HF_HUB_OFFLINE` in the environment,
   **when** `python rag.py search "q"` runs, **then** it returns results with zero network
   requests (no PEFT/adapter probe, no 401 possible).
2. **Given** the same environment, **when** any `ocr.py` subcommand runs (not just `batch`),
   **then** inference is likewise offline by default.
3. **Given** the user explicitly sets `HF_HUB_OFFLINE=0` (e.g. to download a new
   `--model`/`--embed-model`), **when** either tool runs, **then** the explicit value wins —
   the default never overrides the user.
4. **Given** a fresh shell with no venv active and any cwd, **when** `./library.sh search "q"`
   runs, **then** the search executes with the repo's venv and offline settings applied.
5. **Given** new photos in `in/`, **when** `./library.sh update` runs, **then** `ocr.py batch`
   and `rag.py index` run in sequence (both resume-by-default), leaving the catalog current.

### Edge cases
- The wrapper is for interactive, one-shot use; multi-hour resilient runs remain
  `run_overnight.sh` (caffeinate + retry + lock). The wrapper MUST NOT duplicate that
  machinery.
- `ocr.py batch` already defaulted offline internally; the default is promoted to import
  time in both files so *every* subcommand inherits it before any HuggingFace-touching
  import runs.

## Requirements

### Functional
- **FR-001** `ocr.py` and `rag.py` MUST apply `os.environ.setdefault("HF_HUB_OFFLINE", "1")`
  at import time, before any HuggingFace-consuming library is imported (lazy imports
  included by construction).
- **FR-002** The default MUST be `setdefault`, never an unconditional set: an explicit
  `HF_HUB_OFFLINE` value in the caller's environment always wins (escape hatch for
  intentional downloads, Principle IV's model switching).
- **FR-003** `library.sh` MUST resolve the repo directory from its own location, activate
  `.venv`, and export `HF_HUB_OFFLINE=1`, so it works from any caller cwd (Principle VII's
  contract: tools own their paths).
- **FR-004** `library.sh` MUST provide: `update` (= `ocr.py batch` then `rag.py index`),
  `search …`/`page …` (thin passthroughs to `rag.py search`/`get-page`), and `ocr …`/`rag …`
  raw passthroughs for everything else. Unknown/missing subcommands print usage.
- **FR-005** The wrapper MUST stay dependency-free bash (Principle III) and add no retry,
  lock, or logging machinery — that belongs to `run_overnight.sh`.

### Key entities
- **Offline default** — the import-time `setdefault` in each tool; the single switch that
  makes "forgot the env var" a non-event.
- **`library.sh`** — the everyday entrypoint bundling venv + offline + subcommand dispatch.

## Review & Acceptance Checklist
- [x] `rag.py search` works offline with no env var set (verified against the cached model)
- [x] All `ocr.py` subcommands inherit the default (module-level, pre-import)
- [x] Explicit `HF_HUB_OFFLINE=0` still reaches the network path (setdefault semantics)
- [x] `library.sh` runs from any cwd; `update`, `search`, `page`, `ocr`, `rag` dispatch
- [x] No new dependencies; overnight machinery not duplicated

## Decision log (non-normative)
- **Why in-process, not shell config.** A shell alias/export in `~/.zshrc` fixes one shell on
  one machine and silently breaks for MCP/Skill invocations from other projects. The tools
  themselves are the single place every entry path flows through (Principle VII).
- **Why `setdefault`, not hard `=1`.** A hard set would make first-time model downloads
  (`--model`, `--embed-model`) impossible without editing code — the explicit environment
  must stay authoritative (mirrors the pre-existing `cmd_batch` behaviour, now promoted).
- **Why a second script beside `run_overnight.sh`.** The overnight script is deliberately
  heavyweight (caffeinate, singleton lock, 8× retry, log file) — wrong ergonomics for a
  10-second search. Sharing code between the two would mean a third file; at ~30 lines of
  bash, duplication of `cd`+`source`+`export` is cheaper (Principle III).
- **The stale-token trap this closes.** With online access allowed, sentence-transformers
  probes the Hub for a PEFT `adapter_config.json` even when the model is fully cached; a
  broken cached OAuth token (`~/.cache/huggingface/token`) turns that probe into a 401 that
  huggingface_hub reports as `RepositoryNotFoundError` — looking exactly like a model
  takedown. Offline-by-default makes the probe (and the trap) unreachable.
