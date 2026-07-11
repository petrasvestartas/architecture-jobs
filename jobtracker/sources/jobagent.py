"""Scraper for the jobagent.ch network via architekten-jobs.ch.

architekten-jobs.ch is the architecture vertical of the jobchannel/jobagent
network (same backend as jobagent.ch and emploi-batiment.ch) and lists all
Swiss architecture jobs (~1000). Listing pages are server-rendered HTML at
https://architekten-jobs.ch/search?page=N (~105 items/page).

Anti-bot note (DataDome): the first response sets a `datadome` cookie; if
that cookie is sent back, every following request gets 403. The fix is to
drop the site's cookies before each request so every page fetch looks like
a fresh visitor. No other bypass is needed or used.

Fields: no posted date is exposed on the listing pages -> posted_at = "".
Canton comes from the data-gtm-jp attribute on each result item.
"""

import sys

import json
import re
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import net
from net import http_get, strip_tags, extract_workload

BASE = "https://architekten-jobs.ch/"
SEARCH = "https://architekten-jobs.ch/search"
PAGE_CAP = 8          # hard page cap (~105 items/page, site has ~10 pages)
RETRY_BUDGET = 2      # extra attempts allowed across the whole run

ITEM_RE = re.compile(r'<li class="item"(.*?)</li>', re.S)
CANTON_RE = re.compile(r'data-gtm-jp="([A-Z]{2})"')
URL_RE = re.compile(r'<a href="([^"]+)" class="title"')
TITLE_RE = re.compile(r'<span class="jobtitle">(.*?)</span>', re.S)
LOC_RE = re.compile(r'<span class="location">(.*?)</span>', re.S)
COMPANY_RE = re.compile(r'<span class="company">(.*?)</span>', re.S)


def _drop_site_cookies():
    """Remove this network's cookies (incl. DataDome) from the shared jar."""
    try:
        for c in list(net._cookies):
            if "architekten-jobs.ch" in c.domain or "jobagent.ch" in c.domain:
                try:
                    net._cookies.clear(c.domain, c.path, c.name)
                except KeyError:
                    pass
    except Exception:
        pass


def _clean_url(u):
    """Absolute URL, no query/fragment (job URLs are /job/<slug>/<id>)."""
    u = urljoin(BASE, u)
    parts = urlsplit(u)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _fetch_page(page, retries_left):
    url = SEARCH if page == 1 else f"{SEARCH}?page={page}"
    attempts = 1 + (1 if retries_left > 0 else 0)
    used_retry = False
    for a in range(attempts):
        _drop_site_cookies()
        try:
            body, _ = http_get(url, tries=1,
                               headers={"Referer": SEARCH,
                                        "Upgrade-Insecure-Requests": "1"})
            return body, used_retry
        except Exception:
            if a + 1 >= attempts:
                raise
            used_retry = True
            time.sleep(2.0)
    return "", used_retry  # unreachable


def scan_jobagent():
    jobs = []
    seen = set()
    retries_left = RETRY_BUDGET
    for page in range(1, PAGE_CAP + 1):
        if page > 1:
            time.sleep(1.0)
        try:
            body, used_retry = _fetch_page(page, retries_left)
            if used_retry:
                retries_left -= 1
        except Exception:
            break  # blocked or down; keep whatever we already have

        new = 0
        for m in ITEM_RE.finditer(body):
            block = m.group(1)
            mu = URL_RE.search(block)
            mt = TITLE_RE.search(block)
            if not mu or not mt:
                continue
            url = _clean_url(mu.group(1))
            if url in seen:
                continue
            seen.add(url)
            title = strip_tags(mt.group(1))
            if not title:
                continue
            ml = LOC_RE.search(block)
            location = strip_tags(ml.group(1)) if ml else ""
            location = re.sub(r"^CH-", "", location).strip()
            mc = COMPANY_RE.search(block)
            company = strip_tags(mc.group(1)) if mc else ""
            mk = CANTON_RE.search(block)
            job = {
                "title": title,
                "company_name": company,
                "location": location,
                "url": url,
                "posted_at": "",
            }
            wl = extract_workload(title) or extract_workload(location)
            if wl:
                job["workload"] = wl
            if mk:
                job["canton"] = mk.group(1)
            jobs.append(job)
            new += 1
        if new == 0:
            break  # past the last page (or only repeats)
    return jobs


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    jobs = scan_jobagent()
    print(json.dumps({"count": len(jobs), "sample": jobs[:3]},
                     ensure_ascii=False, indent=1))
