#!/usr/bin/env bash
# Resilient overnight OCR + RAG re-index.
# Both `ocr.py batch` and `rag.py index` are cache-aware and resume-by-default,
# so on any crash (OOM, etc.) we just re-run and they pick up from disk cache.
set -u

# Keep the Mac awake for the entire run (the "caffeinated" part): a multi-hour OCR
# batch will otherwise stall when the machine idle/disk/system-sleeps overnight.
# Re-exec once under caffeinate; resumability means even a missed sleep is harmless.
SCRIPT="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
if [[ -z "${OVERNIGHT_CAFFEINATED:-}" ]]; then
  export OVERNIGHT_CAFFEINATED=1
  exec caffeinate -ims "$SCRIPT" "$@"
fi

cd "$(dirname "$SCRIPT")"   # repo root (this script lives there)
source .venv/bin/activate
export HF_HUB_OFFLINE=1

LOG="out/overnight.log"
mkdir -p out

ts() { date "+%Y-%m-%d %H:%M:%S"; }
log() { echo "[$(ts)] $*" | tee -a "$LOG"; }

# Singleton guard. A *sequential* re-run is safe (the cache resumes), but two
# *concurrent* runs share out/cache and contend for the GPU — that's the race/OOM
# we hit. Refuse to start if another run is live; clear the lock on exit. The PID is
# re-checked against the process table (and its command) so a stale or reused PID
# from a hard kill can't wedge future runs.
LOCK="out/overnight.lock"
if [[ -f "$LOCK" ]]; then
  oldpid="$(cat "$LOCK" 2>/dev/null)"
  if [[ -n "$oldpid" ]] && ps -p "$oldpid" -o command= 2>/dev/null | grep -q run_overnight; then
    log "another overnight run is active (PID $oldpid) — refusing to start a second"
    exit 1
  fi
  log "removing stale lock (PID ${oldpid:-?} not running)"
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT INT TERM

# Run a command, retrying until it succeeds or we hit max attempts.
# Resumability means each retry continues from where the last left off.
retry() {
  local name="$1"; shift
  local max=8 n=0
  while (( n < max )); do
    n=$((n+1))
    log "$name: attempt $n/$max — $*"
    if "$@" >>"$LOG" 2>&1; then
      log "$name: SUCCESS on attempt $n"
      return 0
    fi
    log "$name: exited non-zero (attempt $n); sleeping 30s before resume"
    sleep 30
  done
  log "$name: GAVE UP after $max attempts"
  return 1
}

log "=== overnight run start ==="
log "input images: $(ls in/ | grep -icE '\.(jpe?g|png)$'), cached records: $(ls out/cache/*.json 2>/dev/null | wc -l | tr -d ' ')"

retry "OCR batch" python ocr.py batch || { log "OCR failed terminally — skipping index"; exit 1; }
retry "RAG index" python rag.py index || { log "RAG index failed terminally"; exit 1; }

log "=== overnight run COMPLETE ==="
log "books: $(ls out/book_*.md 2>/dev/null | wc -l | tr -d ' '), catalog: $(ls -la out/rag.db 2>/dev/null | awk '{print $5}') bytes"
