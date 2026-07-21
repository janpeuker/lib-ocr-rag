# Feature 027 — Body text recovery from cover/imprint shots

**Status:** Delivered · **Constitution:** III, VI, VII
**Related:** shot detection → [004](../004-shot-type-detection/spec.md); metadata →
[007](../007-bibliographic-metadata/spec.md); the retrieval side of the same
investigation → [028](../028-retrieval-quality/spec.md)

A COVER/IMPRINT shot is classified `role: meta` and read only for bibliographic
fields, so its transcription never reaches a book file — and therefore never reaches
the RAG catalog. That is right for a shot showing only a title block, and wrong for
the very common case where a title-page verso or imprint page also carries real
prose. **This is a retrieval *coverage* hole, not a ranking problem**: no amount of
embedding or reranking work can find a page that was never indexed.

## User Scenarios & Testing

### Primary user story
As someone searching the library for "Wilhelm Braun", I get nothing from `IMG_1234` —
a page holding ~5 000 characters of ordinary prose that names Wilhem Braun — because the
shot was typed `IMPRINT` and its text was discarded at emit.

### Acceptance scenarios
1. **Given** a `role: meta` record whose `ocr_text` is at least `META_BODY_MIN_CHARS`,
   **when** the book file is written, **then** it gets a `## {image}` section in
   normal capture order, marked in-body as recovered text.
2. **Given** a `role: meta` record that is only a title/publisher block (below the
   gate), **when** the book file is written, **then** nothing changes — its text is
   already consumed by `parse_metadata` and would only be noise in the catalog.
3. **Given** the fix, **when** `ocr.py batch` re-runs, **then** it completes **from
   cache with no re-OCR** — `ocr_text` has always been persisted — and the recovered
   sections appear immediately.
4. **Given** a recovered section, **when** `rag.py` parses the book, **then** the
   image label resolves normally, so `get-page` and the `image_path` escape hatch
   work for it exactly as for a body page.

### Edge cases
- Meta `ocr_text` never passed the prompt-echo/runaway guards, which run only on the
  `body` path. A meta shot of a near-textless spine can therefore hold a parroted
  prompt; the recovery path must re-apply both guards.
- The recovery marker must live **in the body, never in the `##` heading** —
  `parse_book()` reads the heading as the image label.
- A shot can be both the book's title source and a body page; recovering its prose
  does not change what `parse_metadata`/`cover_title` extract from it.

## Requirements

### Functional
- **FR-001** `write_book()` MUST emit a page section for a `role: meta` record whose
  `ocr_text` length is ≥ `META_BODY_MIN_CHARS`, in the existing record order.
- **FR-002** The section heading MUST be the bare `## {image}` filename; the
  `META_BODY_MARKER` note MUST be the first body line, so the RAG image label and
  every downstream path keyed on it stay valid.
- **FR-003** Recovery MUST re-apply `_is_prompt_echo` and `_is_runaway` to the meta
  text, since neither guard ran when the record was cached.
- **FR-004** Recovery MUST NOT touch the cache, grouping, `PROMPT_VERSION`, or the
  metadata a meta shot contributes (Principle V, VIII); it is an emit-time change only.
- **FR-005** `write_index()` MUST report such a record as `recovered` rather than
  `no-fields`, so the coverage is auditable in `index.md`.

### Key entities
- **`recovered_body(rec)`** — the guarded prose stranded on a meta record, or `""`.

## Review & Acceptance Checklist
- [x] 98 sections across 47 books recovered from cache, no re-OCR
- [x] `IMG_1234`'s "Wilhem Braun" text now present in its book file
- [x] `index.md` shows 39 `recovered`; `no-fields` drops to 68
- [x] Chunk count 8829 → 9499 after re-index

## Decision log (non-normative)
- **Why a length gate rather than a new classifier pass.** The signal needed is just
  "is there prose here beyond the title block", and character count answers it with no
  model call. 400 chars sits well above a title/author/publisher block and well below
  the ~2500-char median of the shots that matter. Re-classifying these shots properly
  (SPREAD vs IMPRINT) is a much larger change to spec 004 and would require re-OCR.
- **Why emit into the book rather than teach `rag.py` to read `out/cache/`.** Principle
  VII: the two tools' contract is the `out/*.md` files. Letting the RAG tool reach into
  the OCR tool's cache would couple them to a schema that exists for resumability, not
  for consumption.
- **Why not suppress the shot's metadata role.** The same page legitimately serves both
  purposes — the imprint block feeds `parse_metadata`, the prose feeds retrieval. Making
  it one or the other would trade a coverage hole for a metadata hole.
- **Duplication is accepted.** A recovered imprint page repeats the title/publisher lines
  that also live in the frontmatter. That costs a few dozen tokens per book and keeps the
  emit path free of a text-subtraction heuristic that could eat real prose.
