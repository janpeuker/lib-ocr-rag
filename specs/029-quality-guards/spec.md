# Feature 029 — Quality guards: coverage audit · catalog doctor · eval drift

**Status:** Delivered · **Constitution:** III, V, VI, VII
**Related:** the two fixes that motivated it → [027](../027-meta-shot-text-recovery/spec.md),
[028](../028-retrieval-quality/spec.md); instrumentation → [014](../014-reporting-instrumentation/spec.md)

Features 027 and 028 fixed real defects, but the more important finding was **how long
they survived**. The library grew from 21 books to 126 and retrieval quality decayed the
whole way, silently:

- **Text that was never indexed** (99 imprint pages, ~295 k chars) — retrieval can only
  fail to *rank* what it was given; it can never report what it never saw.
- **Constants outgrown** — `CANDIDATES = 50`, tuned at 1163 chunks, was still 50 at 9483.
- **Stale measurement** — three of five probes matched `book_NN` numbers that had since
  renumbered onto different books, so eval reported failure and nobody read it.
- **An error rationalized into documentation** — `parse_book` treated any `## ` line as
  an image label, so a page's own Markdown subheading started a bogus "image" section.
  123 chunks sat under 71 invented labels with broken citations and null `image_path`s.
  This had been *observed* and written into CLAUDE.md as expected behaviour
  ("some labels are section headings, not filenames — those resolve to null").

This story adds the checks that would have caught each one. The design constraint that
shapes everything: **`in/`, `out/` and `test/` are gitignored**, so a second user of this
repo has a completely different corpus and no fixtures. Guards must therefore be
**relative invariants computed against whatever library is present** — never golden
numbers, never a committed sample.

## User Scenarios & Testing

### Primary user story
As the maintainer of a library that grows a few hundred pages at a time, I want the
everyday command to tell me when an assumption has quietly stopped holding — before
retrieval quality decays far enough for me to notice it by hand.

### Acceptance scenarios
1. **Given** a batch, **when** it finishes, **then** `out/coverage.json` records for every
   shot either that its text reached a book file or a **named reason** why not
   (`thin-text`, `prompt-echo-healed`, `runaway-healed`, `meta-below-recovery-gate`);
   anything else is `UNEXPLAINED` and warns on stderr.
2. **Given** a catalog, **when** `rag.py doctor` runs, **then** it reports on uncitable
   image labels, books contributing no chunks, unembedded or model-stale vectors, windows
   at risk of silent truncation, the candidate pool vs corpus size, probe validity, eval
   staleness, OCR↔RAG page reconciliation, and cross-image duplicate passages.
3. **Given** `rag.py index`, **when** it completes, **then** the same checks run
   **warn-only** — never blocking, never non-zero — because a guard you must remember to
   run is a guard that does not run.
4. **Given** a fresh clone with someone else's photos, **when** they run
   `rag.py probes --scaffold`, **then** they get a starter probe set drawn from *their*
   corpus, keyed on **image** ids, with an explicit warning that verbatim sentences
   flatter the lexical channel and should be paraphrased.
5. **Given** repeated evals, **when** one runs, **then** it appends corpus size and
   per-channel scores to `out/eval_history.jsonl` and flags a drop in reranked MRR since
   the previous comparable run.
6. **Given** a change to window-splitting logic, **when** `index` re-runs, **then** every
   window is rebuilt — `WINDOW_VERSION` invalidates them the way `PROMPT_VERSION`
   invalidates the OCR cache (Principle V).

### Edge cases
- A page consisting only of sub-`MIN_CHARS` fragments is folded into its neighbour and
  legitimately has no chunk of its own, so the OCR↔RAG reconciliation must tolerate a
  small gap and warn only when it is disproportionate.
- The truncation check is **char-based and therefore script-dependent**: at ~4.0
  chars/token for this corpus it catches page-grain regressions, but 26 windows of
  non-Latin text still exceed the cap at 600 chars and it cannot see them. It is a
  regression guard on `WINDOW_CHARS`, not a proof of no truncation.
- Checks must never block a long overnight batch.

## Requirements

### Functional
- **FR-001** `ocr.py` MUST write `out/coverage.json` after each batch: a per-shot verdict
  of emitted-or-named-reason, plus a summary. It MUST be computed from the same in-memory
  state the emit path just used, so it cannot drift from what was written.
