# -*- coding: utf-8 -*-
"""Hochparterre Stellenplattform scraper.

Source: https://www.hochparterre.ch/stellenplattform
(the Swiss architecture/planning/design magazine's job platform).

How it works
------------
The listing page is an Alpine.js app. Jobs are "blog posts" with
format uid 27 ("Stellenausschreibung") / publication uid 50
("Stellenplattform"). The page loads them by POSTing a JSON filter to
the TYPO3 search endpoint:

    POST https://www.hochparterre.ch/?type=80324
    body: {"format":["27"],"publication":[],"sort":[],"query":[],
           "tag":[],"from":"","to":"","limit":20,"offset":0}

Response: {"found": <int>, "posts": [...], "facets": {...}}
Each post has: title, subtitle (= office/company name), teasertext,
postdate ("DD.MM.YYYY"), deeplinkGet (absolute stable detail URL like
https://www.hochparterre.ch/stellenplattform/detail/<slug>), geoData
(lat/lon, often 0), tags. Pagination is offset-based (site pageSize=20).
Only currently active listings are indexed (expired ones disappear).
"""
import sys

import json
import re
import time

from net import http_get, get_json, post_json, strip_tags, extract_workload

BASE = "https://www.hochparterre.ch"
SEARCH_URL = BASE + "/?type=80324"
LISTING_URL = BASE + "/stellenplattform"

PAGE_SIZE = 20      # same as the site's own pageSize
MAX_PAGES = 8       # hard cap -> at most 8 requests / 160 jobs per run

# Common Swiss city names that offices append to their name
# ("BGP Architekten Zürich") or that appear in teaser texts.
_CITIES = [
    "Zürich", "Basel", "Bern", "Genf", "Genève", "Lausanne", "Luzern",
    "St. Gallen", "St.Gallen", "Winterthur", "Zug", "Chur", "Biel",
    "Aarau", "Baden", "Thun", "Solothurn", "Schaffhausen", "Fribourg",
    "Freiburg", "Neuchâtel", "Sion", "Lugano", "Bellinzona", "Rapperswil",
    "Rapperswil-Jona", "Uster", "Wil", "Frauenfeld", "Olten", "Wetzikon",
    "Dietikon", "Dübendorf", "Kreuzlingen", "Herisau", "Glarus", "Sarnen",
    "Stans", "Altdorf", "Appenzell", "Delémont", "Liestal", "Schwyz",
    "Zollikon", "Küsnacht", "Wädenswil", "Horgen", "Brugg", "Vaduz",
]

# teaser-text patterns that reliably announce the work location
_LOC_PATTERNS = [
    re.compile(r"[Aa]m Standort ([A-ZÄÖÜ][\w.\-]+(?:[ -][A-ZÄÖÜ][\w.\-]+)?)"),
    re.compile(r"Standort(?:e|es)? (?:in )?([A-ZÄÖÜ][\w.\-]+(?:[ -][A-ZÄÖÜ][\w.\-]+)?)"),
    re.compile(r"[Mm]it Sitz in ([A-ZÄÖÜ][\w.\-]+(?:[ -][A-ZÄÖÜ][\w.\-]+)?)"),
    re.compile(r"[Bb]üro in ([A-ZÄÖÜ][\w.\-]+(?:[ -][A-ZÄÖÜ][\w.\-]+)?)"),
]


def _guess_location(teaser, company):
    """Best-effort location: teaser patterns first, then a known city
    appearing in teaser or trailing the company name."""
    for pat in _LOC_PATTERNS:
        m = pat.search(teaser or "")
        if m:
            loc = m.group(1).strip(" .,;:")
            # avoid capturing sentence starts like "Standort Die ..."
            if loc and loc not in ("Die", "Der", "Das", "Sie", "Wir", "Ihre"):
                return loc
    for city in _CITIES:
        if company and (company.endswith(" " + city) or company.endswith(" in " + city)):
            return city
    for city in _CITIES:
        if teaser and re.search(r"\b" + re.escape(city) + r"\b", teaser):
            return city
    return ""


def _date_iso(d):
    """'09.07.2026' -> '2026-07-09'"""
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})", (d or "").strip())
    if not m:
        return ""
    return "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1)))


def scan_hochparterre():
    jobs = []
    seen = set()
    headers = {"Referer": LISTING_URL, "Origin": BASE}
    offset = 0
    found = None
    for page in range(MAX_PAGES):
        filt = {
            "format": ["27"],        # 27 = Stellenausschreibung (job ad)
            "publication": [],
            "sort": [],
            "query": [],
            "tag": [],
            "from": "",
            "to": "",
            "limit": PAGE_SIZE,
            "offset": offset,
        }
        try:
            data = post_json(SEARCH_URL, filt, tries=2, timeout=30, headers=headers)
        except Exception as e:
            print("hochparterre: request failed at offset %d: %s" % (offset, e), file=sys.stderr)
            break
        if not isinstance(data, dict):
            break
        if found is None:
            found = data.get("found") or 0
        posts = data.get("posts") or []
        if isinstance(posts, dict):          # defensive: object instead of array
            posts = list(posts.values())
        if not posts:
            break
        for p in posts:
            if not isinstance(p, dict):
                continue
            # guard: only actual job ads
            fmts = {f.get("uid") for f in (p.get("format") or []) if isinstance(f, dict)}
            if p.get("teaserType") != "job" and 27 not in fmts:
                continue
            url = (p.get("deeplinkGet") or "").strip()
            title = strip_tags(p.get("title") or "")
            if not url or not title:
                continue
            if not url.startswith("http"):
                url = BASE + ("" if url.startswith("/") else "/") + url
            url = url.split("#")[0]
            if url in seen:
                continue
            seen.add(url)
            company = strip_tags(p.get("subtitle") or "")
            teaser = strip_tags(p.get("teasertext") or "")
            job = {
                "title": title,
                "company_name": company,
                "location": _guess_location(teaser, company),
                "url": url,
                "posted_at": _date_iso(p.get("postdate")),
            }
            wl = extract_workload(title) or extract_workload(teaser)
            if wl:
                job["workload"] = wl
            geo = p.get("geoData") or {}
            try:
                lat = float(geo.get("latitude") or 0)
                lon = float(geo.get("longitude") or 0)
                if lat and lon:
                    job["lat"] = lat
                    job["lon"] = lon
            except (TypeError, ValueError):
                pass
            jobs.append(job)
        offset += PAGE_SIZE
        if len(posts) < PAGE_SIZE:
            break
        if found is not None and offset >= found:
            break
        time.sleep(0.8)
    return jobs


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    jobs = scan_hochparterre()
    print(json.dumps({"count": len(jobs), "sample": jobs[:3]}, ensure_ascii=False, indent=1))
