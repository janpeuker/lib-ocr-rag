"""OCR prompt — kept in one auditable place.

Tune PROMPT against `ocr.py eval`. The batch pipeline (ocr.py batch) uses this
same prompt for every shot and then detects the shot type from the OCR text
(dots.mocr ignores classify-style instructions), so no separate classify or
metadata prompt is needed here.
"""

# Bump this when the prompt or the pipeline's record schema changes; the cache
# layer uses it for invalidation.
# v4: gated layout dual-pass figure detection (figures field in the record).
PROMPT_VERSION = "4"

# ---------------------------------------------------------------------------
# Body-text OCR prompt (Pass B: PAGE / SPREAD images)
# ---------------------------------------------------------------------------

PROMPT = (
    "Transcribe only the printed/typeset text in this image into "
    "GitHub-flavored Markdown.\n"
    "\n"
    "DROP all handwriting: underlines, circles, highlights, marginal lines, "
    "and any handwritten notes in the margins or between lines. "
    "Ignore stamps or marks that are clearly ink applied by hand.\n"
    "\n"
    "KEEP all printed text, including printed library slips, call-number "
    "stamps/labels, and any barcode printed text — these are typeset, not "
    "handwriting.\n"
    "\n"
    "For a two-page spread, output each page under a `### Page N` heading "
    "using the printed page numbers visible on the page.\n"
    "\n"
    "Preserve *italics*, blockquotes (`>`), and footnote-reference "
    "superscripts.\n"
    "\n"
    "For figures, maps, or diagrams: do NOT transcribe the interior of the "
    "figure. Instead emit exactly one placeholder line in this form:\n"
    "> **[Figure — <label and caption text>]**\n"
    "Example: > **[Figure — MAP 14-4: The Network of Orang Suku Laut "
    "Inter-related Territories]**\n"
    "\n"
    "Do not add commentary, translation, or correction."
)

# ---------------------------------------------------------------------------
# Layout-only prompt (figure detection — Candidate C, IMPLEMENTATION_PLAN §8.4)
# ---------------------------------------------------------------------------
# dots.mocr ignores the figure-placeholder instruction above when transcribing
# (measured: 0 figure recall — §8.4), so figures are detected with a SEPARATE
# layout-only pass that returns bbox+category JSON, never free text. Its output
# is used ONLY to FLAG figure/map/table regions; it must NEVER be concatenated
# into the transcription body, or it would reintroduce the marginalia/handwriting
# that PROMPT deliberately drops. Kept as a separate constant for exactly that
# reason — do not merge it into PROMPT.
LAYOUT_PROMPT = (
    "Please output the layout information from this image, including each layout "
    "element's bbox and its category. The bbox should be in the format "
    "[x1, y1, x2, y2]. The layout categories are ['Caption', 'Footnote', "
    "'Formula', 'List-item', 'Page-footer', 'Page-header', 'Picture', "
    "'Section-header', 'Table', 'Text', 'Title']. Do not output the text content. "
    "The output must be a single JSON array."
)

# ---------------------------------------------------------------------------
# Cover-title prompt (largest-font Title selection — IMPLEMENTATION_PLAN §16)
# ---------------------------------------------------------------------------
# The body OCR (PROMPT) emits a cover's text in spatial *reading* order, which
# puts the publisher/author/imprint first as often as the title — so taking the
# first title-like line (`_cover_title`) named books after their publisher
# ("The Guilford Press") or author ("Geoffrey Benjamin"). A book title is the
# *largest font* on the cover, not the first line, so we ask for layout WITH text
# (unlike LAYOUT_PROMPT, which suppresses text) and pick the tallest `Title`
# element's bbox. Used ONLY on COVER shots to set the title; its text never enters
# the transcription body. Tune via `python ocr.py batch` cover output, not inference.
COVER_TITLE_PROMPT = (
    "Please output the layout information from this image, including each layout "
    "element's bbox, its category, and the corresponding text content within the "
    "bbox. The bbox should be in the format [x1, y1, x2, y2]. The layout categories "
    "are ['Caption', 'Footnote', 'Formula', 'List-item', 'Page-footer', "
    "'Page-header', 'Picture', 'Section-header', 'Table', 'Text', 'Title']. The "
    "output must be a single JSON array of objects, each with keys 'bbox', "
    "'category', and 'text'."
)
