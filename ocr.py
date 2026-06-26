#!/usr/bin/env python3
"""Local, offline OCR for book-page photos → structured Markdown + plain text.

Runs a prompt-steerable document VLM on Apple Silicon (MLX/Metal). The prompt
(see prompts.py) instructs the model to keep only printed/typeset text and drop
handwritten annotations.

Usage:
    python ocr.py run IMG [IMG ...] [--model NAME] [--out DIR]
    python ocr.py batch [DIR] [--model NAME] [--out DIR] [--force]
    python ocr.py eval [--models a,b,c] [--max-edge 1600,1280,1024]

100% offline after the first model download. Set HF_HUB_OFFLINE=1 to enforce.
"""

import argparse
import contextlib
import datetime
import difflib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageOps, ExifTags

from prompts import PROMPT, PROMPT_VERSION, LAYOUT_PROMPT, COVER_TITLE_PROMPT

DEFAULT_MODEL = "mlx-community/dots.mocr-4bit"
MAX_TOKENS = 4096

# Batch pipeline tuning (see IMPLEMENTATION_PLAN.md §3–§4).
MAX_EDGE = 1600      # downscale long edge to cap VLM cost/memory on 12MP photos
MIN_TEXT_SCORE = 200  # letters OCR'd at 0°; above this we trust the orientation and skip retries

# Orientation probes: when 0° reads poorly the page is likely rotated. Rather than
# pay a FULL transcription at every orientation, run a cheap, low-res, short-decode
# probe to RANK orientations, then do one full pass at the winner. ~2 full passes
# worst case instead of 4. Both tunable (see IMPLEMENTATION_PLAN.md).
PROBE_MAX_EDGE = 1024  # probe resolution — enough to read orientation, not the page
PROBE_TOKENS = 64      # probe decode cap — a few lines is enough to score by yield

# Adaptive resolution (IMPLEMENTATION_PLAN.md §11): transcribe at FAST_MAX_EDGE first;
# if the read quality looks poor (scrambled/looping → likely too-low resolution), retry
# that page at the sharper MAX_EDGE and keep the better read.
# Eval-validated 2026-06-14 via `eval --max-edge 1600,1280,1024`: 1280 holds the IMG_3020
# diagnostic at 0.945 (== 1600), while 1024 collapses it to 0.469 (repetition loop). So
# 1280 is the eval floor; escalation to MAX_EDGE is the safety net for pages that regress.
FAST_MAX_EDGE = 1280  # first-pass resolution; quality gate escalates to MAX_EDGE on poor reads
QUALITY_RETRY = 0.95      # text_quality below this → re-OCR sharper (0..1; eval-tuned
                          # against test/: clean reads score ~0.997, garble/loops < 0.16)

# Per-book grouping (pure pass over cached records — no inference). A book is a
# contiguous run of shots that share a title identity; see group_images().
SESSION_GAP_S = 6 * 3600  # capture-time gap (s) that *suggests* a new session; a matching
                          # running title overrides it (one book may span sessions/days)
GPS_SESSION_DEG = 0.5     # coarse GPS change (~50 km) that marks a different location/session.
                          # Phone GPS jitters by km within one session, so smaller deltas are
                          # ignored — GPS only separates continents, never adjacent shots.
HEADER_MATCH = 0.6        # running-header similarity at/above which two shots share a book
HEADER_MAXLEN = 45        # a title-like running header is short (a body line is not a title)
COVER_OVERRIDE_VOTES = 3  # a running title repeated on ≥ this many shots overrides a cover-shot
                          # title it disagrees with — i.e. a stray/UI shot (chat screenshot)
                          # misclassified as the book's "cover" loses to the real running title

# Candidate models for `eval`, smallest → largest. Decision rule: pick the
# smallest model whose IMG_3020 (annotated page) score is acceptable.
EVAL_MODELS = [
    "mlx-community/dots.mocr-4bit",
    "mlx-community/Qwen2.5-VL-3B-Instruct-4bit",
    "mlx-community/olmOCR-7B-0725-4bit",
]

TEST_DIR = Path(__file__).parent / "test"


# --------------------------------------------------------------------------- #
# Inference
# --------------------------------------------------------------------------- #
def ocr_image(model, processor, config, image_path: str, max_tokens: int = MAX_TOKENS):
    """Run one image through a loaded model. Returns (markdown_text, stats) where
    stats is lightweight cost telemetry: prefill vs decode token counts and rates,
    wall seconds, and finish_reason ('length' = hit the token cap = runaway)."""
    from mlx_vlm import generate
    from mlx_vlm.prompt_utils import apply_chat_template

    formatted = apply_chat_template(processor, config, PROMPT, num_images=1)
    t0 = time.time()
    result = generate(
        model,
        processor,
        formatted,
        image=[image_path],
        temperature=0.0,
        max_tokens=max_tokens,
        verbose=False,
    )
    stats = {
        "seconds": round(time.time() - t0, 1),
        "prompt_tokens": getattr(result, "prompt_tokens", None),
        "generation_tokens": getattr(result, "generation_tokens", None),
        "prompt_tps": round(getattr(result, "prompt_tps", 0) or 0, 1),
        "generation_tps": round(getattr(result, "generation_tps", 0) or 0, 1),
        "finish_reason": getattr(result, "finish_reason", None),
    }
    return result.text.strip(), stats


def _patch_detokenizer_utf8():
    """Make mlx_vlm's BPE streaming detokenizer tolerant of stray bytes.

    `tokenizer_utils.BPEStreamingDetokenizer.add_token` flushes its buffer with a
    *strict* `.decode("utf-8")`, so a single bad byte the model emits mid-word
    (e.g. b' cont\\x98rovert') raises UnicodeDecodeError and kills the whole page.
    The class's own `finalize()` already decodes with errors="ignore"; we make the
    streaming flush match, so the bad byte is dropped and the (ASCII) text is
    recovered (b' cont\\x98rovert' -> ' controvert') instead of the page being lost.
    Idempotent; applied once per process at model load."""
    from mlx_vlm import tokenizer_utils as _tu

    cls = _tu.BPEStreamingDetokenizer
    if getattr(cls, "_utf8_tolerant", False):
        return

    def add_token(self, token, skip_special_token_ids=[]):
        if token in skip_special_token_ids:
            return
        v = self.tokenmap[token]
        if self._byte_decoder[v[0]] == 32:  # token starts with space -> flush
            current_text = bytearray(
                self._byte_decoder[c] for c in self._unflushed
            ).decode("utf-8", errors="ignore")
            if self.text or not self.trim_space:
                self.text += current_text
            else:
                self.text += _tu._remove_space(current_text)
            self._unflushed = v
        else:
            self._unflushed += v

    cls.add_token = add_token
    cls._utf8_tolerant = True


def load_model(model_name: str):
    """Load model + processor + config (cached after first download)."""
    from mlx_vlm import load
    from mlx_vlm.utils import load_config

    _patch_detokenizer_utf8()
    model, processor = load(model_name)
    config = load_config(model_name)
    return model, processor, config


# --------------------------------------------------------------------------- #
# Markdown → plain text (for pasting into a citation note)
# --------------------------------------------------------------------------- #
def md_to_text(md: str) -> str:
    """Flatten Markdown to readable plain text: strip markers, join wrapped
    lines within a paragraph, keep paragraph breaks. No extra dependencies."""
    out_paragraphs = []
    for block in re.split(r"\n\s*\n", md.strip()):
        lines = []
        for line in block.splitlines():
            line = line.strip()
            line = re.sub(r"^#{1,6}\s*", "", line)      # headings
            line = re.sub(r"^>\s?", "", line)            # blockquote markers
            line = re.sub(r"^[-*+]\s+", "", line)        # list bullets
            line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)  # bold
            line = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", line)  # italics
            line = re.sub(r"`(.+?)`", r"\1", line)       # inline code
            if line:
                lines.append(line)
        if lines:
            out_paragraphs.append(" ".join(lines))
    return "\n\n".join(out_paragraphs) + "\n"


# --------------------------------------------------------------------------- #
# Scoring (eval)
# --------------------------------------------------------------------------- #
def normalize(text: str) -> str:
    """Lowercase, strip Markdown, collapse whitespace — for fair comparison."""
    flat = md_to_text(text).lower()
    flat = re.sub(r"\s+", " ", flat)
    return flat.strip()


def similarity(pred: str, truth: str) -> float:
    """difflib ratio on normalized text. Ground truth may be cut off at the
    end (per the goal), so we cap the prediction to the truth's length to avoid
    penalizing extra trailing content the model legitimately read."""
    p, t = normalize(pred), normalize(truth)
    if len(p) > len(t):
        p = p[: len(t)]
    return difflib.SequenceMatcher(None, p, t).ratio()


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_run(args):
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading model: {args.model} ...", file=sys.stderr)
    model, processor, config = load_model(args.model)

    for img in args.images:
        img_path = Path(img)
        if not img_path.exists():
            print(f"  skip (not found): {img}", file=sys.stderr)
            continue
        print(f"  OCR: {img} ...", file=sys.stderr)
        md, _ = ocr_image(model, processor, config, str(img_path))
        txt = md_to_text(md)

        (out_dir / f"{img_path.stem}.md").write_text(md, encoding="utf-8")
        (out_dir / f"{img_path.stem}.txt").write_text(txt, encoding="utf-8")
        print(f"    -> {out_dir / (img_path.stem + '.md')}", file=sys.stderr)
        print(f"    -> {out_dir / (img_path.stem + '.txt')}", file=sys.stderr)


def _eval_label(model_name: str, edge) -> str:
    """Row label for the eval table: model id, suffixed with @<edge> when sweeping."""
    return f"{model_name} @{edge}" if edge else model_name


@contextlib.contextmanager
def _maybe_downscaled(fixture: Path, edge):
    """Yield an image path to OCR. With `edge` set, downscale the fixture via the
    batch pipeline's `prep_image` to a temp file (same input the model sees in a
    real run); otherwise yield the raw fixture path unchanged."""
    if not edge:
        yield str(fixture)
        return
    with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
        prep_image(str(fixture), max_edge=edge).convert("RGB").save(tmp.name)
        yield tmp.name


def cmd_eval(args):
    fixtures = sorted(TEST_DIR.glob("*.jpeg"))
    if not fixtures:
        sys.exit(f"No *.jpeg fixtures in {TEST_DIR}")
    models = args.models.split(",") if args.models else EVAL_MODELS

    # --max-edge "1600,1280,1024" sweeps the FAST_MAX_EDGE candidates: each value
    # downscales the fixture via prep_image (the same path the batch pipeline feeds
    # the model) before OCR, so the scores reflect real first-pass resolution.
    # Empty = legacy behaviour (feed the raw full-res fixture, no row label).
    edges = [int(e) for e in args.max_edge.split(",")] if args.max_edge else [None]

    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    names = [f.stem for f in fixtures]
    rows = []
    for model_name in models:
        print(f"\nLoading model: {model_name} ...", file=sys.stderr)
        try:
            model, processor, config = load_model(model_name)
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED to load {model_name}: {e}", file=sys.stderr)
            for edge in edges:
                rows.append((_eval_label(model_name, edge), {n: None for n in names}))
            continue

        for edge in edges:
            label = _eval_label(model_name, edge)
            scores = {}
            for f in fixtures:
                truth_path = f.with_name(f.stem + "_text.txt")
                if not truth_path.exists():
                    scores[f.stem] = None
                    continue
                print(f"  OCR: {f.name} @{edge or 'native'} ...", file=sys.stderr)
                with _maybe_downscaled(f, edge) as img_path:
                    md, _ = ocr_image(model, processor, config, img_path)
                if out_dir:
                    tag = model_name.split("/")[-1]
                    suffix = f".{edge}" if edge else ""
                    (out_dir / f"{f.stem}.{tag}{suffix}.md").write_text(md, encoding="utf-8")
                scores[f.stem] = similarity(md, truth_path.read_text(encoding="utf-8"))
            rows.append((label, scores))

    # Report table
    col = max(len(m) for m, _ in rows) + 2
    header = "model".ljust(col) + "".join(n.ljust(12) for n in names) + "mean"
    print("\n" + header)
    print("-" * len(header))
    for model_name, scores in rows:
        vals = [scores.get(n) for n in names]
        present = [v for v in vals if v is not None]
        mean = sum(present) / len(present) if present else None
        line = model_name.ljust(col)
        for v in vals:
            line += (f"{v:.3f}" if v is not None else "  -  ").ljust(12)
        line += f"{mean:.3f}" if mean is not None else "  -  "
        print(line)


