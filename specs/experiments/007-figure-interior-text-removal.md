# EXP-007 — Drop the text printed inside a figure

**Status:** ⏳ Deferred (figures are *flagged*; interior words still leak)
**Date:** 2026-06 · **Related:** feature [005](../005-figure-detection/spec.md)

## Goal
A figure/map is now flagged with a `> **[Figure — …]**` placeholder (feature 005), but the words
printed *inside* the map — place names, legend labels — still appear as ordinary body text in the
output. We'd like to remove them so a figure-heavy page doesn't dump map labels into the prose.

## Why not done
Today's gated layout pass (`LAYOUT_PROMPT`) returns each region's **bbox + category only** — not
the text inside it — deliberately, so layout output never re-enters the transcription body (which
would reintroduce the dropped marginalia). To delete interior words we'd need positions for the
*transcribed* text too.

## Sketch when built
Switch the layout pass to dots.ocr's box-**with-text** mode (the same JSON shape as
`COVER_TITLE_PROMPT` already uses), then drop any transcribed line whose position falls inside a
`Picture` box. Must stay gated to figure-suspect pages and must not feed layout text into the
body — only *subtract* body text that overlaps a figure region.

## Risk to watch
A caption or a body line that legitimately sits next to a figure must not be eaten — tune the
overlap test against the known map pages (IMG_4406) before trusting it.
