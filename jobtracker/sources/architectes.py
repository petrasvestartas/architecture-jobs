"""architectes.ch — Romandie/French-Swiss architecture portal.

Listings: https://www.architectes.ch/fr/annonces-offres-demploi
Server-rendered Webflow + Finsweet list. Two panes:
  - left  ("jobs-list_left"):  paginated cards (8/page), pagination via
    ?<listid>_page=N where <listid> is a hex instance id discovered from the
    arrow hrefs (e.g. ?01e7f84a_page=2). Cards carry title, absolute raw date
    ("June 30, 2026" in div.date_job), canton (Lieu), hidden city, occupation
    ("60-80 %"), company logo-name.
  - right ("jobs-list_right"): ALL job detail components on every page
    (no date) — used as a coverage safety net.
Stable per-job deep link: ?job=<slug> (Finsweet equal-filter on hidden input
name="job"); slug comes from data-job-slug and carries a unique hash suffix.
"""
import sys

import json
import re
import time
import html
from datetime import date, timedelta
from urllib.parse import quote

from net import http_get, strip_tags, extract_workload

BASE = "https://www.architectes.ch"
LIST_URL = BASE + "/fr/annonces-offres-demploi"
MAX_PAGES = 5

MONTHS = {
    # English (Webflow renders raw dates in English on this site)
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
    # French, in case the CMS date format changes
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "aout": 8, "août": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
    "décembre": 12,
}

CANTON = {
    "vaud": "VD", "geneve": "GE", "genève": "GE", "valais": "VS",
    "fribourg": "FR", "neuchatel": "NE", "neuchâtel": "NE", "jura": "JU",
    "berne": "BE", "bern": "BE", "zurich": "ZH", "zürich": "ZH",
    "bale": "BS", "bâle": "BS", "bale-ville": "BS", "bâle-ville": "BS",
    "bale-campagne": "BL", "bâle-campagne": "BL", "argovie": "AG",
    "lucerne": "LU", "tessin": "TI", "ticino": "TI", "soleure": "SO",
    "saint-gall": "SG", "st-gall": "SG", "thurgovie": "TG", "grisons": "GR",
    "schwyz": "SZ", "zoug": "ZG", "uri": "UR", "obwald": "OW",
    "nidwald": "NW", "glaris": "GL", "schaffhouse": "SH",
    "appenzell rhodes-exterieures": "AR", "appenzell rhodes-extérieures": "AR",
    "appenzell rhodes-interieures": "AI", "appenzell rhodes-intérieures": "AI",
}


def _clean(s):
    return re.sub(r"\s+", " ", strip_tags(s)).strip()


def _parse_raw_date(raw):
    """'June 30, 2026' or '30 juin 2026' -> 'YYYY-MM-DD' or ''."""
    raw = _clean(raw)
    m = re.match(r"([A-Za-zÀ-ÿ]+)\s+(\d{1,2}),?\s+(\d{4})", raw)
    if m:
        mon = MONTHS.get(m.group(1).lower())
        if mon:
            return "%s-%02d-%02d" % (m.group(3), mon, int(m.group(2)))
    m = re.match(r"(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\.?\s+(\d{4})", raw)
    if m:
        mon = MONTHS.get(m.group(2).lower())
        if mon:
            return "%s-%02d-%02d" % (m.group(3), mon, int(m.group(1)))
    return ""


def _parse_rel_date(text):
    """\"Il y a 3 jours\" / \"Aujourd'hui\" / \"Hier\" -> 'YYYY-MM-DD' or ''."""
    t = _clean(text).lower()
    if not t:
        return ""
    today = date.today()
    if "aujourd" in t:
        return today.isoformat()
    if "hier" in t:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"il y a\s+(\d+)\s+(jour|semaine|mois|an)", t)
    if not m:
        return ""
    n = int(m.group(1))
    unit = m.group(2)
    days = {"jour": 1, "semaine": 7, "mois": 30, "an": 365}[unit] * n
    return (today - timedelta(days=days)).isoformat()


def _company(logo_name):
    """'Induni & Cie SA • Genève • Vaud | Entreprise générale' -> 'Induni & Cie SA'."""
    name = html.unescape(logo_name or "")
    name = name.split("•")[0].split("|")[0]
    return re.sub(r"\s+", " ", name).strip()


