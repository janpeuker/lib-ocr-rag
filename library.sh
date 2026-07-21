#!/usr/bin/env bash
# Everyday wrapper for the OCR → RAG pipeline (spec 021): applies the correct
# settings (repo cwd, venv, offline) so none of them need remembering. For
# multi-hour resilient batches use run_overnight.sh instead (caffeinate + retry).
set -euo pipefail

cd "$(cd "$(dirname "$0")" && pwd)"
source .venv/bin/activate
export HF_HUB_OFFLINE=1   # redundant with the in-tool default; kept as belt & braces

usage() {
  cat <<'EOF'
Usage: ./library.sh <command> [args...]

  update              OCR new photos in in/ then refresh the RAG catalog
                      (= ocr.py batch + rag.py index; both resume-by-default).
                      Ends with a warn-only health check (spec 029).
  search "<query>"    Search the library (passthrough to rag.py search: -k, --mode,
                      --book, --json, ...)
  page IMG_x          Fetch one page by image label (passthrough to rag.py get-page)
  books               List what the catalog holds
  doctor              Re-check the assumptions corpus growth invalidates
  eval                Score retrieval against rag_probes.json (see rag.py probes
                      --scaffold to bootstrap a probe set)
  ocr <args...>       Raw ocr.py passthrough (e.g. ./library.sh ocr run in/IMG_x.jpeg)
  rag <args...>       Raw rag.py passthrough (e.g. ./library.sh rag index --force)
EOF
  exit 1
}

cmd="${1:-}"; [[ $# -gt 0 ]] && shift
case "$cmd" in
  update)  python ocr.py batch "$@" && python rag.py index ;;
  search)  python rag.py search "$@" ;;
  page)    python rag.py get-page "$@" ;;
  books)   python rag.py books "$@" ;;
  doctor)  python rag.py doctor "$@" ;;
  eval)    python rag.py eval "$@" ;;
  ocr)     python ocr.py "$@" ;;
  rag)     python rag.py "$@" ;;
  *)       usage ;;
esac
