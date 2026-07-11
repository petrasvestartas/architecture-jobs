# -*- coding: utf-8 -*-
"""architekturstellen.ch — Swiss architecture job board (Hochschule Luzern).

WordPress, custom post type "job" (NOT exposed via WP REST — /wp/v2/posts is
empty and /wp/v2/types lists no job type). Listings are server-rendered on the
homepage (30 newest) and paginated via an admin-ajax endpoint used by the
"mehr Stelleninserate" button (theme script f4.paging.js):

    POST https://www.architekturstellen.ch/wp-admin/admin-ajax.php
         action=get_jobs
         post_query=<url-DECODED value of the data-paging-query attribute
                     on the div.paging element of the homepage>
         paged=<N>            # separate form field; the paged value INSIDE
                              # the serialized post_query is ignored by the
                              # server, and feeding back the returned
                              # post_query never advances the page either.
         lang=

    -> JSON {html_content, html_paging, post_query, has_more_pages, ...}
       html_content holds the same <article class="teaser teaser--job"> markup
       as the homepage, 30 jobs per page, ordered by post date DESC.

Cards carry no full date (only a year in the timeline column), so posted_at
is joined from the Yoast job sitemap (https://www.architekturstellen.ch/
job-sitemap.xml), which lists every job post URL with a <lastmod> timestamp
(~publish date; ~850 entries back to 2019).

The archive keeps every posting ever published, so this scan stops after
MAX_PAGES pages or once a whole page is older than CUTOFF_DAYS.
"""
import sys


import datetime
import json
import re
import time
import urllib.parse
import urllib.request

import net
from net import http_get, extract_workload, strip_tags

BASE = "https://www.architekturstellen.ch"
AJAX = BASE + "/wp-admin/admin-ajax.php"
MAX_PAGES = 3      # 30 jobs/page; page 3 already reaches ~5 months back
CUTOFF_DAYS = 120  # drop postings older than this (archive goes back to 2019)

CARD = re.compile(r'<article class="teaser teaser--job[^"]*"(.*?)</article>', re.S)
HEADLINE = re.compile(r'class="teaser__headline[^"]*"\s*>(.*?)</h\d>', re.S)
COMPANY = re.compile(r'<p class="teaser__text[^"]*"\s*>(.*?)</p>', re.S)
CATEGORY = re.compile(r'class="teaser__category[^"]*"\s*>(.*?)</div>', re.S)
LINK = re.compile(r'<a href="([^"]+)" class="teaser__link', re.S)
PAGING_QUERY = re.compile(r'data-paging-query="([^"]+)"')
SITEMAP_PAIR = re.compile(r"<loc>([^<]+)</loc>\s*<lastmod>([^<]+)</lastmod>")


def _post_form(url, fields, tries=3, timeout=30):
    """Form-encoded POST through the shared scraper opener/cookie jar.

    (scraper.post_json sends a JSON body, which WP admin-ajax does not parse.)
    """
    data = urllib.parse.urlencode(fields).encode("utf-8")
    headers = {
        "User-Agent": net.UA,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": BASE,
        "Referer": BASE + "/",
        "Accept-Language": "de-CH,de;q=0.9,en;q=0.8,fr;q=0.7",
    }
    last = None
    for a in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with net._opener.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = e
            time.sleep(1 + a)
    raise last


def _sitemap_dates():
    """url -> 'YYYY-MM-DD' from the Yoast job sitemap. Best effort."""
    try:
        body, _ = http_get(BASE + "/job-sitemap.xml", accept="application/xml")
        return {u: d[:10] for u, d in SITEMAP_PAIR.findall(body)}
    except Exception:
        return {}


def _parse_cards(html_chunk, dates, seen):
    jobs = []
    for m in CARD.finditer(html_chunk):
        block = m.group(1)
        lm = LINK.search(block)
        hm = HEADLINE.search(block)
        if not lm or not hm:
            continue
        url = urllib.parse.urljoin(BASE, lm.group(1)).split("#")[0]
        if url in seen:
            continue
        seen.add(url)
        title = strip_tags(hm.group(1))
        if not title:
            continue
        cm = COMPANY.search(block)
        gm = CATEGORY.search(block)
        job = {
            "title": title,
            "company_name": strip_tags(cm.group(1)) if cm else "",
            "location": strip_tags(gm.group(1)) if gm else "",
            "url": url,
            "posted_at": dates.get(url, ""),
        }
        wl = extract_workload(title)
        if wl:
            job["workload"] = wl
        jobs.append(job)
    return jobs


def scan_architekturstellen():
    dates = _sitemap_dates()
    cutoff = (datetime.date.today() - datetime.timedelta(days=CUTOFF_DAYS)).isoformat()

    home, _ = http_get(BASE + "/")
    seen = set()
    jobs = _parse_cards(home, dates, seen)

    pq = None
    pm = PAGING_QUERY.search(home)
    if pm:
        pq = urllib.parse.unquote(pm.group(1))

    for page in range(2, MAX_PAGES + 1):
        if not pq:
            break
        time.sleep(1.0)
        try:
            d = _post_form(AJAX, {"action": "get_jobs", "post_query": pq,
                                  "paged": page, "lang": ""})
        except Exception:
            break
        batch = _parse_cards(d.get("html_content") or "", dates, seen)
        if not batch:
            break
        jobs.extend(batch)
        page_dates = [j["posted_at"] for j in batch if j["posted_at"]]
        # listing is date DESC: once a whole page is past the cutoff, stop
        if page_dates and max(page_dates) < cutoff:
            break
        if not d.get("has_more_pages"):
            break

    # drop stale archive entries (board keeps postings back to 2019)
    jobs = [j for j in jobs if not j["posted_at"] or j["posted_at"] >= cutoff]
    return jobs


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    jobs = scan_architekturstellen()
    print(json.dumps({"count": len(jobs), "sample": jobs[:3]}, ensure_ascii=False, indent=1))