def _field(card, name):
    m = re.search(r'fs-list-field="%s"[^>]*>(.*?)<' % re.escape(name), card, re.S)
    return _clean(m.group(1)) if m else ""


def _canton_and_location(lieu, city):
    canton = CANTON.get(lieu.lower().strip(), "")
    m = re.search(r"\(([A-Z]{2})\)", city or "")
    if not canton and m:
        canton = m.group(1)
    if city and canton and "(" not in city:
        location = "%s (%s)" % (city, canton)
    else:
        location = city or lieu
    return canton, location


def _split_cards(segment):
    parts = segment.split('data-job-slug="')[1:]
    out = []
    for p in parts:
        slug = p.split('"', 1)[0].strip()
        if slug:
            out.append((slug, p))
    return out


def _parse_left(body, jobs, seen):
    left = body.find("jobs-list_left")
    right = body.find("jobs-list_right")
    if left < 0:
        return 0
    seg = body[left:right] if right > left else body[left:]
    added = 0
    for slug, card in _split_cards(seg):
        if slug in seen:
            continue
        title = _field(card, "offre")
        if not title:
            continue
        seen.add(slug)
        m = re.search(r'class="date_job"[^>]*>([^<]*)<', card)
        posted = _parse_raw_date(m.group(1)) if m else ""
        if not posted:
            m = re.search(r"Il y a[^<]*|Aujourd[^<]*|Hier[^<]*", card)
            posted = _parse_rel_date(m.group(0)) if m else ""
        lieu = _field(card, "Lieu")
        city = _field(card, "city")
        canton, location = _canton_and_location(lieu, city)
        m = re.search(r'joboffer_card-logo-name">([^<]*)<', card)
        company = _company(m.group(1)) if m else ""
        workload = extract_workload(_field(card, "occupation")) or extract_workload(title)
        job = {
            "title": title,
            "company_name": company,
            "location": location,
            "url": "%s?job=%s" % (LIST_URL, quote(slug, safe="")),
            "posted_at": posted,
        }
        if workload:
            job["workload"] = workload
        if canton:
            job["canton"] = canton
        jobs.append(job)
        added += 1
    return added


def _parse_right(body, jobs, seen):
    """Right pane lists ALL jobs (no date) — catch any the paginated left missed."""
    right = body.find("jobs-list_right")
    if right < 0:
        return
    for slug, card in _split_cards(body[right:]):
        if slug in seen:
            continue
        title = _field(card, "offre")
        if not title:
            continue
        seen.add(slug)
        m = re.search(r'jobdetail_company-name">([^<]*)<', card)
        company = _company(m.group(1)) if m else ""
        m = re.search(r'localisation w-embed"><p>([^<]*)</p>', card)
        location = _clean(m.group(1)) if m else ""
        m = re.search(r"\(([A-Z]{2})\)", location)
        canton = m.group(1) if m else ""
        workload = extract_workload(_field(card, "occupation")) or extract_workload(title)
        job = {
            "title": title,
            "company_name": company,
            "location": location,
            "url": "%s?job=%s" % (LIST_URL, quote(slug, safe="")),
            "posted_at": "",
        }
        if workload:
            job["workload"] = workload
        if canton:
            job["canton"] = canton
        jobs.append(job)


def scan_architectes():
    jobs = []
    seen = set()
    body, _ = http_get(LIST_URL)
    first = body
    _parse_left(body, jobs, seen)
    # Discover Finsweet pagination param, e.g. href="?01e7f84a_page=2"
    m = re.search(r'href="\?([0-9a-f]{4,}_page)=\d+"', body)
    param = m.group(1) if m else None
    if param:
        for n in range(2, MAX_PAGES + 1):
            if ('href="?%s=%d"' % (param, n)) not in body:
                break
            time.sleep(0.8)
            body, _ = http_get("%s?%s=%d" % (LIST_URL, param, n))
            if _parse_left(body, jobs, seen) == 0:
                break
    # Safety net: right pane of page 1 carries every job (undated).
    _parse_right(first, jobs, seen)
    return jobs


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    jobs = scan_architectes()
    print(json.dumps({"count": len(jobs), "sample": jobs[:3]},
                     ensure_ascii=False, indent=1))
