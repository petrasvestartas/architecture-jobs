# Architecture Jobs Switzerland

Daily-updated tracker of architecture (building design) jobs across
Switzerland, centered on Bern, design/Entwurf positions first.

**Website: https://petrasvestartas.github.io/architecture-jobs/**

## How it works

- `.github/workflows/scan.yml` runs **every morning (~05:30 CH)** on GitHub
  Actions: scrapes 17 job sources (SIA, swiss-architects, Hochparterre,
  architekturstellen, architectes.ch, archi-jobs, NZZ, ostjob, Job-Room/RAV,
  jobs.ch, jobup.ch, jobagent network, LinkedIn, Indeed, company career
  pages, watched sites, manual), geocodes, dedupes, filters out
  engineering / Baumanagement / internship / IT-architect ads, writes the
  updated SQLite database and regenerates `public/`, commits, and deploys.
- `.github/workflows/pages.yml` deploys `public/` to GitHub Pages **on every
  push** to main.
- `jobtracker/` is the full pipeline: `scraper.py` (sources + career
  watchdog + dedupe), `sources/` (one module per board), `geo.py` (offline
  Swiss geocoder, GeoNames), `db.py`, `export_site.py` (writes `public/`),
  `jobtracker.db` (SQLite, committed so job history and dedup state persist
  between runs).
- `public/index.html` is the self-contained website (no build step, no
  external dependencies); `public/data.js` is the generated data;
  `public/jobs-simple.html` is a no-script fallback list.

A local copy also runs on the owner's Windows machine (Task Scheduler,
07:30) and pushes via `jobtracker/publish_web.py`; generated files are
last-writer-wins.

Note: sources behind aggressive bot protection (swiss-architects.com,
architekten-jobs.ch/DataDome) may be blocked from datacenter IPs — the local
scan covers those; the website's Job Websites tab shows per-source status.

## Manual run

Actions tab → "Daily job scan" → Run workflow. Locally:
`cd jobtracker && python scraper.py` (see `jobtracker/README.md`).
