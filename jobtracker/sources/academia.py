# -*- coding: utf-8 -*-
"""Swiss academic / research architecture jobs.

Portals covered
---------------
1. myScience.ch  (https://www.myscience.ch/jobs)
   Swiss academic job aggregator (ETH, EPFL, Unis, FH/HES). Server-rendered
   HTML with schema.org ItemList markup inside <div id="results_table">.
   Search: GET /jobs?ctrl=1&r=c_Switzerland&q=<term>  (q supports * wildcard,
   terms are stemmed: architektur == architecture) and a category filter
   d[]=Architecture+%26+Buildings. Pagination: &s=<offset>, 20 per page,
   total in 'Ergebnisse <b>1</b> an <b>20</b> von <b>N</b>'.
   Per item: url (/jobs/id<NNNNN>-slug), title (itemprop="name", keyword
   hits wrapped in <b>), organization (span.results_organization),
   location (span.location), date (<time datetime="YYYY-MM-DD">).
   NOTE: the RSS feed (mode=rss) ignores the q filter, so HTML is parsed.

2. ETH Zürich  (https://jobs.ethz.ch)
   Yii app; the homepage server-renders ALL open positions (~110) in one
   list (no pagination). Per item (inside <div data-key=..><li>..</li>):
   href /job/view/<id>, h3.job-ad__item__title, .job-ad__item__details
   ("40%, Zürich, befristet"), .job-ad__item__company
   ("15.05.2026 | D-ARCH, Chair of ..."). There is no keyword search
   (filter form is POST + csrf, only region/type/workload selects), so we
   fetch once and keep items whose title/department match an
   architecture/urbanism regex.

3. EPFL  (https://careers.epfl.ch)
   SAP SuccessFactors Career Site Builder ("jobs2web"). Server-rendered
   search: GET /search/?q=<term>&locale=en_US, rows are
   <tr class="data-row"> with a.jobTitle-link (title + href
   /job/<slug>/<numeric id>/) and span.jobShifttype (= the city; CSB column
   mapping quirk). Total in 'Results 1 to N of M', pagination &startrow=25.
   Tiles carry no posting date -> posted_at stays "".

All portals are merged and deduped by URL; each portal failure is caught
and reported without killing the run. Hard budget: MAX_REQUESTS HTTP
requests per full run, 0.5-1.5 s sleep between requests.
"""
import sys

import html
import json
import random
import re
import time
import urllib.request

import net as _net
from net import http_get, get_json, post_json, strip_tags, extract_workload

MAX_REQUESTS = 20          # hard cap for a full run, all portals together
_req_count = 0


def _budget():
    """Enforce the request cap and the politeness sleep."""
    global _req_count
    if _req_count >= MAX_REQUESTS:
        raise RuntimeError("academia: request budget (%d) exhausted" % MAX_REQUESTS)
    if _req_count:
        time.sleep(random.uniform(0.5, 1.5))
    _req_count += 1


def _get(url, **kw):
    """Budgeted, polite GET (UTF-8 portals: ETH, EPFL)."""
    _budget()
    body, final = http_get(url, **kw)
    return body


def _get_charset_aware(url, tries=3, timeout=30):
    """Budgeted GET that honors the server's charset declaration.

    myScience serves iso-8859-15; net.http_get hard-decodes UTF-8 with
    'replace' which irreversibly turns every umlaut into U+FFFD, so fetch
    the bytes ourselves (reusing net's browser UA + shared cookie jar) and
    decode with the declared charset.
    """
    _budget()
    h = {"User-Agent": _net.UA, "Accept": "text/html",
         "Accept-Language": "de-CH,de;q=0.9,en;q=0.8,fr;q=0.7"}
    req = urllib.request.Request(url, headers=h)
    opener = getattr(_net, "_opener", None) or urllib.request.build_opener()
    last = None
    for a in range(tries):
        try:
            with opener.open(req, timeout=timeout) as r:
                raw = r.read()
                m = re.search(r"charset=([\w\-]+)",
                              r.headers.get("Content-Type") or "")
                enc = m.group(1) if m else "utf-8"
                try:
                    return raw.decode(enc, "replace")
                except LookupError:
                    return raw.decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(1 + a)
    raise last


def _clean(s):
    """strip tags/entities and collapse whitespace."""
    return re.sub(r"\s+", " ", strip_tags(s or "")).strip()


