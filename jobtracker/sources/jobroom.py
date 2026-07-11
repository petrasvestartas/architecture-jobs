# -*- coding: utf-8 -*-
"""Scraper for job-room.ch — official Swiss government (SECO/RAV) job board.

Endpoint:
  POST https://www.job-room.ch/jobadservice/api/jobAdvertisements/_search
       ?page=N&size=100&sort=date_desc
Body (JobAdvertisementSearchRequest — the API rejects unknown fields):
  {"workloadPercentageMin":10,"workloadPercentageMax":100,"permanent":null,
   "companyName":null,"onlineSince":31,"displayRestricted":false,
   "keywords":[...],"professionCodes":[],"communalCodes":[],"cantonCodes":[],
   "radiusSearchRequest":null}
Response: JSON array of {"jobAdvertisement": {...}} wrappers; total count is in
the X-Total-Count response header (not needed — we paginate until a short page).
Detail URL (SPA route): https://www.job-room.ch/job-search/<uuid>
"""
import sys


import json
import re
import time

from net import http_get, get_json, post_json, strip_tags, extract_workload  # noqa: F401

API = ("https://www.job-room.ch/jobadservice/api/jobAdvertisements/_search"
       "?page={page}&size={size}&sort=date_desc")
DETAIL = "https://www.job-room.ch/job-search/{id}"
TERMS = ["architekt", "architecte", "architect", "bauleitung", "hochbau"]
SIZE = 100
MAX_PAGES = 5          # hard cap per term
SLEEP = 0.8
HEADERS = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# language preference for jobDescriptions (multi-language ads)
_LANG_PREF = {"de": 0, "fr": 1, "en": 2, "it": 3}


def _body(term):
    return {
        "workloadPercentageMin": 10,
        "workloadPercentageMax": 100,
        "permanent": None,
        "companyName": None,
        "onlineSince": 31,
        "displayRestricted": False,
        "keywords": [term],
        "professionCodes": [],
        "communalCodes": [],
        "cantonCodes": [],
        "radiusSearchRequest": None,
    }


def _pick_title(job_content):
    descs = job_content.get("jobDescriptions") or []
    descs = [d for d in descs if isinstance(d, dict) and (d.get("title") or "").strip()]
    if not descs:
        return ""
    descs.sort(key=lambda d: _LANG_PREF.get((d.get("languageIsoCode") or "").lower(), 9))
    # titles can carry <em> search-highlight markup and HTML entities
    title = strip_tags(descs[0].get("title") or "")
    return re.sub(r"\s+", " ", title).strip()


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_item(wrapper):
    ad = (wrapper or {}).get("jobAdvertisement") or wrapper or {}
    ad_id = ad.get("id") or ""
    jc = ad.get("jobContent") or {}
    title = _pick_title(jc)
    if not ad_id or not title:
        return None

    company = ((jc.get("company") or {}).get("name")) or ""
    company = re.sub(r"\s+", " ", strip_tags(company)).strip()

    loc = jc.get("location") or {}
    city = (loc.get("city") or "").strip()
    plz = (loc.get("postalCode") or "").strip()
    location = f"{plz} {city}".strip()

    posted = ((ad.get("publication") or {}).get("startDate")) or ""
    posted = posted[:10]
    if not _DATE.match(posted):
        posted = ""

    job = {
        "title": title,
        "company_name": company,
        "location": location,
        "url": DETAIL.format(id=ad_id),
        "posted_at": posted,
    }

    emp = jc.get("employment") or {}
    lo, hi = str(emp.get("workloadPercentageMin") or ""), str(emp.get("workloadPercentageMax") or "")
    if lo.isdigit() and hi.isdigit():
        job["workload"] = lo if lo == hi else f"{lo}-{hi}"
    elif hi.isdigit():
        job["workload"] = hi

    coords = loc.get("coordinates") or {}
    lat, lon = _to_float(coords.get("lat")), _to_float(coords.get("lon"))
    if lat is not None and lon is not None:
        job["lat"], job["lon"] = lat, lon

    canton = (loc.get("cantonCode") or "").strip()
    if canton:
        job["canton"] = canton

    return job


def scan_jobroom():
    jobs = []
    seen = set()
    for term in TERMS:
        body = _body(term)
        for page in range(MAX_PAGES):
            url = API.format(page=page, size=SIZE)
            data = post_json(url, body, headers=HEADERS)
            if not isinstance(data, list) or not data:
                break
            for wrapper in data:
                job = _parse_item(wrapper)
                if job is None:
                    continue
                if job["url"] in seen:
                    continue
                seen.add(job["url"])
                jobs.append(job)
            if len(data) < SIZE:  # short page -> last page for this term
                break
            time.sleep(SLEEP)
        time.sleep(SLEEP)
    return jobs


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    jobs = scan_jobroom()
    print(json.dumps({"count": len(jobs), "sample": jobs[:3]}, ensure_ascii=False, indent=1))
