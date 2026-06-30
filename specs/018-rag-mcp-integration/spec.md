# Feature 018 — RAG MCP server + portable integration bundle

**Status:** Delivered · **Origin:** `library-ocr@0558341`, `061d04f` · **Old ref:** §12.7
**Constitution:** I, III, IV, VII
**Related:** engine → [016](../016-rag-retrieval-engine/spec.md); CLI → [017](../017-rag-cli/spec.md)

## User Scenarios & Testing

### Primary user story
As a developer of **another** Claude project, I want to query this library from that project —
either as a registered MCP tool or as a CLI Skill that shells out — without wiring anything into
this repo and without a real local path leaking into the public repo.

### Acceptance scenarios
1. **Given** an MCP client, **when** it connects to `rag.py serve`, **then** a FastMCP **stdio**
   server exposes `search_library` and `get_page` as thin wrappers over the same functions the
   CLI uses (feature 017), fully offline.
2. **Given** another Claude project, **when** I copy `integration/skill/library-search/` into its
   `.claude/skills/`, **then** the Skill shells out to `python rag.py search "<q>" --json` and
   cites the returned `citation` field — the non-brittle replacement for lazy-loading book context.
3. **Given** another project, **when** I add `integration/mcp/library-search.mcp.json` (via
   `claude mcp add` or the target's `.mcp.json`), **then** the MCP server is registered.
4. **Given** the public repo, **when** I inspect tracked files, **then** the bundle ships a
   `/ABSOLUTE/PATH/TO/lib-ocr-rag` **placeholder** — never a real local path — to be substituted
   on install per `integration/README.md`.

### Edge cases
- The Skill and MCP are **independent** — import either or both per project.
- The Skill/MCP are **NOT** wired into this repo; they live only in `integration/` as a portable
  bundle for *other* projects (which query this install over absolute paths).
- Both surfaces resolve paths against the install dir (`SCRIPT_DIR`), so they work from any cwd
  (feature 017, FR-006).

## Requirements

### Functional
- **FR-001** `rag.py serve` MUST run an **optional** MCP server over **stdio** (official `mcp`
  Python SDK / FastMCP), fully offline, exposing `search_library` / `get_page` as thin wrappers
  over the CLI's shared `result_dict()`/`citation()` helpers — never a divergent code path.
- **FR-002** The MCP server MUST be optional, not required: the CLI Skill is the primary path
  (feature 017); MCP is for users who prefer a registered tool (Principle III — minimal default).
- **FR-003** `integration/` MUST be a portable bundle, **not** wired into this repo:
  `skill/library-search/SKILL.md` (CLI Skill, preferred) and `mcp/library-search.mcp.json`
  (optional), independently importable, documented in `integration/README.md`.
- **FR-004** Tracked integration files MUST contain only a `/ABSOLUTE/PATH/TO/lib-ocr-rag`
  placeholder; a real local path MUST NOT leak into the public repo (Constitution: data
  discipline).
- **FR-005** Both surfaces MUST run offline (`HF_HUB_OFFLINE=1`) and reuse the engine + CLI
  functions verbatim.

### Key entities
- **MCP tools** — `search_library`, `get_page` over stdio.
- **Integration bundle** — `integration/skill/library-search/` + `integration/mcp/…mcp.json`.

## Review & Acceptance Checklist
- [x] FastMCP stdio server wrapping the shared CLI functions
- [x] Portable Skill + MCP bundle, independent, external to this repo
- [x] Placeholder path only; no real local path in tracked files
- [x] CLI-first, MCP-optional

## Decision log (non-normative)
- **Why a bundle, not wired-in (step 5 correction to the original plan).** The Skill/MCP are not
  installed into this repo — they're an importable bundle so *other* projects can use this
  library. To "create the skill" elsewhere: copy `integration/skill/library-search/` into that
  project's `.claude/skills/`. The two are independent.
- **CLI Skill primary, MCP optional.** A Skill shelling out to `rag.py search … --json` keeps
  nothing resident and has no server lifecycle; MCP is kept as a thin wrapper for users who want
  a registered tool. Both back onto the same functions, so behaviour can't drift.
- **Placeholder discipline.** The bundle ships `/ABSOLUTE/PATH/TO/lib-ocr-rag`; the real clone
  path is substituted on install (`ef5145e` removed all local absolute paths from tracked files).