def _date_iso_dmy(d):
    """'15.05.2026' -> '2026-05-15'"""
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})", (d or "").strip())
    if not m:
        return ""
    return "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1)))


# --------------------------------------------------------------------------
# 1. myScience.ch
# --------------------------------------------------------------------------
MS_BASE = "https://www.myscience.ch"
# category filter first (precise), then keyword searches; the engine stems,
# so 'architektur' also matches 'architecture'; '*' is a wildcard.
MS_SEARCHES = [
    "/jobs?ctrl=1&r=c_Switzerland&d%5B%5D=Architecture+%26+Buildings",
    "/jobs?ctrl=1&r=c_Switzerland&q=architektur",
    "/jobs?ctrl=1&r=c_Switzerland&q=urban*",
    "/jobs?ctrl=1&r=c_Switzerland&q=st%C3%A4dteb*",
    "/jobs?ctrl=1&r=c_Switzerland&q=raumplan*",
]
MS_PAGE = 20
MS_MAX_PAGES = 2            # per search
MS_TOTAL = re.compile(r"Ergebnisse <b>\d+</b> an <b>\d+</b> von <b>(\d+)</b>")
MS_ITEM = re.compile(
    r'<a href="(?P<url>/jobs/id\d+[^"]*)" itemprop="url">(?P<blk>.*?)</a>', re.S)


def _norm_inst(name):
    """Normalize the big institutions' many spellings on myScience."""
    if re.search(r"(?i)\bETHZ\b|ETH Z(?:u|ü|&uuml;)rich|Technische Hochschule Z", name):
        return "ETH Zürich"
    if re.search(r"(?i)\bEPFL\b|Institute of Technology Lausanne"
                 r"|Polytechnique F(?:e|é)d(?:e|é)rale de Lausanne", name):
        return "EPFL"
    return name


def _scan_myscience():
    jobs, seen = [], set()
    for search in MS_SEARCHES:
        for page in range(MS_MAX_PAGES):
            url = MS_BASE + search + ("&s=%d" % (page * MS_PAGE) if page else "")
            body = _get_charset_aware(url)
            i = body.find('id="results_table"')
            if i < 0:                      # zero hits -> no results_table
                break
            seg = body[i:]
            n_items = 0
            for m in MS_ITEM.finditer(seg):
                n_items += 1
                href = html.unescape(m.group("url")).split("#")[0]
                job_url = MS_BASE + href
                if job_url in seen:
                    continue
                blk = m.group("blk")
                tm = re.search(r'itemprop="name">(.*?)</span>', blk, re.S)
                title = _clean(tm.group(1)) if tm else ""
                if not title:
                    continue
                seen.add(job_url)
                om = re.search(r'class="results_organization">(.*?)</span>', blk, re.S)
                lm = re.search(r'class="location">(.*?)</span>', blk, re.S)
                dm = re.search(r'<time datetime="(\d{4}-\d{2}-\d{2})"', blk)
                job = {
                    "title": title,
                    "company_name": _norm_inst(_clean(om.group(1)) if om else ""),
                    "location": _clean(lm.group(1)) if lm else "",
                    "url": job_url,
                    "posted_at": dm.group(1) if dm else "",
                }
                wl = extract_workload(title)
                if wl:
                    job["workload"] = wl
                jobs.append(job)
            tm = MS_TOTAL.search(body)
            total = int(tm.group(1)) if tm else 0
            if n_items < MS_PAGE or (page + 1) * MS_PAGE >= total:
                break
    return jobs


# --------------------------------------------------------------------------
# 2. ETH Zürich
# --------------------------------------------------------------------------
ETH_BASE = "https://jobs.ethz.ch"
ETH_BLOCK = re.compile(r'<div data-key="[^"]+">\s*<li.*?</li>', re.S)
# architecture / urbanism / planning signal in title or department
ETH_ARCH = re.compile(
    r"(?i)architek|architec|urbanis|urban design|urban plan|städteb|"
    r"stadtplan|raumplan|raumentwick|landschaftsarch|landscape architec|"
    r"\bd-arch\b|\bgta\b|baukultur|denkmalpf|hochbau|gebäudetechn")