# --------------------------------------------------------------------------- #
# Batch pipeline: preprocessing, classification, per-book grouping
# (heterogeneous library photos → one document per book). See IMPLEMENTATION_PLAN.
# --------------------------------------------------------------------------- #
def _gps_to_latlon(gps_ifd):
    """Convert an EXIF GPSInfo IFD to a coarse (lat, lon), rounded so shots from
    the same library cluster. Returns None if absent/unparseable."""
    try:
        def dec(vals, ref):
            d, m, s = (float(x) for x in vals)
            v = d + m / 60 + s / 3600
            return round(-v if ref in ("S", "W") else v, 3)

        lat, lon = gps_ifd.get(2), gps_ifd.get(4)
        if lat is None or lon is None:
            return None
        return (dec(lat, gps_ifd.get(1)), dec(lon, gps_ifd.get(3)))
    except Exception:  # noqa: BLE001
        return None


def read_exif(path: str) -> dict:
    """Capture time + coarse GPS — the session/location fences for grouping."""
    dt, gps = None, None
    try:
        exif = Image.open(path).getexif()
        raw = exif.get(36867) or exif.get(306)  # DateTimeOriginal, else DateTime
        if raw:
            try:
                dt = datetime.datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
            except ValueError:
                dt = None
        try:
            gps = _gps_to_latlon(exif.get_ifd(ExifTags.IFD.GPSInfo))
        except Exception:  # noqa: BLE001
            gps = None
    except Exception:  # noqa: BLE001
        pass
    return {"datetime": dt, "gps": gps}


