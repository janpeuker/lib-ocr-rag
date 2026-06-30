# Specifications (spec-kit layout)

This directory is the **source of truth for what the system does and why**. It
replaces the chronological `IMPLEMENTATION_PLAN.md` (now a tombstone redirect) by
decomposing the work into discrete, independently-testable **feature specs**,
reconstructed from the commit history of both this repo and its parent
`library-ocr`.

Layout follows [github/spec-kit](https://github.com/github/spec-kit):

```
.specify/memory/constitution.md   ← governing principles (all specs inherit these)
specs/<NNN>-<slug>/spec.md         ← one feature story: WHAT + WHY, testable FRs
```

Each `spec.md` uses the spec-kit template: **User Scenarios & Testing** (primary
story + Given/When/Then acceptance scenarios + edge cases), **Requirements**
(numbered `FR-###` "MUST" statements + Key Entities), a **Review checklist**, and a
non-normative **Decision log** that preserves the rationale and eval evidence the old
plan carried (so nothing is lost in the conversion). `plan.md` / `tasks.md` are
optional per spec-kit and intentionally omitted for already-delivered stories — the
durable *HOW* lives in `ARCHITECTURE.md`.

## Feature index

The five heuristics worth reading first — title by **colour** (009) and **largest
font** (008), **bibliography** hints (010), the **cover-first-or-last** position rule
(012), and the **time/GPS-per-burst** session hint (011) — each have their own story.

| # | Story | Status | Origin (commits) | Old § |
|---|-------|--------|------------------|-------|
| 001 | [Single-page OCR + eval](001-single-page-ocr/spec.md) | Delivered | `c04a47f` | Appendix |
| 002 | [Batch book pipeline](002-batch-book-pipeline/spec.md) | Delivered | `eb26153` | §1–§7 |
| 003 | [Orientation + adaptive resolution](003-orientation-adaptive-resolution/spec.md) | Delivered | `6daa5ec`, `b1fa172` | §10, §11 |
| 004 | [Shot-type detection (text)](004-shot-type-detection/spec.md) | Delivered | `7af1cfb` | §3 |
| 005 | [Figure/map detection](005-figure-detection/spec.md) | Delivered | `124454c`, `259facf` | §8.4 |
| 006 | [Book grouping (title identity)](006-book-grouping/spec.md) | Delivered | `76b43a9` | §9 |
| 007 | [Bibliographic metadata + cover-title resolution](007-bibliographic-metadata/spec.md) | Delivered | `f551263` | §15 |
| 008 | [Cover title by **largest font**](008-cover-title-largest-font/spec.md) | Delivered | `441d58a` | §16 |
| 009 | [**Colour**-assisted cover detection](009-colour-cover-detection/spec.md) | Delivered | `5f90a77` | §17 |
| 010 | [**Bibliography** (RIS) + title-override hints](010-bibliography-title-hints/spec.md) | Delivered | `f0e5ebd` | §9, §15 |
| 011 | [Capture **time + GPS** per-burst hint](011-burst-session-hint/spec.md) | Delivered | `76b43a9` | §9 |
| 012 | [Cover **position** (leads or trails the burst)](012-cover-position-heuristic/spec.md) | Delivered | `c465e37` | §9.1 |
| 013 | [Library-wide duplicate merge](013-duplicate-merge/spec.md) | Delivered | `ab7a77b` | §14 |
| 014 | [Reporting + instrumentation](014-reporting-instrumentation/spec.md) | Delivered | `afd9849` | §8.1–§8.2 |
| 015 | [Resumability + resilience](015-resumability-resilience/spec.md) | Delivered | `60a0e59`, `199e680`, `3f1c758` | §4, §13 |
| 016 | [RAG retrieval engine (chunking · embeddings · numpy)](016-rag-retrieval-engine/spec.md) | Delivered | `8e590d0`, `83366cb`, `1d4298d` | §12.2–§12.6 |
| 017 | [RAG CLI (index · search · get-page · eval)](017-rag-cli/spec.md) | Delivered | `0558341`, `29e81dd`, `56ef31e` | §12.7, §12.9 |
| 018 | [RAG MCP server + integration bundle](018-rag-mcp-integration/spec.md) | Delivered | `061d04f` | §12.7 |
| 019 | [Title/imprint detection gaps](019-title-imprint-gaps/spec.md) | Gap A done · Gap B proposed | `5f90a77` | §18 |

History note: the parent `library-ocr` carries the full commit-by-commit history
(`a63e4bd` → `ef5145e`); this fork (`lib-ocr-rag`) squashes that into its
`Initial commit` (`5bab837`) and adds the largest-font cover title (`441d58a`, §16)
and colour-assisted cover detection + the SPREAD→IMPRINT fix (`5f90a77`, §17/§18-A).
The "Origin" column cites the parent's hashes where the work originated there.

## How to evolve this

Add a feature the spec-kit way: create `specs/<next-NNN>-<slug>/spec.md` from the
template, write the requirements *before* the code, tune against eval, then implement.
Promote story 019's **Gap B** from **Proposed** to **Delivered** once its FRs pass.
