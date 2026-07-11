"""swiss-architects.com (World-Architects) job board scraper.

Board: https://www.swiss-architects.com/de/stellenanzeigen
The site 403s naive fetchers; fetching the homepage first (shared cookie jar)
and sending browser-like navigation headers with a same-origin Referer works.

Listing layout (server-rendered HTML):
  - "Featured Jobs" ticker on page 1: <li class="well row"> blocks with
    <dt><a href=ABS_URL>title</a></dt>, <dd>company</dd>, <dd>location</dd>
    (no posted date).
  - Regular list: <li class="row"> blocks with <dt><a href=REL_URL>title</a>,
    <dd>company</dd>, <address>location</address>,
    <time datetime="DD.MM.YY, HH:MM">.
  - Pagination: /de/stellenanzeigen?page=N (hard cap 5 pages here).
"""
import sys

import html
import json
import random
import re
import time

from net import http_get, strip_tags, extract_workload

BASE = "https://swiss-architects.com"
LIST_URL = BASE + "/de/stellenanzeigen"
MAX_PAGES = 5

NAV_HEADERS = {
    "Referer": "https://www.swiss-architects.com/de",
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Upgrade-Insecure-Requests": "1",
}

RE_REGULAR = re.compile(r'<li class="row">(.*?)</li>', re.DOTALL)
RE_FEATURED = re.compile(r'<li class="well row">(.*?)</li>', re.DOTALL)
RE_TITLE = re.compile(r'<dt>\s*<a href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
RE_DD = re.compile(r"<dd>(.*?)</dd>", re.DOTALL)
RE_ADDRESS = re.compile(r"<address[^>]*>(.*?)</address>", re.DOTALL)
RE_TIME = re.compile(r'<time[^>]*datetime="([^"]*)"')
RE_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{2})")


def _clean(s):
    return re.sub(r"\s+", " ", strip_tags(s)).strip()


def _abs_url(href):
    """Absolute, canonical (https, no-www host), no fragment/session junk."""
    u = html.unescape(href or "").strip()
    if u.startswith("/"):
        u = BASE + u
    u = re.sub(r"^http://", "https://", u)
    u = u.replace("https://www.swiss-architects.com", BASE)
    return u.split("#")[0]


def _parse_date(dt_attr):
    """'11.07.26, 17:13' -> '2026-07-11'."""
    m = RE_DATE.search(dt_attr or "")
    if not m:
        return ""
    d, mo, y = m.groups()
    return "20{}-{}-{}".format(y, mo, d)


def _job(url, title, company, location, posted_at):
    j = {
        "title": title,
        "company_name": company,
        "location": location,
        "url": url,
        "posted_at": posted_at,
    }
    w = extract_workload(title)
    if w:
        j["workload"] = w
    return j


def _parse_regular(body):
    jobs = []
    for block in RE_REGULAR.findall(body):
        m = RE_TITLE.search(block)
        if not m:
            continue
        url = _abs_url(m.group(1))
        title = _clean(m.group(2))
        if not title or "/stellenanzeigen/" not in url:
            continue
        dds = RE_DD.findall(block)
        company = _clean(dds[0]) if dds else ""
        ma = RE_ADDRESS.search(block)
        location = _clean(ma.group(1)) if ma else ""
        mt = RE_TIME.search(block)
        posted = _parse_date(mt.group(1)) if mt else ""
        jobs.append(_job(url, title, company, location, posted))
    return jobs


def _parse_featured(body):
    jobs = []
    for block in RE_FEATURED.findall(body):
        m = RE_TITLE.search(block)
        if not m:
            continue
        url = _abs_url(m.group(1))
        title = _clean(m.group(2))
        if not title or "/stellenanzeigen/" not in url:
            continue
        # dd #1 = office/company (often a profile link), dd #2 = location
        dds = [_clean(d) for d in RE_DD.findall(block)]
        company = dds[0] if len(dds) > 0 else ""
        location = dds[1] if len(dds) > 1 else ""
        jobs.append(_job(url, title, company, location, ""))
    return jobs


def scan_swissarchitects():
    # Warm up the shared cookie jar on the homepage first — the jobs pages
    # return 403 without cookies + browser navigation headers.
    http_get("https://www.swiss-architects.com/")
    time.sleep(random.uniform(0.5, 1.5))

    jobs, seen = [], set()
    featured = []
    for page in range(1, MAX_PAGES + 1):
        url = LIST_URL if page == 1 else "{}?page={}".format(LIST_URL, page)
        body, _final = http_get(url, headers=NAV_HEADERS)
        page_jobs = _parse_regular(body)
        if page == 1:
            featured = _parse_featured(body)
        if not page_jobs:
            break
        for j in page_jobs:
            if j["url"] not in seen:
                seen.add(j["url"])
                jobs.append(j)
        # stop when there is no link to the next page
        if 'rel="next"' not in body:
            break
        time.sleep(random.uniform(0.5, 1.5))
    # Featured/top jobs have no posted date; add only the ones not already
    # present in the regular list.
    for j in featured:
        if j["url"] not in seen:
            seen.add(j["url"])
            jobs.append(j)
    return jobs


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    jobs = scan_swissarchitects()
    print(json.dumps({"count": len(jobs), "sample": jobs[:3]},
                     ensure_ascii=False, indent=1))
