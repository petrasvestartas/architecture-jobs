# -*- coding: utf-8 -*-
"""Daily job scan: sweeps job boards for architecture jobs in Switzerland,
stores posting links in the tracker DB, and (in rotation) probes company
websites for career/unsolicited-application pages.

Usage:
    python scraper.py                 # scan all boards
    python scraper.py --careers 150   # also probe 150 company sites for career pages
    python scraper.py --source sia    # only one source
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import db
import geo
from net import (UA, http_get, get_json, post_json, strip_tags,  # noqa: F401
                 extract_workload)

HERE = os.path.dirname(os.path.abspath(__file__))

SEARCH_TERMS = ["architekt", "architect", "architecte", "architetto",
                "bauleitung", "hochbau", "innenarchitektur", "raumplanung"]


def load_config():
    path = os.path.join(HERE, "config.json")
    cfg = {"reference_place": "Bern", "career_watch_daily": 300, "career_probe_daily": 150}
    if os.path.exists(path):
        cfg.update(json.load(open(path, encoding="utf-8")))
    return cfg


CONFIG = load_config()


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Sources — each returns a list of dicts:
#   {title, company_name, location, url, posted_at}
# --------------------------------------------------------------------------

def scan_sia():
    """SIA Jobs — the official architecture/engineering association board."""
    jobs = []
    d = get_json("https://postgrest.sia.ch/rpc/get_open_positions?lang=en")
    for pos_id, chunks in d.items():
        title = company = posted = None
        regions = []
        for c in chunks:
            if isinstance(c, dict):
                if "title" in c:
                    title = c.get("title")
                    company = c.get("company")
                    posted = c.get("start_date")
                if "regions" in c:
                    regions = [r.get("region", "") for r in c["regions"] or []]
        if title:
            jobs.append({
                "title": title, "company_name": company,
                "location": ", ".join(regions) or "Switzerland",
                "url": f"https://www.sia.ch/en/jobs/offers/{pos_id}",
                "posted_at": posted,
            })
    return jobs


def _jobcloud(base, lang_path, terms):
    """jobs.ch / jobup.ch share the JobCloud public search API (rows max = 20)."""
    jobs, seen = [], set()
    for term in terms:
        page = 1
        while page <= 8:
            q = urllib.parse.urlencode({"query": term, "rows": 20, "page": page})
            d = get_json(f"{base}/api/v1/public/search?{q}")
            docs = d.get("documents") or []
            for doc in docs:
                jid = doc.get("job_id") or doc.get("slug")
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                links = doc.get("_links") or {}
                url = None
                for key in (lang_path, "detail_en", "detail_de", "detail_fr"):
                    if key in links:
                        url = links[key].get("href")
                        break
                if not url:
                    continue
                jobs.append({
                    "title": doc.get("title"),
                    "company_name": doc.get("company_name"),
                    "location": doc.get("place"),
                    "url": url,
                    "posted_at": (doc.get("publication_date") or "")[:10],
                })
            if page >= (d.get("num_pages") or 1):
                break
            page += 1
        time.sleep(0.5)
    return jobs


def scan_jobsch():
    return _jobcloud("https://www.jobs.ch", "detail_en", SEARCH_TERMS)


def scan_jobup():
    return _jobcloud("https://www.jobup.ch", "detail_fr",
                     ["architecte", "architekt", "conducteur de travaux", "urbaniste"])


LINKEDIN_CARD = re.compile(
    r'base-card__full-link[^"]*"\s+href="([^"]+)".*?'
    r'base-search-card__title[^>]*>\s*(.*?)\s*</h3>.*?'
    r'(?:base-search-card__subtitle[^>]*>.*?<a[^>]*>\s*(.*?)\s*</a>|hidden-nested-link[^>]*>\s*(.*?)\s*</a>).*?'
    r'job-search-card__location[^>]*>\s*(.*?)\s*</span>(?:.*?datetime="([^"]+)")?',
    re.S)


def scan_linkedin():
    """LinkedIn public guest job search (no login). Keep volume low."""
    jobs, seen = [], set()
    for term in ["Architekt", "Architect"]:
        for start in (0, 25, 50):
            q = urllib.parse.urlencode({
                "keywords": term, "location": "Switzerland",
                "f_TPR": "r604800",  # posted in last 7 days
                "start": start,
            })
            url = ("https://www.linkedin.com/jobs-guest/jobs/api/"
                   "seeMoreJobPostings/search?" + q)
            try:
                body, _ = http_get(url)
            except Exception:
                break
            found = 0
            for m in LINKEDIN_CARD.finditer(body):
                href = m.group(1).split("?")[0]
                if href in seen:
                    continue
                seen.add(href)
                found += 1
                jobs.append({
                    "title": re.sub("<[^>]+>", "", m.group(2)).strip(),
                    "company_name": re.sub("<[^>]+>", "", (m.group(3) or m.group(4) or "")).strip(),
                    "location": m.group(5).strip(),
                    "url": href,
                    "posted_at": m.group(6) or "",
                })
            if found == 0:
                break
            time.sleep(1.5)
    return jobs


def scan_generic():
    """Extra HTML sources from sources.json: [{name, url, pattern, base}]"""
    path = os.path.join(HERE, "sources.json")
    if not os.path.exists(path):
        return []
    jobs = []
    for src in json.load(open(path, encoding="utf-8")):
        try:
            body, final = http_get(src["url"])
            for m in re.finditer(src["pattern"], body, re.S):
                g = m.groupdict()
                u = g.get("url") or ""
                if u and not u.startswith("http"):
                    u = urllib.parse.urljoin(src.get("base") or final, u)
                title = re.sub("<[^>]+>", " ", g.get("title") or "").strip()
                if not (u and title):
                    continue
                jobs.append({
                    "title": title,
                    "company_name": (g.get("company") or "").strip() or src["name"],
                    "location": (g.get("location") or "").strip() or "Switzerland",
                    "url": u, "posted_at": (g.get("date") or "")[:10],
                    "_source": src["name"],
                })
        except Exception as e:
            print(f"  generic source {src['name']} failed: {e}", flush=True)
    return jobs


def scan_manual():
    """User-maintained manual_jobs.json — permanent manual additions.
    The website's Backup tab exports its localStorage manual jobs in
    exactly this schema for round-tripping into the DB."""
    path = os.path.join(HERE, "manual_jobs.json")
    if not os.path.exists(path):
        return []
    jobs = []
    for j in json.load(open(path, encoding="utf-8")):
        if j.get("url") and j.get("title"):
            jobs.append({
                "title": j["title"], "company_name": j.get("company_name") or "",
                "location": j.get("location") or "", "url": j["url"],
                "posted_at": (j.get("posted_at") or "")[:10],
            })
    return jobs


# --- generic job-link extraction (career pages + user-watched sites) -----

A_TAG = re.compile(r'<a\b[^>]*?href="([^"#]+)[^"]*"[^>]*>(.*?)</a>', re.S | re.I)
NAV_NOISE = re.compile(
    r"^(mehr|weiter|zur[uü]ck|home|start|kontakt|contact|news|aktuell|impressum|"
    r"datenschutz|agb|login|anmelden|projekte?|referenz|b[uü]ro|about|team|"
    r"initiativ|offene stellen|jobs?|karriere|stellen(angebote)?|mehr erfahren|"
    r"alle stellen|read more|en savoir plus|pdf speichern|top job|credits?|"
    r"zu den )\W*", re.I)
# a link only counts as a job posting if its text names a role (or a workload %)
ROLE_WORDS = re.compile(
    r"architekt|architect|architecte|zeichner|bauleit|projektleit|hochbau|"
    r"innenarchitekt|landschaftsarchitekt|raumplan|"
    r"urban|st[aä]dtebau|bim[- ]|techniker|konstrukteur|planer(?:in)?\b|"
    r"dessinateur|conducteur|chef de projet|direktion|gesch[aä]ftsleit",
    re.I)
ATS_HOSTS = ("prospective.ch", "jobbase.io", "refline.ch", "onlyfy.jobs",
             "softgarden.io", "jobs.dvinci.de", "recruitingapp", "umantis.com",
             "abacuscity.ch", "jobcloud.ai")
# never harvest links that point at job boards we scrape directly — the jobs
# would be misattributed to the company whose page links there
BOARD_HOSTS = ("swiss-architects.com", "world-architects.com", "jobs.ch",
               "jobup.ch", "jobagent.ch", "architekten-jobs.ch",
               "emploi-batiment.ch", "hochparterre.ch", "archi-jobs.ch",
               "architekturstellen.ch", "architectes.ch", "ostjob.ch",
               "nzz.ch", "indeed.com", "linkedin.com", "jobscout24.ch")


def _on_hosts(host, hosts):
    return any(host == h or host.endswith("." + h) for h in hosts)


def extract_job_links(body, base_url, company_name, location, arch_only=False):
    """Pull plausible job-posting links out of an HTML page."""
    base_host = urllib.parse.urlparse(base_url).netloc.replace("www.", "")
    jobs, seen = [], set()
    for m in A_TAG.finditer(body):
        href, text = m.group(1).strip(), strip_tags(m.group(2))
        if href.startswith(("mailto:", "javascript:", "tel:")) or not text:
            continue
        full = urllib.parse.urljoin(base_url, href).split("#")[0]
        host = urllib.parse.urlparse(full).netloc.replace("www.", "")
        if _on_hosts(host, BOARD_HOSTS):
            continue
        same_site = host == base_host or host.endswith("." + base_host)
        if not (same_site or _on_hosts(host, ATS_HOSTS) or
                any(a in host for a in ATS_HOSTS)):
            continue
        titleish = (8 <= len(text) <= 110 and " " in text
                    and not NAV_NOISE.match(text))
        role_signal = ROLE_WORDS.search(text) or extract_workload(text)
        if not (titleish and role_signal):
            continue
        if db.normalize_name(text) == db.normalize_name(company_name):
            continue  # link text is just the firm's own name/logo
        if arch_only and not ARCH_KEYWORDS.search(text):
            continue
        if full in seen:
            continue
        seen.add(full)
        jobs.append({
            "title": text, "company_name": company_name, "location": location,
            "url": full, "posted_at": "",
        })
    return jobs


def scan_watchsites():
    """User-added sites from watch_sites.json: [{name, url}] — the website's
    Sources tab exports this file."""
    path = os.path.join(HERE, "watch_sites.json")
    if not os.path.exists(path):
        return []
    jobs = []
    for src in json.load(open(path, encoding="utf-8")):
        if not src.get("url"):
            continue
        try:
            body, final = http_get(src["url"], tries=2, timeout=20)
            found = extract_job_links(body, final, src.get("name") or "", "",
                                      arch_only=True)
            for j in found:
                j["_source"] = "watch"
            jobs.extend(found)
            time.sleep(1)
        except Exception as e:
            print(f"  watch site {src.get('name')} failed: {e}", flush=True)
    return jobs


# board-specific scan functions (built + adversarially verified 2026-07-11);
# they import net, not scraper, so there is no import cycle
from sources.archijobs import scan_archijobs               # noqa: E402
from sources.nzz import scan_nzz, scan_ostjob              # noqa: E402
from sources.architekturstellen import scan_architekturstellen  # noqa: E402
from sources.architectes import scan_architectes           # noqa: E402
from sources.hochparterre import scan_hochparterre         # noqa: E402
from sources.swissarchitects import scan_swissarchitects   # noqa: E402
from sources.jobagent import scan_jobagent                 # noqa: E402
from sources.jobspy_src import scan_indeed_google          # noqa: E402
from sources.jobroom import scan_jobroom                   # noqa: E402
from sources.academia import scan_academia                 # noqa: E402

# scan order: cheap/reliable JSON APIs first, heavier HTML boards after,
# library-driven and user-defined sources last.
# GENERAL_BOARDS get the ARCH_KEYWORDS title filter, specialist boards don't.
SOURCES = {
    "sia": scan_sia,
    "archi-jobs": scan_archijobs,
    "hochparterre": scan_hochparterre,
    "swiss-architects": scan_swissarchitects,
    "architekturstellen": scan_architekturstellen,
    "architectes": scan_architectes,
    "academia": scan_academia,
    "nzz": scan_nzz,
    "ostjob": scan_ostjob,
    "jobroom": scan_jobroom,
    "jobs.ch": scan_jobsch,
    "jobup.ch": scan_jobup,
    "jobagent": scan_jobagent,
    "linkedin": scan_linkedin,
    "indeed-google": scan_indeed_google,
    "watch": scan_watchsites,
    "manual": scan_manual,
    "generic": scan_generic,
}

GENERAL_BOARDS = {"jobs.ch", "jobup.ch", "linkedin", "generic", "nzz", "ostjob",
                  "jobroom", "jobagent", "indeed-google", "watch", "academia"}

# shown on the website's Sources tab; search_url doubles as the manual
# fallback link when a source is blocked or fails
SOURCE_META = {
    "sia": {"label": "SIA Jobs", "home": "https://www.sia.ch/en/jobs/",
            "search_url": "https://www.sia.ch/en/jobs/"},
    "archi-jobs": {"label": "archi-jobs.ch", "home": "https://www.archi-jobs.ch/",
                   "search_url": "https://www.archi-jobs.ch/"},
    "hochparterre": {"label": "Hochparterre Stellenmarkt",
                     "home": "https://www.hochparterre.ch/stellenmarkt",
                     "search_url": "https://www.hochparterre.ch/stellenmarkt"},
    "swiss-architects": {"label": "Swiss-Architects Jobs",
                         "home": "https://www.swiss-architects.com/de/stellenanzeigen",
                         "search_url": "https://www.swiss-architects.com/de/stellenanzeigen"},
    "architekturstellen": {"label": "architekturstellen.ch (HSLU)",
                           "home": "https://www.architekturstellen.ch/",
                           "search_url": "https://www.architekturstellen.ch/"},
    "architectes": {"label": "architectes.ch (Romandie)",
                    "home": "https://www.architectes.ch/fr/annonces-offres-demploi",
                    "search_url": "https://www.architectes.ch/fr/annonces-offres-demploi"},
    "nzz": {"label": "NZZ Jobs", "home": "https://jobs.nzz.ch/",
            "search_url": "https://jobs.nzz.ch/suche?q=Architektur"},
    "ostjob": {"label": "ostjob.ch", "home": "https://www.ostjob.ch/",
               "search_url": "https://www.ostjob.ch/job-suchen?q=Architekt"},
    "jobroom": {"label": "Job-Room (arbeit.swiss / RAV)",
                "home": "https://www.job-room.ch/",
                "search_url": "https://www.job-room.ch/job-search"},
    "academia": {"label": "ETH / EPFL / Hochschulen (research)",
                 "home": "https://www.myscience.ch/jobs",
                 "search_url": "https://www.myscience.ch/jobs?q=architektur"},
    "jobs.ch": {"label": "jobs.ch", "home": "https://www.jobs.ch/",
                "search_url": "https://www.jobs.ch/de/stellenangebote/?term=architekt"},
    "jobup.ch": {"label": "jobup.ch", "home": "https://www.jobup.ch/",
                 "search_url": "https://www.jobup.ch/fr/emplois/?term=architecte"},
    "jobagent": {"label": "jobagent.ch network", "home": "https://www.jobagent.ch/",
                 "search_url": "https://www.jobagent.ch/architektur-jobs"},
    "linkedin": {"label": "LinkedIn", "home": "https://www.linkedin.com/jobs/",
                 "search_url": "https://www.linkedin.com/jobs/search?keywords=Architekt&location=Switzerland"},
    "indeed-google": {"label": "Indeed CH + Google Jobs", "home": "https://ch.indeed.com/",
                      "search_url": "https://ch.indeed.com/jobs?q=architekt&l=Bern"},
    "career-page": {"label": "Company career pages (SIA firms)", "home": "",
                    "search_url": ""},
    "watch": {"label": "Your watched sites", "home": "", "search_url": ""},
    "manual": {"label": "Manually added", "home": "", "search_url": ""},
    "generic": {"label": "sources.json extras", "home": "", "search_url": ""},
}

ARCH_KEYWORDS = re.compile(
    r"architekt|architect|architecte|architetto|hochbau|bauleit|baumanagement|"
    r"innenarchitekt|landschaftsarchitekt|st[aä]dtebau|urban|raumplan|zeichner",
    re.I)

# roles Petras does not want at all: construction-management and engineering
# ads are dropped everywhere unless the title itself also names an architect
EXCLUDE_TITLES = re.compile(r"baumanagement|ingenieur|ing[ée]nieur|engineer|ingegnere", re.I)
ARCH_CORE = re.compile(r"architekt|architect|architecte|architetto", re.I)
# internships/apprenticeships are dropped unconditionally
EXCLUDE_ALWAYS = re.compile(
    r"praktik|stagiaire|\bstage\b|internship|\bintern\b|lehrstell|lernende", re.I)

# research vocabulary that counts as architecture on academic portals only
ACADEMIA_EXTRA = re.compile(
    r"\b(building|bauwesen|baukultur|constructi\w*|habitat|d-arch|gta|"
    r"denkmal|heritage|monument|housing|wohnen|territoir)\w*\b", re.I)

# software/IT roles that contain "architect" — NZZ/Indeed/LinkedIn noise
NON_ARCH = re.compile(
    r"\b(?:it|ict|tech|software|cloud|data|solutions?|systems?|network|"
    r"security|enterprise|application|integration|plat(?:t)?form|informatik|"
    r"digital|sap|azure|aws|devops|bi|web|java|\.net|ml|ai|rf|hardware|"
    r"embedded|firmware)"
    r"[-\s/]*(?:architekt|architect)"
    r"|(?:architekt|architect)\w*\s+(?:\.net|java|cloud|sap|azure|aws)\b",
    re.I)


# --------------------------------------------------------------------------
# Career-page probing (unsolicited applications)
# --------------------------------------------------------------------------
CAREER_HREF = re.compile(
    r'href="([^"]*(?:job|karriere|career|stellen|offene|emploi|lavora|'
    r'initiativ|bewerbung|vacan|team)[^"]*)"', re.I)


def probe_career(company):
    website = (company["website"] or "").strip().rstrip("/")
    if not website.startswith("http"):
        return None
    try:
        body, final = http_get(website, tries=1, timeout=15)
    except Exception:
        return ""
    candidates = []
    for m in CAREER_HREF.finditer(body):
        href = m.group(1)
        if href.startswith(("mailto:", "javascript:", "#")):
            continue
        full = urllib.parse.urljoin(final, href)
        if urllib.parse.urlparse(full).netloc != urllib.parse.urlparse(final).netloc:
            continue
        score = 0
        low = full.lower()
        for kw, s in (("job", 3), ("karriere", 3), ("career", 3), ("offene", 4),
                      ("stellen", 3), ("initiativ", 5), ("emploi", 3), ("vacan", 3),
                      ("bewerbung", 2), ("team", 1)):
            if kw in low:
                score += s
        candidates.append((score, full))
    if not candidates:
        return ""
    candidates.sort(reverse=True)
    return candidates[0][1]


def _ref_point():
    """User's reference location from config.json (default Bern)."""
    hit = geo.lookup_place(CONFIG.get("reference_place", "Bern"))
    return hit or (46.9481, 7.4474, "BE")


