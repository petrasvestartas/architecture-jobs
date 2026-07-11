# -*- coding: utf-8 -*-
"""Shared HTTP layer for the scraper and the board modules in sources/.

Lives in its own module (not scraper.py) so sources/* can import it without
creating an import cycle with scraper.py.
"""
import html
import http.cookiejar
import json
import re
import time
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

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
