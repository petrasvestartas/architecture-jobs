#!/bin/sh
# Daily Swiss architecture job scan — macOS/Linux entry point.
# The script locates itself, so it works wherever Google Drive mounts.
cd "$(dirname "$0")" || exit 1
export PYTHONIOENCODING=utf-8
PY="$(command -v python3 || command -v python)"
"$PY" scraper.py --careers 150 --career-watch 300 >> scraper.log 2>&1
