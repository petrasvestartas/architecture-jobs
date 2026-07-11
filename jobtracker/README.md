# Swiss Architecture Job Tracker

Finds architecture (building design) jobs across **all major Swiss job sources**,
checks them **daily and automatically**, and shows everything on a fast static
website that opens straight from your Google Drive folder — on Windows and Mac.

**Start here:** the user guide (how to open the website + things to know) is
one folder up, in `G:\My Drive\job\README.md`. This file covers the technical
side.

## Open the website

- **Windows:** double-click `G:\My Drive\job\site\index.html`
- **Mac:** open `My Drive/job/site/index.html` inside your synced Google Drive
  folder (usually `~/Library/CloudStorage/GoogleDrive-…/My Drive/job/site/`)

It works fully offline and needs no server. **Note:** it must be opened from the
*synced folder* — the drive.google.com web preview does not run scripts.

Default location is **Bern**; type any Swiss city or postal code into the
location picker to re-center every distance on the page instantly (no re-scan
needed). Your statuses, notes and manual additions are saved in the browser —
use the **Backup** tab to move them between machines.

## What runs daily (07:30)

`scraper.py` scans every source, geocodes the postings, removes cross-board
duplicates, and regenerates `site/data.js`:

- Specialist architecture boards: SIA Jobs, archi-jobs.ch, Hochparterre,
  swiss-architects.com, architekturstellen.ch, architectes.ch
- General boards filtered to architecture: jobs.ch, jobup.ch, NZZ Jobs,
  jobagent.ch network, LinkedIn, Indeed CH + Google Jobs
- **Company websites:** career pages of the SIA member firms are fetched and
  new job ads extracted — the firms nearest your reference location every day,
  the rest in rotation (`--career-watch 300`); 150 more firms/day are probed
  to discover their career page in the first place (`--careers 150`)
- Your watched sites (`watch_sites.json`) and manual jobs (`manual_jobs.json`)

Sources that block robots are shown on the site's **Sources** tab with a
"search manually" link — check those by hand now and then.

## Adding things yourself

- **A job you found:** website → *Add job* tab (instant). To make it permanent
  in the database, click *Download manual_jobs.json* and save the file into
  this `jobtracker/` folder.
- **A site you want scanned daily:** website → *Sources* tab → *Watched sites*
  → add name + URL → *Download watch_sites.json* → save into this folder.
- **Change the default location** for the site: just use the picker (it
  persists). To also re-prioritize which company career pages get checked
  daily, edit `reference_place` in `config.json`.

## Scheduling

- **Windows:** Task Scheduler job `SwissArchJobScan` runs `run_daily.cmd` at
  07:30 (catches up after boot if the PC was off).
- **Mac:** edit the path inside `com.jobtracker.daily.plist` (your Google
  Drive mount), then:
  `cp com.jobtracker.daily.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/com.jobtracker.daily.plist`
- Enable the schedule on **one machine at a time** — the SQLite database lives
  in Google Drive and must not be written by two machines simultaneously.

## Commands

```
python scraper.py                          # scan all boards + export site
python scraper.py --source sia            # one source only
python scraper.py --careers 150           # + discover career pages
python scraper.py --career-watch 300      # + extract jobs from career pages
python export_site.py                     # re-export site/data.js only
python geo.py                             # geocoding self-test + backfill
```

Legacy local web app (optional): `run_web.cmd` serves the old Flask UI at
http://127.0.0.1:5050 — the static site has replaced it.

## Files

`scraper.py` sources + career watchdog + dedup · `db.py` SQLite schema ·
`geo.py` offline Swiss geocoder (GeoNames, `data/CH.txt`) · `export_site.py`
writes `../site/data.js` · `config.json` reference location · `../site/`
the website · `jobtracker.db` the database · `scraper.log` daily log

**Want it to run even when your computers are off?** Ask Claude to set up the
free GitHub Pages + Actions variant (cloud scraping, same site on a URL).
