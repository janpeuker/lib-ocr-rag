# Feature 010 — Bibliography (Zotero/RIS) + title-override hints

**Status:** Delivered · **Origin:** `library-ocr@f0e5ebd` (RIS), title overrides (§15) · **Old ref:** §9 (RIS), §15
**Constitution:** III, V, VIII (the no-op-if-absent contract)

## User Scenarios & Testing

### Primary user story
As a user whose OCR can't always read a faint cover or a sideways spine, I want to drop my
existing Zotero/RIS export into the input folder and have each book's title **corrected** and
its author/publisher/year/ISBN/city **completed** from the bibliography — and, as a last
resort, force a specific title when even the bibliography can't supply it.

### Acceptance scenarios
1. **Given** a `*.ris` export in the input folder, **when** `batch` runs, **then** `load_ris`
   parses it and `match_ris` matches each book, correcting the title and completing
   author/publisher/year/ISBN/city in both `report.md` and the per-book YAML frontmatter.
2. **Given** two books that share a subtitle suffix, **when** matching, **then** matching
   compares only the **main** title (pre-colon) at ≥ 0.85 ratio / containment, so a shared
   subtitle cannot cause a false match (e.g. book 5 is NOT matched to a "Piracy and Politics"
   decoy).
3. **Given** a book whose title is buried in a series list or lost to a runaway read AND
   absent from the RIS, **when** I add `IMG_xxxx = Some Title` to `in/titles.txt`, **then**
   that book's title is forced.
4. **Given** none of these files exist, **when** `batch` runs, **then** behaviour is
   unchanged — the hints are pure no-ops.

### Edge cases
- `match_ris` MUST skip `_GENERIC_TITLES` queries so a mis-titled `Preface` page does not match
  a Foucault "Preface" RIS entry.
- A title override is a last resort — prefer fixing the read or the bibliography first.

## Requirements

### Functional
- **FR-001** A `*.ris` file in the input folder MUST be parsed (`load_ris`) and each book
  matched (`match_ris`) to correct the title and complete author/publisher/year/ISBN/city;
  `book_record` MUST drive both `write_report` and per-book frontmatter; `_yaml_val` MUST
  quote colon-bearing titles.
- **FR-002** RIS matching MUST compare only the main title (before any subtitle) at
  ≥ 0.85 ratio or containment, and MUST skip generic titles.
- **FR-003** `in/titles.txt` MUST support `IMG_xxxx = Some Title` to force a book's title.
- **FR-004** All hint files (`*.ris`, `titles.txt`, and the feature 013 `merges.txt`) MUST
  honour the no-op-if-absent contract (Principle VIII): absent ⇒ no-op; output-only; they MUST
  NEVER touch the cache, change grouping decisions, or affect `PROMPT_VERSION`.

### Key entities
- **RIS record** — parsed bibliographic entry `{ title, author, publisher, year, isbn, city }`.
- **Title override** — `IMG_xxxx → title` mapping from `in/titles.txt`.

## Review & Acceptance Checklist
- [x] RIS corrects title + completes fields; main-title-only matching avoids false positives
- [x] `titles.txt` last-resort override
- [x] All hints are output-only no-ops when absent; cache/grouping untouched

## Decision log (non-normative)
- **RIS result (§9, sample of 5 books):** 3 of 5 title-corrected + enriched ("Leaves of the
  **Same Tree** …", full "Enemy of All" and "Power and Politics" subtitles, authors, ISBNs);
  the 2 unmatched keep OCR metadata. Output-only — the cache, grouping, and `PROMPT_VERSION`
  never depend on the RIS.
- **Why title overrides exist (§15/§16).** A residual class of books can't be auto-titled:
  title buried in a series-page list (e.g. "Cosmopolitical Ecologies Across Asia", not in
  `Studio.ris`), or a model that emits no `Title` box (spine-only/sideways/imprint shots). For
  those, `in/titles.txt` is the explicit last-resort override (e.g.
  `IMG_5922 = Singapore: A Modern History`).
- This story shares the exact no-op-if-absent contract with the feature 013 `merges.txt`
  duplicate-merge allow-list — three optional `in/` files, all output-only.
