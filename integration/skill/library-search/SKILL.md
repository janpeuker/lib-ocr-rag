---
name: library-search
description: >-
  Search Jan's local OCR'd book library for citations and passages. Use whenever
  the user asks where or what a book/author says about a topic, wants a quote or
  source located, or asks to look something up "in the library/books" — so you can
  answer with an exact citation WITHOUT loading whole books into context. Offline;
  no network. Backed by a separate lib-ocr-rag install (see paths below).
---

# Library search

A separate project (`lib-ocr-rag`) OCRs book-page photos and indexes them into a
local hybrid-retrieval catalog (dense `bge-small` embeddings + SQLite FTS5, fused
with RRF). Query it through that project's `rag.py` instead of reading book files —
a search returns a few citation-stamped page snippets (a few hundred tokens). The
point is **look up, don't load.**

> **Install location** — replace the `/ABSOLUTE/PATH/TO/lib-ocr-rag` placeholder below
> with the real absolute path to your clone (and re-edit if the repo moves).
> The catalog and embedding model live there; this Skill just shells out to it, so
> it works regardless of which project you're in.

## When to use

- "Where does <author/book> say something about <topic>?"
- "Find the passage / quote about <topic> in the library."
- "What does the library have on <topic>?" / "Cite a source for <claim>."

The query can be paraphrased and the author name approximate — hybrid retrieval
handles fuzzy proper nouns + semantic concepts.

## How to search

```bash
HF_HUB_OFFLINE=1 /ABSOLUTE/PATH/TO/lib-ocr-rag/.venv/bin/python \
  /ABSOLUTE/PATH/TO/lib-ocr-rag/rag.py search "<natural language query>" --json
```

Flags: `-k N` (results, default 5) · `--book <substr>` (restrict to one book,
e.g. `--book book_13`) · `--mode hybrid|dense|lexical` (default `hybrid`; use
`lexical` for an exact phrase/name, `dense` for purely conceptual queries).

Each JSON result has: `score`, `citation` (paste-ready), `book`, `author`, `year`,
`image`, `image_path`, `page`, `book_file`, `text` (the full chunk).

`image_path` is the absolute path to the **original page photo** (or `null` if the page
isn't a scanned image). You normally answer from `text` — but the catalog only holds the
OCR'd text, so when that text looks garbled or truncated, the page has a figure/table/map
the text flattened, or you need handwriting/marginalia the OCR dropped, open the bitmap
directly with the `Read` tool (`Read` renders images): `Read <image_path>`. The photo is
the only place dropped annotations still exist.

## How to answer

1. Read the returned `text` fields and answer from them.
2. **Always cite the `citation` field** (e.g. *Andaya, Leaves of the Same Tree
   (2008) · IMG_4894 p.3*). Never invent page numbers — use what's returned.
3. For surrounding context, fetch the full page (± neighbours) instead of opening
   the book file:

   ```bash
   HF_HUB_OFFLINE=1 /ABSOLUTE/PATH/TO/lib-ocr-rag/.venv/bin/python \
     /ABSOLUTE/PATH/TO/lib-ocr-rag/rag.py get-page <IMAGE_ID> --neighbors 1 --json
   ```

4. If results look off-topic, retry with different wording or `--mode lexical`.

## If the catalog is missing or stale

It lives at `…/lib-ocr-rag/out/rag.db`. Rebuild it from that project's root after
new OCR (re-running embeds only new/changed pages):

```bash
HF_HUB_OFFLINE=1 /ABSOLUTE/PATH/TO/lib-ocr-rag/.venv/bin/python \
  /ABSOLUTE/PATH/TO/lib-ocr-rag/rag.py index
```
