# -*- coding: utf-8 -*-
"""Export the tracker DB to ../site/data.js as `window.DATA = {...}` so the
static site works when opened directly via file:// (fetch() would not).

    python export_site.py    # standalone re-export
"""
import datetime
import json
import os
import re

import db
import geo

HERE = os.path.dirname(os.path.abspath(__file__))
# on GitHub Actions the site is written to the repo root instead of ../site
SITE_DIR = os.environ.get("JOBTRACKER_SITE_DIR") or \
    os.path.normpath(os.path.join(HERE, "..", "site"))

# curated "find offices / search yourself" links shown on the Sources tab
DIRECTORIES = [
    {"label": "SIA member register (all Swiss architecture firms)",
     "url": "https://agm.sia.ch/directory/members"},
    {"label": "Swiss-Architects office directory",
     "url": "https://www.swiss-architects.com/de"},
    {"label": "BSA/FAS members (Bund Schweizer Architekten)",
     "url": "https://www.bsa-fas.ch/de/mitglieder"},
    {"label": "Kanton Bern - offene Stellen (public sector)",
     "url": "https://www.jobs.sites.be.ch/de/start/jobs.html"},
    {"label": "Stadt Bern - offene Stellen",
     "url": "https://www.bern.ch/themen/arbeiten-fuer-die-stadt-bern/offene-stellen"},
    {"label": "berner-stellen.ch (all jobs in Kanton Bern)",
     "url": "https://berner-stellen.ch/"},
    {"label": "Hochparterre Stellenmarkt",
     "url": "https://www.hochparterre.ch/stellenmarkt"},
]


def _source_rows(con, source_meta):
    """Merge SOURCE_META with the latest run's per-source outcome."""
    last = con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    stats = {}
    finished = None
    if last:
        finished = last["finished"]
        try:
            stats = json.loads(last["source_stats"] or "{}")
        except ValueError:
            stats = {}
    counts = {r["source"]: r["c"] for r in con.execute(
        "SELECT source, COUNT(*) c FROM jobs WHERE active=1 GROUP BY source")}
    rows = []
    for name, meta in source_meta.items():
        raw = stats.get(name)
        if isinstance(raw, int):
            status, detail = "ok", f"{raw} found"
        elif isinstance(raw, str) and raw.startswith("BLOCKED"):
            status, detail = "blocked", raw
        elif isinstance(raw, str):
            status, detail = "failed", raw
        else:
            status, detail = "idle", ""
        rows.append({
            "name": name, "label": meta["label"], "home": meta["home"],
            "search_url": meta["search_url"], "status": status,
            "detail": detail, "active_jobs": counts.get(name, 0),
            "last_run": finished,
        })
    return rows


def export(con=None):
    import scraper as _s  # for SOURCE_META/CONFIG (avoid circular import at module level)
    own = con is None
    if own:
        db.init()
        con = db.connect()

    hash_by_company_id = {r["id"]: r["hash"] for r in
                          con.execute("SELECT id, hash FROM companies")}

    jobs = []
    alts = {}
    for r in con.execute("SELECT * FROM jobs WHERE active=1 AND dup_of IS NOT NULL"):
        alts.setdefault(r["dup_of"], []).append(
            {"source": r["source"], "url": r["url"]})
    for r in con.execute("""SELECT * FROM jobs WHERE active=1 AND dup_of IS NULL
                            ORDER BY first_seen DESC, posted_at DESC"""):
        jobs.append({
            "url": r["url"], "source": r["source"], "title": r["title"],
            "company": r["company_name"] or "",
            "company_hash": hash_by_company_id.get(r["company_id"]),
            "location": r["location"] or "", "lat": r["lat"], "lon": r["lon"],
            "canton": r["canton"] or "", "workload": r["workload"] or "",
            "posted": (r["posted_at"] or "")[:10],
            "first_seen": (r["first_seen"] or "")[:10],
            "alt": alts.get(r["id"], []),
        })

    companies = []
    for r in con.execute("SELECT * FROM companies WHERE status='ok' ORDER BY name"):
        companies.append({
            "hash": r["hash"], "name": r["name"], "city": r["city"] or "",
            "plz": str(r["plz"]).split(".")[0] if r["plz"] not in (None, "") else "",
            "canton": r["canton"] or "",
            "lat": r["lat"], "lon": r["lon"],
            "website": r["website"] or "", "career_url": r["career_url"] or "",
            "email": r["email"] or "", "phone": r["phone"] or "",
            "specialisations": r["specialisations"] or "",
            "rating": r["rating"], "sia_url": r["sia_url"] or "",
            "applied_db": r["applied"] or 0, "applied_at_db": r["applied_at"],
            "notes_db": r["notes"] or "",
        })

    payload = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "reference_default": _s.CONFIG.get("reference_place", "Bern"),
        "jobs": jobs,
        "companies": companies,
        "places": geo.places(),
        "sources": _source_rows(con, _s.SOURCE_META),
        "directories": DIRECTORIES,
    }

    os.makedirs(SITE_DIR, exist_ok=True)
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    out = os.path.join(SITE_DIR, "data.js")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("window.DATA = " + blob + ";\n")
    os.replace(tmp, out)  # atomic: Google Drive never syncs a half-written file
    size = os.path.getsize(out) // 1024
    print(f"exported {len(jobs)} jobs, {len(companies)} companies -> {out} ({size} KB)",
          flush=True)

    # zero-JS fallback list: renders even inside the Google Drive app preview
    # (which blocks scripts). Bern-sorted, Entwurf/design roles pinned first.
    _write_simple(payload)

    # single-file build for phones: index.html with the data inlined, so one
    # downloaded file works in Chrome on Android/iOS without a sibling data.js
    idx_path = os.path.join(SITE_DIR, "index.html")
    if os.path.exists(idx_path):
        idx = open(idx_path, encoding="utf-8").read()
        tag = '<script src="data.js" charset="utf-8"></script>'
        if tag in idx:
            mob = idx.replace(
                tag, "<script>window.DATA = " + blob.replace("</", "<\\/") + ";</script>", 1)
            mout = os.path.join(SITE_DIR, "jobtracker-phone.html")
            with open(mout + ".tmp", "w", encoding="utf-8") as f:
                f.write(mob)
            os.replace(mout + ".tmp", mout)
            print(f"phone build -> {mout} ({os.path.getsize(mout) // 1024} KB)", flush=True)
        else:
            print("phone build skipped: data.js script tag not found in index.html",
                  flush=True)
    if own:
        con.close()


