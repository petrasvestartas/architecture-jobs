# -*- coding: utf-8 -*-
"""Shared HTTP layer for the scraper and the board modules in sources/.

Lives in its own module (not scraper.py) so sources/* can import it without
creating an import cycle with scraper.py.
"""
import html
import http.cookiejar
import json
import os
import re
import time
import urllib.request

# Keep the Chrome major roughly current. "Chrome/126.0" was two years stale
# by mid-2026 and is a giveaway that nothing here is a browser: in
# interleaved requests to architekten-jobs.ch, with every other header held
# constant, the 126 string was refused three times while a current one was
# served. Bumping it is hygiene rather than a proven fix — the same host now
# refuses this whole machine regardless — but a stale major can only cost us.
#
# To refresh, read the version off the browser Playwright already installs:
#   .venv/bin/python -c "from playwright.sync_api import sync_playwright; \
#     p=sync_playwright().start(); b=p.chromium.launch(); \
#     print(b.new_context().new_page().evaluate('navigator.userAgent'))"
# JOBTRACKER_UA overrides it without a code change if a board starts
# objecting between refreshes.
UA = os.environ.get("JOBTRACKER_UA") or (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

# one shared opener with an in-memory cookie jar: several boards
# (swiss-architects, jobagent) gate on cookies + Referer
_cookies = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookies))


def http_get(url, accept="text/html", tries=3, timeout=30, headers=None):
    last = None
    for a in range(tries):
        try:
            h = {"User-Agent": UA, "Accept": accept,
                 "Accept-Language": "de-CH,de;q=0.9,en;q=0.8,fr;q=0.7"}
            if headers:
                h.update(headers)
            req = urllib.request.Request(url, headers=h)
            with _opener.open(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace"), r.geturl()
        except Exception as e:
            last = e
            time.sleep(1 + a)
    raise last


def http_get_cond(url, etag=None, last_mod=None, timeout=15):
    """Single conditional GET (If-None-Match / If-Modified-Since).

    Returns (body, final_url, etag, last_mod, changed). On 304 Not Modified,
    body/final_url are None and changed is False — the caller can skip
    parsing entirely. Servers without validators always return changed=True.
    """
    import urllib.error
    h = {"User-Agent": UA, "Accept": "text/html",
         "Accept-Language": "de-CH,de;q=0.9,en;q=0.8,fr;q=0.7"}
    if etag:
        h["If-None-Match"] = etag
    if last_mod:
        h["If-Modified-Since"] = last_mod
    req = urllib.request.Request(url, headers=h)
    try:
        with _opener.open(req, timeout=timeout) as r:
            return (r.read().decode("utf-8", "replace"), r.geturl(),
                    r.headers.get("ETag") or "",
                    r.headers.get("Last-Modified") or "", True)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, None, etag or "", last_mod or "", False
        raise


def get_json(url, **kw):
    body, _ = http_get(url, accept="application/json", **kw)
    return json.loads(body)


def post_json(url, payload, tries=3, timeout=30, headers=None):
    last = None
    body = json.dumps(payload).encode("utf-8")
    for a in range(tries):
        try:
            h = {"User-Agent": UA, "Accept": "application/json",
                 "Content-Type": "application/json",
                 "Accept-Language": "de-CH,de;q=0.9,en;q=0.8,fr;q=0.7"}
            if headers:
                h.update(headers)
            req = urllib.request.Request(url, data=body, headers=h)
            with _opener.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = e
            time.sleep(1 + a)
    raise last


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


WORKLOAD = re.compile(r"(\d{2,3})\s*(?:[-–—]|bis|à)?\s*(\d{2,3})?\s*%")


def extract_workload(text):
    m = WORKLOAD.search(text or "")
    if not m:
        return ""
    lo, hi = m.group(1), m.group(2)
    return f"{lo}-{hi}" if hi else lo