# clockwise degrees -> PIL transpose op (PIL.rotate is counter-clockwise)
_CW = {
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


def prep_image(path: str, rotation: int = 0, max_edge: int = MAX_EDGE) -> Image.Image:
    """Straighten (EXIF + detected rotation) and downscale a photo for the VLM.
    `max_edge` overrides the long-edge cap (orientation probes downscale harder)."""
    img = ImageOps.exif_transpose(Image.open(path))
    if rotation in _CW:
        img = img.transpose(_CW[rotation])
    long_edge = max(img.size)
    if long_edge > max_edge:
        scale = max_edge / long_edge
        img = img.resize((round(img.width * scale), round(img.height * scale)),
                         Image.LANCZOS)
    return img


# --- per-image disk cache (resumability) ---------------------------------- #
def cache_path(out_dir, img_path) -> Path:
    return Path(out_dir) / "cache" / (Path(img_path).stem + ".json")


def load_cache(out_dir, img_path, model, prompt_version):
    """Return the cached record only if model + prompt_version still match."""
    p = cache_path(out_dir, img_path)
    if not p.exists():
        return None
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if rec.get("model") != model or rec.get("prompt_version") != prompt_version:
        return None
    return rec


def save_cache(out_dir, img_path, record: dict) -> None:
    p = cache_path(out_dir, img_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")


# --- text-based shot detection (Option A) --------------------------------- #
# dots.mocr is a transcriber, not a classifier: it ignores "classify this"
# instructions and just OCRs. So we detect the shot type from the OCR text it
# *does* produce — reliable for imprint pages, zero new deps, fully offline.
# (A small zero-shot image classifier — Option B — could replace this later.)
IMPRINT_MARKERS = re.compile(
    r"(?i)\b(isbn|first published|all rights reserved|"
    r"catalogu(?:e|ing)[- ]in[- ]publication|library of congress|"
    r"printed in|no part of this (?:book|publication))\b|©")
COVER_TEXT_MAX = 280  # below this many non-space chars (and no imprint marker) → cover/spine shot
ISBN_RE = re.compile(r"(?i)\bISBN[:\s]*((?:97[89][\s-]?)?[\d][\d\s-]{7,14}[\dXx])")
YEAR_RE = re.compile(r"(?i)(?:©|\(c\)|copyright|first published)[^\n]*?\b((?:19|20)\d{2})\b")
PUBLISHER_RE = re.compile(r"(?i)published[^\n]*?\bby\b[:\s]*\n?\s*([^\n]+)")


def detect_type(text: str) -> str:
    """COVER / IMPRINT / SPREAD / PAGE from the OCR'd text of a shot."""
    t = text.strip()
    has_marker = bool(IMPRINT_MARKERS.search(t))
    if len(re.sub(r"\s+", "", t)) < COVER_TEXT_MAX and not has_marker:
        return "COVER"
    if has_marker:
        return "IMPRINT"
    if len(re.findall(r"###\s*Page\s+\d+", t)) >= 2:
        return "SPREAD"
    return "PAGE"


def parse_metadata(typ: str, text: str, cover_title: str = "") -> str:
    """Pull bibliographic fields out of a cover/imprint shot's OCR text into a
    small single-doc YAML block. No second transcription call — dots.mocr would
    just re-transcribe, so we parse the text we already have. `cover_title` (the
    largest-font Title from the layout pass, §16) overrides the reading-order text
    heuristic when available."""
    fields = {}
    m = ISBN_RE.search(text)
    if m:
        fields["isbn"] = re.sub(r"\s+", " ", m.group(1)).strip()
    m = YEAR_RE.search(text)
    if m:
        fields["year"] = m.group(1)
    m = PUBLISHER_RE.search(text)
    if m:
        fields["publisher"] = m.group(1).strip()
    if typ == "COVER":  # the title printed on a cover/spine (may wrap across lines)
        t = cover_title or _cover_title(text)
        if t:
            fields["title"] = t
    order = ("title", "author", "publisher", "year", "isbn", "call_number")
    return "\n".join(f"{k}: {fields[k]}" for k in order if k in fields)


def _clean_meta(s: str) -> str:
    """Drop all code-fence lines and collapse blank runs. A cover/imprint shot may
    contain several books, so the model can emit multiple ```yaml blocks."""
    s = re.sub(r"(?m)^\s*```[a-zA-Z]*\s*$", "", s)
    return re.sub(r"\n{3,}", "\n\n", s.strip()).strip()


def _split_meta_docs(s: str):
    """Split cleaned metadata into one block per book (a new `title:` starts one)."""
    blocks, cur = [], []
    for line in s.splitlines():
        if re.match(r"(?i)^\s*title:", line) and cur:
            blocks.append("\n".join(cur).strip())
            cur = [line]
        else:
            cur.append(line)
    if cur:
        blocks.append("\n".join(cur).strip())
    return [b for b in blocks if b.strip()]


def running_header(md: str) -> str:
    """First meaningful printed line — usually the page's running title/author.
    Skips bare folios like "337" or "Page 12"."""
    for line in md.splitlines():
        s = line.strip().lstrip("#>* ").strip()
        if not s or re.fullmatch(r"(?i)(page\s+)?\d{1,4}", s):
            continue
        return s
    return ""


def page_numbers(md: str):
    nums = [int(n) for n in re.findall(r"###\s*Page\s+(\d+)", md)]
    if nums:
        return nums
    for line in md.splitlines():
        s = line.strip()
        if s.isdigit() and len(s) <= 4:
            return [int(s)]
    return []


def _text_score(s: str) -> int:
    """Letters transcribed — a proxy for a correct read vs. a sideways garble."""
    return sum(c.isalpha() for c in s)


def text_quality(text: str) -> float:
    """Heuristic 0..1 read-quality score (higher = cleaner). Flags scrambled,
    looping, or garbled output — the symptom of too-low resolution on small print —
    so the page can be retried sharper and the report can hint at it. Pure-text,
    stdlib-only. Does NOT catch confident-but-wrong character substitutions (those
    need token logprobs — see IMPLEMENTATION_PLAN.md §11 Option A)."""
    words = re.findall(r"[^\W\d_]{2,}", text)  # alphabetic word tokens, length ≥ 2
    if len(words) < 5:
        return 1.0  # too little text to judge garble (cover/sparse page) — don't flag
    # 1) word plausibility: real words almost always contain a vowel; scrambled OCR
    #    produces vowelless consonant-clusters.
    voweled = sum(1 for w in words if re.search(r"[aeiouyà-öø-ÿ]", w, re.IGNORECASE))
    word_validity = voweled / len(words)
    # 2) non-repetition: looping/runaway output repeats itself. Check at two grains,
    #    because a runaway loop sometimes repeats whole *lines* and sometimes repeats a
    #    phrase inline with no line breaks (one giant paragraph — e.g. dots.mocr stuck on
    #    "…the Jutai, the Jutai…"), which a line-level check alone scores as clean.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    line_uniq = len(set(lines)) / len(lines) if lines else 1.0
    toks = text.split()
    if len(toks) >= 30:  # enough tokens to judge phrase looping
        tri = list(zip(toks, toks[1:], toks[2:]))
        phrase_uniq = len(set(tri)) / len(tri)
    else:
        phrase_uniq = 1.0
    return round(word_validity * min(line_uniq, phrase_uniq), 3)


def ocr_oriented(model, processor, config, img_path, td):
    """OCR at 0°; if the read looks degenerate (few letters → likely rotated), find
    the right orientation with CHEAP probes (low-res, short-decode) instead of full
    transcriptions, then do one full pass at the winner. We can't trust dots.mocr to
    *report* rotation, so we infer it from OCR yield. Most upright pages cost a single
    full pass; a rotated page costs ~2 full passes + 4 probes instead of 4 full passes.
    Returns (text, rotation, full_passes, probes, text_score, stats) — the trailing
    fields are cost instrumentation (orientation work is the main batch cost)."""
    def full(rot):  # full transcription at the fast first-pass resolution
        p = Path(td) / f"r{rot}.jpg"
        prep_image(img_path, rot, max_edge=FAST_MAX_EDGE).convert("RGB").save(
            p, "JPEG", quality=90)
        return ocr_image(model, processor, config, str(p))  # (text, stats)

    def probe(rot):  # cheap orientation test: low-res, few tokens → letters yielded
        p = Path(td) / f"probe{rot}.jpg"
        prep_image(img_path, rot, max_edge=PROBE_MAX_EDGE).convert("RGB").save(
            p, "JPEG", quality=85)
        text, _ = ocr_image(model, processor, config, str(p), max_tokens=PROBE_TOKENS)
        return _text_score(text)

    # Fast path: most pages are upright. One full pass; trust a good 0° read.
    text0, stats0 = full(0)
    score0 = _text_score(text0)
    if score0 >= MIN_TEXT_SCORE:
        return text0, 0, 1, 0, score0, stats0

    # Likely rotated (or a genuinely sparse page). Rank all four orientations with
    # cheap probes — including 0°, so a sparse-but-upright page stays at 0° — then
    # do at most one more full pass at the winner.
    probe_scores = {rot: probe(rot) for rot in (0, 90, 270, 180)}
    best_rot = max(probe_scores, key=probe_scores.get)
    if best_rot == 0:
        return text0, 0, 1, 4, score0, stats0  # reuse the 0° full read
    text, stats = full(best_rot)
    return text, best_rot, 2, 4, _text_score(text), stats


# --- gated figure/map detection (layout dual-pass, Candidate C) ------------- #
# dots.mocr ignores PROMPT's figure-placeholder instruction when transcribing
# (measured: 0 recall — IMPLEMENTATION_PLAN §8.4). So figures are found with a
# SECOND, layout-only pass, GATED to pages that look figure-bearing (a standalone
# Map/Figure/Table/Plate caption in the OCR text) to keep the cost ~+3% batch-wide.
# Layout output only FLAGS regions; it never re-enters the transcription text.
FIGURE_CAPTION_RE = re.compile(
    r"(?i)^(map|fig(?:ure|\.)?|table|plate|diagram|chart)\s+[\dIVXLC]")
_FIG_CATS = {"Picture", "Table"}


def _parse_layout(text: str):
    """Tolerant parse of a layout-only response → [{category, bbox}]; None on fail."""
    s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip())
    m = re.search(r"\[.*\]", s, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, list):
        return None
    return [{"category": el.get("category"), "bbox": el.get("bbox"),
             "text": el.get("text")}
            for el in data if isinstance(el, dict) and el.get("category")]


def layout_figures(model, processor, config, img_path) -> int:
    """One dots.mocr layout-only pass → count of figure/table regions (≥0).
    Reuses the loaded model — no second model, no extra resident memory."""
    from mlx_vlm import generate
    from mlx_vlm.prompt_utils import apply_chat_template

    formatted = apply_chat_template(processor, config, LAYOUT_PROMPT, num_images=1)
    res = generate(model, processor, formatted, image=[img_path],
                   temperature=0.0, max_tokens=MAX_TOKENS, verbose=False)
    boxes = _parse_layout(res.text) or []
    return sum(b["category"] in _FIG_CATS for b in boxes)


# --- cover title by largest font (layout dual-pass) ------------------------ #
# A cover's book title is the largest type on the page, but PROMPT transcribes in
# reading order, so the *first* title-like line is as often the publisher/author.
# COVER_TITLE_PROMPT returns layout WITH text; we take the tallest `Title` bbox
# (font-size proxy = box height) as the title — IMPLEMENTATION_PLAN §16. The model
# only labels the genuine title `Title`, so we trust ONLY that category: when none
# is returned (a stylized title the model folds into the cover `Picture`, e.g.
# "Bahasa Mah Meri"), we return "" and let the reading-order text heuristic run —
# picking the next-largest *text* box would just resurface the author/publisher.
_COVER_TITLE_FONT_RATIO = 0.55   # join Title boxes within this fraction of the tallest
                                 # (a wrapped title: "Tribal Communities" / "in the Malay World")
_COVER_SUBTITLE_GAP_RATIO = 0.8  # a subtitle hugs the title: the gap above it is under this
                                 # fraction of its own line height; the author/imprint sits farther
_COVER_SUBTITLE_MAXLINES = 2     # at most this many boxes absorbed as a subtitle
_COVER_TITLE_MAXLEN = 90
_COVER_SUBTITLE_CATS = ("Title", "Section-header", "Text", "List-item")
# lowercase function words mark a phrase (subtitle); a byline/author line has none.
_BYLINE_FUNC = {"and", "of", "the", "in", "on", "for", "to", "with", "from", "at",
                "by", "as", "how", "why", "what", "a", "an", "&"}


def _looks_like_byline(s: str) -> bool:
    """True if `s` reads like an author/editor line (a run of capitalized names and
    initials) rather than a title/subtitle. Used to stop a subtitle scan from eating
    an author set close under the title (e.g. "Denis Wood" under a big cover title).
    A leading article or any lowercase function word means it's a phrase, not a name."""
    toks = s.strip().rstrip(".").split()
    if not toks or toks[0].lower() in ("a", "an", "the"):
        return False
    if any(t.islower() and t.lower() in _BYLINE_FUNC for t in toks):
        return False
    if len(toks) > 6:
        return False
    return all(t == "&" or re.fullmatch(r"[A-Z]\.?", t)
               or re.fullmatch(r"[A-Z][\w'’.-]*", t) for t in toks)


def _box_height(el) -> float:
    bb = el.get("bbox") or []
    return float(bb[3] - bb[1]) if len(bb) == 4 else 0.0


def _box_top(el) -> float:
    bb = el.get("bbox") or []
    return float(bb[1]) if len(bb) == 4 else 0.0


def _box_bottom(el) -> float:
    bb = el.get("bbox") or []
    return float(bb[3]) if len(bb) == 4 else 0.0


def _el_text(el) -> str:
    return re.sub(r"\s+", " ", (el.get("text") or "").strip())


def _cover_title_ok(t: str) -> bool:
    """A plausible cover title from a layout box — looser than `_is_title_like`
    (an authoritative `Title` box may run longer than a running header), but still
    rejects folios, slip fields, and mostly-symbol garble."""
    t = (t or "").strip()
    if not (3 <= len(t) <= _COVER_TITLE_MAXLEN) or _folio(t) or _SLIP_NOISE.match(t):
        return False
    letters = sum(c.isalpha() for c in t)
    return letters >= 0.5 * len(t) and t[0].isalnum()


def _pick_cover_title(elements) -> str:
    """The book title from a layout+text response. The title is the largest type on
    the cover: take the tallest `Title` box (joining same-size `Title` boxes for a
    wrapped title, de-duping the front/spine repeat), then absorb a subtitle that
    *hugs* it — the next box(es) below within `_COVER_SUBTITLE_GAP_RATIO` of the
    title's height (the author/imprint sits farther down, so the gap stops there).
    "" when the model labels no usable `Title` (caller falls back to the text
    heuristic) — picking the next-largest text box would just resurface the author."""
    titles = [el for el in elements
              if el.get("category") == "Title" and _el_text(el)]
    if not titles:
        return ""
    maxh = max(_box_height(el) for el in titles) or 1.0
    head, seen = [], set()
    for el in titles:                       # document order = top-to-bottom
        if _box_height(el) >= _COVER_TITLE_FONT_RATIO * maxh:
            t = _el_text(el)
            if t.lower() not in seen:        # drop the front-cover/spine duplicate
                seen.add(t.lower())
                head.append(el)
    if not head:
        return ""
    parts = [_el_text(el) for el in head]
    # subtitle: boxes strictly below the title, each within half a title-height of the
    # one above it (a tight gap = same title block; a wide gap = author/series/imprint).
    bottom = max(_box_bottom(el) for el in head)
    below = sorted((el for el in elements
                    if el.get("category") in _COVER_SUBTITLE_CATS and _el_text(el)
                    and _box_top(el) >= bottom),
                   key=_box_top)
    for el in below:
        s = _el_text(el)
        gap = _box_top(el) - bottom
        if (gap > _COVER_SUBTITLE_GAP_RATIO * (_box_height(el) or maxh)
                or len(parts) - len(head) >= _COVER_SUBTITLE_MAXLINES
                or _SLIP_NOISE.match(s) or _folio(s) or _looks_like_byline(s)):
            break
        parts.append(s)
        bottom = _box_bottom(el)
    cand = " ".join(parts).strip()
    return cand if _cover_title_ok(cand) else ""


# Title detection needs the full MAX_EDGE image (the title is unreadable when downscaled
# further — measured: a 1024px pass misses small-on-cover titles), but most covers don't
# need a full 4096-token transcription: capping the decode keeps the common (sparse) cover
# fast. A chatty/back-cover shot, though, overruns the cap and the truncated layout JSON
# scrambles the boxes (a garbled title with a half-token tail) — so on truncation we MUST
# redo at the full budget rather than trust the cut-off read. Adaptive: fast common case,
# correct on the few text-heavy covers. Eval-tuned — IMPLEMENTATION_PLAN §16.
COVER_LAYOUT_MAX_TOKENS = 1536


def cover_title_layout(model, processor, config, img_path) -> str:
    """One layout+text pass over a cover → its largest-font book title (or "").
    Reuses the loaded model; the returned text NEVER enters the transcription body.
    Caps the decode for speed, retrying at full budget if the cover overran the cap
    (a truncated layout JSON yields a scrambled title, so it can't be trusted)."""
    from mlx_vlm import generate
    from mlx_vlm.prompt_utils import apply_chat_template

    formatted = apply_chat_template(processor, config, COVER_TITLE_PROMPT, num_images=1)
    res = generate(model, processor, formatted, image=[img_path],
                   temperature=0.0, max_tokens=COVER_LAYOUT_MAX_TOKENS, verbose=False)
    if getattr(res, "finish_reason", None) == "length":  # truncated → unreliable, redo full
        res = generate(model, processor, formatted, image=[img_path],
                       temperature=0.0, max_tokens=MAX_TOKENS, verbose=False)
    return _pick_cover_title(_parse_layout(res.text) or [])


def figure_captions(text: str):
    """Standalone figure/map/table caption lines in the OCR text. These are the
    gate (do we bother with a layout pass?) and the placeholder caption text —
    the caption always comes from the text pass, never from layout output."""
    caps = []
    for line in text.splitlines():
        s = line.strip().lstrip("#>*").strip()
        if FIGURE_CAPTION_RE.match(s):
            caps.append(s)
    return caps


def inject_figure_placeholders(text: str, caps) -> str:
    """Replace each detected caption line with a `> **[Figure — …]**` placeholder
    so the page is flagged. (Removing the figure's *interior* labels that dots.mocr
    transcribes inline needs the text-bearing layout pass — left as a follow-up.)"""
    capset = set(caps)
    out = []
    for line in text.splitlines():
        s = line.strip().lstrip("#>*").strip()
        if s in capset:
            out.append(f"> **[Figure — {s}]**")
            capset.discard(s)
        else:
            out.append(line)
    return "\n".join(out)


def process_image(model, processor, config, img_path, out_dir, model_name):
    """Orientation-correcting OCR, then detect the shot type from its text → a
    cache record. Caller checks the cache first; this always recomputes."""
    exif = read_exif(img_path)
    figures = []
    with tempfile.TemporaryDirectory() as td:
        text, rot, passes, probes, score, stats = ocr_oriented(
            model, processor, config, img_path, td)
        quality = text_quality(text)
        # Adaptive resolution: a poor read is often just too-low resolution. Re-OCR the
        # chosen orientation at the sharper MAX_EDGE and keep the better read. Live since
        # FAST_MAX_EDGE was eval-tuned to 1280 < MAX_EDGE (IMPLEMENTATION_PLAN.md §11).
        if quality < QUALITY_RETRY and FAST_MAX_EDGE < MAX_EDGE:
            p = Path(td) / f"r{rot}.jpg"
            prep_image(img_path, rot, max_edge=MAX_EDGE).convert("RGB").save(
                p, "JPEG", quality=90)
            hi_text, hi_stats = ocr_image(model, processor, config, str(p))
            passes += 1
            if text_quality(hi_text) > quality:  # sharper read is cleaner → adopt it
                text, stats, score = hi_text, hi_stats, _text_score(hi_text)
                quality = text_quality(hi_text)
        typ = detect_type(text)
        role = "meta" if typ in ("COVER", "IMPRINT") else "body"
        # Largest-font cover title: COVER shots pay one layout+text pass so the title
        # is the biggest type on the page, not whatever line OCR'd first (§16). The
        # chosen-orientation image is still on disk in `td`.
        cover_title = ""
        if typ == "COVER":
            oriented = Path(td) / f"r{rot}.jpg"
            cover_title = cover_title_layout(model, processor, config, str(oriented))
        # Gated figure detection: only body pages with a figure-ish caption pay the
        # extra layout pass; the chosen-orientation image is still on disk in `td`.
        caps = figure_captions(text) if role == "body" else []
        if caps:
            oriented = Path(td) / f"r{rot}.jpg"
            n_regions = layout_figures(model, processor, config, str(oriented))
            if n_regions:  # confirmed by layout → flag it
                figures = caps
                text = inject_figure_placeholders(text, caps)

    rec = {
        "image": Path(img_path).name,
        "type": typ,
        "rotation": rot,
        "orient_passes": passes,   # full transcription passes (1 = upright; 2 = rotated)
        "orient_probes": probes,   # cheap orientation probes spent (0 = upright fast path)
        "text_score": score,       # letters transcribed at the chosen orientation
        "quality": quality,        # 0..1 read-quality hint (low = scrambled/looping; review)
        "pass_stats": stats,       # chosen pass cost: prefill/decode tokens, tps, finish_reason
        "role": role,
        "figures": figures,        # caption lines flagged as figures/maps (may be [])
        "raw_md": text if role == "body" else "",
        "ocr_text": text,  # always kept (debug / cover-title fallback)
        "cover_title": cover_title,  # largest-font Title on a COVER ("" otherwise/none)
        "metadata": parse_metadata(typ, text, cover_title) if role == "meta" else "",
        "running_header": running_header(text) if role == "body" else "",
        "page_numbers": page_numbers(text) if role == "body" else [],
        "datetime": exif["datetime"].isoformat() if exif["datetime"] else None,
        "gps": list(exif["gps"]) if exif["gps"] else None,
        "model": model_name,
        "prompt_version": PROMPT_VERSION,
        "processed_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    save_cache(out_dir, img_path, rec)
    return rec


def backfill_cover_title(model, processor, config, img_path, out_dir, rec, model_name):
    """Add the layout-derived `cover_title` (§16) to an already-cached COVER record
    without re-running the full transcription. One layout+text pass at the cached
    orientation; the field is then re-saved so the work is checkpointed and the next
    run skips it (resumable, like the main batch). Marks the field even when empty so
    a title-less cover isn't reprobed every run. Body OCR/cache are untouched."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "cover.jpg"
        prep_image(img_path, rec.get("rotation", 0), max_edge=MAX_EDGE).convert(
            "RGB").save(p, "JPEG", quality=90)
        title = cover_title_layout(model, processor, config, str(p))
    rec["cover_title"] = title
    if rec.get("role") == "meta":  # refresh the title: line from the new signal
        rec["metadata"] = parse_metadata(rec.get("type"), rec.get("ocr_text", ""), title)
    save_cache(out_dir, img_path, rec)
    return rec


# --- per-book grouping (no inference) -------------------------------------- #
def _parse_dt(s):
    return datetime.datetime.fromisoformat(s) if s else None


# Library-slip / call-number / form-field noise that looks line-shaped but is
# never a book title (seen across the in/ batch). Kept out of the title signal.
_SLIP_NOISE = re.compile(
    r"(?i)^(pick-?up location|user group|status|due date|barcode|location|"
    r"author|editor|title|call ?(?:no|number)|class(?:mark)?|isbn|shelf|"
    r"first published|published (?:in|by)|reprinted|copyright|all rights|"
    r"wag\s*\d|n\d|[A-Z]{1,3}\d)")


def _is_title_like(s: str) -> bool:
    """A short, mostly-alphabetic line that reads like a running title/book title
    — not a body sentence, a folio, a call number, or a library-slip field."""
    if not (3 <= len(s) <= HEADER_MAXLEN):
        return False
    if s[0].isdigit() or _SLIP_NOISE.match(s) or FIGURE_CAPTION_RE.match(s):
        return False
    if re.fullmatch(r"(?i)(page\s+)?\d{1,4}", s):   # bare folio, e.g. "Page 1"
        return False
    letters = sum(c.isalpha() for c in s)
    if letters < 0.6 * len(s):          # drops call numbers / ISBNs / mostly-symbol lines
        return False
    return s.isupper() or s.istitle() or s.count(" ") <= 5


def _cover_title(text: str) -> str:
    """The book title printed on a cover/spine shot. dots.mocr prefixes the shot with
    a "### Page N" folio and may emit a call-number/ISBN/slip line above the title; we
    skip that leading noise (anything not title-like), then take the first run of
    consecutive title-like lines — joining a title that wraps across lines (e.g.
    "THE ECONOMIC HISTORY" / "OF SINGAPORE") and stopping at the blank line that
    separates the title from the author/imprint block. "" if no title-like line."""
    parts = []
    for line in text.splitlines():
        s = line.strip().lstrip("#>* ").strip()
        if _is_title_like(s):
            parts.append(s)
        elif parts:                 # blank/author/noise after the title began → done
            break
        # else: still above the title (folio / call-no / ISBN) → keep skipping
    return " ".join(parts)


def page_header(rec) -> str:
    """The shot's best book-title signal, or "". For cover/imprint shots use the
    parsed/CIP title; for body pages use the running header at the top of the page
    (the repeated book/chapter title), ignoring folios and slip noise."""
    if rec.get("role") == "meta":
        ct = (rec.get("cover_title") or "").strip()  # largest-font Title (§16) wins
        if ct:
            return ct
        mt = re.search(r"(?im)^\s*title:\s*(.+)$", rec.get("metadata", ""))
        if mt:
            t = mt.group(1).strip().strip("\"'")
            if not re.fullmatch(r"(?i)(page\s+)?\d{1,4}", t):  # skip "Page 1" mis-parse
                return t
        t = _cip_title(rec.get("ocr_text", ""))
        if t:
            return t
        if rec.get("type") == "COVER":   # spine/cover title; imprint lines are boilerplate
            return _cover_title(rec.get("ocr_text", ""))
        return ""
    for line in (rec.get("ocr_text") or rec.get("raw_md", "")).splitlines():
        s = line.strip().lstrip("#>* ").strip()
        if not s or re.fullmatch(r"(?i)(page\s+)?\d{1,4}", s):
            continue          # skip blank lines and bare folios / "### Page N"
        return s if _is_title_like(s) else ""  # first real line decides; else no header
    return ""


def _hdr_match(a: str, b: str) -> bool:
    """Fuzzy equality of two title-like strings (same book). Containment counts as
    a match so a short cover title ("Tribal Communities") binds to the longer CIP
    title with subtitle ("Tribal Communities in the Malay World: …")."""
    if not (a and b):
        return False
    na, nb = normalize(a), normalize(b)
    if len(na) >= 6 and len(nb) >= 6:
        short, lng = (na, nb) if len(na) <= len(nb) else (nb, na)
        # containment must land on word boundaries, so a cover title ("Tribal
        # Communities") still binds to its longer CIP form ("Tribal Communities in
        # the Malay World") but a bare word does NOT match a different title that
        # merely embeds it ("Singapore" ⊄ "Leluhur Singapore's Kampong Gelam").
        if re.search(r"(?:^|\s)" + re.escape(short) + r"(?:\s|$)", lng):
            return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= HEADER_MATCH


# A library shelfmark / call number printed on a pickup slip — e.g. "N305.80095/18".
# Requires a leading letter so capture years ("1994/95") don't match. Each book has
# its own, so a change of call number is a reliable book boundary within a session.
_CALL_RE = re.compile(r"\b([A-Z]{1,3}\d{3}[.\d]*\s*/\s*\d{1,3})\b")


def call_number(rec) -> str:
    m = _CALL_RE.search(rec.get("ocr_text", ""))
    return re.sub(r"\s+", "", m.group(1)) if m else ""


def _page_reset(prev, rec) -> bool:
    """The page numbering restarts — a new book/section begins. Either the folio
    drops (rec starts below where prev ended) or it returns to the front matter."""
    pn, rn = prev.get("page_numbers"), rec.get("page_numbers")
    if not (pn and rn):
        return False
    return rn[0] < pn[-1] or (rn[0] <= 1 < pn[-1])


def _session_gap(prev, rec) -> bool:
    """A long capture-time gap OR a continent-scale GPS change. Soft — a matching
    title overrides it (the same book may be photographed across days/sessions)."""
    a, b = _parse_dt(prev["datetime"]), _parse_dt(rec["datetime"])
    if a and b and abs((b - a).total_seconds()) > SESSION_GAP_S:
        return True
    pg, rg = prev.get("gps"), rec.get("gps")
    if pg and rg and (abs(pg[0] - rg[0]) > GPS_SESSION_DEG
                      or abs(pg[1] - rg[1]) > GPS_SESSION_DEG):
        return True
    return False


def _new_book(hdr: str, anchored: bool, call: str = "", session_start: bool = False) -> dict:
    return {"records": [], "metadata": [], "has_body": False, "identity": hdr or "",
            "anchored": anchored, "headers": [], "call": call,
            "session_start": session_start, "key_images": []}


def group_images(records, merges=None):
    """Segment the shots (in capture order) into per-book runs, then merge runs
    that share a title. Driven by title identity, not by time/GPS fences:

      1. a cover/imprint with a *new* title starts a book;
      2. a session gap starts a book (unless a matching title says otherwise);
      3. a title-like header that disagrees with the current book's established
         title starts a book (but never splits a cover/imprint-anchored book on
         its varying chapter headers);
      4. everything else (headerless pages, figures, slips) joins the current book.

    Because a book's title can first appear several pages in (a headerless opening
    page, then the running header), a final pass merges adjacent runs whose header
    sets overlap — this rejoins one book shot across separate sessions/days."""
    recs = [r for r in records if r.get("role") != "skip"]
    books, cur, prev = [], None, None
    for r in recs:
        hdr = page_header(r)
        call = call_number(r)
        is_meta = r.get("role") == "meta"
        start = False
        sess = prev is not None and _session_gap(prev, r)
        if cur is None:
            start = True
        elif is_meta and hdr and cur["identity"] and not _hdr_match(hdr, cur["identity"]):
            start = True                                   # rule 1: new cover/imprint title
        elif hdr and cur["identity"] and _hdr_match(hdr, cur["identity"]):
            start = False                                  # same title → keep (overrides gap)
        elif call and cur["call"] and call != cur["call"]:
            start = True                                   # rule 2: a different library call number
        elif _session_gap(prev, r):
            start = True                                   # rule 3: session boundary
        elif hdr and cur["identity"] and not cur["anchored"] \
                and not _hdr_match(hdr, cur["identity"]) and _page_reset(prev, r):
            start = True            # rule 4: title change mid-session, confirmed by a page reset
            # (a reset guards against splitting a book whose verso/recto headers alternate,
            #  e.g. book title vs. chapter title on facing pages)

        if start:
            cur = _new_book(hdr, is_meta and bool(hdr), call, session_start=(cur is None or sess))
            books.append(cur)
        cur["records"].append(r)
        if hdr:
            cur["headers"].append(hdr)
        if call and not cur["call"]:
            cur["call"] = call
        if is_meta:
            cur["metadata"].append(r)
            if hdr and not cur["anchored"]:
                cur["identity"], cur["anchored"] = hdr, True
        else:
            cur["has_body"] = True
            if hdr and not cur["identity"]:
                cur["identity"] = hdr
        prev = r
    books = _infer_key_image_titles(
        _fold_orphan_covers(_fold_key_images(_merge_shared_title(books))))
    books = _merge_library_duplicates(books)          # safe auto Tier 1 + Tier 2
    if merges:                                        # human-confirmed allow-list
        groups, moves = merges
        books = _apply_manual_merges(books, groups, moves)
    return books


def _fold_key_images(books):
    """A session often opens on a shelf/spine overview shot that previews the next
    few books (e.g. IMG_4310, three spines). It lands as a lone titleless group at a
    session start; fold it into the following same-session book as a recorded *key
    image* (provenance) rather than counting it as a book of its own."""
    out = []
    for i, b in enumerate(books):
        nxt = books[i + 1] if i + 1 < len(books) else None
        is_key = (b["session_start"] and len(b["records"]) == 1 and not b["identity"]
                  and not b["metadata"])
        if is_key and nxt and not _session_gap(b["records"][-1], nxt["records"][0]):
            nxt["records"] = b["records"] + nxt["records"]
            nxt["key_images"] = [b["records"][0]["image"]] + nxt["key_images"]
            nxt["session_start"] = True
            if not nxt["call"]:
                nxt["call"] = b["call"]
        else:
            out.append(b)
    return out


# --- orphan cover folding (cover may lead OR trail its pages) -------------- #
# The forward pass assumes a cover/imprint *leads* its book: such a shot starts a
# book and the following pages join it (so a leading cover is never left alone).
# But a cover photographed AFTER its pages (user workflow: shoot the pages, then
# the cover — e.g. IMG_2249 covering pages from IMG_2230) has no following pages to
# adopt it, so it splits off as a body-less one-shot "book". The same happens to a
# short caption page misclassified as COVER (a stray Map/Figure). This pass folds
# such a body-less meta-only book back into the same-session neighbour it belongs to.
def _gap_secs(rec_a, rec_b):
    """Absolute capture-time gap in seconds between two shots, or None if unknown."""
    a, b = _parse_dt(rec_a.get("datetime")), _parse_dt(rec_b.get("datetime"))
    return abs((b - a).total_seconds()) if a and b else None


def _is_real_cover_title(t: str) -> bool:
    """A confident book-title string (so it may name the host book) vs. a misclassified
    figure caption / slip fragment (which must NOT override the neighbour's title).
    Builds on `_is_title_like` (already rejects folios, figure captions, slip noise),
    adding: must start upper-case and not be an all-lowercase sentence fragment."""
    t = (t or "").strip()
    return bool(t) and _is_title_like(t) and t[0].isupper() and t != t.lower()


def _title_matches_book(t: str, book) -> bool:
    """Does title `t` match this book's established title/headers (same book)?"""
    if _hdr_match(t, book.get("identity", "")) or _hdr_match(t, book_title(book)):
        return True
    return any(_hdr_match(t, h) for h in book.get("headers", []))


def _choose_orphan_neighbour(b, prev, nxt):
    """Pick the book a body-less meta shot belongs to: a title match wins; else the
    nearer same-session neighbour in capture time (trailing covers default to prev).
    Returns 'prev', 'next', or None (genuinely standalone — keep as its own book)."""
    t = book_title(b)
    pm = prev is not None and _title_matches_book(t, prev)
    nm = nxt is not None and _title_matches_book(t, nxt)
    if pm and not nm:
        return "prev"
    if nm and not pm:
        return "next"
    ps = prev is not None and not _session_gap(prev["records"][-1], b["records"][0])
    ns = nxt is not None and not _session_gap(b["records"][-1], nxt["records"][0])
    if ps and not ns:
        return "prev"
    if ns and not ps:
        return "next"
    if not ps and not ns:
        return None
    gp = _gap_secs(prev["records"][-1], b["records"][0])
    gn = _gap_secs(b["records"][-1], nxt["records"][0])
    if gp is not None and gn is not None and gn < gp:
        return "next"
    return "prev"  # both same-session and times tie/unknown → trailing-cover default


def _strip_title_line(meta_text: str) -> str:
    return "\n".join(ln for ln in meta_text.splitlines()
                     if not re.match(r"(?i)^\s*title\s*:", ln)).strip()


def _absorb_orphan(host, orphan, where: str) -> None:
    """Merge a body-less orphan into `host`. Records keep capture order (prepend for a
    leading cover, append for a trailing one). The orphan's bibliographic fields are
    added, but its title is kept only when it's a confident cover — `book_title` takes
    the first metadata title, so a stripped-title block can never override the host."""
    recs = orphan["records"]
    host["records"] = recs + host["records"] if where == "prepend" \
        else host["records"] + recs
    host["has_body"] = host["has_body"] or orphan["has_body"]
    host["headers"] += orphan["headers"]
    if not host["call"] and orphan["call"]:
        host["call"] = orphan["call"]
    keep_title = _is_real_cover_title(book_title(orphan))
    for m in orphan["metadata"]:
        if keep_title:
            host["metadata"].append(m)
        else:
            host["metadata"].append({**m, "metadata": _strip_title_line(m.get("metadata", ""))})
    if keep_title and not host["identity"]:
        host["identity"] = book_title(orphan)


def _fold_orphan_covers(books):
    """Fold each body-less meta-only book (a cover/imprint or stray caption shot left
    on its own) into the same-session neighbour it belongs to (see _choose_neighbour).
    A no-op for normal body-bearing books, so it can't fragment a real book — it only
    ever merges a lone meta shot away."""
    def is_orphan(b):
        has_body = any(r["role"] == "body" and r.get("raw_md", "").strip()
                       for r in b["records"])
        return (not has_body) and bool(b["metadata"])

    out, pending = [], []
    for idx, b in enumerate(books):
        for o in pending:                 # a previous orphan chose to lead this book
            _absorb_orphan(b, o, "prepend")
        pending = []
        if not is_orphan(b):
            out.append(b)
            continue
        prev = out[-1] if out else None
        nxt = books[idx + 1] if idx + 1 < len(books) else None
        choice = _choose_orphan_neighbour(b, prev, nxt)
        if choice == "prev":
            _absorb_orphan(prev, b, "append")
        elif choice == "next":
            pending.append(b)
        else:
            out.append(b)                 # standalone — no same-session neighbour
    out.extend(pending)                   # trailing orphan with no next book to lead
    return out


# A shelf/spine overview shot OCRs as one block per spine (each book's pickup slip
# repeats "Wag NNN" / "Pick-up Location"). We split on that marker and read the spine
# title out of each block — used to name an otherwise-untitled book by *exclusion*.
_SPINE_SPLIT = re.compile(r"(?im)^\s*Wag\s*\d+\s*$")
_SPINE_STOP = re.compile(r"(?i)\b(press|university|publish|librar|gener(?:al)?\s+ref|"
                         r"reference|ref(?:erence)?\s+librar|state\s+librar|room|"
                         r"building|street|reading|bashir|macquarie|user group|"
                         r"pick-?up|location|n\.?s\.?w)\b")


def _spine_titles(block: str):
    """Title-like lines in one spine block. The slip prints the spine title right
    after its "User Group:" field, so we REQUIRE that anchor and read from there;
    a block without it is a slip fragment (e.g. a runaway-truncated "GENERAL REF"
    library stamp), never a title. Drops authors (lone SURNAME), publishers, and
    the address/boilerplate lines."""
    m = re.search(r"(?i)user group:?", block)
    if not m:
        return []
    region = block[m.end():]
    out = []
    for line in region.splitlines():
        s = line.strip().lstrip("#>* ").strip()
        if (_is_title_like(s) and not _SPINE_STOP.search(s)
                and "(" not in s and ")" not in s
                and not (s.isupper() and len(s.split()) <= 1)):  # not a lone SURNAME
            out.append(s)
    return out


def infer_title_by_exclusion(key_recs, known_titles) -> str:
    """Read the spine titles off a key image, drop any block whose title belongs to
    an already-identified sibling book, and return the first title left over — the
    name of the unknown book, by elimination. No call numbers involved."""
    for rec in key_recs:
        for block in _SPINE_SPLIT.split(rec.get("ocr_text", "")):
            titles = _spine_titles(block)
            if titles and not any(_hdr_match(t, k) for t in titles for k in known_titles):
                return titles[0]
    return ""


def _infer_key_image_titles(books):
    """Fill in an untitled book that carries a key image by excluding the titles of
    the other (already-named) books visible on that same shelf/spine shot."""
    confident = [book_title(b) for b in books]
    for i, b in enumerate(books):
        if not b.get("key_images") or not book_title(b).startswith("Untitled ("):
            continue
        known = [t for j, t in enumerate(confident)
                 if j != i and not t.startswith("Untitled (")]
        key_recs = [r for r in b["records"] if r["image"] in b["key_images"]]
        t = infer_title_by_exclusion(key_recs, known)
        if t:
            b["inferred_title"] = t
    return books


def _merge_shared_title(books):
    """Fold an adjacent run into the previous book when their title-header sets
    overlap (fuzzy). Rejoins e.g. a book shot on two different days whose later
    session opens on a headerless page before the running title reappears."""
    merged = []
    for b in books:
        prev = merged[-1] if merged else None
        call_conflict = prev and prev["call"] and b["call"] and prev["call"] != b["call"]
        if prev and not b["anchored"] and not call_conflict and any(
                _hdr_match(h, p) for h in b["headers"] for p in prev["headers"]):
            prev["records"] += b["records"]
            prev["metadata"] += b["metadata"]
            prev["headers"] += b["headers"]
            prev["has_body"] = prev["has_body"] or b["has_body"]
            if not prev["identity"]:
                prev["identity"] = b["identity"]
            if not prev["call"]:
                prev["call"] = b["call"]
        else:
            merged.append(b)
    return merged


# --- library-wide duplicate merge (a book read in non-adjacent sessions) ---- #
# `_merge_shared_title` only rejoins *adjacent* runs. A book photographed across
# the library in two sittings (other books shot in between) lands as two separate
# books; a stray cover shot lands as a third. Two safe AUTO passes rejoin the
# unambiguous cases; the rest is left to a human-confirmed allow-list (in/merges.txt)
# because title identity alone is unsafe — distinct books share generic titles
# ("Singapore"), and real duplicates here often share NO resolved title at all
# (a Gusinde plate-book titled "Kawësqar woman."). See IMPLEMENTATION_PLAN §14.
def _book_isbn(book) -> str:
    """Normalized ISBN digits from any of the book's metadata blocks, or ""."""
    for m in book["metadata"]:
        mm = re.search(r"(?im)^\s*isbn\s*:\s*([\dxX\- ]+)", m.get("metadata", ""))
        if mm:
            return re.sub(r"[^\dxX]", "", mm.group(1))
    return ""


def _book_strong_key(book) -> str:
    """An exact, low-false-positive identity: ISBN, else library call number. Two
    books with the SAME strong key are the same book; with DIFFERENT keys they are
    provably different (different editions of one work — a hard negative)."""
    return _book_isbn(book) or (book.get("call") or "").strip()


def _book_has_body(book) -> bool:
    return any(r.get("role") == "body" and r.get("raw_md", "").strip()
              for r in book["records"])


def _fold_into(host, b, lead=False) -> None:
    """Merge book b into host. `lead` prepends b's records (a cover leads its book);
    otherwise append. Bibliographic fields and provenance accumulate; records are
    re-sorted into capture order by the caller."""
    host["records"] = (b["records"] + host["records"]) if lead \
        else (host["records"] + b["records"])
    host["metadata"] += b["metadata"]
    host["headers"] += b["headers"]
    host["has_body"] = host["has_body"] or b["has_body"]
    host["key_images"] = host.get("key_images", []) + b.get("key_images", [])
    if not host["call"] and b["call"]:
        host["call"] = b["call"]
    host.setdefault("merged_from", [host["records"][0]["image"]]).append(
        b["records"][0]["image"])


def _sort_records(book) -> None:
    """Order a book's shots by capture time then filename, so two merged readings
    read in sequence (each session's pages stay contiguous and in order)."""
    book["records"].sort(key=lambda r: (_parse_dt(r.get("datetime"))
                                        or datetime.datetime.min, r["image"]))


def _merge_library_duplicates(books):
    """Tier 1 + Tier 2 AUTO-merges (safe), library-wide. Title-only page+page
    merges are NOT done here — they go to merge_candidates() for human review."""
    # Tier 1: a body-less meta-only book (a lone cover/imprint, incl. a false-positive
    # cover) whose CONFIDENT title matches a body-bearing book anywhere -> fold in.
    body_books = [b for b in books if _book_has_body(b)]
    after_t1 = []
    for b in books:
        if _book_has_body(b) or not b["metadata"]:
            after_t1.append(b)
            continue
        t = book_title(b)
        host = next((hb for hb in body_books if hb is not b
                     and _is_real_cover_title(t)
                     and (_hdr_match(t, book_title(hb)) or _hdr_match(t, hb["identity"]))),
                    None)
        if host is not None:
            _fold_into(host, b, lead=True)
            _sort_records(host)
        else:
            after_t1.append(b)

    # Tier 2: two body-bearing books sharing an EXACT strong key (ISBN/call) -> merge.
    # A differing key never merges (kept apart as distinct editions).
    after_t2, by_key = [], {}
    for b in after_t1:
        k = _book_strong_key(b) if _book_has_body(b) else ""
        if k and k in by_key:
            _fold_into(by_key[k], b)
            _sort_records(by_key[k])
        else:
            if k:
                by_key[k] = b
            after_t2.append(b)
    return after_t2


# --- Tier 3: ranked candidates for human review (a discovery aid, not applied)- #
_GENERIC_TITLE = re.compile(r"(?i)^(singapore|directions|sea|east|map\s*\d*|preface|"
                            r"appendix|contents|introduction|memoir)\b")


def _candidate_score(ba, bc) -> int:
    """Confidence that two body-bearing books are the same book. Higher = surer."""
    ta, tc = book_title(ba), book_title(bc)
    ka, kc = _book_strong_key(ba), _book_strong_key(bc)
    if ka and kc and ka != kc:
        return -100  # different ISBN/call — distinct editions, never one book
    s = 0
    if normalize(ta) == normalize(tc):
        s += 3                                   # exact title equality (mod case/space)
    if (ka or kc) and normalize(ta) == normalize(tc):
        s += 2                                   # one-sided key + exact title (intertidal)
    if len(normalize(ta)) >= 20:
        s += 1                                   # long, specific titles rarely collide
    if _GENERIC_TITLE.match(ta.strip()) or _GENERIC_TITLE.match(tc.strip()):
        s -= 3                                   # generic title (the "Singapore problem")
    return s


def merge_candidates(books):
    """Body+body book pairs whose confident titles match but lack a shared strong
    key — merges that need a human to confirm (write them into in/merges.txt).
    Ranked best-first. Title-invisible duplicates won't appear here by design."""
    bb = [b for b in books if _book_has_body(b)
          and _is_real_cover_title(book_title(b))]
    out = []
    for i in range(len(bb)):
        for j in range(i + 1, len(bb)):
            if _hdr_match(book_title(bb[i]), book_title(bb[j])):
                sc = _candidate_score(bb[i], bb[j])
                if sc > 0:
                    out.append({"score": sc,
                                "a": bb[i]["records"][0]["image"], "a_title": book_title(bb[i]),
                                "b": bb[j]["records"][0]["image"], "b_title": book_title(bb[j])})
    return sorted(out, key=lambda d: -d["score"])


# --- manual allow-list (optional in/merges.txt; mirrors the RIS hint) ------- #
# Two operators, one group per line ('#' comments, blanks ignored):
#   IMG_a + IMG_b [+ IMG_c]   fold the WHOLE books containing these shots into one
#   IMG_host += IMG_x [IMG_y] MOVE individual shots into the host's book (for a
#                             stray cover/page the grouper put in the wrong book)
def load_merges(in_dir):
    """Parse in/merges.txt -> (groups, moves). Absent file -> ([], []). Never
    affects the cache or auto-grouping unless present."""
    p = Path(in_dir) / "merges.txt"
    if not p.exists():
        return [], []
    groups, moves = [], []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if "+=" in line:
            host, rest = line.split("+=", 1)
            shots = [s for s in re.split(r"[+\s]+", rest) if s]
            host = host.strip()
            if host and shots:
                moves.append((host, shots))
        else:
            stems = [s.strip() for s in line.split("+") if s.strip()]
            if len(stems) >= 2:
                groups.append(stems)
    return groups, moves


def load_titles(in_dir):
    """Parse in/titles.txt -> {stem: title}. Optional human title override for a book
    the OCR can't title itself — a title buried in a title-page list, lost to a runaway
    read, or otherwise absent from the bibliography. Absent file -> {}. Like the *.ris
    and merges.txt hints it only enriches output and never touches the cache/grouping.
    Syntax: `IMG_xxxx = Some Title` (any shot of the book), '#' starts a comment."""
    p = Path(in_dir) / "titles.txt"
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        stem, title = line.split("=", 1)
        stem, title = stem.strip(), title.strip()
        if stem and title:
            out[Path(stem).stem] = title
    return out


def _apply_manual_merges(books, groups, moves):
    """Apply allow-list whole-book merges then per-shot moves. Stems match a shot
    whose filename stem equals the given token (extension optional)."""
    def index():
        return {Path(r["image"]).stem: b for b in books for r in b["records"]}

    # whole-book merges: fold each group's later books into the first present one.
    idx, dropped = index(), set()
    for stems in groups:
        host = next((idx.get(s) for s in stems
                     if idx.get(s) is not None and id(idx[s]) not in dropped), None)
        if host is None:
            continue
        for s in stems:
            b = idx.get(s)
            if b is not None and b is not host and id(b) not in dropped:
                _fold_into(host, b)
                dropped.add(id(b))
        _sort_records(host)
    books = [b for b in books if id(b) not in dropped]

    # per-shot moves: pull individual shots out of their book into the host's book.
    idx = index()
    rec_of = {Path(r["image"]).stem: r for b in books for r in b["records"]}
    for host_stem, shots in moves:
        host = idx.get(host_stem)
        if host is None:
            continue
        for s in shots:
            rec, src = rec_of.get(s), idx.get(s)
            if rec is None or src is None or src is host:
                continue
            src["records"].remove(rec)
            if rec in src["metadata"]:
                src["metadata"].remove(rec)
            host["records"].append(rec)
            if rec.get("role") == "meta":
                host["metadata"].append(rec)
            host["has_body"] = host["has_body"] or (
                rec.get("role") == "body" and rec.get("raw_md", "").strip() != "")
            host.setdefault("merged_from", [host["records"][0]["image"]]).append(rec["image"])
            idx[s] = host
        _sort_records(host)
    return [b for b in books if b["records"]]


# --- output ---------------------------------------------------------------- #
def _yaml_val(v: str) -> str:
    """Double-quote a scalar that would otherwise break YAML (e.g. a title with a
    colon, like 'The Enemy of All: Piracy and the Law of Nations')."""
    v = str(v)
    if re.search(r'[:#\[\]{}&*!|>%@`\"]', v) or v[:1] in "-?,'":
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return v


def _slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:40].strip("-") or "book"


def _cip_title(text: str):
    """Title from a library Cataloguing-in-Publication block on an imprint page."""
    m = re.search(r"(?i)catalogu(?:e|ing)[- ]in[- ]publication data\s*\n+\s*([^\n/]+)",
                  text)
    if m:
        t = m.group(1).strip().strip("\"'")
        return t[:1].upper() + t[1:] if t else None
    return None


def _folio(s: str) -> bool:
    return bool(re.fullmatch(r"(?i)(page\s+)?\d{1,4}", s.strip()))


def book_title(book) -> str:
    """A short, sane title — never a body paragraph. Prefer the book's own title-page
    cover, then a CIP title, then the most common running header across the book's
    pages, else an Untitled placeholder keyed to the first shot."""
    from collections import Counter
    # 0. a human title override (in/titles.txt) wins outright — for a book the OCR can't
    #    title itself (title buried in a series-page list, or lost to a runaway read).
    if book.get("title_override"):
        return book["title_override"]
    # running-header consensus, computed up front: the fallback title, and also the
    # veto on a stray cover (see COVER_OVERRIDE_VOTES).
    headers = book.get("headers") or [h for r in book["records"]
                                      if (h := page_header(r))]
    top, topn = ("", 0)
    if headers:
        top, topn = Counter(headers).most_common(1)[0]

    # 1. the title printed on the book's own cover/title page. Use the EARLIEST cover
    #    (the genuine title page) so a later interior page misread as a COVER (e.g. a
    #    short "PREFACE" page) can't out-rank it; skip generic section words; and re-
    #    derive from the cover text when the cached title: is a "Page 1" folio mis-parse.
    for m in sorted((m for m in book["metadata"] if m.get("type") == "COVER"),
                    key=lambda r: (_parse_dt(r.get("datetime")) or datetime.datetime.min,
                                   r["image"])):
        # the largest-font Title from the layout pass (§16) is authoritative; only when
        # it's absent do we fall back to the cached title: line, then the text heuristic.
        c = (m.get("cover_title") or "").strip()
        if not c:
            mt = re.search(r"(?im)^\s*title:\s*(.+)$", m.get("metadata", ""))
            c = mt.group(1).strip().strip("\"'") if mt else ""
            if not c or _folio(c):
                c = _cover_title(m.get("ocr_text", ""))
        if c and _norm_title(c) not in _GENERIC_TITLES:
            # a running title repeated on many shots that disagrees means this "cover"
            # is a stray/UI shot — trust the book's own running title instead. But a
            # byline running header (e.g. the editors "Geoffrey Benjamin" printed as a
            # verso header) is NOT a title and must never veto a real cover title (§16).
            if (topn >= COVER_OVERRIDE_VOTES and not _hdr_match(c, top)
                    and not _looks_like_byline(top)):
                return top
            return c

    # 2. an explicit non-folio title: on a non-cover meta block, then CIP cataloguing data
    for m in book["metadata"]:
        mt = re.search(r"(?im)^\s*title:\s*(.+)$", m["metadata"])
        if mt:
            t = mt.group(1).strip().strip("\"'")
            if t and not _folio(t):
                return t
    for m in book["metadata"]:
        t = _cip_title(m.get("ocr_text", ""))
        if t:
            return t
    # 3. most frequent title-like running header among the book's shots (voting)
    if headers:
        return top
    # 4. title inferred by exclusion from a shared spine/shelf key image
    if book.get("inferred_title"):
        return book["inferred_title"]
    body = next((r for r in book["records"]
                 if r["image"] not in book.get("key_images", [])), book["records"][0])
    return f"Untitled ({Path(body['image']).stem})"


# --- optional Zotero/RIS bibliography hint --------------------------------- #
# A user-supplied `*.ris` export in the input folder is matched against each book
# to *correct* the OCR'd title and *complete* author/publisher/year/ISBN. It only
# enriches output — grouping and the cache never depend on it (it may be absent).
def load_ris(path) -> list:
    """Parse an RIS file into book-ish records. Tolerant of tag spacing/casing."""
    recs, cur = [], {}
    for line in Path(path).read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        if line.startswith("ER"):           # end of record — check before the tag match,
            recs.append(cur)                # since "ER  -" also matches the tag pattern
            cur = {}
            continue
        m = re.match(r"^([A-Z][A-Z0-9])\s+-\s?(.*)$", line)
        if m:
            cur.setdefault(m.group(1), []).append(m.group(2).strip())
    if cur:
        recs.append(cur)
    out = []
    for r in recs:
        ti = " ".join(r.get("TI") or r.get("T1") or [])
        if ti:
            out.append({
                "type": (r.get("TY") or [""])[0],
                "title": ti,
                "authors": r.get("AU") or r.get("A1") or [],
                "year": (r.get("PY") or r.get("Y1") or [""])[0][:4],
                "publisher": (r.get("PB") or [""])[0],
                "isbn": (r.get("SN") or [""])[0],
                "city": (r.get("CY") or [""])[0],
            })
    return out


def _norm_title(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.lower())).strip()


