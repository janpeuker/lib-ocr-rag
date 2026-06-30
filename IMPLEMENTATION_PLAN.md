# Implementation plan — batch OCR of heterogeneous library photos

> **Status:** designed, ready to implement. Supersedes the original single-page plan
> (now compressed into the *Historic reference* appendix at the bottom).
> **Target:** Mac M3, 16 GB, Python 3, Apple Silicon MLX, **100% offline**, bare-bones.
> Read alongside `CLAUDE.md` (hard constraints) and `README.md` (usage).

---

## 1. Why this plan exists

The tool was built for clean, pre-cropped single pages (the two `test/` fixtures). The
real input in `in/` is different, which is why the design needs a refresh:

**128 iPhone photos (~12 MP, ~1.9 GB)** taken across four library sessions
(Dec 2 2025 → Jan 2 2026). They are *heterogeneous*, not a uniform page set:

- **Body-text pages** — clean single pages (IMG_3556, IMG_3708).
- **Two-page spreads** — landscape, with inserted blank sheets, a finger, and back-of-page
  bleed-through (IMG_4380 = pp. 272/273).
- **Imprint / contents identity pages** — publisher, year, ISBN, title (IMG_4360).
- **Cataloging shots** — covers, spines, library pickup slips on a table (IMG_4310);
  spine text rotated 90°; no body text.
- **Rotated captures** — a spread shot sideways so text runs vertically (IMG_3558).
  EXIF is already orient=1, so the rotation is *in the pixels* and we must fix it.
- **Figures / maps** — pages dominated by maps or diagrams (IMG_4406, two maps).
- **Blurry pages** with a library slip in frame (IMG_4340).

The current `ocr.py run` assumes every image is a clean page: no directory batch, no
preprocessing of skewed 12 MP photos, and one prompt that only drops *handwriting*. It
would mis-handle covers/spines/rotations and emit disconnected per-image files with no
book structure.

### Confirmed requirements (from the user)

1. **Cover/imprint shots → extract bibliographic metadata** (title, author, publisher,
   year, ISBN, call number), not body OCR.
2. **Printed library slips / call-number stamps on pages → keep them** (transcribe).
   Only *handwriting* (underlines, circles, margin notes) is dropped, per the original goal.
3. **Output grouped per book.** Grouping is *inferred* — the user doesn't label books.
4. **Straighten rotated photos** before OCR.
5. **Figures/maps → flag with a caption-only placeholder** in the Markdown; do not
   extract them (extraction may come later).
6. **Keep GPS as a grouping signal** — future batches will span multiple libraries.

### Grouping hints the user described

- Photos of one book are taken **in a row**; the user rarely interleaves books.
- A **cover (IMG_4310)** or **imprint page (IMG_4360)** identifies a book and may sit
  **before or after** its page run (placement is inconsistent).
- **EXIF**: per book the GPS location is the same and the timeframe is narrow (a burst of
  shots, then a long gap). *Caveat:* GPS + time separate **sessions/locations**, not books
  *within* one session — within the dense Jan-2 run the cadence is steady (~30–90 s, no
  lulls), so they only prevent cross-session merges.
- **Visual continuity**: a stable running header (book title/author in the page header)
  and monotonic page numbers indicate the same book; font/layout is approximated by
  running-header similarity (no embeddings — stays bare-bones).

---

## 2. Constraints (must hold — see CLAUDE.md)

- Python 3, Apple Silicon MLX, **100% offline**, bare-bones, minimal deps.
- Preprocessing uses **Pillow only** (already transitive via mlx-vlm). No OpenCV/new deps.
- **One model** via `--model` (default `mlx-community/dots.mocr-4bit`) serves *all* passes
  (classify + metadata + body); never hardcode a second model path. If dots.mocr follows
  the richer instructions poorly, switch the whole run with
  `--model mlx-community/Qwen2.5-VL-3B-Instruct-4bit` (better instruction-following).
- All prompts live only in `prompts.py`.
- Scoring stays `difflib`-only; **add no new eval fixtures** (the new behaviors can't be
  auto-scored against `test/` — verify by inspecting `in/` outputs). The existing `eval`
  keeps working unchanged.

---

## 3. Architecture (two inference passes + a pure grouping pass)

Per image: **(A) classify + detect rotation → (B) route to metadata or body OCR**, then a
non-inference **(C) grouping** pass over all results, then **(D) emit per-book files**.

Reuses existing `load_model`, `ocr_image`, `md_to_text`, `normalize`/`similarity` in
`ocr.py`.

**Pass A — classify + orientation.** `CLASSIFY_PROMPT` returns two tokens on a downscaled
image (fast, tiny `max_tokens`):
- **type** ∈ `COVER` · `IMPRINT` · `PAGE` · `SPREAD` · `OTHER`
- **rotation** ∈ `0|90|180|270` (clockwise to upright). Parse defensively → default `PAGE 0`.

**Preprocess — `prep_image(path, rotation)`.** `ImageOps.exif_transpose`; apply the Pass-A
rotation (`Image.rotate(expand=True)` / `transpose`) so a sideways image OCRs correctly;
downscale long edge to `MAX_EDGE` (~2200 px) preserving aspect (caps VLM cost/memory —
biggest robustness win). No crop/contrast tricks. If `mlx_vlm.generate` needs a path, write
prepped copies to a temp dir (auto-cleaned), never into `in/`.

**Pass B — route.**
- `COVER`/`IMPRINT` → `METADATA_PROMPT` → small YAML block (title, author, publisher, year,
  isbn, call_number; omit unknowns). Identifies/names the book.
- `PAGE`/`SPREAD` → existing `PROMPT` (body). `PROMPT` is tightened to: **keep** printed
  library slips/stamps/labels; **drop** handwriting; keep `### Page N` spread behavior;
  **flag figures/maps** with a caption-only placeholder, e.g.
  `> **[Figure — MAP 14-4: The Network of Orang Suku Laut Inter-related Territories]**`.
- From each body result capture two grouping signals: the **running header** (first
  non-empty line) and any **printed page numbers** (regex).
- `OTHER` → recorded in the index, skipped for body output.

**Pass C — `group_images(records)` (no inference).** Title-identity segmentation —
**superseded the original hard session/GPS fences** (see §9; the fences fragmented one
book into 60). Combine all signals:
- *Identity continuity (primary):* each shot's **`page_header`** (the short, title-like
  running header — `_is_title_like` filters body prose, folios, call numbers and
  library-slip fields) defines a book identity. Shots whose headers fuzzy-match
  (`_hdr_match`, containment-aware) stay in one book — *across time/GPS gaps*.
- *Markers:* a `COVER`/`IMPRINT` with a new title starts a book; an anchored book is not
  re-split by its varying chapter headers.
- *Library call number:* `call_number` (shelfmark on a pickup slip) is per-book — a
  change of call number starts a new book (this splits same-session adjacent books).
- *Soft session/GPS:* a *large* time gap or a *continent-scale* GPS change starts a book
  **unless** a matching title/call number overrides it. Phone GPS jitters km within one
  session, so small deltas are ignored.
- *Mid-session header change* starts a book only when a **page-number reset** confirms it
  (so a verso/recto book-title-vs-chapter-title alternation does not fragment a run).
- *Post-pass:* `_merge_shared_title` rejoins adjacent runs whose header sets overlap
  (one book shot across two days); `_fold_key_images` folds a session-opening shelf/spine
  overview shot into the next book as recorded provenance (`key_images`) rather than a book.
- Each book = its `COVER`/`IMPRINT` metadata (if any) + its `PAGE`/`SPREAD` bodies in
  filename order. `write_report` adds structured Title/Publisher/Year/ISBN/Call-no.,
  capture timespan + duration, GPS centroid ± radius, and key-image provenance — a wide
  span/radius surfaces a possibly mis-grouped book.

**Pass D — emit.**
- `out/book_NN_<slug>.md` — YAML frontmatter (metadata) + `## <source img>` body sections.
- `out/book_NN_<slug>.txt` — flattened plain text via `md_to_text`.
- `out/index.md` — one row per image: filename · type · rotation · book # · status
  (ok / blurry-empty / skipped). Makes grouping auditable and surfaces failures.

---

## 4. Resumability (recover from a killed session / exhausted credits)

The batch is ~256 inference calls and will run long. Make every expensive result a
**disk checkpoint** so a rerun skips finished work:

- **Per-image cache** at `out/cache/<img>.json`: `{type, rotation, role, raw_md,
  running_header, page_numbers, exif:{dt,gps}, model, prompt_version}`. Pass A/B write it
  once per image; on rerun, an image with a complete cache entry is **skipped**.