ENTWURF = re.compile(r"entwurf|design\s*architekt|design\s*architect|conception", re.I)


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _write_simple(payload):
    """site/jobs-simple.html — plain HTML, no JavaScript at all."""
    ref = geo.lookup_place(payload.get("reference_default") or "Bern") or (46.9481, 7.4474, "BE")

    def dist(j):
        if j["lat"] is None or j["lon"] is None:
            return None
        return geo.haversine_km(ref[0], ref[1], j["lat"], j["lon"])

    jobs = sorted(payload["jobs"], key=lambda j: (
        0 if ENTWURF.search(j["title"] or "") else 1,
        dist(j) if dist(j) is not None else 9e9))
    rows = []
    for j in jobs:
        d = dist(j)
        km = f"{d:.0f} km" if d is not None else "–"
        rows.append(
            "<tr><td><a href=\"" + _esc(j["url"]) + "\">" + _esc(j["title"]) + "</a>"
            + ("<br><small>" + _esc(j["company"]) + "</small>" if j["company"] else "")
            + "</td><td>" + _esc(j["location"]) + "<br><small>" + km + "</small></td><td>"
            + _esc(j["workload"] or "–") + "</td><td>" + _esc(j["posted"] or j["first_seen"])
            + "</td></tr>")
    html_doc = (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<meta name=\"robots\" content=\"noindex, nofollow, noarchive\">"
        "<script>if(/\\.github\\.io$/.test(location.hostname)){try{if("
        "localStorage.getItem('jobtracker_gate')!=='1')location.replace("
        "'index.html');}catch(e){location.replace('index.html');}}</script>"
        "<title>Architecture Jobs Switzerland (simple list)</title><style>"
        "body{font-family:'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;font-size:14px;"
        "margin:12px;color:#1a1a1a}h1{font-size:16px;text-transform:uppercase;"
        "letter-spacing:.06em}h1 span{color:#e2001a}table{border-collapse:collapse;width:100%}"
        "td,th{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}"
        "th{font-size:11px;text-transform:uppercase;letter-spacing:.05em}"
        "a{color:#1a1a1a}small{color:#777}</style></head><body>"
        "<h1><span>&#9632;</span> Architecture Jobs Switzerland</h1>"
        "<p><small>Simple no-script list &middot; sorted from "
        + _esc(payload.get("reference_default") or "Bern")
        + ", Entwurf first &middot; generated " + _esc(payload["generated"])
        + " &middot; full app: site/index.html (or jobtracker-phone.html in Chrome)</small></p>"
        "<table><tr><th>Position</th><th>Location</th><th>%</th><th>Posted</th></tr>"
        + "".join(rows) + "</table></body></html>")
    out = os.path.join(SITE_DIR, "jobs-simple.html")
    with open(out + ".tmp", "w", encoding="utf-8") as f:
        f.write(html_doc)
    os.replace(out + ".tmp", out)
    print(f"simple no-JS list -> {out} ({os.path.getsize(out) // 1024} KB)", flush=True)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    export()
