# -*- coding: utf-8 -*-
"""architalent.ch — Swiss architecture job board (Symfony/Turbo).

Listings are fully server-rendered: /en/jobs is page 1, /en/jobs/pN the
following pages (~10 <div class="job-card"> per page, newest first). Pages
past the end still return 200 but contain zero cards, so the scan stops on
the first empty page.

Cards carry title, organisation, location and a one-line summary but no
date. posted_at is joined from /en/sitemap-jobs.xml, which lists every job
detail URL with a <lastmod> stamp matching the "Posted here on ..." date of
the detail page (~560 entries back to 2025).

The board keeps an archive, so the scan stops after MAX_PAGES pages or once
a whole page is older than CUTOFF_DAYS.
"""
import sys

import datetime
import json
import re
import time
import urllib.parse

from net import http_get, strip_tags

BASE = "https://architalent.ch"
MAX_PAGES = 15     # ~10 jobs/page; 15 pages reach ~2 months back
CUTOFF_DAYS = 60   # drop postings older than this (archive goes back to 2025)

CARD = re.compile(r'<div class="job-card[^"]*">(.*?)<div class="job-more">',
                  re.S)
TITLE_LINK = re.compile(r'<h1><a href="([^"]+)"[^>]*>(.*?)</a></h1>', re.S)
COMPANY = re.compile(r'<div class="job-organisation">(.*?)</div>', re.S)
LOCATION = re.compile(r'<div class="job-location">(.*?)</div>', re.S)
SITEMAP_PAIR = re.compile(r"<loc>([^<]+)</loc>\s*<lastmod>([^<]+)</lastmod>")


def _sitemap_dates():
    """url -> 'YYYY-MM-DD' from the jobs sitemap. Best effort."""
    try:
        body, _ = http_get(BASE + "/en/sitemap-jobs.xml",
                           accept="application/xml")
        return {u: d[:10] for u, d in SITEMAP_PAIR.findall(body)}
    except Exception:
        return {}


def _location(chunk):
    """The location div holds place and region on separate lines."""
    m = LOCATION.search(chunk)
    if not m:
        return ""
    parts = [p.strip() for p in strip_tags(m.group(1)).splitlines()]
    return ", ".join(p for p in parts if p)


def _parse_cards(body, dates, seen):
    jobs = []
    for m in CARD.finditer(body):
        chunk = m.group(1)
        tm = TITLE_LINK.search(chunk)
        if not tm:
            continue
        url = urllib.parse.urljoin(BASE, tm.group(1)).split("#")[0]
        if url in seen:
            continue
        seen.add(url)
        title = strip_tags(tm.group(2))
        if not title:
            continue
        cm = COMPANY.search(chunk)
        jobs.append({
            "title": title,
            "company_name": strip_tags(cm.group(1)) if cm else "",
            "location": _location(chunk),
            "url": url,
            "posted_at": dates.get(url, ""),
        })
    return jobs


def scan_architalent():
    dates = _sitemap_dates()
    cutoff = (datetime.date.today()
              - datetime.timedelta(days=CUTOFF_DAYS)).isoformat()
    jobs, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        url = BASE + "/en/jobs" + (f"/p{page}" if page > 1 else "")
        body, _ = http_get(url)
        batch = _parse_cards(body, dates, seen)
        if not batch:
            break
        jobs.extend(batch)
        page_dates = [j["posted_at"] for j in batch if j["posted_at"]]
        # listing is newest first: once a whole page is past the cutoff, stop
        if page_dates and max(page_dates) < cutoff:
            break
        time.sleep(0.5)
    return [j for j in jobs if not j["posted_at"] or j["posted_at"] >= cutoff]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    jobs = scan_architalent()
    print(json.dumps({"count": len(jobs), "sample": jobs[:3]},
                     ensure_ascii=False, indent=1))