def _scan_eth():
    jobs, seen = [], set()
    body = _get(ETH_BASE + "/")
    for m in ETH_BLOCK.finditer(body):
        blk = m.group(0)
        hm = re.search(r'href="(/job/view/[^"]+)"', blk)
        tm = re.search(r'job-ad__item__title">\s*(.*?)\s*</h3>', blk, re.S)
        if not hm or not tm:
            continue
        title = _clean(tm.group(1))
        dm = re.search(r'job-ad__item__details">\s*(.*?)\s*</div>', blk, re.S)
        details = _clean(dm.group(1)) if dm else ""
        cm = re.search(r'job-ad__item__company">\s*(.*?)\s*</div>', blk, re.S)
        company_line = _clean(cm.group(1)) if cm else ""
        # company line = "15.05.2026 | D-ARCH, Chair of Alexandre Theriot"
        date_s, dept = "", ""
        if "|" in company_line:
            date_s, dept = [p.strip() for p in company_line.split("|", 1)]
        else:
            dept = company_line
        if not (ETH_ARCH.search(title) or ETH_ARCH.search(dept)):
            continue
        job_url = ETH_BASE + html.unescape(hm.group(1))
        if not title or job_url in seen:
            continue
        seen.add(job_url)
        # details = "40%, Zürich, befristet" -> workload, city, term
        location = ""
        parts = [p.strip() for p in details.split(",") if p.strip()]
        for p in parts:
            if "%" in p or re.search(
                    r"(?i)befristet|unbefristet|fixed-term|permanent|temporary", p):
                continue
            location = p
            break
        job = {
            "title": title,
            "company_name": "ETH Zürich",
            "location": location,
            "url": job_url,
            "posted_at": _date_iso_dmy(date_s),
        }
        wl = extract_workload(details) or extract_workload(title)
        if wl:
            job["workload"] = wl
        jobs.append(job)
    return jobs


# --------------------------------------------------------------------------
# 3. EPFL
# --------------------------------------------------------------------------
EPFL_BASE = "https://careers.epfl.ch"
EPFL_SEARCHES = ["architecture", "urbanisme"]
EPFL_PAGE = 25
EPFL_MAX_PAGES = 2          # per search
EPFL_ROW = re.compile(r'<tr class="data-row">(.*?)</tr>', re.S)
EPFL_TOTAL = re.compile(r"Results\s+1\s+to\s+\d+\s+of\s+(\d+)")


def _scan_epfl():
    jobs, seen = [], set()
    for term in EPFL_SEARCHES:
        for page in range(EPFL_MAX_PAGES):
            url = (EPFL_BASE + "/search/?q=" + term + "&locale=en_US"
                   + ("&startrow=%d" % (page * EPFL_PAGE) if page else ""))
            body = _get(url)
            rows = EPFL_ROW.findall(body)
            for blk in rows:
                hm = re.search(
                    r'<a\s+href="(/job/[^"]+)"\s+class="jobTitle-link">(.*?)</a>',
                    blk, re.S)
                if not hm:
                    continue
                job_url = EPFL_BASE + html.unescape(hm.group(1)).split("#")[0]
                title = _clean(hm.group(2))
                if not title or job_url in seen:
                    continue
                seen.add(job_url)
                lm = re.search(
                    r'colShifttype hidden-phone.*?class="jobShifttype">(.*?)</span>',
                    blk, re.S)
                job = {
                    "title": title,
                    "company_name": "EPFL",
                    "location": _clean(lm.group(1)) if lm else "",
                    "url": job_url,
                    "posted_at": "",          # tiles carry no date
                }
                wl = extract_workload(title)
                if wl:
                    job["workload"] = wl
                jobs.append(job)
            tm = EPFL_TOTAL.search(body)
            total = int(tm.group(1)) if tm else 0
            if not rows or (page + 1) * EPFL_PAGE >= total:
                break
    return jobs


# --------------------------------------------------------------------------
def scan_academia():
    """Merge all academic portals, dedupe by URL, tolerate portal failures."""
    jobs, seen = [], set()
    for name, fn in (("myscience", _scan_myscience),
                     ("eth", _scan_eth),
                     ("epfl", _scan_epfl)):
        try:
            found = fn()
        except Exception as e:
            print("academia: %s failed: %s" % (name, e), file=sys.stderr)
            continue
        for job in found:
            if job["url"] in seen:
                continue
            seen.add(job["url"])
            jobs.append(job)
    return jobs


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    jobs = scan_academia()
    print(json.dumps({"count": len(jobs), "sample": jobs[:3]},
                     ensure_ascii=False, indent=1))
