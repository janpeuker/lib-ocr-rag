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

A separate project (`lib-ocr-rag`) OCRs book-page photos and indexes them into a local
hybrid-retrieval catalog: dense `bge-small` embeddings over sub-page windows, three
SQLite FTS5 lexical channels, fused with RRF and re-ranked by a small cross-encoder.
Query it through that project's `rag.py` instead of reading book files — a search
returns a few citation-stamped page snippets (a few hundred tokens). The point is
**look up, don't load.**

> **Install location** — replace the `/ABSOLUTE/PATH/TO/lib-ocr-rag` placeholder below
> with the real absolute path to your clone (and re-edit if the repo moves).
> The catalog and models live there; this Skill just shells out to it, so it works
> regardless of which project you're in.

Throughout, `$RAG` stands for:

```bash
HF_HUB_OFFLINE=1 /ABSOLUTE/PATH/TO/lib-ocr-rag/.venv/bin/python /ABSOLUTE/PATH/TO/lib-ocr-rag/rag.py
```

## When to use

- "Where does <author/book> say something about <topic>?"
- "Find the passage / quote about <topic> in the library."
- "What does the library have on <topic>?" / "Cite a source for <claim>."

The query can be paraphrased and a proper noun approximate — the fuzzy channel matches
OCR'd near-spellings (a page reading "Wilhem Braun" is found by "Wilhelm Braun"), and the
cross-encoder ranks by what a passage actually *says*, not just which topic it is about.

## How to search

```bash
$RAG search "<natural language query>" --json
```

Flags:

| Flag | Use |
|---|---|
| `-k N` | results (default 5). Use **10–15 for a survey** ("what does the library have on X"), 5 for a single citation. |
| `--book <substr>` | restrict to one book, e.g. `--book book_A`. Get the substring from `books` — never guess a number. |
| `--per-book N` | max results from one book (default 3). Use `0` when you *want* everything one book says. |
| `--mode hybrid\|dense\|lexical` | default `hybrid`. `lexical` only for an exact phrase you know is on the page. |
| `--no-rerank` | skip the cross-encoder (~2× faster, noticeably less precise). |

Each JSON result has: `score`, `citation` (paste-ready), `book`, `author`, `year`,
`image`, `image_path`, `page`, `book_file`, `text` (the full chunk).

### Search well, not once

- **Issue 2–3 differently-worded queries** for a topic before concluding the library
  has nothing. The reranker rewards queries phrased like the sentence you expect to
  find ("weavers acted as lenders to merchants") over bare keywords ("weavers lenders").
- Results are **one per page** and capped per book by default, so a thin-looking result
  set is breadth, not scarcity — raise `-k`, or use `--book`/`--per-book 0` to go deep
  on one source.
- Each search pays ~5 s of model loading, so prefer one `-k 15` call over three `-k 5` calls.

## What's in the library

```bash
$RAG books            # file, title, author, year, page/chunk counts per book
$RAG books --json
```

Use this to scope `--book`, to answer "what's in the library", or to check whether a
book is there at all before searching for it.

## How to answer

1. Read the returned `text` fields and answer from them.
2. **Always cite the `citation` field** (e.g. *Author, Title
   (2019) · IMG_1234 p.42*). Never invent page numbers — use what's returned.
3. For surrounding context, fetch the full page (± neighbours) instead of opening
   the book file:

   ```bash
   $RAG get-page <IMAGE_ID> --neighbors 1 --json
   ```

4. `image_path` is the absolute path to the **original page photo** (or `null`). You
   normally answer from `text` — but the catalog only holds OCR'd text, so when that text
   looks garbled or truncated, the page has a figure/table/map the text flattened, or you
   need handwriting/marginalia the OCR dropped, open the bitmap with the `Read` tool:
   `Read <image_path>`. The photo is the only place dropped annotations still exist.
5. Some pages are marked *"(text recovered from a cover/imprint shot)"* — that is real
   page text that happened to be photographed as a title/imprint page. Cite it normally.

## If the catalog is missing or stale

It lives at `…/lib-ocr-rag/out/rag.db`. Rebuild it from that project's root after new
OCR (it is cache-aware and resumable — only new/changed pages are re-embedded):

```bash
$RAG index
```
