"""Scraper for the jobagent.ch network via architekten-jobs.ch.

architekten-jobs.ch is the architecture vertical of the jobchannel/jobagent
network (same backend as jobagent.ch and emploi-batiment.ch) and lists all
Swiss architecture jobs (~1000). Listing pages are server-rendered HTML at
https://architekten-jobs.ch/search?page=N (~105 items/page).

Anti-bot note (DataDome): the first response sets a `datadome` cookie; if
that cookie is sent back, every following request gets 403, so the site's
cookies are dropped before each request. No other bypass is used.

Since 2026-07-15 that is no longer enough: the cloud scan has recorded 0
jobs here every single day since, where it used to find ~440. What was
measured on 2026-07-25 from a residential connection: the listing page
served once (111 items), then 403 for every request after a short burst,
and still 403 an hour and a half later — DataDome flagging the address, not
a header we can fix. Headless Chromium is refused the same way, and so are
the sibling domains (jobagent.ch, emploi-batiment.ch) and the sitemap.

So this source is currently unreachable from both the cloud and one home
connection, and no bypass is attempted. A local scan is still the better
bet — it is a different address, unflagged, making 8 requests a run — but
it is worth trying, not a guaranteed fix. Meanwhile scan_jobagent raises
instead of returning an empty list, so the run records BLOCKED and the
website offers a manual-search link rather than silently claiming the
board is empty.

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
    first_error = None
    for page in range(1, PAGE_CAP + 1):
        if page > 1:
            time.sleep(1.0)
        try:
            body, used_retry = _fetch_page(page, retries_left)
            if used_retry:
                retries_left -= 1
        except Exception as e:
            # blocked or down; keep whatever we already have, but remember
            # why we stopped — a swallowed 403 here is what let this source
            # report "0 jobs, all good" for eleven days straight
            first_error = first_error or e
            break

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
    if not jobs and first_error is not None:
        # nothing at all AND a failed fetch: report it so the run records
        # "BLOCKED: ..." and the site offers a manual-search link, instead
        # of an empty list that is indistinguishable from a quiet board
        raise first_error
    return jobs


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    jobs = scan_jobagent()
    print(json.dumps({"count": len(jobs), "sample": jobs[:3]},
                     ensure_ascii=False, indent=1))
