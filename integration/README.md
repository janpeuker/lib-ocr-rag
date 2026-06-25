# Import "library search" into another Claude project

This folder lets **other** Claude projects query the OCR'd book library that lives in
this `lib-ocr-rag` install — to look up citations/passages without loading whole
books into context. Nothing here changes *this* project; copy what you need into a
target project.

Both options talk to the same backend (this install's `rag.py` + `out/rag.db` +
cached `bge-small` model), so they always return the same results. They are
**independent** — use either, both, or switch later. **The CLI Skill is the
recommended primary path; the MCP server is an optional secondary.**

> **Path assumption.** Both reference absolute paths into this install via the
> placeholder `/ABSOLUTE/PATH/TO/lib-ocr-rag`. Before use, replace every occurrence
> with the real absolute path to your clone in `skill/library-search/SKILL.md` and
> `mcp/library-search.mcp.json` (e.g. `sed -i '' "s|/ABSOLUTE/PATH/TO/lib-ocr-rag|$(pwd)|g"`
> run from the repo root).

---

## Option A (preferred) — CLI Skill

A Skill that shells out to `rag.py … --json`. No resident process, no extra config;
Claude just runs the command when relevant and cites the results.

**Import:** copy the skill folder into the target project (or your user scope):

```bash
cp -r skill/library-search <target-project>/.claude/skills/
# or user-wide:  cp -r skill/library-search ~/.claude/skills/
```

That's it — start Claude in the target project and ask "where does <author> say
about <topic>?".

---

## Option B (optional) — MCP server

Registers `rag.py serve` as an MCP server exposing `search_library` and `get_page`
tools. Useful if you prefer a registered tool over a shell-out. Requires the `mcp`
package in this install (already added via `requirements-rag.txt`).

**Import — either** merge `mcp/library-search.mcp.json` into the target project's
`.mcp.json`, **or** register it with one command:

```bash
claude mcp add library-search --env HF_HUB_OFFLINE=1 -- \
  /ABSOLUTE/PATH/TO/lib-ocr-rag/.venv/bin/python \
  /ABSOLUTE/PATH/TO/lib-ocr-rag/rag.py serve
```

---

## Choosing / switching

- They don't conflict, but running **both** in one project gives Claude two ways to
  do the same thing. Pick one per project to keep it simple — Skill by default.
- To switch: delete the skill folder, or remove the MCP server
  (`claude mcp remove library-search`) / drop it from `.mcp.json`. The other keeps
  working untouched.
- Both are read-only lookups against `…/lib-ocr-rag/out/rag.db`. After new OCR,
  rebuild it once from this install: `python rag.py index`.
