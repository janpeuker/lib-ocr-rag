# Feature 026 — Metadata hygiene: full-title RIS match · CIP-boilerplate title rejection · runaway-body heal

**Status:** Delivered · **Origin:** Jul 2026 catalog-cleanup pass · **Old ref:** —
**Constitution:** III (offline, deterministic over cache), V, VIII (output-only, cache untouched)

## What & why

Three OCR/bibliography degeneracies surfaced while cleaning a 125-book catalog. Each
corrupts *derived* metadata only — the cache and grouping are correct — so each is fixed
purely at emit time (re-runnable, no re-OCR, hints and cache untouched).

1. **RIS match misses when OCR drops the subtitle colon.** `match_ris` compared only the
   pre-colon *main* title (feature 010 FR-002, to stop a shared subtitle causing a false
   match). But dots.mocr routinely reads a cover's title and subtitle as one run-on line
   with no colon — `ACROSS OCEANS OF LAW The Komagata Maru and Jurisdiction in the Time of
   Empire`. Its main title is then the whole run-on string, which no longer matches the
   RIS record's short main title `Across Oceans of Law`, so a book that *is* in the
   bibliography shows an empty author/year (observed: Sather *The Bajau Laut*, Mawani
   *Across Oceans of Law*, Bhandar *Colonial Lives of Property* — all present as `BOOK`
   records, all unmatched).

2. **Library CIP boilerplate accepted as a title.** An imprint page prints "A catalogue
   record for this book is available from the British Library"; on a run of imprint-heavy
   shots this line becomes the plurality running header and is picked as the book's title
   (observed: three distinct books all titled "A catalogue record …" — Weizman *Hollow
   Land* ×2 and an Ingold volume).

3. **A cover misread as a body page loops into a runaway.** dots.mocr stuck on a big cover
   title emits it hundreds of times (`The Law of the Sea` × 683). Stored as body `raw_md`
   it floods the RAG index with one repeated line (observed: Couper, book_06 / IMG_4667).

## User Scenarios & Testing

### Acceptance scenarios
1. **Given** a book whose OCR title is a colon-less run-on of a title+subtitle that a RIS
   `BOOK` record spells with a colon, **when** `batch` runs, **then** `match_ris` matches
   it (via full-title comparison) and fills author/year — e.g. *Across Oceans of Law* →
   Mawani, 2018.
2. **Given** two *different* books that share only a subtitle suffix, **when** matching,
   **then** neither the main-title nor the full-title path matches them (the feature 010
   "Piracy and Politics" decoy still does not match "Power and Politics").
3. **Given** a book whose only title signal is the British-Library CIP boilerplate,
   **when** deriving its title, **then** that line is rejected and the book falls back to
   its next title signal (or an override / Untitled placeholder), never showing the
   boilerplate as the title.
4. **Given** a body read that is a ≥ 90 %-duplicate repetition loop, **when** emitting,
   **then** its `raw_md`/header/page-numbers are blanked in memory so it reaches neither
   the per-book file nor the RAG index (the cache is untouched).

### Edge cases
- Full-title matching MUST NOT relax the ≥ 0.85 / length-guarded-containment bar; it adds
  a second comparison (full↔full) beside the existing main↔main, taking the max.
- Runaway detection is deliberately conservative (≥ 10 lines AND < 0.1 unique-line/phrase
  ratio) so a legitimate list/table/index is never blanked.
- A cover misclassified as a body page loses its body-derived title when healed; its real
  title must then come from `cover_title`, a running header, or a `titles.txt` override
  (IMG_4667 → `The Law of the Sea` via override).

## Requirements

### Functional
- **FR-001** `match_ris` MUST compare both the pre-colon main titles (main↔main) and the
  full normalized titles (full↔full) of each query/RIS pair, scoring each book by the
  maximum, and keep the feature 010 acceptance bar (≥ 0.85 ratio or length-guarded
  containment) and generic-title skip. A shared subtitle alone MUST NOT match.
- **FR-002** Title derivation (`book_title`) MUST reject a library CIP/copyright
  boilerplate candidate (`_is_nontitle` / `_BOILERPLATE_TITLE`) at every candidate path —
  cover title, `title:` metadata line, CIP title, and running-header voting — falling
  through to the next non-boilerplate signal.
- **FR-003** `emit_all` MUST blank (in memory, cache untouched) any body record whose
  `raw_md` is a degenerate repetition loop (`_is_runaway`), extending the spec-023
  prompt-echo heal.
- **FR-004** All three fixes MUST be pure over the cache and re-runnable: no re-OCR, no
  change to grouping, `PROMPT_VERSION`, or the cache (Principle VIII).

- **FR-005** `sim`'s containment shortcut MUST NOT be gated on the 0.7 length ratio alone.
  A shorter title fully contained in a longer one MUST also be accepted once it is
  **≥ `_CONTAINMENT_MIN_CHARS` (20)** normalised characters — long enough to be specific
  on its own. Rationale and the measurement that fixes the constant are in the decision
  log; re-measure before lowering it.
