# -*- coding: utf-8 -*-
"""Per-source health: compare this run's yield against the recent history.

A scraper that quietly returns zero rows looks exactly like a board that
simply has no jobs today, so a broken source can rot unnoticed for weeks.
That is not hypothetical: `jobagent` (architekten-jobs.ch) went from ~440
jobs per scan to 0 on 2026-07-15 and stayed silent for eleven days,
because the module catches its own HTTP errors and returns an empty list —
the run was still recorded as `ok`, and the website's Sources tab showed
"ok · 0 found".

This module reads the per-source counts earlier runs stored in the `runs`
table, derives a median baseline per source, and flags the ones that
collapsed against their own history. Verdicts go three places:

  * the run's `source_stats` (under the "_health" key) so the website's
    Sources tab can show a DEGRADED pill with a "search manually" link;
  * the scan log;
  * ::warning:: annotations when running on GitHub Actions, so a broken
    source is visible on the run page without reading the log.

Deliberately conservative — a false alarm every week would train the
reader to ignore the warnings, which is the failure mode being fixed.
"""
import json
import os
import statistics

# a source needs this many past observations before it has a baseline at all
MIN_OBSERVATIONS = 3
# how many past runs to look at (the DB keeps roughly one row per daily scan)
LOOKBACK_RUNS = 30
# "returned nothing" only counts as a regression above this usual yield —
# below it, an empty day is normal (archi-jobs legitimately has ~3 ads)
DEAD_BASELINE_MIN = 5
# a partial collapse needs a bigger, steadier source to be meaningful
COLLAPSE_BASELINE_MIN = 50
COLLAPSE_RATIO = 0.25

# counts driven by the run's own budget flags (--careers/--career-watch)
# rather than by the board, so they swing legitimately between a full cloud
# scan (300 pages) and a light local one (60) — no baseline comparison
BUDGET_DRIVEN = {"career-page"}
# user-maintained inputs: empty is the normal state, not a regression
USER_DRIVEN = {"manual", "watch", "generic"}


def _past_counts(con, lookback=LOOKBACK_RUNS):
    """{source: [count, ...]} over the last `lookback` recorded runs.

    Only integer entries count: a stored "BLOCKED: ..." string was already
    reported as a failure at the time and must not be read as a yield.
    """
    history = {}
    rows = con.execute(
        "SELECT source_stats FROM runs ORDER BY id DESC LIMIT ?",
        (lookback,)).fetchall()
    for row in rows:
        try:
            stats = json.loads(row["source_stats"] or "{}")
        except ValueError:
            continue
        for name, value in stats.items():
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            history.setdefault(name, []).append(value)
    return history


def baselines(con, lookback=LOOKBACK_RUNS):
    """{source: median yield when the source was working}.

    Zero runs are excluded from the median on purpose. A source measured
    against a window that already contains its own outage normalises the
    outage away: jobagent had produced ~440 jobs a run for months, but
    after eleven silent days its median over the last 30 runs was 0, so a
    plain median would have declared the dead source healthy. Taking the
    median of the runs where it *did* produce something keeps the baseline
    at what the source is actually worth, and a source that has never
    produced anything (manual/watch/generic) simply gets no baseline.
    """
    out = {}
    for name, counts in _past_counts(con, lookback).items():
        worked = [c for c in counts if c > 0]
        if len(worked) >= MIN_OBSERVATIONS:
            out[name] = statistics.median(worked)
    return out


def check(con, stats, sources, lookback=LOOKBACK_RUNS):
    """Compare this run's `stats` against history.

    `sources` is the allowlist of real scraper sources. It has to be passed
    in rather than inferred, because `stats` also carries bookkeeping
    counters (matched_to_companies, duplicates_marked) whose numbers swing
    freely and would otherwise be reported as broken boards.

    Returns {source: human-readable detail} for every source that collapsed.
    Sources that already reported a hard failure this run (their stat is a
    "BLOCKED: ..." / "ERROR: ..." string) are left alone — they are surfaced
    by the existing path and would only be reported twice.
    """
    base = baselines(con, lookback)
    verdicts = {}
    for name, value in stats.items():
        if name not in sources or name in BUDGET_DRIVEN or name in USER_DRIVEN:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            continue  # already-reported failure, or a non-count stat
        usual = base.get(name)
        if usual is None:
            continue  # brand-new source: nothing to compare against yet
        if value == 0 and usual >= DEAD_BASELINE_MIN:
            verdicts[name] = (
                f"0 found, normally ~{usual:.0f} — the source is probably "
                f"blocking us or changed its markup")
        elif (usual >= COLLAPSE_BASELINE_MIN
                and value < usual * COLLAPSE_RATIO):
            verdicts[name] = (
                f"only {value} found, normally ~{usual:.0f} — partial "
                f"failure (pagination or markup change)")
    return verdicts


def report(verdicts):
    """Print the verdicts; annotate the run when on GitHub Actions."""
    if not verdicts:
        return
    on_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    print(f"\n!! {len(verdicts)} source(s) degraded against their history:",
          flush=True)
    for name, detail in sorted(verdicts.items()):
        print(f"   {name}: {detail}", flush=True)
        if on_actions:
            # one-line annotation: newlines would end the workflow command
            flat = detail.replace("\n", " ")
            print(f"::warning title=Source degraded: {name}::{flat}",
                  flush=True)
