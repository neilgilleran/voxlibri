#!/usr/bin/env bash
# VoxLibri book ingest, on its own Pulse job since 2026-10-02 (it used to run inside the
# Loom's night; the Loom now only reads this database). New books from ~/FromLaptop/books,
# analysed on the Codex subscription with Luna pinned (Sol is for Forge only).
# About 130 model calls per book. Files already loaded are skipped by content hash.
#   scripts/nightly-ingest.sh          one new book (the nightly job)
#   scripts/nightly-ingest.sh 10       ten new books (a batch after a usage reset)
#   scripts/nightly-ingest.sh --list   what is waiting, no model calls
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
DROP="$HOME/FromLaptop/books"
if [ "${1:-}" = "--list" ]; then
  exec .venv/bin/python manage.py ingest_books "$DROP" --list
fi
LIMIT="${1:-1}"
[[ "$LIMIT" =~ ^[0-9]+$ ]] || { echo "usage: nightly-ingest.sh [N|--list]" >&2; exit 2; }
VOXLIBRI_LLM_PROVIDER=codex VOXLIBRI_CLI_MODEL=gpt-5.6-luna \
  exec .venv/bin/python manage.py ingest_books "$DROP" --analyze --limit "$LIMIT"