- **FR-006** The record filter MUST be a named predicate (`_is_citable`) used by **both**
  the ISBN and the fuzzy path, and it MUST admit **journal-level serial records** —
  `JOUR`/`SER` whose `TI == T2`. Article records MUST stay excluded: a library holds
  hundreds, and a book's cover title fuzzy-matching an article title would attribute a
  whole book to one paper's author. `load_ris` MUST therefore parse `T2` (`container`).

### Key entities
- **Non-title** — a title candidate that is a generic section word (feature 010) or a
  CIP/copyright boilerplate line (this spec).
- **Runaway body** — a body read that is a near-total repetition loop, not page content.

## Review & Acceptance Checklist
- [x] Colon-dropped run-on titles match their RIS `BOOK` record; subtitle-only decoy still unmatched
- [x] British-Library CIP boilerplate never shown as a title
- [x] Runaway body blanked at emit; cache/grouping/PROMPT_VERSION untouched
- [x] Sather / Mawani / Bhandar fill author/year with no per-book override

## Decision log (non-normative)

### Two matcher bugs found by auditing the corpus against the bibliography (2026-07-31)
A user diff of "books with no author/year" against their Zotero export showed the list was
mostly wrong: the records existed, `match_ris` could not reach them. Two independent causes,
both measured over the whole corpus before fixing.

**1. The 0.7 length guard rejects a true containment.** The OCR reads a cover's main title
while Zotero carries the full subtitle, so the query is a clean substring that is simply much
shorter:

| OCR title | RIS record | contained | 0.7 guard | difflib |
|---|---|---|---|---|
| `A HISTORY OF SINGAPORE` (22) | *Seven hundred years : a history of Singapore* (42) | yes | **fails** (0.52) | 0.688 |
| `The China Sea Directory Vol. I` (29) | *The China Sea Directory, vol. I. Containing…* (104) | yes | **fails** (0.28) | 0.436 |

These were the two largest unattributed books in the corpus, 169 pp. FR-005 adds an absolute
-length escape. **The constant is not a guess** — sweeping candidate values over all books:

| `_CONTAINMENT_MIN_CHARS` | books changed | verdict |
|---|---|---|
| 16 | 5 | **breaks one**: `the power of maps` (17 ch) is contained in *Rethinking the Power of Maps*, flipping Wood's 1992 book onto the 2010 one |
| **18 / 20** | **4** | all correct: Kwa, King's *China Sea Directory*, the JIA, and *Mapping the Unmappable?* |

18 and 20 are indistinguishable in effect, so **20** ships — three characters of headroom over
the measured false positive rather than one.

**2. Serials were structurally unmatchable.** The filter admitted only `BOOK`/`CHAP`/`EDBOOK`,
so a photographed journal run could never match however the user catalogued it — the *Journal
of the Indian Archipelago* record (Earl & Logan, Mission Press, 1847) is `TY - JOUR` and was
skipped before comparison. Simply admitting `JOUR` is unsafe: this library holds 105 article
records, and a cover title fuzzy-matching an article would credit a whole book to one paper's
author — the confident-wrong-citation failure spec 029 exists to prevent. FR-006's test is
`TI == T2`, which distinguishes the run from a paper inside it and changed **exactly one book**
when measured alone. Consequence worth stating: a serial the user holds only as articles still
cannot match, and now *can* be made to match by adding one journal-level record.

**Method note.** Both changes were simulated across all 128 books and diffed against current
behaviour *before* being written to `ocr.py`. That is what caught the 16-character false
positive, which no amount of reasoning about the constant would have surfaced.
- **Why full↔full instead of loosening the main-title bar.** The main-title-only rule
  (010 FR-002) exists to stop a shared subtitle matching two different books. Rather than
  weaken it, we ADD a full-title comparison and take the max: a full match requires the
  *whole* title (incl. subtitle) to agree, which two different books sharing only a
  subtitle can't do — so scenario 2 is preserved while the colon-dropped real matches are
  recovered. Verified: Mawani/Sather/Bhandar match; "Power and Politics" stays `None`.
- **Boilerplate as a regex, not a `_GENERIC_TITLES` entry.** The CIP line is long and
  varies ("British Library" / "Library of Congress"), so it's matched by
  `_BOILERPLATE_TITLE` rather than the exact-string generic set, and folded into a shared
  `_is_nontitle` used everywhere a candidate is weighed.
- **Heal (blank) the runaway, don't collapse it.** Blanking mirrors the spec-023 echo
  heal and keeps the emit path simple; a properly-classified cover keeps its title via
  `cover_title`. IMG_4667 is a cover *misclassified* as a PAGE, so it additionally needs a
  `titles.txt` override — a one-book cost, not a reason to add title-recomputation to the
  heal. Threshold < 0.1 unique-ratio (≥ 90 % duplicate, ≥ 10 lines) so tables/indices are
  safe.
- **Not a hint file.** All three are code, not `in/` allow-lists — they fix systematic OCR
  degeneracies, not per-book judgements. The per-book residue (IMG_4667's name, the
  Chou/Malay boundary) stays in `merges.txt`/`titles.txt`.
