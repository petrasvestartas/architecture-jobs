"""Scraper for archi-jobs.ch — Swiss architecture job board (WordPress REST API).

Endpoint: GET https://www.archi-jobs.ch/wp-json/wp/v2/job?per_page=100&page=N&_embed=wp:term
Paginates until an empty page or HTTP 400 (rest_post_invalid_page_number).
"""
import sys

import html
import json
import random
import re
import time
import urllib.error

from net import http_get, get_json, post_json, strip_tags, extract_workload  # noqa: F401

API = "https://www.archi-jobs.ch/wp-json/wp/v2/job?per_page=100&_embed=wp:term&page={page}"
MAX_PAGES = 5  # hard cap; the board is small (1-2 pages)

# Company suffixes that make a strong/heading line in the content "obviously" an office name
_COMPANY_PAT = re.compile(
    r"^[\w&.\-+:’' ]{2,60}\b(AG|GmbH|Architekten|Architekten\s+AG|Architekten\s+GmbH|"
    r"Architektur|Architekturb[üu]ro|Partner|Sàrl|SA|SIA)\.?$",
    re.IGNORECASE,
)
_TITLE_BEI_PAT = re.compile(r"\bbei\s+(.{2,60}?)\s*$", re.IGNORECASE)
_STRONG_OR_HEAD = re.compile(
    r"<(strong|h1|h2|h3)\b[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL
)


def _location_from_item(item):
    # Preferred: embedded taxonomy terms
    embedded = item.get("_embedded") or {}
    for term_group in embedded.get("wp:term") or []:
        for term in term_group or []:
            if isinstance(term, dict) and term.get("taxonomy") == "job_location":
                name = strip_tags(term.get("name") or "")
                if name:
                    return name
    # Fallback: class_list entry job_location-<slug>
    for cls in item.get("class_list") or []:
        if isinstance(cls, str) and cls.startswith("job_location-"):
            slug = cls[len("job_location-"):]
            return slug.replace("-", " ").title()
    return ""


def _company_from_item(item, title):
    # Pattern 1: title ends with "... bei <Firma>"
    m = _TITLE_BEI_PAT.search(title)
    if m:
        firm = m.group(1).strip(" .,;:-–—|")
        if firm:
            return firm
    # Pattern 2: an early <strong>/<h1-3> line in the content that looks like an
    # office name (company suffix) and is not just the job title repeated.
    content = ((item.get("content") or {}).get("rendered")) or ""
    for _tag, inner in _STRONG_OR_HEAD.findall(content[:4000]):
        line = strip_tags(inner)
        line = re.sub(r"\s+", " ", line).strip(" .,;:-–—|")
        if not line or len(line) > 60:
            continue
        if line.lower() == title.lower():
            continue
        if "%" in line:  # workload lines / repeated job titles
            continue
        if _COMPANY_PAT.match(line):
            return line
    return ""


def scan_archijobs():
    jobs = []
    seen = set()
    for page in range(1, MAX_PAGES + 1):
        url = API.format(page=page)
        try:
            # tries=1 so a legitimate end-of-pagination 400 doesn't get retried
            data = get_json(url, tries=1) if page > 1 else get_json(url)
        except urllib.error.HTTPError as e:
            if e.code == 400:  # rest_post_invalid_page_number -> end of pages
                break
            raise
        if not isinstance(data, list) or not data:
            break
        for item in data:
            title = strip_tags(((item.get("title") or {}).get("rendered")) or "")
            title = re.sub(r"\s+", " ", title).strip()
            link = html.unescape(item.get("link") or "")
            if not title or not link.startswith("http"):
                continue
            if link in seen:
                continue
            seen.add(link)
            date = (item.get("date") or "")[:10]
            job = {
                "title": title,
                "company_name": _company_from_item(item, title),
                "location": _location_from_item(item),
                "url": link,
                "posted_at": date if re.match(r"^\d{4}-\d{2}-\d{2}$", date) else "",
            }
            wl = extract_workload(title)
            if wl:
                job["workload"] = wl
            jobs.append(job)
        if len(data) < 100:  # short page -> last page
            break
        time.sleep(random.uniform(0.5, 1.5))
    return jobs


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    jobs = scan_archijobs()
    print(json.dumps({"count": len(jobs), "sample": jobs[:3]}, ensure_ascii=False, indent=1))