- Invalidate a cache entry when `model` or `prompt_version` differs (so prompt/model
  changes re-run only what's affected).
- **Grouping (C) and emit (D) are pure and cheap** — always recomputed from the cache, so
  they cost nothing to re-run and never need checkpointing.
- `batch` is resume-by-default; add `--force` to ignore the cache and recompute.
- Process images in sorted filename order and flush the cache **after each image**, so a
  crash loses at most the in-flight image.

This turns "session died at image 90/128" into "rerun, it resumes at 91."

---

## 5. Implementation steps (each independently testable & committable)

1. **Scaffolding (no inference).** Add constants `MAX_EDGE`, `TIME_GAP_S`, `HEADER_SIM`,
   `PROMPT_VERSION`. Implement `read_exif(path)` (datetime + GPS) and
   `prep_image(path, rotation)`. Unit-check on a couple of `in/` images that rotation +
   downscale produce sane sizes.
2. **Prompts.** In `prompts.py` add `CLASSIFY_PROMPT` (type + rotation) and
   `METADATA_PROMPT`; tighten `PROMPT` (keep slips/stamps, drop handwriting, figure/map
   placeholder). Bump `PROMPT_VERSION`.
3. **Cache layer.** `cache_path`, `load_cache`, `save_cache` over `out/cache/<img>.json`
   with model+prompt-version invalidation. Test read/write round-trip.
4. **Pass A — classify + rotation.** `classify_image(...)`; persist to cache. Spot-check
   types/rotation on the smoke set (step 8) via `index.md` before doing Pass B.
5. **Pass B — route + extract.** `extract_metadata(...)` for COVER/IMPRINT; body OCR for
   PAGE/SPREAD reusing `ocr_image`; capture running header + page numbers; persist to cache.
6. **Pass C — grouping.** `group_images(records)` implementing the multi-signal rules.
   Pure function — test directly on cached records, no inference.
7. **Pass D + CLI.** `write_book`, `out/index.md`, and `cmd_batch` wired into `argparse`
   as `batch [DIR] [--model] [--out] [--force]` (default DIR `in/`, out `out/`). Mirrors
   `cmd_run`'s load-once-then-loop. `run` and `eval` unchanged.
8. **Docs + hygiene.** Update `README.md` (batch usage, per-book layout, resume/`--force`,
   figure placeholders); ensure `.gitignore` covers `out/` (incl. `out/cache/`). No
   `requirements.txt` change (Pillow is transitive).

Suggested commit boundaries: 1–2, 3–5, 6–7, 8. Each leaves the tree runnable.

---

## 6. Files to modify

- `ocr.py` — constants; `read_exif`, `prep_image`, cache helpers, `classify_image`,
  `extract_metadata`, `group_images`, `write_book`, `cmd_batch`; wire `batch` into
  `argparse`. Reuse `load_model`, `ocr_image`, `md_to_text`, `normalize`/`similarity`.
- `prompts.py` — `CLASSIFY_PROMPT`, `METADATA_PROMPT`, tightened `PROMPT`, `PROMPT_VERSION`.
- `IMPLEMENTATION_PLAN.md` — this file.
- `README.md` — `batch` usage + output layout + resume.
- `.gitignore` — ensure `out/` (incl. `out/cache/`) ignored.

---

## 7. Verification

1. **Smoke set** — copy IMG_4310 (cover), IMG_4360 (imprint), IMG_3558 (rotated spread),
   IMG_3708 (page), IMG_4406 (maps), IMG_3554 (slip + handwriting) into a scratch dir and
   `python ocr.py batch <scratch>`: confirm metadata from cover/imprint, IMG_3558 is
   straightened and readable, printed slips kept, handwriting dropped, IMG_4406 maps become
   caption-only placeholders.
2. **Classify + grouping spot-check** — eyeball `out/index.md` against ~15 known images
   across sessions; confirm type, rotation, and book boundaries. If dots.mocr mislabels,
   re-run the scratch set with `--model …Qwen2.5-VL-3B-Instruct-4bit`.
3. **Resumability** — interrupt a run mid-way (Ctrl-C), rerun, confirm it skips cached
   images and finishes; `--force` recomputes.
4. **Full run** — `python ocr.py batch in/ --out out/`; spot-check several `book_NN_*.md`
   for page order + clean body; check `index.md` for blurry/empty failures (expect IMG_4340).
5. **Regression** — `python ocr.py eval` still scores the two `test/` fixtures unchanged.
6. **Offline** — re-run step 1 with `HF_HUB_OFFLINE=1` to prove no network at inference.

---

## 8. Planned improvements / follow-ups

Not yet implemented; ordered roughly by value.

1. **Central report / index of everything.** Generate one top-level Markdown report
   (e.g. `out/report.md`) summarizing the whole batch: every book with its parsed
   metadata, and under each book a linked list of its pages — `[IMG_4406](book_04_…md)`
   style links into the per-book files (and/or anchors to page sections). This is the
   human entry point to a 128-image run; `index.md` stays as the flat per-image audit
   table. Bonus: write/refresh the report **incrementally** during the run so progress
   is visible (see #2).

2. **Quieter, clearer progress + visible output during the run.** Today stdout shows a
   noisy per-image line that can look like a "download," and `out/` appears empty until
   the very end because per-book files are only written after grouping (which needs all
   pages); only `out/cache/*.json` fills incrementally. Note the model **is** loaded once
   and reused (`cmd_batch` lazy-loads under `if model is None` and passes it down — there
   is no per-image reload), so the noise is HF-hub cache-checking / library logging, not
   re-initialization. Improvements: (a) ensure `HF_HUB_OFFLINE=1` so nothing is fetched
   per image (the model is already cached locally); (b) replace ad-hoc prints with one
   structured line per image — `[3/128] IMG_4360 → IMPRINT (book 4)`; (c) surface
   progress in `out/` by updating the report (#1) as each image completes.

3. **Robust cover/spine detection via a small zero-shot image classifier (Option B).**
   The text heuristic detects imprints well but misreads cover/shelf shots (e.g. the
   multi-spine IMG_4310 → `SPREAD`). A small offline zero-shot classifier (SigLIP/CLIP,
   ~400 MB, `torch`+`transformers` already installed) labelling the photo *before* OCR
   ("book cover", "copyright/imprint page", "page of text", "book spines on a table")
   would make cover/spine routing reliable. Weigh against the "bare-bones" constraint.
   **Update (2026-06-14): the §8.4 experiment chose C (dots.mocr layout dual-pass) over
   this classifier (B) — B adds a second resident model against the single-model
   constraint. See §8.4 "Results & decision".**

4. **Experiment: pick the detection mechanism — small separate classifier (B) vs.
   dots.mocr dual-pass (C).** Two unmet needs share one question: (i) reliable shot-type
   routing (the text heuristic misreads multi-spine IMG_4310 as `SPREAD`) and (ii)
   figure/map *region* detection (today we rely on the model voluntarily emitting the
   `> **[Figure — …]**` placeholder; IMG_4406 is two maps). dots.mocr advertises native
   layout parsing — a single JSON object with `[x1,y1,x2,y2]` bboxes over 11 categories
   (`Picture`, `Table`, `Section-header`, `Page-footer`, …) — but the pipeline never sees
   it because `prompts.py` runs the model in free-text transcription mode, deliberately,
   to satisfy the **drop-handwriting** requirement (native layout mode transcribes
   marginalia as `Text`). So the design choice is *not* "prompt dots harder"; it's which
   of two detectors to bolt on. **Run this as an experiment before committing to either —
   don't just implement #3.**

   - **Candidate B — small zero-shot image classifier** (the SigLIP/CLIP route in #3).
     One ~400 MB resident model, a ~tens-of-ms pass *before* OCR; labels the whole photo
     (cover / imprint / page / spines-on-table / figure-heavy). Touches none of the text
     path, so the handwriting steer is untouched. Cost: a second model resident — directly
     relevant after the MAX_EDGE work, since memory pressure (swap) was the batch's real
     drag; +400 MB on a 16 GB M3 may reopen that. Gives a *page-level* label, not figure
     bboxes — enough for routing, weak for locating multiple figures on one page.
   - **Candidate C — dots.mocr dual-pass (hybrid).** Reuse the model already loaded: a
     second call with the native layout prompt to get `Picture`/`Table`/spread structure +
     bboxes, while the existing transcription prompt still produces the handwriting-free
     text. No new weights (no extra resident memory), and figure detection is *positional*
     (bbox per figure). Cost: ~2× inference per image — right after we halved it — though
     it can be gated to run only on images the cheap text heuristic already flags as
     ambiguous (low text, or ≥1 unplaced figure), not every page.

   **Method (reuse existing data — do not shoot new test photos; CLAUDE.md forbids new
   eval fixtures).** Hand-label a subset of the existing `in/*.jpeg` for shot-type and
   figure-presence (a throwaway labels file, not committed as a fixture), including the
   known-hard cases IMG_4310 (multi-spine), IMG_4406 (maps), and an imprint + a plain
   page. For each candidate measure: shot-type accuracy on that subset, figure-detection
   recall/precision, **added wall-time per image**, **peak memory / swap delta** (the
   metric that bit us — `vm.swapusage` before/after), and whether the `IMG_3020`
   handwriting-drop score regresses (it must not — Candidate C only regresses it if layout
   output ever feeds the text path, which it must not).

   **Decision rule.** Prefer **C (dual-pass)** if gated inference keeps added per-image time
   under ~50% *and* it doesn't push the run back into swap, since it adds no resident
   memory and yields figure bboxes. Fall back to **B (classifier)** only if its accuracy is
   materially higher *and* the +400 MB stays clear of swap. If neither clears the bar,
   keep the current text heuristic and just tighten `COVER_TEXT_MAX` / the spine case.
   Whichever wins, detection output must never re-enter the transcription text.

   ### Results & decision (run 2026-06-14)

   Harness: `experiments/detector_experiment.py` over 8 hand-labeled `in/` images
   (`experiments/labels.json`; throwaway, gitignored — not an eval fixture), incl. the
   hard cases IMG_4310 (multi-spine shelf) and IMG_4406 (two-map spread). One
   figure-positive content page in the subset (IMG_4406), so figure precision/recall is
   indicative, not statistically tight. Baseline **A** = current text heuristic;
   **C** = dots.mocr `layout-only` second pass (bboxes + categories, never fed to the
   text path); **B** = `google/siglip-base-patch16-224` zero-shot.

   | Detector | shot-type acc | figure recall | figure precision | added time/img | swap Δ |
   |---|---|---|---|---|---|
   | A text heuristic | **0.63** | 0.0 | 0.0 | 0 (baseline) | 0 |
   | C layout dual-pass | 0.25¹ | **1.0** | **1.0** | +43.6 s (ungated)² | **0 MB** |
   | B SigLIP zero-shot | 0.50 | **1.0** | **1.0** | +0.16 s + load | **0 MB** |

   ¹ C's *shot-type* score is low **by design**: `layout-only` can't read text, so it
   can't see ISBN (misses both IMPRINTs) — C is a *figure/region* detector, not a router.
   ² +43.6 s is the cost on a *triggered* image (~one extra OCR-length pass). The decision
   rule's "<50%" bar is batch-amortized *with gating*: triggering only the few
   figure-suspect images (~5/128) ⇒ ≈ +1.7 s/img avg (~+3%), comfortably under the bar.

   **Findings.**
   - **The figure placeholder in `prompts.py` does not work.** A's figure recall is 0 —
     dots.mocr in transcription mode ignores the `> **[Figure — …]**` instruction (and
     even false-positived on IMG_4340). Figure detection *requires* a real detector; the
     prompt line should not be relied on.
   - **Both B and C detect figure presence perfectly here**; C additionally yields
     **positional bboxes** (multiple figures per page), B only a whole-image label.
   - **Neither pushed the 16 GB M3 into swap** (Δ 0 MB). C reuses the one model (no new
     memory); SigLIP-base's ~370 MB also stayed clear — the memory risk did not materialize.
   - C's layout signal also flags the **multi-spine IMG_4310** as figure-heavy (3 `Picture`
     boxes, 0.55 area, sparse text) — a usable cue to stop routing it as a plain `PAGE`
     (which A does wrong). B nailed IMG_4310 `COVER` (1.00) but confused IMPRINT/COVER/SPREAD
     elsewhere (shot acc only 0.50).

   **Decision: adopt C (dots.mocr layout dual-pass), gated.** It satisfies the decision
   rule — gated added time ≪ 50%, **zero** swap, and it respects the hard single-model
   constraint (CLAUDE.md: "never hardcode a second model path"), which **B violates** by
   adding a second resident model. C also returns figure bboxes (future multi-figure pages)
   and a cue for the multi-spine cover. Keep the **text heuristic A for COVER/IMPRINT/SPREAD/
   PAGE routing** (it's good and free for IMPRINT via ISBN; C is weak there). SigLIP (B) is
   recorded as a strong, ~270× cheaper alternative to **revisit only if** the single-model
   constraint is relaxed or gated-C's wall-time proves too high in practice.

   **Implementation sketch (the now-unblocked §8.3 / §5 follow-up).** Gate: after body OCR,
   if the image is figure-suspect — low `text_score` for its pixel area, or a content page
   where the heuristic emitted no placeholder — run one `layout-only` pass; map `Picture`/
   `Table` bboxes to `> **[Figure — …]**` placeholders (caption text still from the text
   pass, never from layout). Reuse the loaded model; add `LAYOUT_PROMPT` to the experiment's
   isolation pattern, **not** to the transcription `PROMPT`. Bump `PROMPT_VERSION`.

---

## 9. Grouping redesign — title identity over session fences (run 2026-06-14)

The original Pass C (§3) used **hard fences**: split a new book on any capture-time gap
> 30 min or any GPS change. On the real `in/` batch this produced **61 books for 5**:

- **GPS jitter** — consecutive phone shots round to different 3rd-decimal coords (≈ km of
  drift *within one session*), so `prev.gps != rec.gps` fired on nearly every page.
- **Time gaps split real books** — "The enemy of all" was photographed on Dec 10 *and*
  Dec 12; any time fence cut it in two (or, with jitter, into one-book-per-page).
- **No per-page identity** — `running_header()` returned the first *body* line, so the
  header/page-reset splitter almost never fired and titles became 10-line paragraphs.

**Fix (pure re-grouping over the cache — no re-OCR, `PROMPT_VERSION` unchanged):**
replace the fences with the title-identity segmentation now documented in §3 — running
header (`page_header` + `_is_title_like`), library call number, soft (overridable)
session/GPS hints, page-reset-gated mid-session splits, a shared-title merge, and
key-image folding. `book_title` reordered to never return a body line. `write_report`
enriched with structured biblio fields + capture span + GPS radius + provenance so
mis-groupings are visible. Result on the 128 cached records: **5 books, correct
boundaries** (incl. the cross-session "enemy of all" and the 3 Sydney books separated by
call number). New helpers in `ocr.py`: `page_header`, `_is_title_like`, `_hdr_match`,
`call_number`, `_page_reset`, `_merge_shared_title`, `_fold_key_images`, `gps_radius`,
`book_capture`, `book_meta`. Removed: `_fence`, `_discontinuous`, `TIME_GAP_S`, `HEADER_SIM`.

**Title by exclusion (no call numbers).** A shelf/spine overview shot (the key image)
OCRs as one block per spine. `infer_title_by_exclusion` reads the spine title out of each
block (anchored on the slip's "User Group:" field; drops authors/publishers/addresses),
drops every block whose title matches an already-identified sibling book, and names the
remaining untitled book from what's left — e.g. IMG_4310 → "Leaves of the Sametree" (the
Power-and-Politics and Tribal-Communities spines are eliminated). Surfaced in the report's
key-image provenance line.

**Optional Zotero/RIS bibliography hint.** A `*.ris` export in the input folder is parsed
(`load_ris`) and each book is matched (`match_ris`) to **correct its title** and **complete
author/publisher/year/ISBN/city** (`book_record` drives both `write_report` and `write_book`
frontmatter; `_yaml_val` quotes colon-bearing titles). Matching compares only the *main*
title (pre-colon) at ≥ 0.85 ratio / containment, so a shared subtitle suffix can't false-match
(e.g. book 5 is *not* matched to the decoy "Piracy and Politics in the Malay World"). On the
sample: 3 of 5 books title-corrected + enriched ("Leaves of the **Same Tree** …", the full
"Enemy of All" and "Power and Politics" subtitles, authors, ISBNs); the 2 unmatched keep OCR
metadata. Output-only — the cache, grouping, and `PROMPT_VERSION` never depend on the RIS.

### 9.1 Orphan-cover folding — cover may LEAD or TRAIL its pages (run 2026-06-24)

**Problem.** The forward pass (§9) assumes a cover/imprint *leads* its book: such a shot
starts a book and the following pages join it, so a **leading** cover is never left alone
(it accretes its pages, and an anchored book doesn't split on varying chapter headers). But
the user also shoots **cover-last** (pages first, then the cover — e.g. `IMG_2249` covers the
pages from `IMG_2230`). A trailing cover has no following pages to adopt it, so rule 1
(`is_meta and hdr and cur.identity and not _hdr_match`) splits it off as a body-less one-shot
"book". This bites **only when the page headers differ from the title** (when they match, the
cover joins on the title regardless of position). The same split happens to a short caption
page misclassified as COVER (a stray `Map`/`Figure`). Diagnosed on the cache: 9 body-less
meta-only "books" (a mix of genuine trailing covers and misclassified figures).

**Fix (pure post-pass over the groups — no re-OCR, `PROMPT_VERSION` unchanged).**
`_fold_orphan_covers` runs after `_fold_key_images`: each body-less meta-only book is folded
into the same-session neighbour it belongs to — chosen by (a) title match, else (b) the nearer
capture-time gap (trailing covers default to prev) — and kept standalone only if neither
neighbour is same-session. A confident cover title (`_is_real_cover_title`, building on
`_is_title_like`) may name the host *only when the host has no cover/imprint title of its own*
(`book_title` takes the first metadata title; a non-cover orphan's `title:` line is stripped
before merging, so a misclassified figure/slip is re-attached for provenance but can never
override the host). New helpers: `_fold_orphan_covers`, `_choose_orphan_neighbour`,
`_absorb_orphan`, `_is_real_cover_title`, `_title_matches_book`, `_gap_secs`, `_strip_title_line`.

**Validation (existing cache, before/after diff).** 88 → 80 books, all 9 orphans cleared;
**no body-bearing book was split** and no host title was corrupted (the figure/slip orphans
folded in without changing their host's title; real covers folded into already-titled hosts).
Synthetic check: trailing-cover-with-differing-header now yields 1 book (was 2). **Known
limitation:** a cover in the *middle* of a run whose headers also differ still splits — it is
not body-less (it accretes the following pages), and lead-vs-continue is genuinely ambiguous
there; left as-is since the user's workflow is cover-first or cover-last, not mid-run.

---

## 10. Orientation probes + per-pass telemetry (run 2026-06-14)

The original `ocr_oriented` (§3) inferred rotation by running a **full** transcription at
0/90/270/180 and keeping the orientation with the most letters. Upright pages short-circuit
after one pass, but a rotated page paid ~4× the full per-page cost — observed at **~350s for
`IMG_4808`**. Separately, a single dense upright page (`IMG_4865`, no rotation) took **131.5s**,
and we discarded all of mlx-vlm's timing/token telemetry, so the bottleneck (prefill vs decode,
or a runaway hitting the 4096-token cap) was invisible.

**Fix (code; `PROMPT_VERSION` unchanged — orientation is not part of the cache key semantics,
but bump on the next cache-shape change since the record gained fields):**

- **Cheap orientation probes.** `ocr_oriented` keeps the upright fast path (one full pass at 0°,
  trusted when `_text_score ≥ MIN_TEXT_SCORE`). When 0° reads poorly, it ranks all four
  orientations with **probes** — `prep_image(..., max_edge=PROBE_MAX_EDGE=1024)` +
  `ocr_image(..., max_tokens=PROBE_TOKENS=64)`, scored by `_text_score` — then does **one** full
  pass at the winner (or reuses the 0° read if 0° wins, so a sparse-but-upright page stays at 0°).
  Cost: **~2 full passes + 4 probes** for a rotated page vs 4 full passes before. `prep_image`
  gained a `max_edge` param; new constants `PROBE_MAX_EDGE` / `PROBE_TOKENS` near the tuning block.
  Probes stay within the existing "infer orientation from OCR yield" design rather than adding a
  classical CV detector (which can't resolve upside-down 180°).
- **Per-pass telemetry.** `ocr_image` now returns `(text, stats)` where stats =
  `{seconds, prompt_tokens, generation_tokens, prompt_tps, generation_tps, finish_reason}` from
  the `GenerationResult`. Threaded through `ocr_oriented` → `process_image` into the record
  (`pass_stats`, plus `orient_passes` = full passes and new `orient_probes`), surfaced in
  `instrument.jsonl` and the console line (`…tok@…tps`, `RUNAWAY` when `finish_reason == "length"`).

**Single-pass speedup (Step 4 — DONE 2026-06-14, eval-gated).** Sweep added to `ocr.py eval` as
`--max-edge "1600,1280,1024"`: each value downscales the fixture through the batch pipeline's
`prep_image` (via `_maybe_downscaled`) before OCR, so scores reflect real first-pass resolution
(previously eval fed the model the raw full-res fixture and never exercised `MAX_EDGE` at all).

Result on `mlx-community/dots.mocr-4bit` over `test/`:

| max_edge | IMG_3018 | IMG_3020 (diagnostic) | mean |
|----------|----------|-----------------------|------|
| 1600     | 0.835    | 0.945                 | 0.890 |
| 1280     | 0.835    | 0.945                 | 0.890 |
| 1024     | 0.842    | 0.469                 | 0.656 |

`IMG_3020` holds at 1280 and **collapses at 1024** — the dumped 1024 prediction is a verbatim
repetition loop (the too-low-resolution runaway the quality gate is designed to catch). So
`FAST_MAX_EDGE = 1280` is adopted as the eval floor; `MAX_EDGE` stays 1600 and the §11 quality gate
escalates pages that regress. This makes adaptive escalation **live** for the first time
(`1280 < 1600`). **Caveat:** the `test/` fixtures are only 1280 px on the long edge, so the sweep
proves 1280 is non-lossy *down to the fixture resolution* and that 1024 breaks — it cannot directly
prove 1280 ≈ 1600 on true 12 MP captures. That residual risk is exactly what the quality-gated
escalation to `MAX_EDGE` backstops. `PROMPT_VERSION` was **not** bumped: existing cache records were
read at ≥1280 and stay valid; only new/`--force` runs pick up the 1280 first pass.

---

## 11. Read-quality heuristic + adaptive resolution (designed 2026-06-14)

**Goal.** Detect a poor OCR read (scrambled / looping / dropped-out text — the classic symptom of
too-low resolution on small print) so the pipeline can (a) **retry that page at higher resolution**
and (b) **surface a confidence hint in the report** for human review. This turns Step 4's static
`MAX_EDGE` drop into *adaptive resolution*: a cheap low-res first pass for every page, escalating to
full/native res only for pages that read poorly — most pages stay cheap, hard pages stay accurate.

### Two ways to measure "confidence" (both documented; we ship B first)

**Option A — model confidence (mean token logprob).** The principled signal: the model's own
per-token log-probability, averaged over the generation. Catches uncertainty even when the output
*looks* plausible. Cost: mlx-vlm's non-streaming `generate` only carries the **last** token's
logprobs on the returned `GenerationResult` (`dispatch.py:1372`); the full per-token sequence is
only available via `stream_generate`, which yields `(token, logprobs)` per step (`dispatch.py:1137`).
So Option A requires rewriting `ocr_image` to consume the stream and accumulate the sampled-token
logprob each step — more code plus small per-token Python overhead. **Deferred to phase 2.**

**Option B — pure-text quality heuristic (no model internals).** Dependency-free, in keeping with
the codebase. A 0..1 `text_quality(text)` score combining:
- **word plausibility** — fraction of word tokens containing a vowel (scrambled OCR yields
  vowelless consonant-garble);
- **non-repetition** — `min(unique-line ratio, unique-trigram ratio)`; a runaway loop
  repeats whole lines *or* repeats a phrase inline with no line breaks (the trigram grain
  catches the latter, which the line ratio alone scored as clean — see §15);
- gated by the signals already added in §10 (`finish_reason == "length"` runaway, `_text_score`
  yield/dropout).

**Honest limitation (why A stays on the roadmap):** Option B catches garble, loops, and dropout but
**cannot** catch *confident-but-wrong character substitutions* (a low-res "rn"→"m" misread that
produces a real word). Only token logprobs (A) see that. B is the cheap 80% solution; escalate to A
if eval shows B misses real degradations.

### Implementation (B)

- `text_quality(text) -> float` in `ocr.py` (near `_text_score`), pure + stdlib-only.
- **Adaptive resolution** in the OCR path: orientation is still decided by yield via probes (§10),
  but the chosen-orientation full pass runs first at `FAST_MAX_EDGE`; if `text_quality` is below
  `QUALITY_RETRY`, re-OCR that one orientation at `MAX_EDGE` and keep the higher-quality read. New
  constants `FAST_MAX_EDGE` / `QUALITY_RETRY` near the tuning block. **All three numbers
  (`FAST_MAX_EDGE`, `QUALITY_RETRY`, and the §10 probe sizes) are eval-pending** — initial values
  are conservative (`FAST_MAX_EDGE = MAX_EDGE` ⇒ escalation is a no-op) until the `ocr.py eval`
  sweep runs after the batch, so this ships without changing read behavior or invalidating the
  in-flight cache.
- **Record + report:** store `quality` per record; `log_event` already carries the cost signals, add
  `quality`. In `write_report`, flag low-quality pages inline (`⚠ low read quality (0.xx)`) so the
  report doubles as a review queue.
- **Cache/versioning:** `quality` is an additive record field; `write_report`/`log_event` read it
  with `.get()` so old (in-flight) cache records without it still render. **Do not bump
  `PROMPT_VERSION` now** — it would invalidate the entire running batch. Bump only when we later
  enable an eval-tuned `FAST_MAX_EDGE` and want a clean, uniform re-run.

### Verification

**`QUALITY_RETRY` set to 0.95** (eval'd against `test/`): clean fixture ground truth scores
1.000 / 0.995 and the actual model reads 0.998 / 0.997, while garble/looping reads score 0.12–0.16
— so 0.95 sits in a wide clean gap and flags real degradation without false-flagging good pages.
Re-confirm `text_quality` on the `test/` fixtures (`python ocr.py batch` over `test/` or the
ground-truth `*_text.txt`) whenever the heuristic changes. **`FAST_MAX_EDGE` is now eval-tuned to
1280** (see Step 4 above): the `--max-edge` sweep lowered it as far as eval (esp. `IMG_3020`)
tolerates — 1280 holds, 1024 collapses — and the quality gate recovers any page that regresses.

---

## 12. Local RAG + MCP lookup over the produced Markdown (designed 2026-06-14)

> **Status:** designed, ready to implement. New sibling tool `rag.py`; does **not** touch
> `ocr.py`/`prompts.py`. Same hard constraints as the OCR tool (Python 3, Apple-Silicon,
> 100% offline after one model download, bare-bones, minimal deps).

### 12.1 Goal & non-goals

**Goal.** Let Claude answer questions like *"where does James C. Scott say something about
legibility in a maritime context"* against the `out/book_*.md` corpus **without loading whole
books into context** — the MCP `search` tool returns a few short, citation-stamped page
snippets, so the model reads ~hundreds of tokens, not a book. This replaces the brittle
lazy-loading-skill approach with a single MCP tool call.

**The query is the design driver.** It has two halves with opposite retrieval needs:
- *"legibility … maritime context"* — paraphrased concept, no exact keyword → **dense
  embeddings** (semantic).
- *"James C. Scott"* — an approximate **proper noun**, the single worst case for dense vectors
  (name embeddings collapse together) → a **lexical** channel.

So the retriever is **hybrid** (dense + lexical, fused), not vectors-only. This is the biggest
accuracy lever for exactly this kind of question and it stays dependency-free (see §12.4).

**Non-goals (keep it bare-bones).** No ingestion/extraction framework (we already emit clean
Markdown), no document chunkers from LangChain/LlamaIndex, no web UI, no server DB process, no
cloud. No re-OCR. The corpus is small (~21 books, ~1.6 MB, est. ~1–1.5 k page-chunks), which is
*why* most RAG machinery is unnecessary — a brute-force matmul is sub-millisecond here.

### 12.2 Why build vs. adopt (records the decision behind the user's options)

- **Chroma** — its real value (good embeddings + a Skill) is two functions we replicate; in
  return it drags onnxruntime + a server/store model. Sledgehammer at this scale, fights
  "minimal deps". Rejected as default.
- **FAISS** — only earns its keep at ~10⁵+ vectors; here numpy is faster to stand up with
  nothing to tune. **Kept as an opt-in backend** so the user can play with real ANN indexes
  (see §12.3) — not the default.
- **mcp-local-rag** — right shape, but its weight is in ingestion, the part we've already
  solved; we'd import it to delete most of it. Rejected.
- **niazarifin/rag_local** — Mongo + hash embeddings; hash embeddings can't do semantic match.
  Rejected.

### 12.3 Architecture — durable catalog + **pluggable** vector backend

Two layers, deliberately separated so the experimental part is swappable (answers the user's
asks **(a)** swap SQLite↔FAISS↔DuckDB and **(b)** swap NumPy↔FAISS search):

**Layer 1 — Catalog (always SQLite at `out/rag.db`, stdlib, the source of truth).** One row per
chunk: `id, book_file, book_title, author, year, image, page, text, embed_text, vec BLOB,
content_sha`. This is cheap, human-inspectable, and **stable** — it is *not* the thing we swap. An
FTS5 virtual table over `text`+`author`+`book_title` provides the lexical channel (§12.4).

**How vectors actually live in SQLite (no native vector type).** SQLite has no vector column;
`vec` is a plain **BLOB of `float32` bytes** — a 384-dim `bge-small` vector is `384×4 = 1536`
bytes, written with `np.asarray(v, np.float32).tobytes()` and read back with
`np.frombuffer(blob, np.float32)`. SQLite stores/returns it opaquely and does **no math** on it;
the similarity is computed in numpy after loading the BLOBs (the `numpy` backend below). We
deliberately **do not** use `sqlite-vec`/`sqlite-vss` (a real in-SQLite KNN type) — extra binary
dep, and at ~1.5 k vectors in-memory numpy is simpler and faster. Storing the embedding as a BLOB
here is also what makes re-indexing into a different backend free — the bytes are portable, no
re-embedding (see §12.6).

**Layer 2 — VectorBackend (the swappable part).** A small `Protocol`:

```
class VectorBackend(Protocol):
    def build(self, ids: list[str], vecs: np.ndarray) -> None: ...   # ingest all vectors
    def query(self, qvec: np.ndarray, k: int) -> list[tuple[str, float]]: ...  # ids+scores
    def save(self, path) -> None: ...
    def load(self, path) -> None: ...
```

Selected by `--backend {numpy,faiss,duckdb}` (default `numpy`). Three implementations:
- **`numpy`** (default, zero extra deps) — vectors held in memory from the SQLite `vec` BLOBs;
  `query` = one normalized `vecs @ qvec` argpartition top-k. **This backend is itself the place
  the two concerns (a)/(b) are visibly separate**: storage = SQLite blob, search = brute-force
  NumPy.
- **`faiss`** (opt-in, `faiss-cpu`) — `IndexFlatIP` (exact) by default, or `IndexHNSWFlat`
  (approx, a knob to *play* with ANN); persisted via `faiss.write_index`.
- **`duckdb`** (opt-in, `duckdb`) — table of `FLOAT[]` + the VSS extension
  (`array_cosine_similarity` / HNSW); lets the user poke vectors with SQL.

**Honest note on (a) vs (b).** Storage and search are only *fully* orthogonal for the `numpy`
backend; for FAISS and DuckDB the index **is** both the store and the searcher, so the right
granularity is **one `--backend` switch**, not two independent flags. The catalog (Layer 1)
always remains SQLite regardless of backend, and any backend is rebuildable from it in seconds —
so "swap the store" and "swap the search" are both satisfied without pretending they're
independent where they aren't. Opt-in backends are **lazy-imported** with a clear "pip install
faiss-cpu" message, so the default install stays bare-bones.

### 12.4 Retrieval — hybrid dense + lexical (handles the example query)

1. **Embeddings (dense).** `sentence-transformers`, default `BAAI/bge-small-en-v1.5` (384-dim),
   on MPS; torch is already a project dep. **Default chosen: `bge-small-en-v1.5` (fast).** BGE
   needs the query instruction prefix
   (`"Represent this sentence for searching relevant passages: "`) — passages embedded raw.
   `--embed-model` swaps it (e.g. `bge-base-en-v1.5`, 768-dim) as a recall knob; never hardcode a
   second model path (mirrors the `DEFAULT_MODEL` convention). Offline after one download
   (`HF_HUB_OFFLINE=1`, same pattern as the OCR model).
2. **Metadata-enriched embedding text.** Each chunk's `embed_text` is prefixed with a compact
   citation header from the file's frontmatter:
   `"{author} — {title} (p.{page}): {page text}"`. This lets *"James C. Scott"* match
   semantically via the author string even though the body never repeats the author — directly
   serving the example query.
3. **Lexical channel (SQLite FTS5, stdlib — no new dep).** The same chunks indexed for
   full-text/prefix match over `text` + `author` + `book_title`. This is what reliably catches
   the **proper noun** "Scott" that dense vectors blur.
4. **Fusion.** Reciprocal Rank Fusion (RRF, `1/(k0+rank)`, k0≈60) over the dense top-N and the
   FTS5 top-N — stdlib arithmetic, no extra dep, robust to score-scale differences. Default
   `hybrid`; `--mode {hybrid,dense,lexical}` to compare. **Hybrid is the default precisely
   because the motivating query needs both halves.**
5. **(Deferred, phase 2) cross-encoder rerank** (`bge-reranker-base`) over the fused top-N for
   precision. A "bell/whistle" — left out of v1 to stay minimal; noted as the first upgrade if
   eval (§12.8) shows fusion alone misses.

### 12.5 Chunking

Natural unit = **one page = one chunk** (`### Page N` under `## IMG_xxxx.jpeg`), because each page
already carries its citation anchor (book title + image id + page number) and pages are already
small. Rules: merge a tiny page (< ~200 chars) into its neighbor; split a very long page on
paragraph boundaries with a small overlap; keep figure/caption placeholders (they carry caption
text); parse frontmatter once per file for `title/author/year`. Output of chunking is rows for
Layer 1.

### 12.6 Resumability (CLAUDE.md mandate — embedding is the expensive step)

- **Embedding cache by content hash.** `content_sha = sha1(embed_text)`; the `vec` BLOB is keyed
  by `(content_sha, embed_model)`. `rag.py index` re-embeds **only** new/changed chunks; unchanged
  pages are skipped. A killed run resumes; `--force` re-embeds everything.
- **Backend build is cheap and derived.** Switching `--backend` rebuilds Layer 2 from the cached
  `vec` BLOBs in Layer 1 with **no re-embedding** — so experimenting across numpy/faiss/duckdb
  costs seconds, not a re-encode.
- Catalog writes are per-chunk/flushed so a crash loses at most the in-flight chunk.

### 12.7 CLI-first surface, with MCP as an optional add-on (mirrors `ocr.py`'s subcommands)

**Decision: the primary interface is the CLI, invoked on demand by a Claude Code Skill — not an
always-running MCP server.** A Skill shells out to `rag.py search …`, gets citation-stamped
snippets back, done; nothing stays resident, no server lifecycle to manage, and it composes with
the existing `ocr.py` CLI ergonomics. The MCP server is kept as a thin **optional** wrapper for
users who prefer a registered tool, but it is no longer the main path.

New single file `rag.py`:
- `rag.py index [--src out/] [--backend numpy] [--embed-model …] [--force]` — chunk → embed
  (cached) → build backend. Resume-by-default.
- `rag.py search "QUERY" [-k 5] [--mode hybrid] [--backend …] [--book …] [--json]` — the
  **primary** retrieval path. Default output is compact human/Markdown; `--json` emits a structured
  array `[{citation, book, author, page, image, text, score}]` for a Skill to parse. `citation` is
  paste-ready (`"Andaya, Leaves of the Same Tree (2008), IMG_4894 p.3"`). Must be **fast to invoke**
  (cold start matters for a per-call CLI): lazy-load the embedding model only when a dense/hybrid
  query needs it, and keep the numpy backend's load-blobs-and-matmul path warm-start-free.
- `rag.py get-page IMAGE_ID [--neighbors N]` — fetch a hit's page (± adjacent pages) for more
  context without loading the whole book; also `--json`.
- `rag.py serve [--backend …]` — **optional** MCP server over **stdio** (official `mcp` Python SDK /
  FastMCP), fully offline, exposing `search_library` / `get_page` as thin wrappers over the same
  functions the CLI uses. For users who register an MCP tool; not required for the Skill path.

**The Skill (primary deliverable).** A small Claude Code Skill (e.g. `library-search`) whose
instructions tell the model to run `python rag.py search "<query>" --json` (and `get-page` for
more context) and cite the returned `citation` field. This is the brittle-lazy-loading replacement:
the Skill is a *thin shell-out*, not a context loader — all retrieval logic lives in `rag.py`.
Document both paths (Skill/CLI primary, MCP optional) in `README.md`.

### 12.8 Dependencies

- **Default:** `sentence-transformers`, `mcp`. (`numpy`/`sqlite3` already present.)
- **Opt-in extras (lazy-imported):** `faiss-cpu`, `duckdb` — only if that `--backend` is chosen.
- A separate `requirements-rag.txt` (don't bloat the OCR `requirements.txt`); extras commented as
  optional. No CUDA, no cloud, no web framework.

### 12.9 Implementation steps (each independently testable)

> **Progress (resume here) — as of 2026-06-14.** Steps 1–3 + 5 done. `rag.py` has `index`,
> `search` (+`--json`), `get-page`, and `serve` (MCP stdio). `out/rag.db` is populated (1163
> chunks, `bge-small-en-v1.5` dim 384, FTS5 index). **The Skill + MCP are NOT wired into this
> project** — by user decision they live in `integration/` as a portable bundle to import into
> *other* Claude projects (which query this install over absolute paths): `integration/skill/…`
> (CLI Skill, preferred) and `integration/mcp/library-search.mcp.json` (optional MCP). The two are
> independent — import either/both per project. **Only step 4 (faiss/duckdb backends) remains, and
> it's optional.** Everything offline (`HF_HUB_OFFLINE=1`); `sentence-transformers` + `mcp`
> installed; `bge-small` cached. Paths resolve against the install dir (`SCRIPT_DIR`) so the
> Skill/MCP work from any cwd.
>
> *Step-3 result (the validation bar, met):* the motivating sea-nomads query ("…not by name of a
> particular island … but identification with the sea itself") ranks the true passage
> (`book_01` / IMG_3557 / p.135) at **dense #4, lexical #1, hybrid #1** — hybrid+RRF lifted it to
> the top exactly as predicted. The fuzzy-author case also works: "Heller-Roazen … pirates as
> enemies of all mankind" returns `book_02` passages (author-enriched embed_text + FTS author
> column). Use both as regression checks.

1. **Chunker + catalog.** ✅ **DONE** (commit `8e590d0`). `rag.py index` parses frontmatter +
   `## IMG_*` / `### Page N` into SQLite rows; tiny-page merge + long-page split share a
   `_fold_small` helper (no sub-`MIN_CHARS` stubs); upsert preserves `vec` on unchanged content,
   nulls it on change. 21 books → **1163 chunks** (203–2658 chars); idempotent re-runs.
   Found+fixed a real bug: `split_long` was flushing a short paragraph alone before a huge one.
2. **Embedding + cache.** ✅ **DONE** (commit `83366cb`). sentence-transformers on MPS, normalized
   float32 BLOBs in `chunks.vec`; cache key is `(content_sha, vec_model)` via a new `vec_model`
   column + a `meta` table (`embed_model`/`embed_dim`); batches of 64 checkpoint per batch;
   `--no-embed` chunks only; `--force` re-embeds. Re-run embeds nothing; offline verified.
   `requirements-rag.txt` added (sentence-transformers active; mcp/faiss/duckdb commented).
   **Passages are embedded raw — the BGE query-side prefix is NOT applied at index time; it
   belongs in step 3's `search`** (`"Represent this sentence for searching relevant passages: "`).
3. **VectorBackend protocol + `numpy` impl + FTS5 + RRF fusion.** ✅ **DONE.** `rag.py search`
   works in `--mode {dense,lexical,hybrid}` with `-k`/`--book`. `NumpyBackend` (build/query
   contract that step 4 mirrors) loads `vec` BLOBs into one `(n,384)` matrix + matmul against the
   BGE-prefixed query vector; FTS5 standalone table over `text`+`author`+`book_title` rebuilt each
   `index` (`build_fts`); `_fts_query` = OR-of-terms (stopword-filtered) ranked by bm25; `rrf`
   fuses (k0=60). Motivating query → hybrid #1 (see progress box).
4. **FAISS & DuckDB backends (opt-in, lazy import).** ⏸ **DEFERRED — optional** (user decision):
   numpy meets the need at ~1.2k vectors. When wanted: `--backend faiss|duckdb` rebuilds from
   cached vectors; assert top-k parity with numpy on exact indexes. `load_backend()` already
   raises a clear "step 4" message for these names — replace with real lazy-imported impls so the
   default install stays bare-bones.
5. **CLI output modes + Skill + MCP, as a portable bundle.** ✅ **DONE.** `search --json` emits the
   structured array (`score, citation, book, author, year, image, page, book_file, text` — full
   chunk text so it's quotable); `get-page IMAGE_ID [--neighbors N] [--json]` fetches a page ±
   neighbours; both back `serve` (FastMCP stdio, tools `search_library`/`get_page`), verified with
   a real MCP client. Shared `result_dict()` + `citation()` back CLI + MCP. **Key correction from
   the original plan:** the Skill/MCP are *not* installed into this repo — they're an importable
   bundle in `integration/` (`skill/library-search/SKILL.md` with absolute paths + preferred;
   `mcp/library-search.mcp.json` optional) so *other* projects can use this library. The two are
   independent; `integration/README.md` documents import + how to choose. (Bug fixed: human
   `get-page` must call `citation(row)`, not `row['citation']`.)
6. **Eval harness (stdlib only).** ✅ **DONE.** `rag.py eval` scores dense/lexical/hybrid by
   recall@1/3/5 + MRR against a **gitignored** `rag_probes.json` (`{query, book?/image?/page?}`
   matchers; throwaway, *not* a committed fixture — mirrors `experiments/`). Loads embedder +
   backend once. Result on 5 content-grounded probes: dense MRR 0.77, lexical 0.90, hybrid 0.87 —
   the headline being **robustness**: on the literal sea-nomads query dense=3 / lexical=1 /
   hybrid=1, and hybrid is the only mode with R@3=1.00 across both paraphrase and proper-noun
   probes (small set ⇒ MRR deltas are noisy, so don't over-read lexical edging hybrid here).
   **Also fixed:** relative `--src/--db/--probes` now resolve against `SCRIPT_DIR` (the install),
   not the caller's cwd — the original Skill failed with "no catalog at out/rag.db" when run from
   another project. `serve` resolves the same way.

Suggested commit boundaries: 1–2, 3, 4, 5, 6. Each leaves the tree runnable.

### 12.10 Files

- **New:** `rag.py` (chunk/index/search/get-page/serve), the `library-search` Skill,
  `requirements-rag.txt`, README section, optional MCP config entry; `.gitignore` adds
  `out/rag.db` + `rag_probes.json`.
- **Untouched:** `ocr.py`, `prompts.py` — the RAG tool only *reads* `out/*.md`.

### 12.11 Verification

1. **Motivating query** — `rag.py search "where does James C. Scott discuss legibility in a
   maritime context"` returns the right page(s) with correct citation; confirm hybrid beats
   dense-only and lexical-only on §12.6's probe set.
2. **Token win** — a `search_library` call returns only top-k page snippets (hundreds of tokens),
   not a book.
3. **Backend parity** — numpy vs faiss(Flat) vs duckdb return the same top-k on the same query.
4. **Resumability** — re-run `index`, confirm zero re-embeds; `--force` re-embeds; killed run
   resumes.
5. **Offline** — `serve` and `search` run under `HF_HUB_OFFLINE=1` with no network.

---

## 13. Detokenizer UTF-8 monkeypatch (run 2026-06-15) — REVISIT ON DEP UPDATE

**Symptom.** During an 873-image batch, `IMG_5906` crashed the whole run with
`UnicodeDecodeError: 'utf-8' codec can't decode byte 0x98` — the model generated the
word *"controvert"* and emitted a stray byte (`b' cont\x98rovert'`). All 8 wrapper
retries hit the same deterministic page and gave up, so the run never indexed.

**Root cause (upstream bug, `mlx-vlm==0.6.3`).** `mlx_vlm/tokenizer_utils.py`
`BPEStreamingDetokenizer.add_token` flushes its buffer with a *strict* `.decode("utf-8")`
(line ~237). The same class's `finalize()` (line ~250) already decodes with
`errors="ignore"` — the streaming flush path was simply missed. A single bad byte mid-stream
therefore kills the entire page (and, without a guard, the whole batch).

**Fix (two layers, both in `ocr.py`).**
1. `_patch_detokenizer_utf8()` — an idempotent monkeypatch applied once in `load_model()`
   that re-defines `add_token` to flush with `errors="ignore"`, matching the library's own
   `finalize()`. Bad bytes are dropped and the (ASCII) text is recovered intact
   (`b' cont\x98rovert'` → `' controvert'`). We only care about printed ASCII/Latin text, so
   dropping an undecodable byte is lossless in practice. Verified: `IMG_5906` then OCR'd to
   6035 chars including "controvert".
2. `cmd_batch` wraps each `process_image` in try/except → logs to `out/failures.jsonl` and
   skips, so any *other* unrecoverable page (truly corrupt file, OOM) can't kill an overnight
   run. Kept as a backstop even though #1 fixes this specific class.

**REVISIT condition.** The monkeypatch reaches into vendored library internals and is keyed
to `mlx-vlm==0.6.3`'s `BPEStreamingDetokenizer`. On any `mlx-vlm` bump:
- Check whether `tokenizer_utils.add_token` now decodes with `errors=` (upstream may have
  fixed it). If so, **delete `_patch_detokenizer_utf8()`** and its call.
- If the class/method was renamed or restructured, the patch silently no-ops (it guards on
  the class attr) — re-point it or remove it. Re-run `python ocr.py run in/IMG_5906.jpeg`
  as the regression check: it must produce text, not raise.
- Keep the `cmd_batch` try/except regardless — it's engine-agnostic resilience.

---

## 14. Library-wide duplicate-book merge (run 2026-06-25)

**Problem.** A book read in two non-adjacent sittings (other books photographed in
between) lands as two separate books; a stray cover shot lands as a third. The
existing `_merge_shared_title` only rejoins *adjacent* runs, so cross-library
duplicates survive. Trigger case: Ingold's *Making* and Gusinde/Barthe & Barral's
*The Lost Tribes of Tierra del Fuego*, each read twice.

**Why auto-merge can't be the whole answer (evidence from the 111-book run).**
- Bibliographic keys are sparse: ISBN 18/111, publisher 12, year 21, call number 3,
  author 0. So a strong key can't be the primary signal.
- Title identity alone is unsafe — distinct books share generic titles
  (`SINGAPORE`/Bloomsbury vs `SINGAPORE`/Oxford-2017; `_hdr_match` even ties
  `DIRECTIONS`~`SAILING DIRECTIONS`~`Friction`).
- The real duplicates here are **title-invisible**: Ingold reads as `MAKING` and
  `It's utility as a relationality`; Gusinde reads as `Page 14-3: The Body Painted
  Shoort` and `Kawësqar woman.` — *no shared resolved title*. Pure title matching
  would miss exactly the cases the user cares about.

**Design — tiered, conservative; the human allow-list is primary.**
`group_images(records, merges=None)` runs the existing pipeline, then:
1. `_merge_library_duplicates` — two AUTO passes, both safe:
   - **Tier 1**: a body-less meta-only book (lone cover/imprint, incl. a
     false-positive cover) whose *confident* title (`_is_real_cover_title`) matches
     a body-bearing book anywhere → fold in (a bare cover has no pages to lose).
   - **Tier 2**: two body-bearing books sharing an **exact** ISBN/call → merge. A
     *differing* key is a hard negative — different editions stay apart (Wood's
     *Power of Maps* Routledge-1993 vs Guilford-2010 are kept separate by design).
2. `_apply_manual_merges(books, groups, moves)` — the **primary** mechanism: an
   optional `in/merges.txt` allow-list (mirrors the RIS hint; absent ⇒ no-op; never
   touches the cache). Two operators:
   - `IMG_a + IMG_b [+ …]` — fold the WHOLE books containing these shots into one.
   - `IMG_host += IMG_x [IMG_y …]` — MOVE individual stray shots into the host's
     book (for a cover/page the grouper filed under the wrong book, e.g. the stray
     *Lost Tribes* cover `IMG_2985` that led Weizman's *Hollow Land*).
   Merged records are re-sorted into capture order, so each reading's pages stay
   contiguous and in sequence; provenance is recorded as `merged_from` and rendered
   as an "Assembled from N shots across multiple readings" note in the book file.

**Discovery aid (not auto-applied).** `merge_candidates(books)` emits
`out/merge_candidates.json` — same-title page+page pairs ranked by a score (exact
title +3, one-sided key +2, long specific title +1, generic title −3, different
strong keys −100) to help a human populate `in/merges.txt`. Title-invisible
duplicates won't surface here — those are found by eye and listed directly.

**Verification (2026-06-25 fixture).** With the fixture `in/merges.txt`
(Ingold `2818+2927`, Gusinde `2881+2973`, move `2881 += 2985`): 111 → 109 books
(absolute counts predate §15's cover-title splits + the later `merges.txt`/`titles.txt`
entries, which take the live batch to 107; the relative merge behavior below is unchanged);
Ingold book spans 2818–2938 across both readings; Gusinde book spans 2881–2985 incl.
the rescued stray cover, records in capture order; Weizman no longer holds 2985;
`SINGAPORE`×2 and the Wood editions stay separate; `merge_candidates.json` ranks the
real `intertidal` duplicate first (+6). RAG re-indexed against the merged books.

**Known limitation.** Merge does not itself resolve titles — a merged book is only as
well-named as `book_title` makes it, which can still pick a plate caption or a spine
fragment over the real title. Correct it via the Zotero RIS hint (title correction) or,
when the title is unreadable/absent from the bibliography, the per-book `in/titles.txt`
override (now implemented — see §15); out of scope for the merge pass itself.

---

## 15. Cover-title resolution + report provenance (run 2026-06-25)

Triggered by a `report.md` review: several books took a wrong/partial title and a few
title pages looked "missing". Root causes and fixes, all in the pure grouping/emit pass
(no re-OCR needed — `book_title`/`page_header` re-derive from the cached `ocr_text`, so
old caches self-correct):

- **`### Page N` swallowed the cover title.** dots.mocr prefixes every shot with a folio
  heading; `parse_metadata` took it as `title: Page 1`, so covers failed to name their
  books and the title fell back to running-header voting (gave `PREFACE`, `THE ECONOMIC
  HISTORY`, `Malacca`). New `_cover_title()` skips the leading folio / call-no / ISBN
  noise, then joins the first run of consecutive title-like lines (so a wrapped title like
  `THE ECONOMIC HISTORY` / `OF SINGAPORE` is captured whole). Used by `parse_metadata`,
  `page_header`, and `book_title`.
- **A stray/interior shot misread as the cover.** `book_title` now takes the EARLIEST
  cover (capture order) so a later short page misclassified COVER (a `PREFACE` page) can't
  out-rank the real title page; skips `_GENERIC_TITLES`; and lets a running title repeated
  on ≥ `COVER_OVERRIDE_VOTES` shots veto a cover it disagrees with (a chat-screenshot shot
  misfiled as a cover loses to the book's own running header — e.g. Unruly Waters).
- **Over-eager containment merge.** `_hdr_match` containment now requires word-boundary
  alignment, so `SINGAPORE` no longer matches `Leluhur Singapore's Kampong Gelam` (which
  had swallowed a following book), while `Tribal Communities` still binds to its CIP form.
- **Generic-title RIS false match.** `match_ris` skips `_GENERIC_TITLES` queries, killing
  a mis-titled `Preface` page matching a Foucault "Preface" RIS entry.
- **Runaway-loop blind spot.** `text_quality` only checked line-level repetition; a runaway
  with no line breaks (one giant `…the Jutai, the Jutai…` paragraph) scored 1.0 and skipped
  the high-res retry. Now also checks trigram diversity (`min(line_uniq, phrase_uniq)`).
  Takes effect on re-OCR (cache is frozen); re-run a single page by deleting its
  `out/cache/IMG_*.json` and re-running `batch` (resumable).
- **Covers were invisible in the report.** `write_report` lists only body pages, so a
  cover/imprint shot (e.g. `IMG_0216`) never appeared and looked "skipped". Added a
  **Cover/title shot(s)** line per book so every input image is traceable.

**Known limitation (unchanged class as §14).** A book whose title is buried in a
title-page *list* (a series page) or lost to a runaway read, AND absent from the RIS,
can't be auto-titled (e.g. "Cosmopolitical Ecologies Across Asia" — not in `Studio.ris`).
Cross-session splits/duplicates are still resolved via `in/merges.txt` (the Invention of
Rivers two sittings, the Buckley fragments, the Pictorial-History pages, and the Bhandar
pages mis-filed under Unruly Waters were added there).

---

## 16. Cover titles by largest font (layout dual-pass, run 2026-06-26)

Triggered by a second `report.md` review: covers were still named after the **publisher**
or **author** rather than the book. Root cause: `_cover_title` (§15) takes the *first* run
of title-like lines in dots.mocr's **reading order**, but on a cover that order is arbitrary
w.r.t. type size — the publisher/author imprint OCRs first as often as not. Confirmed across
the batch:

| Book | Shot | OCR'd as | Cover's largest type (true title) |
|------|------|----------|-----------------------------------|
| 27 | IMG_3036 | The Guilford Press, New York London | Rethinking the Power of Maps |
| 32 | IMG_4358 | Geoffrey Benjamin | Tribal Communities in the Malay World |
| 38 | IMG_4798 | British Library | Secret Maps |
| 43 | IMG_5026 | Geoffrey Benjamin & Cynthia Chou | Tribal Communities in the Malay World |
| 59 | IMG_5922 | Bloomsbury | Singapore: A Modern History |

**Fix — a book title is the biggest type on the cover, not the first line.** `dots.mocr`
exposes type size through its layout pass: `COVER_TITLE_PROMPT` (in `prompts.py`) asks for
layout **with text** (unlike `LAYOUT_PROMPT`, which suppresses text for figure detection)
and `_pick_cover_title` takes the tallest `Title` bbox (height = font-size proxy), joining
boxes within `_COVER_TITLE_FONT_RATIO` (0.55) of the tallest so a wrapped title is captured
whole ("Tribal Communities" / "in the Malay World") and de-duping the front-cover/spine
repeat ("Secret Maps" × 2). No `Title` label ⇒ fall back to the single tallest title-like
text box; nothing usable ⇒ "" and the §15 reading-order heuristic still runs (no regression).
This is one extra layout pass **per COVER shot only** (~150 in the batch), and the layout
text never enters the transcription body.

- **Plumbing.** `process_image` computes `cover_title` for COVER shots and stores it on the
  record; `parse_metadata(typ, text, cover_title)`, `book_title`, and `page_header` all
  prefer it over the text heuristic. `_parse_layout` now carries the `text` field (harmless
  to the figure path, which reads only `category`).
- **No `PROMPT_VERSION` bump.** This is additive and cover-only; bumping the version would
  invalidate all 2200+ body caches for nothing. Instead `cmd_batch` **backfills**: a cached
  COVER lacking `cover_title` gets one layout pass via `backfill_cover_title` and is re-saved
  (resumable — the field, even when "", is the checkpoint; `--no-cover-backfill` keeps the
  fast emit-only path). Body OCR/caches are untouched.

**Spine-stamp leak (book 30, "GENERAL REF").** Separate bug, same review. The only
spine/shelf key-image book (IMG_4310, three spines) was named from a **runaway-truncated
library stamp**: "GENERAL REFERENCE LIBRARY" was cut to "GENERAL REF", which slipped past
`_SPINE_STOP` (the "reference" stem was gone) and isn't a `User Group:` block. `_spine_titles`
now **requires the `User Group:` anchor** (real spine titles always follow it; the slip
fragment has none) and `_SPINE_STOP` also catches `general ref` / `state librar`. With the
stamp gone, exclusion finds no new title (both real spines belong to named siblings) — and
the book's body (Straits of Melaka / Cham / Sailendra trade) is plainly Andaya's *Leaves of
the Same Tree*, the same book as the later cover sitting (IMG_4893), so it's rejoined via
`in/merges.txt` (`IMG_4310 + IMG_4893`) — the §14 title-invisible-duplicate path.

**Refinements found running the 153-cover backfill (subtitle, speed, byline veto, folio):**
- **Subtitle by geometry, not reading order.** `_pick_cover_title` joins same-font `Title`
  boxes, then absorbs a *hugging* subtitle: the next box(es) whose top sits within
  `_COVER_SUBTITLE_GAP_RATIO` of their own height below the title (a tight gap = same title
  block; the author/imprint is set farther down, so the gap stops there). `_looks_like_byline`
  also stops the scan from eating an author set close under the title (e.g. "Denis Wood").
  This recovers "Singapore: Wealth, Power and the Culture of Control" from a `Title`
  "Singapore" + a smaller subtitle box.
- **Adaptive decode budget.** The layout pass needs the full MAX_EDGE image but rarely a full
  4096-token transcription; capping at `COVER_LAYOUT_MAX_TOKENS` (1536) keeps a sparse cover
  fast. A chatty/back-cover shot overruns the cap and the *truncated* JSON scrambles the boxes
  (a garbled title with a half-token tail, e.g. "… Tribal Communities no en"), so on
  `finish_reason=="length"` we redo at the full budget. ~50s/cover at 1600px; a 1024px pass
  is faster but silently misses small-on-cover titles, so resolution is not negotiable.
- **A byline running header must not veto a real cover title.** §15's `COVER_OVERRIDE_VOTES`
  rule (a repeated running title beats a stray "cover") wrongly fired when the verso header was
  the *editors'* names ("Geoffrey Benjamin"): `book_title` now skips that veto when
  `_looks_like_byline(top)`.

**Model non-determinism (escape-hatch cases).** dots.mocr's layout pass is not fully
deterministic on MPS: a few covers alternately emit/withhold the subtitle box or scramble a
busy back-cover, so the heuristic can't pin them. Resolved with the existing hints —
`IMG_4358 + IMG_5026` (the two *Tribal Communities* sittings fold together, so the clean cover
wins) in `in/merges.txt`, and `IMG_5922 = Singapore: A Modern History` in `in/titles.txt`.

**Known limitation.** The font heuristic only fires on shots classified COVER; a title page
mis-classified as a body PAGE still falls to running-header voting. Covers whose model emits
no `Title` box at all (spine-only/sideways/imprint shots — `cover_title == ""`) keep the §15
text-heuristic behaviour, so a residual class of such books (e.g. ones named after a lone
author surname or an imprint line) is unchanged by §16 and still needs `in/titles.txt`.

---

## 17. Colour-assisted cover detection (run 2026-06-30)

**Problem.** `detect_type` classified a page as COVER solely on character count
(`nchars < COVER_TEXT_MAX = 280`).  A book photographed in front of a library shelf label
("Handling the Collection / Reading Room C" on IMG_9458) had the label's real text *plus*
hallucinated fragments pushing the count to 301 — just over the threshold.  Result: the
cover was typed PAGE/body, named a standalone book "Handling the Collection", and the §16
largest-font logic never ran.

**Solution.** `_image_colorfulness(img_path)` — a 128 × 128 HSV thumbnail pass (PIL only,
~2 ms) that returns the fraction of pixels with meaningful saturation (S > 60) and
brightness (V > 30), excluding near-white and near-black pixels.  `detect_type` now accepts
an optional `img_path` and, for pages in the "grey zone" (`COVER_TEXT_MAX ≤ nchars <
COVER_CEILING = 700`), tests the **combined score**:

```
score = colorfulness × (1 − nchars / COVER_CEILING)
if score ≥ COVER_SCORE_MIN (0.30) → COVER
```

The multiplicative form trades the two signals off continuously: more text demands more
colour, so colourful figures embedded in body text (moderate colour + high char count) score
too low, while a real cover behind a library label (high colour + moderate char count) scores
above the threshold.  Calibrated against the full corpus (116 grey-zone body pages):

| Image | nchars | colorfulness | score | verdict |
|-------|--------|-------------|-------|---------|
| IMG_9458 (cover + library label) | 351 | 0.645 | 0.322 | → COVER ✓ |
| IMG_5537 (BL supply slip, "Coastal Urbanities") | 313 | 0.718 | 0.397 | → COVER ✓ |
| IMG_0130 (series page, sat 0.509) | 317 | 0.509 | 0.278 | stays PAGE ✓ |
| IMG_7400 (map page, sat 0.416) | 321 | 0.416 | 0.225 | stays PAGE ✓ |
| IMG_0142 (hallucinated list, sat 0.389) | 492 | 0.389 | 0.115 | stays PAGE ✓ |

Exactly 2 promotions in the full cache — both reasonable.

**Effective required colorfulness by text count:**
- 280 chars → need ≥ 0.50 — any moderately colourful cover qualifies
- 350 chars → need ≥ 0.60 — full-bleed photographic cover required
- 450 chars → need ≥ 0.83 — near-full-bleed required; practically only real covers reach this
- ≥ 480 chars → effectively impossible (would need > 100 % colourfulness)

**Cache note.** This only affects images OCR'd *after* the change; the existing IMG_9458
cache (typed PAGE) is handled by the `in/merges.txt` + `in/titles.txt` entries added in the
same session.  To get the clean fix (proper COVER + §16 cover_title layout pass): delete
`out/cache/IMG_9458.json` and re-run `python ocr.py batch`.

**Constants (all in `ocr.py`):** `COVER_TEXT_MAX = 280`, `COVER_CEILING = 700`,
`COVER_SCORE_MIN = 0.30`.  Tune the score threshold against the grey-zone page list;
bump COVER_CEILING if you encounter a genuine cover that can't stay below 700 chars.

---

## 18. Title/imprint page gap — SPREAD embedding + positional promotion (planned)

**Background.** Investigated while examining why IMG_9458 needed a manual fix (§17).
The question: *are "library index pages" — copyright pages, title pages, "Also Available"
spreads — being detected and used correctly?*

**Corpus review** (six representative examples — IMG_3007, IMG_2986, IMG_2882, IMG_2849,
IMG_2275, IMG_2230, plus IMG_4074, IMG_3213, IMG_3037):

- All copyright/imprint pages (©, "first published", "all rights reserved", "Library of
  Congress") → **already IMPRINT** ✓ — the existing `IMPRINT_MARKERS` regex catches them.
- Cover-only pages (< 280 chars) → **already COVER** ✓.
- IMG_2230 is the "no cover, imprint only" case: correctly IMPRINT, and `parse_metadata`
  extracts a full CIP block with title, author, publisher, year.  Works.

**Two genuine gaps:**

### Gap A — IMPRINT embedded in a SPREAD

IMG_3104 is a two-page spread: page 1 = "Also Available / Five Billion Years of Global
Change / Denis Wood / …" (series list); page 2 = "WEAPONIZING MAPS / Indigenous Peoples and
Counterinsurgency in the Americas / Joe Bryan and Denis Wood / THE GUILFORD PRESS".

`detect_type` sees two `### Page N` headers → **SPREAD** fires before the per-record IMPRINT
check can run.  The title page (page 2) lands in `raw_md` as body text; its title is never
fed into `parse_metadata`.

**Fix:** In `detect_type`, before returning SPREAD, split the OCR text on `### Page N`
boundaries and test `IMPRINT_MARKERS` against each page individually.  If any page matches,
return IMPRINT instead of SPREAD.  One-liner change, no new dependencies.

```python
# proposed addition inside detect_type, just before the SPREAD return:
pages = re.split(r"###\s*Page\s+\d+", t)
if any(IMPRINT_MARKERS.search(p) for p in pages):
    return "IMPRINT"
```

Risk: a SPREAD whose *body* page coincidentally contains "all rights reserved" (e.g., a
block-quote about copyright law) would become IMPRINT.  Mitigate with a char-count guard:
only promote if the matching page is short enough to be an imprint (e.g., < 1 500 chars).

### Gap B — Title-only pages taken in place of a cover

The user sometimes photographs a title page (title + author + publisher, no © block) instead
of the cover.  These pages are white, sparse (say 280–600 chars), and carry no standard
imprint markers — so they fall through as body PAGEs.  The colour score (§17) can't help:
white pages score near zero.

**Signal:** position.  These pages come as the **first page(s) of a book session**, often
immediately after or instead of a COVER.  No content-only detector can reliably distinguish a
bare title page from a short chapter opener.

**Proposed fix (grouper-level, not detect_type):**  In `group_images`, after the initial
grouping pass, look at each book that has **no COVER or IMPRINT** in its first two records.
If the first body record is:
- sparse (nchars < a threshold, e.g. 500), AND
- its first real line is title-like (`_is_title_like`), AND
- there is no running-header disagreement later in the group (i.e., the body pages'
  headers are consistent with that first line being the book's title),
then promote that first body record to a synthetic IMPRINT: call `parse_metadata("IMPRINT",
text)` on it and inject the result into the book's `metadata` list.

This is additive (existing COVER/IMPRINT records are untouched) and does not change the
cache (it's a grouper transformation).

**Do not implement without more examples.** The "title-only, no ©" page is rarer than it
looks: the corpus review found that virtually every photographed info page has at least one
IMPRINT_MARKERS hit.  Validate with a targeted search (`python ocr.py batch` → report →
identify books whose title is an author name or "Untitled") before building the positional
detector.

---

## Appendix — Historic reference (original single-page design)

Condensed from the first plan; kept for rationale, not as current instructions.

- **Model class: a prompt-steerable document VLM, not classical OCR.** Tesseract/EasyOCR/
  PaddleOCR/Surya transcribe every glyph with no instruction channel, so they can't be told
  to drop handwriting. OCR-only sub-1B VLMs (GLM-OCR, PaddleOCR-VL) score highest on *raw*
  transcription but aren't freely steerable → rejected. The steerable VLM's one advantage —
  you can *instruct* it ("ignore the handwriting") — is the whole point.
- **Runtime: `mlx-vlm`** (pulls mlx, transformers, pillow, numpy; no CUDA, no cloud).
  Runs document VLMs on the M3 GPU; downloads once from HuggingFace, then `HF_HUB_OFFLINE=1`.
  First-class auto-prompt support for `dots.ocr`/`dots.mocr`.
- **Model choice for 16 GB:** default `mlx-community/dots.mocr-4bit` (~2 GB, fast, Markdown,
  multilingual). Opt-in `mlx-community/olmOCR-7B-0725-4bit` (highest fidelity, tight/slow on
  16 GB). `Qwen2.5-VL-3B-Instruct-4bit` as a steerable fallback. Decision rule: pick the
  smallest model whose **IMG_3020** (annotated page) eval score is acceptable — a high score
  there means handwriting is actually being dropped.
- **Original tool design:** single-file `ocr.py` with `run` (OCR images → `.md` + flattened
  `.txt`) and `eval` (score candidate models over `test/*.jpeg` vs `*_text.txt` with
  `difflib.SequenceMatcher`, zero extra deps). Prompt isolated in `prompts.py`; tuned via
  `eval`, not by editing inference code.
- **Original prompt intent:** transcribe only printed/typeset text into GFM; ignore every
  handwritten annotation; preserve reading order, `### Page N` per spread, `*italics*`,
  blockquotes, footnote superscripts; no commentary/translation/correction.
- **Out of scope (then):** citation-manager import; batching many pages (now the core of
  this revision).
- **Sources:** olmOCR-2 (Ai2 blog + model cards), mlx-vlm dots.ocr README, Blaizzy/mlx-vlm,
  granite-docling-258M-mlx, GOT-OCR2.0, Modal OCR comparison, OCR SOTA leaderboard (Feb 2026),
  Ubicloud "end-to-end OCR with VLMs", dots.ocr paper (arXiv 2512.02498).
