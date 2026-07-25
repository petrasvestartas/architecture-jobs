#!/usr/bin/env bash
# Local Swiss architecture job scan — Linux/macOS entry point.
#
# Why a local scan exists at all, when the cloud already scans hourly: it
# carries the dependencies the light cloud scan skips — Playwright (career
# pages that build their job list in JavaScript; also the only way to read
# the Stadt/Kanton Bern portals) and python-jobspy (Indeed CH + Google
# Jobs) — installed into a local .venv on first run. It also runs from a
# residential address, which some boards throttle datacenter ranges out of.
#
# Layout: this clone is the working copy. The scan writes public/ and
# jobtracker/jobtracker.db in place, then publish_web.py commits and pushes,
# which deploys GitHub Pages.
#
#   ./run_local.sh              # light scan (the every-30-min default)
#   ./run_local.sh --full       # deep pass: career discovery + JS render
#
# Install the timer:  systemd/install.sh
set -uo pipefail

cd "$(dirname "$0")" || exit 1
JOBTRACKER="$PWD"
REPO="$(git -C "$JOBTRACKER" rev-parse --show-toplevel 2>/dev/null)" || {
  echo "run_local: not inside a git clone" >&2; exit 1; }
VENV="$REPO/.venv"
LOG="$JOBTRACKER/scraper.log"

export PYTHONIOENCODING=utf-8
export JOBTRACKER_SITE_DIR="$REPO/public"
export JOBTRACKER_REPO="$REPO"

log() { echo "[$(date -u +'%F %T')] $*" | tee -a "$LOG"; }

# --- one scan at a time -----------------------------------------------
# The light timer fires every 30 min and the full scan can run for an hour,
# so the two overlap by design. Two scrapers writing the same SQLite file
# will corrupt it, and both would push conflicting commits. Whoever holds
# the lock wins; the other exits quietly and catches the next tick.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$JOBTRACKER/.scan.lock"
  if ! flock -n 9; then
    log "another scan is already running — skipping this tick"
    exit 0
  fi
fi

# --- dependencies -----------------------------------------------------
# python-jobspy pins numpy/pandas, so it gets its own venv rather than the
# system interpreter. uv is preferred (Ubuntu ships python3 without
# ensurepip, so `python3 -m venv` fails on a stock install).
if [ ! -x "$VENV/bin/python" ]; then
  log "creating $VENV"
  if command -v uv >/dev/null 2>&1; then
    uv venv --python 3.12 "$VENV" >>"$LOG" 2>&1
  else
    python3 -m venv "$VENV" >>"$LOG" 2>&1 || {
      log "venv creation failed — install uv (https://astral.sh/uv) or the
           python3-venv package"; exit 1; }
  fi
fi
PY="$VENV/bin/python"

if ! "$PY" -c "import playwright, jobspy" >/dev/null 2>&1; then
  log "installing playwright + python-jobspy"
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$PY" playwright python-jobspy >>"$LOG" 2>&1
  else
    "$VENV/bin/pip" install playwright python-jobspy >>"$LOG" 2>&1
  fi
  "$PY" -m playwright install chromium >>"$LOG" 2>&1
fi

# --- sync -------------------------------------------------------------
# The cloud scan pushes the same DB and the same public/ files. Rebase onto
# it *before* scanning, so this run starts from current state and publishing
# afterwards is a fast-forward in the common case. --autostash covers a
# half-finished edit in the working tree.
log "syncing with origin"
git -C "$REPO" fetch --quiet origin >>"$LOG" 2>&1
git -C "$REPO" pull --rebase -X theirs --autostash --quiet origin main >>"$LOG" 2>&1 \
  || { log "rebase failed — aborting sync, scanning against local state"
       git -C "$REPO" rebase --abort >>"$LOG" 2>&1; }

# --- scan -------------------------------------------------------------
# Light by default: job boards plus a rolling slice of company career pages,
# cheap enough to repeat every 30 minutes. --full adds career-page discovery
# and the Playwright pass, matching the 05:30 cloud scan.
if [ "${1:-}" = "--full" ]; then
  log "full scan"
  JS_WATCH=1 "$PY" scraper.py --careers 150 --career-watch 300 --probe-js 80 \
      --competitions >>"$LOG" 2>&1
else
  log "light scan"
  JS_WATCH=1 "$PY" scraper.py --careers 40 --career-watch 250 >>"$LOG" 2>&1
fi
scan_rc=$?
[ "$scan_rc" -eq 0 ] || log "scraper exited $scan_rc (publishing anyway)"

# --- publish ----------------------------------------------------------
"$PY" publish_web.py >>"$LOG" 2>&1
log "publish exited $?"

# keep the log from growing without bound
if [ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt 5000000 ]; then
  tail -c 1000000 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