- **FR-002** An `UNEXPLAINED` shot MUST warn on stderr with example image names.
- **FR-003** `rag.py doctor` MUST implement the checks in scenario 2, each **relative**
  to the corpus in front of it — no committed fixture, no absolute quality threshold.
- **FR-004** `rag.py index` MUST run the checks warn-only on completion (`--no-check`
  opts out). It MUST NOT exit non-zero; `doctor --strict` is the opt-in for that.
- **FR-005** `rag.py probes --scaffold` MUST generate a probe set from the live catalog,
  matched by `image` (stable) not `book` (renumbers), and MUST refuse to overwrite an
  existing probe set without `--force`.
- **FR-006** `rag.py eval` MUST record `eval_chunks`/`eval_at` in `meta` and append a
  scored entry to `out/eval_history.jsonl`, and MUST report a reranked-MRR drop against
  the previous run with the same probe count.
- **FR-007** `WINDOW_VERSION` MUST invalidate every window when window logic changes.
- **FR-008** `parse_book` MUST treat `## ` as an image boundary **only** when the heading
  is an image filename, so a page's own Markdown headings stay body text.
- **FR-009** `library.sh` MUST expose `books`, `doctor` and `eval`, so the guards are
  reachable from the everyday wrapper.

### Key entities
- **Coverage verdict** — `{image, type, role, reason, chars}`; `reason == ""` ⇒ emitted.
- **Check** — `(level, name, message)` where level ∈ `ok | warn | info`.

## Review & Acceptance Checklist
- [x] `parse_book` heading bug fixed: 123 uncitable chunks → 0; hybrid R@1 .62 → .75
- [x] Coverage audit accounts for all 3260 shots, 0 `UNEXPLAINED`
- [x] `doctor` surfaced three true findings on the live corpus: 2 books contributing no
      chunks, 152 duplicate passages across images, 147 truncated windows
- [x] Truncation fixed at source by a hard window cap (147 → 26, all non-Latin script)
- [x] `WINDOW_VERSION` bump rebuilt and re-embedded all 35 613 windows unprompted
- [x] `probes --scaffold` produces image-keyed probes on this corpus
- [x] Reranked MRR .81 unchanged after the window hard-cap

## Decision log (non-normative)
- **Why invariants, not fixtures.** The corpus is gitignored, so there is nothing to
  compare against and nothing to ship. Every check therefore asks "is this still
  internally consistent / still proportionate to the corpus", which happens to be the
  only form of check that also works for a stranger's library. The one thing worth
  shipping is the *checker*.
- **Why the probe set stays gitignored.** Committing it was considered and rejected: a
  probe set is inherently corpus-specific, so it would be dead weight to any other user
  and would not have prevented the staleness (the probes were *there*, just wrong).
  `--scaffold` gives a new adopter a baseline in one command and leaves the constitution's
  "throwaway probe sets, never fixtures" rule intact — no amendment needed.
- **Why warn-only on the everyday path.** The drift accumulated because nothing spoke
  unprompted; the fix is to make the checks unavoidable, not to make them fatal. A hard
  failure partway through an overnight batch would cost more than the drift it prevents.
  `--strict` exists for anyone wiring this into CI.
- **Why the coverage audit lives in `ocr.py`, not `rag.py`.** The 027 hole was upstream of
  the file contract: the text never reached a `.md`, so `rag.py` could not see it without
  reaching into the OCR cache and breaking Principle VII. `ocr.py` owns that state; the
  reconciliation between the two views is a separate `doctor` check.
- **Char-based truncation check is deliberately imperfect.** A true check needs the
  tokenizer, i.e. loading the embed model, which would make `doctor` slow enough to skip.
  4.0 chars/token was measured over 3000 windows rather than guessed — an earlier 3.5
  flagged 469 windows where only 147 truly overflowed, and a check that cries wolf gets
  ignored, which is the failure mode this whole story is about.
- **`WINDOW_VERSION` was found by this work, not designed in.** Fixing the window hard-cap
  produced no change on re-index, because `content_sha` covers the page text and nothing
  covered the window logic. That is precisely the auto-invalidation Principle V requires,
  and its absence would have frozen every future chunking fix.
