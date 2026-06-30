# Feature 001 — Single-page OCR + model eval

**Status:** Delivered · **Origin:** `library-ocr@c04a47f` · **Old ref:** IMPLEMENTATION_PLAN Appendix
**Constitution:** I, II, III, IV, VI

## User Scenarios & Testing

### Primary user story
As a researcher photographing reference books, I want to turn a clean single-page
photo into structured Markdown + plain text that contains **only the printed text**
(no handwritten underlines, circles, or margin notes), so I can quote and cite the
page without retyping it.

### Acceptance scenarios
1. **Given** a clean page photo, **when** I run `python ocr.py run page.jpeg --out out/`,
   **then** I get `out/page.md` (GitHub-flavored Markdown: `### Page N`, `*italics*`,
   `>` blockquotes, footnote superscripts) and `out/page.txt` (markers stripped,
   wrapped lines rejoined into paragraphs).
2. **Given** a heavily annotated page (handwriting over print), **when** I OCR it,
   **then** the handwritten marks are absent from the output and the printed text is intact.
3. **Given** the `test/` fixtures, **when** I run `python ocr.py eval`, **then** I get a
   per-model `difflib.SequenceMatcher` ratio for `IMG_3018` and `IMG_3020` plus a mean,
   computed with zero non-stdlib scoring deps.
4. **Given** `--model <other-mlx-id>`, **when** I run `run` or `eval`, **then** the
   alternate model is used without any code change.

### Edge cases
- A model that ignores the instruction prompt (e.g. `dots.ocr-4bit`) emits junk and
  scores near zero — eval surfaces this rather than the tool crashing.
- Ground-truth idiosyncrasies (a dropped footnote, a kept running header) cap the
  achievable score below 1.0; that is expected, not a regression.

## Requirements

### Functional
- **FR-001** The tool MUST expose `run <images…> [--model M] [--out DIR]` that OCRs each
  image to `<name>.md` and `<name>.txt`.
- **FR-002** The OCR output MUST contain only printed/typeset text; **all handwriting**
  (underlines, circles, highlights, marginal lines, handwritten notes, hand-applied
  stamps) MUST be dropped.
- **FR-003** Markdown output MUST preserve reading order, `### Page N` per spread,
  `*italics*`, `>` blockquotes, and footnote-reference superscripts; it MUST NOT add
  commentary, translation, or correction.
- **FR-004** `<name>.txt` MUST be the flattened form: Markdown markers stripped,
  wrapped lines joined into paragraphs.
- **FR-005** The single instruction prompt MUST live only in `prompts.py`.
- **FR-006** The tool MUST expose `eval [--models …] [--out DIR]` scoring candidate
  models against `test/*.jpeg` vs `*_text.txt` using `difflib.SequenceMatcher.ratio()`
  over lowercased, Markdown-stripped, whitespace-collapsed text. Scoring MUST be
  stdlib-only.
- **FR-007** The default model MUST be `DEFAULT_MODEL` (`mlx-community/dots.mocr-4bit`),
  overridable via `--model`; no second model path may be hardcoded (Principle IV).
- **FR-008** All inference MUST run offline on MLX (Principles I, II).

### Key entities
- **Page result** — `{ markdown, plain_text }` for one image.
- **Eval row** — `{ model, IMG_3018, IMG_3020, mean }`.

## Review & Acceptance Checklist
- [x] Requirements are testable and implementation-agnostic
- [x] Handwriting-drop is verified by the `IMG_3020` diagnostic
- [x] No new fixtures added; `test/` reused
- [x] Offline + MLX + single-model honored

## Decision log (non-normative)
- **Why a prompt-steerable VLM, not classical OCR.** Tesseract/EasyOCR/PaddleOCR/Surya
  transcribe every glyph with no instruction channel, so they cannot be told to drop
  handwriting — the whole requirement. OCR-only sub-1B VLMs score higher on *raw*
  transcription but aren't steerable → rejected.
- **Runtime `mlx-vlm`** runs document VLMs on the M3 GPU, with first-class auto-prompt
  support for `dots.ocr`/`dots.mocr`.
- **Model choice rule:** pick the smallest model whose `IMG_3020` (annotated page) score
  is acceptable — a high score there means handwriting is actually being dropped.
- **Measured (M3, this prompt):** `dots.mocr-4bit` 0.835 / 0.945 / mean 0.890 (default);
  `olmOCR-7B-0725-4bit` 0.528 / 0.916 / 0.722 (heavier, *worse* here — mid-paragraph hard
  wraps and hyphen-break artifacts hurt the flattened ratio); `Qwen2.5-VL-3B` 0.552 /
  0.891 / 0.721; `dots.ocr-4bit` ~0 (echoes the prompt — not steerable). Conclusion: keep
  the small, fast `dots.mocr-4bit` default.
- **Out of scope (then):** batching many pages and citation-manager import — became the
  core of feature 002 and 011.