def _dist_order_sql(ref):
    """ORDER BY fragment: approximate squared-degree distance to the
    reference point (good enough for ranking; SQLite has no trig)."""
    import math
    coslat = math.cos(math.radians(ref[0]))
    return (f"((lat - {ref[0]}) * (lat - {ref[0]}) + "
            f"(lon - {ref[1]}) * (lon - {ref[1]}) * {coslat * coslat:.4f})")


def run_career_probe(con, limit):
    ref = _ref_point()
    rows = con.execute(f"""
        SELECT id, name, website FROM companies
        WHERE website != '' AND status = 'ok'
        ORDER BY (career_checked_at IS NOT NULL), (lat IS NULL),
                 {_dist_order_sql(ref)}
        LIMIT ?""", (limit,)).fetchall()
    print(f"probing {len(rows)} company sites for career pages...", flush=True)

    def work(row):
        url = probe_career(row)
        return row["id"], url

    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for cid, url in ex.map(work, rows):
            con.execute("UPDATE companies SET career_url=?, career_checked_at=? WHERE id=?",
                        (url or "", now(), cid))
            done += 1
            if done % 25 == 0:
                con.commit()
                print(f"  {done}/{len(rows)}", flush=True)
    con.commit()


def run_career_watch(con, limit, started):
    """Fetch known career pages and extract actual job postings.
    Half the budget goes to the companies nearest the reference point
    (checked every day), half rotates through the least-recently-watched
    rest so every firm gets re-checked within ~2 weeks."""
    ref = _ref_point()
    dist = _dist_order_sql(ref)
    near = con.execute(f"""
        SELECT id, name, career_url, city, plz FROM companies
        WHERE career_url != '' AND career_url IS NOT NULL AND status = 'ok'
        ORDER BY (lat IS NULL), {dist} LIMIT ?""", (limit // 2,)).fetchall()
    have = {r["id"] for r in near}
    rest = [r for r in con.execute(f"""
        SELECT id, name, career_url, city, plz FROM companies
        WHERE career_url != '' AND career_url IS NOT NULL AND status = 'ok'
        ORDER BY (career_watched_at IS NOT NULL), career_watched_at,
                 (lat IS NULL), {dist} LIMIT ?""", (limit,)).fetchall()
            if r["id"] not in have][:limit - len(near)]
    rows = list(near) + rest
    print(f"watching {len(rows)} career pages for job ads...", flush=True)

    def work(row):
        host = urllib.parse.urlparse(row["career_url"]).netloc.replace("www.", "")
        if _on_hosts(host, BOARD_HOSTS):
            return row["id"], [], False  # profile hosted on a board we scrape anyway
        try:
            body, final = http_get(row["career_url"], tries=1, timeout=15)
            loc = " ".join(str(x) for x in (row["plz"], row["city"]) if x)
            return row["id"], extract_job_links(body, final, row["name"], loc), True
        except Exception:
            return row["id"], [], False

    def put(cid, j):
        if EXCLUDE_ALWAYS.search(j["title"]):
            return 0
        hay_x = j["title"] + " " + (j["company_name"] or "")
        if EXCLUDE_TITLES.search(hay_x) and not ARCH_CORE.search(hay_x):
            return 0
        con.execute("""
            INSERT INTO jobs (source, title, company_name, company_id,
                              location, url, posted_at, workload,
                              first_seen, last_seen, active)
            VALUES ('career-page',?,?,?,?,?,?,?,?,?,1)
            ON CONFLICT(url) DO UPDATE SET
                last_seen=excluded.last_seen, active=1
        """, (j["title"], j["company_name"], cid, j["location"],
              j["url"], "", extract_workload(j["title"]), started, started))
        return 1

    row_by_id = {r["id"]: r for r in rows}
    js_candidates = []
    found = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for cid, jobs, fetched in ex.map(work, rows):
            if fetched and not jobs:
                js_candidates.append(cid)
            for j in jobs:
                found += put(cid, j)
            con.execute("UPDATE companies SET career_watched_at=? WHERE id=?",
                        (now(), cid))
    # second pass with a real browser: career pages that render their job
    # list client-side yield zero links to plain HTTP (full cloud scan only)
    if js_candidates and os.environ.get("JS_WATCH"):
        try:
            import js_watch
            pairs = [(cid, row_by_id[cid]["career_url"]) for cid in js_candidates]
            cid_by_url = {}
            for cid, u in pairs:
                cid_by_url.setdefault(u, cid)
            js_found = 0
            for u, final, body in js_watch.fetch_rendered([u for _, u in pairs]):
                row = row_by_id.get(cid_by_url.get(u))
                if row is None:
                    continue
                loc = " ".join(str(x) for x in (row["plz"], row["city"]) if x)
                for j in extract_job_links(body, final, row["name"], loc):
                    js_found += put(row["id"], j)
            found += js_found
            print(f"  career-page js-render: {js_found} extra job links "
                  f"from {len(js_candidates)} script-only pages", flush=True)
        except Exception as e:
            print(f"  career-page js-render skipped: {e}", flush=True)
    con.commit()
    print(f"  career-page: {found} job links", flush=True)
    return found


# --------------------------------------------------------------------------
# Dedup: the same vacancy appears on several boards
# --------------------------------------------------------------------------

# canonical preference: direct/specialist sources beat aggregators
SOURCE_PRIORITY = ["manual", "sia", "archi-jobs", "hochparterre",
                   "swiss-architects", "architekturstellen", "architectes",
                   "career-page", "academia", "nzz", "ostjob", "jobroom", "jobs.ch",
                   "jobup.ch", "jobagent", "linkedin", "indeed-google",
                   "watch", "generic"]


def _prio(source):
    try:
        return SOURCE_PRIORITY.index(source)
    except ValueError:
        return len(SOURCE_PRIORITY)


def dedupe(con):
    """Group active jobs by fingerprint, cluster by <=25 km proximity
    (same title+company in Zurich vs Geneva stays separate), mark all but
    the best-source row with dup_of. Non-destructive and recomputed fully
    on every run."""
    con.execute("UPDATE jobs SET dup_of = NULL")
    groups = {}
    for r in con.execute("""SELECT id, source, fingerprint, lat, lon, first_seen
                            FROM jobs WHERE active=1 AND fingerprint IS NOT NULL
                            AND fingerprint != ''"""):
        groups.setdefault(r["fingerprint"], []).append(r)
    marked = 0
    for rows in groups.values():
        if len(rows) < 2:
            continue
        clusters = []
        for r in rows:
            placed = False
            for cl in clusters:
                anchor = next((c for c in cl if c["lat"] is not None), None)
                if (r["lat"] is None or anchor is None or
                        geo.haversine_km(r["lat"], r["lon"],
                                         anchor["lat"], anchor["lon"]) <= 25):
                    cl.append(r)
                    placed = True
                    break
            if not placed:
                clusters.append([r])
        for cl in clusters:
            if len(cl) < 2:
                continue
            cl.sort(key=lambda r: (_prio(r["source"]), r["first_seen"]))
            for r in cl[1:]:
                con.execute("UPDATE jobs SET dup_of=? WHERE id=?",
                            (cl[0]["id"], r["id"]))
                marked += 1
    con.commit()
    return marked


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def match_companies(con):
    """Link jobs to SIA companies by normalized name."""
    comp = {}
    for r in con.execute("SELECT id, name FROM companies"):
        comp.setdefault(db.normalize_name(r["name"]), r["id"])
    n = 0
    for j in con.execute("SELECT id, company_name FROM jobs WHERE company_id IS NULL"):
        norm = db.normalize_name(j["company_name"])
        cid = comp.get(norm)
        if cid:
            con.execute("UPDATE jobs SET company_id=? WHERE id=?", (cid, j["id"]))
            n += 1
    con.commit()
    return n


def make_fingerprint(company_name, title):
    """Dedup key; NULL when the job can't be attributed safely."""
    nc = db.normalize_name(company_name)
    nt = db.normalize_title(title)
    if nc and len(nt.split()) >= 2:
        return nc + "|" + nt
    return None


def insert_job(con, src, j, started):
    lat = j.get("lat")
    lon = j.get("lon")
    canton = j.get("canton")
    workload = j.get("workload") or extract_workload(j.get("title"))
    fp = make_fingerprint(j.get("company_name"), j.get("title"))
    con.execute("""
        INSERT INTO jobs (source, title, company_name, location, url, posted_at,
                          first_seen, last_seen, active,
                          lat, lon, canton, workload, fingerprint)
        VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?,?)
        ON CONFLICT(url) DO UPDATE SET
            last_seen=excluded.last_seen, active=1,
            title=excluded.title,
            workload=excluded.workload,
            fingerprint=excluded.fingerprint,
            lat=COALESCE(excluded.lat, jobs.lat),
            lon=COALESCE(excluded.lon, jobs.lon),
            canton=COALESCE(excluded.canton, jobs.canton)
    """, (src, j["title"], j.get("company_name"), j.get("location"),
          j["url"], j.get("posted_at"), started, started,
          lat, lon, canton, workload, fp))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="only run one source", default=None)
    ap.add_argument("--careers", type=int, default=0,
                    help="probe N company websites for career pages")
    ap.add_argument("--career-watch", type=int, default=0,
                    help="check N known career pages for actual job ads")
    ap.add_argument("--no-export", action="store_true",
                    help="skip regenerating site/data.js")
    ap.add_argument("--arch-only", action="store_true", default=True,
                    help="keep only architecture-related titles for general boards")
    args = ap.parse_args()

    db.init()
    con = db.connect()
    started = now()
    stats = {}

    for name, fn in SOURCES.items():
        if args.source and args.source != name:
            continue
        try:
            print(f"scanning {name}...", flush=True)
            jobs = fn()
            kept = 0
            for j in jobs:
                title = j.get("title") or ""
                if name != "manual":
                    if EXCLUDE_ALWAYS.search(title):
                        continue  # internships/apprenticeships
                    hay_x = title + " " + (j.get("company_name") or "")
                    if EXCLUDE_TITLES.search(hay_x) and not ARCH_CORE.search(hay_x):
                        continue  # Baumanagement/engineering ads & firms
                if name in GENERAL_BOARDS and args.arch_only:
                    hay = title + " " + (j.get("company_name") or "")
                    keyword_ok = ARCH_KEYWORDS.search(hay) or \
                        (name == "academia" and ACADEMIA_EXTRA.search(hay))
                    if not keyword_ok:
                        continue
                    if NON_ARCH.search(title):
                        continue  # IT/software "architect" roles
                src = j.pop("_source", name)
                insert_job(con, src, j, started)
                kept += 1
            stats[name] = kept
            print(f"  {name}: {kept} jobs", flush=True)
            con.commit()
        except Exception as e:
            msg = str(e)
            status = "BLOCKED" if "403" in msg else "ERROR"
            stats[name] = f"{status}: {msg}"
            print(f"  {name} FAILED ({status}): {msg}", flush=True)

    if args.careers:
        run_career_probe(con, args.careers)
    if args.career_watch:
        stats["career-page"] = run_career_watch(con, args.career_watch, started)

    # deactivate postings not seen for 14 days
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=14)).isoformat()
    con.execute("UPDATE jobs SET active=0 WHERE last_seen < ?", (cutoff,))

    # match first so backfill can inherit coordinates from matched firms
    stats["matched_to_companies"] = match_companies(con)
    jn, cn = geo.backfill(con)
    stats["geocoded"] = f"{jn} jobs, {cn} companies"
    stats["duplicates_marked"] = dedupe(con)

    con.execute("INSERT INTO runs (started, finished, source_stats, ok) VALUES (?,?,?,1)",
                (started, now(), json.dumps(stats)))
    con.commit()

    total = con.execute("SELECT COUNT(*) c FROM jobs WHERE active=1").fetchone()["c"]
    print(f"\nDONE {json.dumps(stats)} | active jobs in DB: {total}", flush=True)

    if not args.no_export:
        import export_site
        export_site.export(con)
    con.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