def _main_title(s: str) -> str:
    """The part before a subtitle colon/dash — the comparable core of a title."""
    return re.split(r"\s*[:—–]\s*", s, maxsplit=1)[0].strip()


# Generic front-matter / section headings that are NOT a book's title. A page mis-titled
# from such a running header ("Preface") must never match a RIS entry of the same name
# (a bibliography full of academic books has many a "Preface", "Introduction", …).
_GENERIC_TITLES = {
    "preface", "introduction", "contents", "table of contents", "index", "foreword",
    "acknowledgements", "acknowledgments", "bibliography", "references", "notes",
    "appendix", "glossary", "prologue", "epilogue", "afterword", "conclusion",
    "abstract", "summary", "abbreviations", "list of illustrations", "list of figures",
}


def match_ris(book, ris):
    """Best RIS book whose *main* title matches one of this book's title guesses.
    Compares pre-colon main titles only, so a shared subtitle suffix (e.g. '… in the
    Malay World') can't cause a false match. Returns the record or None."""
    queries = [book.get("title_override", ""), book_title(book),
               book.get("identity", ""), book.get("inferred_title", "")]
    qs = [_norm_title(_main_title(q)) for q in queries
          if q and not q.startswith("Untitled (")]
    qs = [q for q in qs if len(q) >= 6 and q not in _GENERIC_TITLES]
    if not qs:
        return None
    best, best_score = None, 0.0
    for r in ris:
        if r["type"] not in ("BOOK", "CHAP"):
            continue
        rmain = _norm_title(_main_title(r["title"]))
        if len(rmain) < 6:
            continue
        for q in qs:
            contained = ((q in rmain or rmain in q)
                         and min(len(q), len(rmain)) >= 0.7 * max(len(q), len(rmain)))
            score = 0.99 if contained else difflib.SequenceMatcher(None, q, rmain).ratio()
            if score > best_score:
                best, best_score = r, score
    return best if best_score >= 0.85 else None


