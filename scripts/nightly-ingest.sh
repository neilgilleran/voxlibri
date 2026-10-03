#!/usr/bin/env bash
# VoxLibri book ingest, on its own Pulse job since 2026-10-02 (it used to run inside the
# Loom's night; the Loom now only reads this database). New books from ~/FromLaptop/books,
# analysed on the Codex subscription with Luna pinned (Sol is for Forge only). Measured
# 36 to 60 successful model calls per book, 7 to 63 minutes. Files whose bytes match an
# analysed book are skipped; the batch stops at the first book that comes back empty
# (usage limit), so nothing is stored unanalysed.
#   scripts/nightly-ingest.sh          one new book (the nightly job)
#   scripts/nightly-ingest.sh 10       ten new books (a batch after a usage reset)
#   scripts/nightly-ingest.sh --list   what is waiting, no model calls
# The work runs DETACHED with a lock, like the Loom's worker: Pulse runs its jobs one at
# a time, and a book can take an hour, so the job must return at once. Output goes to
# logs/nightly-ingest.log.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
DROP="$HOME/FromLaptop/books"
LOG="$PWD/logs/nightly-ingest.log"
LOCK="/tmp/voxlibri-ingest.lock"

if [ "${1:-}" = "--worker" ]; then
  trap 'rmdir "$LOCK" 2>/dev/null' EXIT
  {
    echo "=== ingest start $(date '+%F %T') limit $2"
    VOXLIBRI_LLM_PROVIDER=codex VOXLIBRI_CLI_MODEL=gpt-5.6-luna \
      .venv/bin/python manage.py ingest_books "$DROP" --analyze --limit "$2"
    echo "=== ingest end $(date '+%F %T') rc=$?"
  } >>"$LOG" 2>&1
  exit 0
fi

if [ "${1:-}" = "--list" ]; then
  exec .venv/bin/python manage.py ingest_books "$DROP" --list
fi
LIMIT="${1:-1}"
[[ "$LIMIT" =~ ^[1-9][0-9]*$ ]] || { echo "usage: nightly-ingest.sh [N>=1|--list]" >&2; exit 2; }

mkdir -p "$(dirname "$LOG")"
# A lock older than 6 hours is a dead worker's, not a live run.
find "$LOCK" -maxdepth 0 -mmin +360 -exec rmdir {} \; 2>/dev/null
mkdir "$LOCK" 2>/dev/null || { echo "voxlibri ingest already running (lock $LOCK)"; exit 0; }
nohup "$0" --worker "$LIMIT" >/dev/null 2>&1 &
echo "voxlibri ingest started: up to $LIMIT book(s), log $LOG"
