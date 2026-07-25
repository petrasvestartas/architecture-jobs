# Architecture Jobs Switzerland

Daily-updated tracker of architecture (building design) jobs across
Switzerland, centered on Bern, design/Entwurf positions first.

**Website: https://petrasvestartas.github.io/architecture-jobs/**

## How it works

- `.github/workflows/scan.yml` (cloud): full scan 05:30 CH (incl. Playwright
  pass over script-only career pages, commits the DB) + hourly light scans
  07:00-19:00. Scrapes 17 job sources (SIA, swiss-architects, Hochparterre,
  architekturstellen, architectes.ch, archi-jobs, NZZ, ostjob, Job-Room/RAV,
  jobs.ch, jobup.ch, jobagent network, LinkedIn, Indeed, company career
  pages, watched sites, manual), geocodes, dedupes, filters out
  engineering / Baumanagement / internship / non-building "architect"
  (software, IT, business, landscape, …) ads, regenerates `public/`, and
  deploys.
- `jobtracker/notify.py` sends one **daily e-mail digest** early morning
  (the 05:30 full scan only, never the frequent scans) listing new
  building-architecture jobs within 40 km of the reference (or Entwurf within
  120 km), via Gmail SMTP (`GMAIL_APP_PASSWORD` repo secret). Only sent when
  there is something new; no per-scan phone pushes.
- `jobtracker/srchealth.py` compares every source against its own recent
  history and flags the ones that collapsed (0 found where it normally finds
  hundreds). A scraper that quietly returns nothing otherwise looks exactly
  like a board with no jobs today — that is how the jobagent outage stayed
  invisible for eleven days. Verdicts show up as a **DEGRADED** pill on the
  Job Websites tab, as `::warning::` annotations on the Actions run, and as a
  line in the daily digest.
- `.github/workflows/pages.yml` deploys `public/` to GitHub Pages when
  `public/**` changes. It shares the `pages-deploy` concurrency group with
  `scan.yml`'s deploy job, because two overlapping Pages deployments make
  GitHub reject one with *"400 … due to in progress deployment"*.
- `jobtracker/` is the full pipeline: `scraper.py` (sources + career
  watchdog + dedupe), `sources/` (one module per board), `geo.py` (offline
  Swiss geocoder, GeoNames), `db.py`, `export_site.py` (writes `public/`),
  `jobtracker.db` (SQLite, committed so job history and dedup state persist
  between runs).
- `public/index.html` is the self-contained website (no build step, no
  external dependencies); `public/data.js` is the generated data;
  `public/jobs-simple.html` is a no-script fallback list.

## The local scan (Ubuntu)

The local scan is not a redundant backup — measured against the cloud light
scan on 2026-07-25, it reaches more:

- **Playwright.** The cloud installs it only for the 05:30 full scan; the
  local light scan renders career pages that build their job list in
  JavaScript on every run (27 extra job links from 93 script-only pages in
  the test run) and picks up the Stadt/Kanton Bern portals, which are
  client-side apps and invisible without it.
- **python-jobspy.** Indeed CH + Google Jobs, which the light cloud scan
  skips entirely (it logs "pandas not installed, skipping").
- **A residential address.** swiss-architects.com served 139 jobs here
  against 127 from Actions, and it has been blocked from datacenter ranges
  before.

What it does *not* reliably fix is the jobagent network
(architekten-jobs.ch, ~440 jobs a scan, roughly a third of the catalogue).
That has returned 0 from the cloud every day since 2026-07-15. From a home
connection it served once and then started refusing — DataDome flags the
address after a burst, and a headless browser is refused too. The local run
is a different, unflagged address making only 8 requests, so it is the best
remaining shot, but treat it as worth trying rather than solved. Either way
the source now reports **blocked** instead of pretending the board is empty.

### Two ways to run it locally — pick one, not both

**A. Self-hosted Actions runner (preferred).** GitHub triggers the scan, but
it executes here. One workflow definition, logs in the Actions UI, manual
`workflow_dispatch` from the web, and `.github/workflows/scan-local.yml`
stays in version control like everything else.

    jobtracker/systemd/install-runner.sh          # register + enable
    jobtracker/systemd/install-runner.sh --status # is it online?
    gh workflow run 'Job scan (local runner)'     # scan now

Runs on the half hour, interleaved with the cloud scans on the hour. When
the machine is off, runs queue instead of failing, and `cancel-in-progress`
discards stale ones so a week away costs one run rather than a hundred.
Safe here only because the repo is **private**: a self-hosted runner will
execute any workflow in the repo, which on a public repo means anyone's
pull request runs code on your laptop.

**B. systemd timers.** No GitHub involvement — the machine schedules itself
and pushes the results. Better if you would rather not register a runner, or
want scans to keep working while GitHub is down.

    jobtracker/systemd/install.sh

Running both would double-scan and fight over the database. If you install
the runner, leave the timers uninstalled (`install.sh --remove` if they are
already there).

`jobtracker/run_local.sh` — used by option B — is the Linux/macOS entry
point. Unlike the old Windows setup it works **in place**: the clone is the
working copy, the scan
writes `public/` and `jobtracker/jobtracker.db` directly, and
`publish_web.py` commits and pushes, which deploys Pages. It bootstraps its
own `.venv` on first run with Playwright (career pages that build their job
list in JavaScript) and python-jobspy (Indeed CH + Google Jobs) — neither of
which the light cloud scan installs.

    cd jobtracker
    ./run_local.sh            # light scan, ~5 min
    ./run_local.sh --full     # deep pass: career discovery + JS render
    systemd/install.sh        # every 30 min 07:00-21:00, full at 06:30

`systemd/install.sh --status` shows the timers, `--remove` uninstalls them.
They are *user* timers, so `sudo loginctl enable-linger $USER` is what keeps
them running across reboots with nobody logged in.

The Windows Task Scheduler path (`run_light.cmd`, Google Drive layout) still
works — `publish_web.py` detects which layout it is in — but Ubuntu is the
primary local runner now.

The website's Job Websites tab shows per-source status, with a "search
manually" link for anything blocked or degraded.

## Manual run

Actions tab → "Daily job scan" → Run workflow. Locally:
`cd jobtracker && python scraper.py` (see `jobtracker/README.md`).
