"""NZZ Jobs (jobs.nzz.ch) + Ostjob (ostjob.ch) — x28 job-platform engine.

Endpoint discovery notes (verified 2026-07-11):
  Both sites are the same x28/xiag "jobs" SPA. The frontend config blob embedded
  in the homepage HTML exposes:
      "REST_API_URI":        https://api.jobs.nzz.ch   (resp. https://api.ostjob.ch)
      "REST_API_SEARCH_URI": /public/vacancy/search/
  The search endpoint is a plain GET (POST returns 405):
      GET {REST_API_URI}/public/vacancy/search/?search=<term>&page=<n>&pageSize=<m>
  Query parameter names come from the SPA's default searchData object:
      {search:"", category:[], homeOffice, place:"", placeType, placeCode:"",
       placeValue:"", radius, minPercent, maxPercent, lat, lng,
       page:1, pageSize:10, type:[], position:[], order}
  Response envelope: {items:[...], relatedWords:[...], pages:N, total:N,
  flowFound:[...], usePromotedFlag}. "pages" = ceil(total/pageSize).
  NOTE: results are relevance-flow ordered; the tail of the list contains
  related-word backfill (e.g. Planung/Bau jobs for "architekt"), boundaries in
  "flowFound". We return everything and let downstream filtering decide.

  Item titles carry <b class="hi"> highlight markup -> strip_tags required.
  Public board detail URL: https://jobs.nzz.ch/job/{id} (302 -> canonical
  /job/{slug}/{id}); same pattern on https://www.ostjob.ch/job/{id}.
"""

import sys

import json
import re
import time
from urllib.parse import quote

from net import http_get, get_json, post_json, strip_tags, extract_workload

TERMS = ["architekt", "architecte"]
PAGE_SIZE = 100
MAX_PAGES = 12          # hard cap per term (contract)
SLEEP = 0.8             # politeness delay between page requests

CANTON_CODES = {
    "zürich": "ZH", "zurich": "ZH",
    "bern": "BE", "berne": "BE",
    "luzern": "LU", "lucerne": "LU",
    "uri": "UR", "schwyz": "SZ", "obwalden": "OW", "nidwalden": "NW",
    "glarus": "GL", "zug": "ZG",
    "freiburg": "FR", "fribourg": "FR",
    "solothurn": "SO",
    "basel-stadt": "BS", "basel-landschaft": "BL",
    "schaffhausen": "SH",
    "appenzell ausserrhoden": "AR", "appenzell innerrhoden": "AI",
    "st. gallen": "SG", "st.gallen": "SG", "sankt gallen": "SG",
    "graubünden": "GR", "grisons": "GR", "grigioni": "GR",
    "aargau": "AG", "thurgau": "TG",
    "tessin": "TI", "ticino": "TI",
    "waadt": "VD", "vaud": "VD",
    "wallis": "VS", "valais": "VS",
    "neuenburg": "NE", "neuchâtel": "NE", "neuchatel": "NE",
    "genf": "GE", "genève": "GE", "geneve": "GE", "geneva": "GE",
    "jura": "JU",
    "liechtenstein": "LI",
}


def _clean_title(raw):
    # Search-hit highlight markup (<b class="hi">Architekt</b>:in) must be
    # removed WITHOUT inserting a space, else strip_tags yields "Architekt :in".
    s = re.sub(r"</?b\b[^>]*>", "", raw or "")
    return re.sub(r"\s+", " ", strip_tags(s)).strip()


def _canton_code(name):
    name = (name or "").strip()
    if not name:
        return ""
    if len(name) == 2 and name.isalpha():
        return name.upper()
    return CANTON_CODES.get(name.lower(), "")


def _workload(item):
    lo, hi = item.get("typeValueMin"), item.get("typeValueMax")
    try:
        lo = int(lo) if lo else 0
        hi = int(hi) if hi else 0
    except (TypeError, ValueError):
        lo = hi = 0
    if lo and hi and lo != hi:
        return f"{lo}-{hi}"
    if lo or hi:
        return str(lo or hi)
    return extract_workload(_clean_title(item.get("title")))


def _map_item(item, board_base):
    jid = item.get("id")
    title = _clean_title(item.get("title"))
    if not title:
        return None

    url = item.get("urlDescription") or ""
    if not (isinstance(url, str) and url.startswith("http")):
        url = f"{board_base}/job/{jid}" if jid else ""
    if not url:
        return None

    zip_, city = item.get("workplaceZip") or "", item.get("workplaceCity") or ""
    location = f"{zip_} {city}".strip()

    company = item.get("company") or {}
    posted = item.get("dateFirstPublished") or ""

    job = {
        "title": title,
        "company_name": company.get("name") or "",
        "location": location,
        "url": url,
        "posted_at": posted[:10] if len(posted) >= 10 else "",
    }

    wl = _workload(item)
    if wl:
        job["workload"] = wl

    coords = item.get("coordinates") or []
    if coords and isinstance(coords[0], dict):
        try:
            job["lat"] = float(coords[0]["latitude"])
            job["lon"] = float(coords[0]["longitude"])
        except (KeyError, TypeError, ValueError):
            pass

    cantons = item.get("cantons") or []
    code = _canton_code(cantons[0] if cantons else "")
    if code:
        job["canton"] = code

    return job


def _scan_x28(api_base, board_base):
    """Generic scan over an x28-engine board (search endpoint verified live)."""
    jobs, seen = [], set()
    for term in TERMS:
        page, pages_total = 1, 1
        while page <= min(pages_total, MAX_PAGES):
            url = (f"{api_base}/public/vacancy/search/"
                   f"?search={quote(term)}&page={page}&pageSize={PAGE_SIZE}")
            try:
                data = get_json(url)
            except Exception as e:
                print(f"[x28] {url} failed: {e!r}", file=sys.stderr)
                break
            if not isinstance(data, dict):
                break
            pages_total = int(data.get("pages") or 1)
            items = data.get("items") or []
            if not items:
                break
            for item in items:
                jid = item.get("id")
                key = jid if jid is not None else _clean_title(item.get("title"))
                if key in seen:
                    continue
                seen.add(key)
                job = _map_item(item, board_base)
                if job and job["url"] not in seen:
                    seen.add(job["url"])
                    jobs.append(job)
            page += 1
            if page <= min(pages_total, MAX_PAGES):
                time.sleep(SLEEP)
    return jobs


def scan_nzz():
    """NZZ Jobs — jobs.nzz.ch (x28 engine)."""
    return _scan_x28("https://api.jobs.nzz.ch", "https://jobs.nzz.ch")


def scan_ostjob():
    """Ostjob — ostjob.ch (same x28 engine, eastern-Switzerland board)."""
    return _scan_x28("https://api.ostjob.ch", "https://www.ostjob.ch")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    jobs = scan_nzz()
    time.sleep(SLEEP)
    jobs += scan_ostjob()
    print(json.dumps({"count": len(jobs), "sample": jobs[:3]},
                     ensure_ascii=False, indent=1))