def book_record(book, ris):
    """(display_title, ordered_fields, source_note). An RIS match overrides the OCR
    title and fills bibliographic fields. When a bibliography was supplied but this
    book isn't in it, that miss is reported too (metadata then comes from OCR); when
    no bibliography was supplied at all, the note is None."""
    m = match_ris(book, ris) if ris else None
    if m:
        fields = {}
        if m["authors"]:
            fields["author"] = "; ".join(m["authors"])
        for k in ("publisher", "year", "isbn", "city"):
            if m[k]:
                fields[k] = m[k]
        if book.get("call"):
            fields["call_number"] = book["call"]
        return m["title"], fields, f"Matched to “{m['title']}”"
    miss = "No match — title/metadata from OCR only" if ris else None
    return book_title(book), book_meta(book), miss


def write_book(out_dir, idx, book, ris=None) -> str:
    title, fields, note = book_record(book, ris)
    name = f"book_{idx:02d}_{_slugify(title)}"
    raw_meta = next((m["metadata"] for m in book["metadata"]
                     if m["metadata"].strip()), "")
    docs = _split_meta_docs(_clean_meta(raw_meta))
    parts = []
    yaml = "\n".join(f"{k}: {_yaml_val(v)}" for k, v in {"title": title, **fields}.items())
    parts.append("---\n" + yaml + "\n---\n")
    parts.append(f"# {title}\n")
    if book.get("merged_from"):
        seeds = ", ".join(dict.fromkeys(book["merged_from"]))  # de-dup, keep order
        parts.append(f"> _Assembled from {len(book['records'])} shots across "
                     f"multiple readings (seeds: {seeds})._\n")
    if note:
        parts.append(f"> **Zotero match**: {note}\n")
    if len(docs) > 1:  # cover/shelf shot listed more books — keep, don't corrupt frontmatter
        extra = "\n\n".join(docs[1:])
        parts.append("> _Other books visible in the cover/shelf photo:_\n\n"
                     "```yaml\n" + extra + "\n```\n")
    for r in book["records"]:
        if r["role"] == "body" and r["raw_md"].strip():
            parts.append(f"## {r['image']}\n\n{r['raw_md']}\n")
    md = "\n".join(parts).strip() + "\n"
    (Path(out_dir) / f"{name}.md").write_text(md, encoding="utf-8")
    (Path(out_dir) / f"{name}.txt").write_text(md_to_text(md), encoding="utf-8")
    return name


def write_index(out_dir, records, books) -> None:
    img2book = {r["image"]: i for i, b in enumerate(books, 1) for r in b["records"]}
    lines = ["# Index", "",
             "| image | type | rotation | book | figures | status |",
             "|---|---|---|---|---|---|"]
    for r in records:
        if r["role"] == "body" and not r["raw_md"].strip():
            status = "empty"
        elif r["role"] == "meta" and not r["metadata"].strip():
            status = "no-fields"
        else:
            status = "ok"
        bk = img2book.get(r["image"], "-")
        nfig = len(r.get("figures", []))
        lines.append(
            f"| {r['image']} | {r['type']} | {r.get('rotation', 0)} | {bk} "
            f"| {nfig or ''} | {status} |")
    (Path(out_dir) / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _gh_anchor(s: str) -> str:
    """GitHub-style heading anchor: lowercase, drop punctuation, spaces→hyphens.
    `## IMG_4310.jpeg` → `img_4310jpeg` (underscores kept, dot dropped)."""
    a = re.sub(r"[^\w\s-]", "", s.strip().lower())
    return re.sub(r"\s+", "-", a)


def _fmt_duration(secs: float) -> str:
    m = int(round(secs / 60))
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h {m % 60:02d} min"


def book_run_summary(book) -> str:
    """One-line run indicator: image count, when OCR last ran for this book, and a
    text-yield p50 (median letters transcribed per shot). dots.mocr gives no token
    logprobs, so text yield is our only model-quality proxy — a low p50 flags
    sparse/garbled reads. Returns "" if there's nothing to show."""
    recs = book["records"]
    parts = [f"{len(recs)} image" + ("" if len(recs) == 1 else "s")]
    runs = sorted(r["processed_at"] for r in recs if r.get("processed_at"))
    if runs:
        ts = datetime.datetime.fromisoformat(runs[-1]).strftime("%Y-%m-%d %H:%M")
        parts.append(f"OCR run {ts}")
    scores = sorted(r["text_score"] for r in recs if r.get("text_score") is not None)
    if scores:
        parts.append(f"text yield p50 {scores[len(scores) // 2]}")
    return " · ".join(parts)


def book_capture(book):
    """(first_dt, last_dt, duration_str) over the book's shots, or None."""
    dts = sorted(d for r in book["records"] if (d := _parse_dt(r.get("datetime"))))
    if not dts:
        return None
    return dts[0], dts[-1], _fmt_duration((dts[-1] - dts[0]).total_seconds())


def gps_radius(book):
    """(lat, lon, radius_m) — centroid of the book's shots and the farthest shot
    from it (equirectangular approx). A large radius flags a mis-grouped book."""
    pts = [r["gps"] for r in book["records"] if r.get("gps")]
    if not pts:
        return None
    import math
    lat0 = sum(p[0] for p in pts) / len(pts)
    lon0 = sum(p[1] for p in pts) / len(pts)
    k = 111_320.0
    radius = max(math.hypot((p[0] - lat0) * k,
                            (p[1] - lon0) * k * math.cos(math.radians(lat0)))
                 for p in pts)
    return lat0, lon0, radius


def _fmt_radius(m: float) -> str:
    return f"{m/1000:.1f} km" if m >= 1000 else f"{m:.0f} m"


def book_meta(book) -> dict:
    """Structured bibliographic fields for the report, in a fixed order. Pulled
    from the book's cover/imprint meta blocks + its library call number. Fixed
    fields with short values make a malformed value (a paragraph) obvious."""
    fields = {}
    for m in book["metadata"]:
        for line in m.get("metadata", "").splitlines():
            mm = re.match(r"(?i)\s*(publisher|year|isbn|call_number)\s*:\s*(.+)", line)
            if mm:
                k, v = mm.group(1).lower(), mm.group(2).strip()
                if k not in fields and 0 < len(v) <= 80:
                    fields[k] = v
    if book.get("call") and "call_number" not in fields:
        fields["call_number"] = book["call"]
    return fields


def write_report(out_dir, books, ris=None) -> None:
    """Human entry point to a whole batch: every book with structured metadata
    (title, author, publisher, year, ISBN, call no.), capture timespan, location
    radius, key-image provenance, and a linked list of its page shots. Where a
    Zotero/RIS hint is supplied, the title is corrected and fields completed from
    it. Pure + cheap, so it's rewritten after every image."""
    _LBL = {"author": "Author", "publisher": "Publisher", "year": "Year",
            "isbn": "ISBN", "city": "City", "call_number": "Call no."}
    lines = ["# Library OCR — batch report", "",
             f"_{len(books)} book(s)._", ""]
    for i, book in enumerate(books, 1):
        title, meta, note = book_record(book, ris)
        name = f"book_{i:02d}_{_slugify(title)}"
        lines += [f"## {i}. [{title}]({name}.md)", ""]
        lines += [f"_{book_run_summary(book)}_", ""]

        lines.append(f"- **Title:** {title}")
        for k in ("author", "publisher", "year", "isbn", "city", "call_number"):
            if k in meta:
                lines.append(f"- **{_LBL[k]}:** {meta[k]}")
        if note:
            lines.append(f"- **Zotero match**: {note}")

        cap = book_capture(book)
        if cap:
            first, last, dur = cap
            same_day = first.date() == last.date()
            lo = first.strftime("%Y-%m-%d %H:%M")
            hi = last.strftime("%H:%M" if same_day else "%Y-%m-%d %H:%M")
            lines.append(f"- **Captured:** {lo} → {hi} ({dur}, {len(book['records'])} shots)")
        rad = gps_radius(book)
        if rad:
            lat, lon, r = rad
            lines.append(f"- **Location:** {lat:.3f}, {lon:.3f} ± {_fmt_radius(r)}")

        if book.get("key_images"):
            ki = ", ".join(book["key_images"])
            how = (f' Title "{book["inferred_title"]}" inferred by exclusion (the other '
                   "spines on this shot matched already-identified books)."
                   if book.get("inferred_title") else
                   " Such a shot can show several books — verify the other spines belong "
                   "to the following books.")
            lines.append(
                f"- **Identified from key image(s) {ki}** (cover/spine/shelf shot)." + how)
        covers = [r["image"] for r in book["records"] if r.get("role") == "meta"
                  and r["image"] not in book.get("key_images", [])]
        if covers:
            lines.append(f"- **Cover/title shot(s):** {', '.join(covers)} "
                         "(read for metadata; not listed as a page below)")
        lines.append("")

        bodies = [r for r in book["records"] if r["role"] == "body"
                  and r["raw_md"].strip() and r["image"] not in book.get("key_images", [])]
        if bodies:
            lines.append("Pages:")
            for r in bodies:
                anchor = _gh_anchor(r["image"])
                pages = ", ".join(str(n) for n in r.get("page_numbers", []))
                suffix = f" — p. {pages}" if pages else ""
                nfig = len(r.get("figures", []))
                fig = f" · {nfig} figure(s)" if nfig else ""
                q = r.get("quality")
                warn = (f" · ⚠ low read quality ({q:.2f})"
                        if q is not None and q < QUALITY_RETRY else "")
                lines.append(f"- [{r['image']}]({name}.md#{anchor}){suffix}{fig}{warn}")
            lines.append("")
    (Path(out_dir) / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def log_event(out_dir, rec, elapsed_s, book) -> None:
    """Append one JSON line per processed image to out/instrument.jsonl — durable
    instrumentation for reviewing the run later (cost + quality signals). Only
    freshly-processed images are logged; cached ones are never recomputed.

    Each line: timing (elapsed_s, orient_passes — orientation retries are the main
    cost), routing (type, role, rotation, book), and quality proxies (text_score =
    letters transcribed, n_chars, page_numbers — there's no ground truth in batch,
    so these stand in for accuracy)."""
    p = Path(out_dir) / "instrument.jsonl"
    event = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "image": rec["image"],
        "type": rec["type"],
        "role": rec["role"],
        "rotation": rec.get("rotation", 0),
        "orient_passes": rec.get("orient_passes"),
        "orient_probes": rec.get("orient_probes"),
        "book": book,
        "elapsed_s": round(elapsed_s, 2),
        "pass_stats": rec.get("pass_stats"),  # prefill/decode tokens, tps, finish_reason
        "text_score": rec.get("text_score"),
        "quality": rec.get("quality"),  # 0..1 read-quality hint (low = scrambled/looping)
        "n_chars": len(rec.get("ocr_text", "")),
        "page_numbers": rec.get("page_numbers", []),
        "figures": len(rec.get("figures", [])),
        "has_metadata": bool(rec.get("metadata", "").strip()),
        "model": rec.get("model"),
        "prompt_version": rec.get("prompt_version"),
    }
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def emit_all(out_dir, records, ris=None, merges=None, titles=None) -> list:
    """Group cached records and (re)write every output: per-book files, the
    per-image index, and the top-level report. Pure over `records` and cheap, so
    it's safe to call after each image for incremental, visible output. `ris` is
    an optional parsed Zotero bibliography used to correct/complete metadata;
    `merges` is the optional (groups, moves) allow-list from in/merges.txt;
    `titles` is the optional {stem: title} override map from in/titles.txt."""
    books = group_images(records, merges)
    if titles:
        for book in books:
            ov = next((titles[s] for r in book["records"]
                       if (s := Path(r["image"]).stem) in titles), None)
            if ov:
                book["title_override"] = ov
    for stale in (*Path(out_dir).glob("book_*.md"), *Path(out_dir).glob("book_*.txt")):
        stale.unlink()  # slugs change between runs; clear before rewriting
    for i, book in enumerate(books, 1):
        write_book(out_dir, i, book, ris)
    write_index(out_dir, records, books)
    write_report(out_dir, books, ris)
    # Discovery aid: ranked same-title candidates a human may add to in/merges.txt.
    (Path(out_dir) / "merge_candidates.json").write_text(
        json.dumps(merge_candidates(books), ensure_ascii=False, indent=2), encoding="utf-8")
    return books


def cmd_batch(args):
    # The model is already cached locally; enforce no per-image network fetches
    # (otherwise HF-hub "checking" noise looks like a re-download every image).
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import mlx.core as mx  # for per-image Metal buffer-cache release (see loop below)

    in_dir = Path(args.dir)
    exts = ("*.jpeg", "*.jpg", "*.JPG", "*.png")
    images = sorted({p for ext in exts for p in in_dir.glob(ext)})
    if not images:
        sys.exit(f"No images in {in_dir}")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    ris_files = sorted(in_dir.glob("*.ris"))
    ris = load_ris(ris_files[0]) if ris_files else None
    if ris:
        print(f"Bibliography hint: {ris_files[0].name} ({len(ris)} entries)",
              file=sys.stderr)
    merges = load_merges(in_dir)
    if merges[0] or merges[1]:
        print(f"Merge allow-list: merges.txt ({len(merges[0])} book-merges, "
              f"{len(merges[1])} shot-moves)", file=sys.stderr)
    titles = load_titles(in_dir)
    if titles:
        print(f"Title overrides: titles.txt ({len(titles)} books)", file=sys.stderr)

    n = len(images)
    print(f"Batch: {n} images in {in_dir} → {out_dir} "
          f"(model {args.model}, offline)", file=sys.stderr)
    model = processor = config = None
    records = []
    failures = []
    processed = 0
    backfilled = 0
    run_start = time.perf_counter()
    for i, img in enumerate(images, 1):
        cached = None if args.force else load_cache(out_dir, img, args.model, PROMPT_VERSION)
        if cached is not None:
            # One-time enrichment of pre-§16 caches: a COVER lacking the largest-font
            # `cover_title` gets a single layout pass (no full re-OCR). Resumable.
            if (cached.get("type") == "COVER" and "cover_title" not in cached
                    and not args.no_cover_backfill):
                if model is None:
                    print(f"  loading model {args.model} ...", file=sys.stderr)
                    model, processor, config = load_model(args.model)
                cached = backfill_cover_title(model, processor, config, str(img),
                                              str(out_dir), cached, args.model)
                backfilled += 1
                print(f"[{i}/{n}] {img.stem} → COVER (backfilled title: "
                      f"{cached['cover_title'] or '—'})", file=sys.stderr)
            else:
                print(f"[{i}/{n}] {img.stem} → {cached['type']} (cached)", file=sys.stderr)
            records.append(cached)
            continue
        if model is None:  # lazy-load once, only when there's work to do
            print(f"  loading model {args.model} ...", file=sys.stderr)
            model, processor, config = load_model(args.model)
        t0 = time.perf_counter()
        mx.reset_peak_memory()
        try:
            rec = process_image(model, processor, config, str(img), str(out_dir), args.model)
        except Exception as e:  # noqa: BLE001 — one bad page must not kill an overnight batch
            elapsed = time.perf_counter() - t0
            failures.append(img.name)
            fp = out_dir / "failures.jsonl"
            with fp.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"image": img.name, "error": repr(e),
                                    "model": args.model, "ts": time.time()}) + "\n")
            print(f"[{i}/{n}] {img.stem} → FAILED ({type(e).__name__}); "
                  f"skipped after {elapsed:.1f}s, logged to {fp}", file=sys.stderr)
            continue
        finally:
            # Release MLX's Metal buffer cache between images. Left unbounded it grows
            # across a long batch and a late, heavy SPREAD page can OOM a 16 GB machine
            # mid-run (the abort is a C++ terminate the per-page except can't catch).
            # Cheap: sub-second buffer realloc. Runs on the failure path too (finally).
            mx.clear_cache()
        elapsed = time.perf_counter() - t0
        peak_gb = mx.get_peak_memory() / 1024**3
        records.append(rec)
        processed += 1
        # Refresh all outputs so out/ fills incrementally and the run is auditable
        # while it's still going (cheap, pure pass over the records so far).
        books = emit_all(out_dir, records, ris, merges, titles)
        bk = next((b for b, bdict in enumerate(books, 1)
                   for r in bdict["records"] if r["image"] == rec["image"]), "-")
        log_event(out_dir, rec, elapsed, bk)
        rot = f" rot{rec['rotation']}" if rec.get("rotation") else ""
        pss = f" {rec['orient_passes']}p" if rec.get("orient_passes", 1) > 1 else ""
        st = rec.get("pass_stats") or {}
        gen = st.get("generation_tokens")
        tok = f" {gen}tok@{st.get('generation_tps')}tps" if gen else ""
        run = " RUNAWAY" if st.get("finish_reason") == "length" else ""
        q = rec.get("quality")
        ql = f" q{q:.2f}" if q is not None and q < QUALITY_RETRY else ""
        print(f"[{i}/{n}] {img.stem} → {rec['type']}{rot} (book {bk}) "
              f"{elapsed:.1f}s{pss}{tok}{run}{ql} {peak_gb:.1f}GB", file=sys.stderr)

    books = emit_all(out_dir, records, ris, merges, titles)  # final pass (covers the all-cached case)
    bf = f", {backfilled} cover title(s) backfilled" if backfilled else ""
    if processed:
        wall = time.perf_counter() - run_start
        print(f"Done: {len(books)} books, {n} images "
              f"({processed} processed in {wall:.0f}s, "
              f"{wall / processed:.1f}s/img avg{bf}) → {out_dir}/report.md "
              f"(instrumentation: {out_dir}/instrument.jsonl)", file=sys.stderr)
    if failures:
        print(f"WARNING: {len(failures)} image(s) failed and were skipped "
              f"(see {out_dir}/failures.jsonl): {', '.join(failures)}", file=sys.stderr)
    elif not processed:
        print(f"Done: {len(books)} books, {n} images (all cached{bf}) "
              f"→ {out_dir}/report.md", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="OCR images → Markdown + plain text")
    p_run.add_argument("images", nargs="+", help="image file(s)")
    p_run.add_argument("--model", default=DEFAULT_MODEL, help="MLX model id")
    p_run.add_argument("--out", default="out", help="output directory")
    p_run.set_defaults(func=cmd_run)

    p_eval = sub.add_parser("eval", help="score candidate models vs ground truth")
    p_eval.add_argument("--models", default="", help="comma-separated model ids")
    p_eval.add_argument("--out", default="", help="optional dir to dump predictions")
    p_eval.add_argument("--max-edge", dest="max_edge", default="",
                        help="comma-separated long-edge caps to sweep (e.g. 1600,1280,1024); "
                             "downscales fixtures via prep_image like the batch pipeline")
    p_eval.set_defaults(func=cmd_eval)

    p_batch = sub.add_parser("batch", help="OCR a folder of photos, grouped per book")
    p_batch.add_argument("dir", nargs="?", default="in", help="input folder")
    p_batch.add_argument("--model", default=DEFAULT_MODEL, help="MLX model id")
    p_batch.add_argument("--out", default="out", help="output directory")
    p_batch.add_argument("--force", action="store_true", help="ignore cache, recompute")
    p_batch.add_argument("--no-cover-backfill", action="store_true",
                         help="skip the one-time layout pass that adds largest-font "
                              "cover titles to pre-§16 caches (keeps the fast emit-only path)")
    p_batch.set_defaults(func=cmd_batch)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
