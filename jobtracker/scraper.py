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
from net import (UA, http_get, http_get_cond, get_json, post_json,  # noqa: F401
                 strip_tags, extract_workload)
from jobfilter import is_non_building

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

# quote-agnostic: single-quoted and unquoted hrefs occur in the wild
# (three alternatives keep apostrophes inside double-quoted hrefs intact)
A_TAG = re.compile(
    r'<a\b[^>]*?href\s*=\s*(?:"([^"#][^"]*)"|\'([^\'#][^\']*)\'|'
    r'([^"\'\s>#][^\s>]*))[^>]*>(.*?)</a>',
    re.S | re.I)


def _href_of(m):
    return (m.group(1) or m.group(2) or m.group(3) or "").strip()
BASE_TAG = re.compile(r'<base\b[^>]*?href\s*=\s*["\']?([^"\'\s>]+)', re.I)
HEADING = re.compile(r'<(h[1-4])\b[^>]*>(.*?)</\1>', re.S | re.I)
# anchorless postings live in headings/accordions/buttons/href-less <a>
SECTION_TAG = re.compile(
    r'<(h[1-4]|summary|strong|button|a)\b[^>]*>(.*?)</\1>', re.S | re.I)
DATA_HREF = re.compile(r'data-(?:rel|href)\s*=\s*["\']([^"\']+)["\']', re.I)
NAV_NOISE = re.compile(
    r"^(mehr|weiter|zur[uü]ck|home|start|kontakt|contact|news|aktuell|impressum|"
    r"datenschutz|agb|login|anmelden|projekte?|referenz|b[uü]ro|about|team|"
    r"initiativ|offene stellen|jobs?|karriere|stellen(angebote)?\b|mehr erfahren|"
    r"alle stellen|read more|en savoir plus|pdf speichern|top job|credits?|"
    r"leggi tutto|fiche du poste|postuler|jetzt bewerben|anzeigen\b|"
    r"zu den )\W*", re.I)
# a link only counts as a job posting if its text names a role (or a workload %)
ROLE_WORDS = re.compile(
    r"architekt|architect|architecte|architett[oi]\b|zeichner|bauleit|projektleit|"
    r"hochbau|innenarchitekt|landschaftsarchitekt|raumplan|"
    r"urban|st[aä]dtebau|bim[- ]|techniker|technicien|konstrukteur|planer(?:in)?\b|"
    r"planificat(?:eur|rice)|dessinateur|conducteur|"
    r"chef[\s.]*\(?fe\)?[\s.]*de projet|chef de projet|direktion|"
    r"gesch[aä]ftsleit|directeur|directrice|deviseur|"
    r"kostenplan|sachbearbeit|stelleninserat|koordinat|coordinat|"
    r"disegnat|capo\s*progetto|collaborat|direzione lavori",
    re.I)
# these ROLE_WORDS also match nav/project noise (URBAN HOUSE, Collaborateurs);
# they only count together with a workload % or a gender tag
WEAK_ROLE = ("urban", "collaborat")
# grammatical gender tags mark real ads: (m/w/d), /in, :in, *in, ProjektleiterIn
# (case-sensitive: a case-insensitive In\b would match every word ending "in")
GENDER_TAG = re.compile(
    r"\((?:[mwfdhxMWFDHX]\s*/\s*)+[mwfdhxMWFDHX]\)|\*in(?:nen)?\b|"
    r":in(?:nen)?\b|/-?in\b|/-?IN\b|(?<=[a-zäöü])In(?:nen)?\b")
# service-nav / journal / news links that ROLE_WORDS would take for postings
BAD_PATH = re.compile(
    r"/dienstleistung|/leistung|/angebot|/journal|/news|/blog|/publikation|"
    r"/wp-json/", re.I)
# blog-permalink date paths (/2018/11/28/...) are junk ONLY when the title
# carries no job signal — small firms do post real ads as dated blog posts,
# and WP upload paths (/uploads/2026/07/inserat.pdf) hold real ad PDFs
BLOG_DATE = re.compile(r"/20\d\d/\d{1,2}/\d{1,2}/")
# a dated post is a job when it names a concrete role or seeks someone —
# a bare "architektur" mention (award/exhibition posts) is not enough
BLOG_JOB_SIGNAL = re.compile(
    r"projektleit|bauleit|zeichner|dessinat|konstrukteur|techniker|"
    r"technicien|architekt\w*\s*[:/*·]?\s*-?in\b|architecte|gesucht|"
    r"wir suchen|hiring|\bstelle\b|vacan|praktik", re.I)
SERVICE_TITLE = re.compile(
    r"^(architektur|architecture|architettura|planung|leistungen|prestations|"
    r"st[aä]dtebau|siedlungsbau|hochbau|tiefbau|umbau|neubau|sanierung|"
    r"wohnungsbau|immobilien|projekte?|referenzen|urbanisme|wettbewerbe?)\b",
    re.I)
# href targets that are never job ads even when the filename names a role
DOC_NOISE = re.compile(
    r"datenschutz|impressum|\bagb\b|privacy|cookie|zertifikat|brosch|leitbild|"
    r"portrait|jahresbericht|geschaeftsbericht|firmen", re.I)
MEDIA_EXT = re.compile(r"\.(png|jpe?g|gif|svg|webp|mp4|webm|mp3|zip)$", re.I)
ATS_HOSTS = ("prospective.ch", "jobbase.io", "refline.ch", "onlyfy.jobs",
             "softgarden.io", "jobs.dvinci.de", "recruitingapp", "umantis.com",
             "abacuscity.ch", "jobcloud.ai", "recruitee.com")
# never harvest links that point at job boards we scrape directly — the jobs
# would be misattributed to the company whose page links there
BOARD_HOSTS = ("swiss-architects.com", "world-architects.com", "jobs.ch",
               "jobup.ch", "jobagent.ch", "architekten-jobs.ch",
               "emploi-batiment.ch", "hochparterre.ch", "archi-jobs.ch",
               "architekturstellen.ch", "architectes.ch", "ostjob.ch",
               "nzz.ch", "indeed.com", "linkedin.com", "jobscout24.ch")


def _on_hosts(host, hosts):
    return any(host == h or host.endswith("." + h) for h in hosts)


def _page_base(body, final):
    """urljoin base for a page: honor <base href> (else doubled paths 404)."""
    m = BASE_TAG.search(body[:4096])
    return urllib.parse.urljoin(final, m.group(1)) if m else final


def _slug(text):
    """Stable ascii slug for synthetic fragment URLs (re-runs must UPSERT)."""
    s = text.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("é", "e"), ("è", "e"),
                 ("ê", "e"), ("à", "a"), ("â", "a"), ("ç", "c"), ("ß", "ss")):
        s = s.replace(a, b)
    s = s.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60].strip("-")


def _role_grade(text):
    """2 = strong role word, 1 = weak-only (WEAK_ROLE), 0 = none."""
    g = 0
    for m in ROLE_WORDS.finditer(text):
        if any(m.group(0).lower().startswith(w) for w in WEAK_ROLE):
            g = max(g, 1)
        else:
            return 2
    return g


def _strong_signal(text):
    return bool(extract_workload(text) or GENDER_TAG.search(text))


def extract_job_links(body, base_url, company_name, location, arch_only=False,
                      career_page=False):
    """Pull plausible job-posting links out of an HTML page. career_page=True
    additionally harvests anchorless postings (headings/accordions), generic
    anchors whose role word hides in the href slug, and data-rel/href blocks."""
    base = _page_base(body, base_url)
    base_host = urllib.parse.urlparse(base_url).netloc.replace("www.", "")
    page_url = base_url.split("#")[0]
    own = db.normalize_name(company_name)
    heads = [(h.start(), strip_tags(h.group(2))) for h in HEADING.finditer(body)]

    def bogus(title, url):
        path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
        if BAD_PATH.search(path):
            return True  # services/journal/news nav, not a posting
        if (BLOG_DATE.search(path) and not (BLOG_JOB_SIGNAL.search(title)
                                            or _strong_signal(title))
                and not CAREER_WORD.search(path)
                and "inserat" not in path.lower()):
            return True  # dated blog post without any job signal
        if (SERVICE_TITLE.match(title) and not _strong_signal(title)
                and not CAREER_WORD.search(path)
                and "inserat" not in path.lower()):
            return True  # bare category title outside any career context
        nt = db.normalize_name(title)
        if own and len(own) >= 6 and nt:
            if nt in own:
                return True  # link text is just the firm's own name/logo
            if own in nt:  # firm name + trimmings ("Architetto X - Lugano")
                rest = nt.replace(own, " ")
                if not (ROLE_WORDS.search(rest) or extract_workload(rest)):
                    return True
        return False

    def slug_title(url, pos):
        # role word hides in the href slug / PDF filename; title comes from
        # the nearest preceding heading, else the prettified slug itself
        path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
        if MEDIA_EXT.search(path) or DOC_NOISE.search(path):
            return ""  # videos/images/legal docs, not ads
        leaf = re.sub(r"\.(pdf|docx?)$", "", path.rstrip("/").rsplit("/", 1)[-1],
                      flags=re.I)
        words = re.sub(r"^[\d\s.]+", "", re.sub(r"[-_+]+", " ", leaf)).strip()
        if _role_grade(words) < 2:  # strong role word required (precision)
            return ""
        if len(words.split()) < 2 and not path.lower().endswith(".pdf"):
            return ""  # single-word slugs (/bauleitung) are service pages
        nw = db.normalize_name(words)
        if own and len(own) >= 6 and own in nw:
            rest = nw.replace(own, " ")  # firm-name document, not an ad
            if not ROLE_WORDS.search(rest):
                return ""
        prev = ""
        for hp, ht in heads:
            if hp > pos:
                break
            prev = ht
        if prev and _role_grade(prev) and 5 <= len(prev) <= 110:
            return prev
        m = ROLE_WORDS.search(words)  # drop noise prefixes ("V3 Newis ...")
        if m and (m.start() == 0 or words[m.start() - 1] == " "):
            words = words[m.start():]
        return words[:1].upper() + words[1:]

    best = {}  # url -> (strength, title); role-worded text beats slug titles

    def offer(url, title, strength):
        if url not in best or strength > best[url][0]:
            best[url] = (strength, title)

    for m in A_TAG.finditer(body):
        href, text = _href_of(m), strip_tags(m.group(4))
        if href.startswith(("mailto:", "javascript:", "tel:")):
            continue
        full = urllib.parse.urljoin(base, href).split("#")[0]
        host = urllib.parse.urlparse(full).netloc.replace("www.", "")
        if _on_hosts(host, BOARD_HOSTS):
            continue
        same_site = host == base_host or host.endswith("." + base_host)
        if not (same_site or _on_hosts(host, ATS_HOSTS) or
                any(a in host for a in ATS_HOSTS)):
            continue
        if len(text) > 110:  # card anchor wrapping img+heading+description
            hm = re.search(r"<h[1-3]\b[^>]*>(.*?)</h[1-3]>", m.group(4),
                           re.S | re.I)
            text = (strip_tags(hm.group(1)) if hm else
                    re.split(r"\s+[-–—|]\s+", text, maxsplit=1)[0].strip())
        grade = _role_grade(text)
        titleish = (8 <= len(text) <= 110 and " " in text
                    and not NAV_NOISE.match(text))
        ok = titleish and (grade == 2 or
                           (grade == 1 and _strong_signal(text)) or
                           (grade == 0 and extract_workload(text)))
        if ok and grade and not _strong_signal(text):
            # role-worded text without workload/gender tag: could be a
            # team/service nav link — require a job-ish target to keep it
            pr = urllib.parse.urlparse(full)
            leaf = re.sub(r"[-_+]+", " ",
                          urllib.parse.unquote(pr.path).rstrip("/").rsplit("/", 1)[-1])
            ok = bool(CAREER_WORD.search(pr.path)
                      or pr.path.lower().endswith((".pdf", ".doc", ".docx"))
                      or ROLE_WORDS.search(leaf)
                      or _on_hosts(host, ATS_HOSTS))
        if ok:
            offer(full, text, 3 if grade == 2 else 2)
        elif career_page:  # empty/generic/noise anchor: try the href slug
            t = slug_title(full, m.start())
            if t:
                offer(full, t, 1)

    if career_page:  # postings whose URL sits in data-rel/data-href, no <a>
        for m in DATA_HREF.finditer(body):
            full = urllib.parse.urljoin(base, m.group(1)).split("#")[0]
            host = urllib.parse.urlparse(full).netloc.replace("www.", "")
            if not (host == base_host or host.endswith("." + base_host)):
                continue
            hm = re.search(r"<(h[1-4]|strong)\b[^>]*>(.*?)</\1>",
                           body[m.end():m.end() + 800], re.S | re.I)
            t = strip_tags(hm.group(2)) if hm else ""
            if t and _role_grade(t) == 2 and 5 <= len(t) <= 110:
                offer(full, t, 2)

    jobs, seen_titles = [], set()

    def dup_title(key):
        # "Bauleiter/in Tiefbau" vs "Bauleiter/in Tiefbau  Zürich" = same ad
        # (prefix rule only for long keys, else "architekt" eats everything)
        return key in seen_titles or any(
            (k.startswith(key) and len(key) >= 12) or
            (key.startswith(k) and len(k) >= 12) for k in seen_titles)

    def clean(t):
        return " ".join(t.split()).strip(" -–—|·")[:110].strip()

    for url, (strength, title) in best.items():
        title = clean(title)
        if title and _role_grade(title) == 0:
            # workload-only anchor ("80 - 100% in Zürich"): name it by slug
            leaf = re.sub(r"\.(pdf|docx?)$", "", urllib.parse.unquote(
                urllib.parse.urlparse(url).path).rstrip("/").rsplit("/", 1)[-1],
                flags=re.I)
            leaf = re.sub(r"[-_+]+", " ", leaf).strip()
            if _role_grade(leaf) == 2 and len(leaf) <= 60:
                title = clean(leaf[:1].upper() + leaf[1:] + " " + title)
        key = db.normalize_title(title)
        if not title or not key or dup_title(key) or bogus(title, url):
            continue
        if arch_only and not ARCH_KEYWORDS.search(title):
            continue
        seen_titles.add(key)
        jobs.append({"title": title, "company_name": company_name,
                     "location": location, "url": url, "posted_at": ""})

    if career_page and len(jobs) < 2:
        # anchorless postings: titles in h1-h4/summary/strong/button/<a>;
        # role word + workload/gender tag required together (precision) —
        # except real headings on explicitly job-ish paths (/jobs, /emploi):
        # there a strong role word alone counts ("architecte chef de projet")
        jobish = bool(CAREER_SEG.search(
            urllib.parse.urlparse(page_url).path.lower()))
        for m in SECTION_TAG.finditer(body):
            t = clean(strip_tags(m.group(2)))
            if not (5 <= len(t) <= 110):
                continue
            g = _role_grade(t)
            relaxed = (g == 2 and jobish and not NAV_NOISE.match(t)
                       and m.group(1).lower() not in ("strong", "a")
                       and len(t.split()) <= 7 and "?" not in t)  # not a teaser
            if not ((g and _strong_signal(t)) or relaxed):
                continue
            key = db.normalize_title(t)
            if not key or dup_title(key) or bogus(t, page_url):
                continue
            if arch_only and not ARCH_KEYWORDS.search(t):
                continue
            seen_titles.add(key)
            jobs.append({"title": t, "company_name": company_name,
                         "location": location,
                         "url": page_url + "#" + _slug(t), "posted_at": ""})
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


PUBLICJOBS_LIST = "https://publicjobs.ch/jobs/architektur-bauwesen"
PUBLICJOBS_ITEM = re.compile(  # listing cards: <a href="/jobs/..." title="...">
    r'<a href="(/jobs/[a-z-]+/[a-z-]+/[^"]+)"[^>]*\btitle="([^"]+)"')
PUBLICJOBS_CANTON = re.compile(r"^/jobs/([a-z-]+)/")
# Stadt/Kanton Bern job portals are client-side apps: only harvestable where
# playwright is installed (local scans + the 05:30 cloud full scan)
BERN_PORTALS = [
    ("Stadt Bern", "https://jobs.bern.ch/?sort=-startDate"),
    ("Kanton Bern", "https://www.jobs.sites.be.ch/de/start.html"),
]


def scan_publicsector():
    """Public-sector positions: publicjobs.ch architektur-bauwesen category
    (server-rendered, JSON-LD items) + the Stadt/Kanton Bern portals.
    Hochbauamt, Denkmalpflege and Stadtplanung architect jobs often appear
    here and nowhere else."""
    jobs, seen = [], set()
    for page in range(1, 6):
        body, _ = http_get(f"{PUBLICJOBS_LIST}?page={page}", tries=2, timeout=25)
        new = 0
        for m in PUBLICJOBS_ITEM.finditer(body):
            path = m.group(1)
            if path in seen:
                continue
            seen.add(path)
            new += 1
            title = strip_tags(m.group(2)).strip()  # unescape &amp; etc.
            c = PUBLICJOBS_CANTON.search(path)
            loc = c.group(1).replace("-", " ").title() if c else ""
            jobs.append({"title": title, "company_name": "", "location": loc,
                         "url": "https://publicjobs.ch" + path, "posted_at": ""})
        if not new:
            break
        time.sleep(0.5)
    try:
        import js_watch
        for name, url in BERN_PORTALS:
            for u, final, rendered in js_watch.fetch_rendered([url], cap=1):
                found = extract_job_links(rendered, final, name, "Bern",
                                          arch_only=True)
                for j in found:
                    j["company_name"] = name
                jobs.extend(found)
    except Exception as e:
        print(f"  publicsector: Bern portals skipped ({e})", flush=True)
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
    "publicsector": scan_publicsector,
    "watch": scan_watchsites,
    "manual": scan_manual,
    "generic": scan_generic,
}

GENERAL_BOARDS = {"jobs.ch", "jobup.ch", "linkedin", "generic", "nzz", "ostjob",
                  "jobroom", "jobagent", "indeed-google", "watch", "academia",
                  "publicsector"}

# shown on the website's Sources tab; search_url doubles as the manual
# fallback link when a source is blocked or fails
SOURCE_META = {
    "sia": {"label": "SIA Jobs", "home": "https://www.sia.ch/en/jobs/",
            "search_url": "https://www.sia.ch/en/jobs/"},
    "archi-jobs": {"label": "archi-jobs.ch", "home": "https://www.archi-jobs.ch/",
                   "search_url": "https://www.archi-jobs.ch/"},
    "publicsector": {"label": "Öffentliche Hand (publicjobs/Bern)",
                     "home": "https://publicjobs.ch/jobs/architektur-bauwesen",
                     "search_url": "https://publicjobs.ch/jobs/architektur-bauwesen"},
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

# non-building "architect" roles (software/IT/business/landscape/...) are
# filtered via jobfilter.is_non_building — one rule shared with the site
# exporter and the notifier so all three agree on "architecture = buildings"


# --------------------------------------------------------------------------
# Career-page probing (unsolicited applications)
# --------------------------------------------------------------------------
HREF_ATTR = re.compile(r'href\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^"\'\s>]+))',
                       re.I)
CAREER_HREF = re.compile(  # searched against the href VALUE, not the markup
    r'job|karriere|carri[eè]re|career|stellen|offene|emploi|rejoindre|'
    r'postes?\b|posti|lavora|collabor|initiativ|bewerbung|vacan|vakanz|'
    r'recrut|recruit|team|b[uü]ro|buero|bureau|office|agence', re.I)


def _career_hrefs(body):
    """Yield (match, href) for every link whose href names a career word."""
    for m in HREF_ATTR.finditer(body):
        href = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if href and CAREER_HREF.search(href):
            yield m, href
CAREER_SCORE = (("job", 3), ("karriere", 3), ("career", 3), ("carri", 3),
                ("offene", 4), ("stellen", 3), ("emploi", 3), ("vacan", 3),
                ("vakanz", 3), ("lavora", 3), ("recrut", 2), ("recruit", 2),
                ("initiativ", 2), ("bewerbung", 2), ("team", 1), ("buero", 1),
                ("b%c3%bcro", 1), ("bureau", 1), ("office", 1), ("agence", 1))
CAREER_SEG = re.compile(
    r"\b(jobs?|stellen|emplois?|karriere|carri[eè]res?|careers?|vakanzen|lavora)\b")
BAD_CAREER_PATH = re.compile(r"/journal|/news|/blog|/wp-json")
# path guesses when the homepage links no career page (max ~8 requests/site)
CAREER_GUESSES = ("/jobs", "/jobs/", "/karriere", "/stellen", "/offene-stellen",
                  "/emploi", "/emplois", "/carriere", "/carrieres",
                  "/offres-emploi", "/lavora-con-noi", "/vakanzen", "/team")
CAREER_WORD = re.compile(
    r"stellen|karriere|job|emploi|carri[eè]re|career|lavora|vacan|vakanz|"
    r"bewerb|postul|recrut", re.I)


def _career_fallback(final):
    """No usable career link on the homepage: try /sitemap.xml, then guessed
    paths. Soft-404 guard: the final path must still look job-ish."""
    p = urllib.parse.urlparse(final)
    root = f"{p.scheme}://{p.netloc}"
    try:
        sm, _ = http_get(root + "/sitemap.xml", tries=1, timeout=10)
        hits = [u for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sm)
                if re.search(r"job|stellen|emploi|karriere|lavora|vakanz|vacan",
                             u, re.I)
                and not u.lower().endswith(".xml")
                and not BAD_CAREER_PATH.search(urllib.parse.urlparse(u).path.lower())]
        if hits:
            return min(hits, key=len)
    except Exception:
        pass
    for path in CAREER_GUESSES[:8]:
        try:
            b, f = http_get(root + path, tries=1, timeout=10)
        except Exception:
            continue
        if CAREER_WORD.search(urllib.parse.urlparse(f).path) and CAREER_WORD.search(b):
            return f
    return ""


def _career_candidates(body, final):
    """Score the career-page candidates linked from a page, best first.
    Shared by the static probe and the Playwright re-probe."""
    base = _page_base(body, final)
    base_host = urllib.parse.urlparse(final).netloc.replace("www.", "")
    candidates = []
    for _, href in _career_hrefs(body):
        if href.startswith(("mailto:", "javascript:", "#")):
            continue
        full = urllib.parse.urljoin(base, href).split("#")[0]
        pr = urllib.parse.urlparse(full)
        host = pr.netloc.replace("www.", "")
        ats = _on_hosts(host, ATS_HOSTS) or any(a in host for a in ATS_HOSTS)
        if not (ats or host == base_host or host.endswith("." + base_host)):
            continue
        low, path = full.lower(), pr.path.lower()
        score = sum(s for kw, s in CAREER_SCORE if kw in low)
        if not any(kw in path for kw, _ in CAREER_SCORE):
            score -= 3  # keyword only in the query string
        if BAD_CAREER_PATH.search(path):
            score -= 3
        if "initiativbewerbung" in low or "spontanbewerbung" in low:
            score -= 3  # unsolicited-application page: listing pages beat it
        if ats:
            score += 2
        seg = 1 if CAREER_SEG.search(path) else 0
        candidates.append((score, seg, -len(full), full))
    candidates.sort(reverse=True)
    return candidates


def probe_career(company):
    website = (company["website"] or "").strip().rstrip("/")
    if not website.startswith("http"):
        return None
    try:
        body, final = http_get(website, tries=1, timeout=15)
    except Exception:
        return ""
    candidates = _career_candidates(body, final)
    few = len(A_TAG.findall(body)) < 5  # JS-rendered nav: barely any anchors
    if candidates and (candidates[0][0] >= 3 or
                       (candidates[0][0] >= 1 and not few)):
        return candidates[0][3]
    return _career_fallback(final) or (candidates[0][3] if candidates else "")


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


def run_js_probe(con, limit):
    """Playwright pass over sites where the static probe found no career
    page: render the homepage, pick up career links that only exist after
    client-side JS (Wix, script-only navs), store the find in career_url.
    Rotates via career_js_probed_at; real offices near the reference first."""
    ref = _ref_point()
    rows = con.execute(f"""
        SELECT id, name, website FROM companies
        WHERE status = 'ok' AND website != '' AND website IS NOT NULL
          AND (career_url IS NULL OR career_url = '')
        ORDER BY (career_js_probed_at IS NOT NULL), career_js_probed_at,
                 (rating IS NULL OR rating < 4), (lat IS NULL),
                 {_dist_order_sql(ref)}
        LIMIT ?""", (limit,)).fetchall()
    if not rows:
        return
    print(f"js-probing {len(rows)} sites for hidden career pages...", flush=True)
    urls, by_url = [], {}
    for r in rows:
        u = r["website"].strip()
        if not u.startswith("http"):
            u = "https://" + u
        urls.append(u)
        by_url.setdefault(u, r)
    try:
        import js_watch
        rendered = js_watch.fetch_rendered(urls, cap=limit)
    except Exception as e:
        print(f"  js-probe skipped: {e}", flush=True)
        return
    got = {u: (f, b) for u, f, b in rendered}
    stamp, found = now(), 0
    for u, r in by_url.items():
        con.execute("UPDATE companies SET career_js_probed_at=? WHERE id=?",
                    (stamp, r["id"]))
        if u not in got:
            continue
        final, body = got[u]
        cands = _career_candidates(body, final)
        few = len(A_TAG.findall(body)) < 5
        if cands and (cands[0][0] >= 3 or (cands[0][0] >= 1 and not few)):
            con.execute("""UPDATE companies SET career_url=?, career_checked_at=?
                           WHERE id=?""", (cands[0][3], stamp, r["id"]))
            found += 1
    con.commit()
    print(f"  js-probe: {found} new career pages from {len(rendered)} "
          f"rendered sites", flush=True)


SUB_SKIP = re.compile(
    r"/wp-json/|oembed|\.(png|jpe?g|gif|svg|mp4|zip|pdf|docx?)([?#]|$)", re.I)
MORE_JOBS = re.compile(
    r"offene\s+stellen|stellenangebote|postes?\s+(?:ouverts|vacants)|"
    r"offres\s+d.emplois?|posizioni\s+aperte|open\s+positions", re.I)


def _career_sublinks(body, final):
    """Depth-2 candidates: skip wp-json/binaries, prefer job words in the
    path over query/nav hits, allow 3 when the page promises open positions."""
    base = _page_base(body, final)
    base_host = urllib.parse.urlparse(final).netloc.replace("www.", "")
    cands, seen = [], {final.split("#")[0]}
    for _, href in _career_hrefs(body):
        u = urllib.parse.urljoin(base, href).split("#")[0]
        pr = urllib.parse.urlparse(u)
        h = pr.netloc.replace("www.", "")
        if (u in seen or SUB_SKIP.search(u)
                or not (h == base_host or h.endswith("." + base_host))):
            continue
        seen.add(u)
        cands.append((0 if CAREER_SEG.search(pr.path.lower()) else 1,
                      len(cands), u))
    cands.sort()
    return [u for _, _, u in cands[:3 if MORE_JOBS.search(body) else 2]]


def harvest_career(url, name, loc, body=None, final=None):
    """Fetch one career page (+depth-2 sublinks when it yields nothing) and
    extract postings. Shared by run_career_watch and offline verification.
    Pass body/final to reuse an already-downloaded page."""
    if body is None:
        body, final = http_get(url, tries=1, timeout=15)
    jobs = extract_job_links(body, final, name, loc, career_page=True)
    if not jobs:
        for u in _career_sublinks(body, final):
            try:
                b2, f2 = http_get(u, tries=1, timeout=15)
                jobs += extract_job_links(b2, f2, name, loc, career_page=True)
            except Exception:
                pass
    return jobs


def career_accept(j):
    """put()-level filters: internships (also by URL slug), engineering-only
    titles and non-building 'architect' roles are dropped."""
    slug = urllib.parse.unquote(urllib.parse.urlparse(j["url"]).path)
    if EXCLUDE_ALWAYS.search(j["title"] + " " + slug):
        return False
    if is_non_building(j["title"]):
        return False
    hay = j["title"] + " " + (j.get("company_name") or "")
    return not (EXCLUDE_TITLES.search(hay) and not ARCH_CORE.search(hay))


def run_career_watch(con, limit, started):
    """Fetch known career pages and extract actual job postings.
    Half the budget goes to the companies nearest the reference point
    (checked every day), half rotates through the least-recently-watched
    rest so every firm gets re-checked within ~2 weeks."""
    ref = _ref_point()
    dist = _dist_order_sql(ref)
    # watch the detected career page; for real architecture offices (rating>=4)
    # without one, watch the homepage — small Swiss firms announce jobs there
    where = """status = 'ok' AND (
                 (career_url IS NOT NULL AND career_url != '')
                 OR (rating >= 4 AND website IS NOT NULL AND website != ''))"""
    fields = """id, name, city, plz, career_etag, career_last_mod,
                COALESCE(NULLIF(career_url,''), website) AS watch_url,
                (career_url IS NULL OR career_url = '') AS is_home"""
    near = con.execute(f"""
        SELECT {fields} FROM companies WHERE {where}
        ORDER BY (won_at IS NULL OR won_at < datetime('now', '-90 day')),
                 (lat IS NULL), {dist} LIMIT ?""", (limit // 2,)).fetchall()
    have = {r["id"] for r in near}
    rest = [r for r in con.execute(f"""
        SELECT {fields} FROM companies WHERE {where}
        ORDER BY (career_watched_at IS NOT NULL), career_watched_at,
                 (lat IS NULL), {dist} LIMIT ?""", (limit,)).fetchall()
            if r["id"] not in have][:limit - len(near)]
    rows = list(near) + rest
    print(f"watching {len(rows)} career pages for job ads...", flush=True)

    def work(row):
        host = urllib.parse.urlparse(row["watch_url"]).netloc.replace("www.", "")
        if _on_hosts(host, BOARD_HOSTS):
            return row["id"], [], False, None, None  # board profile, scraped anyway
        try:
            loc = " ".join(str(x) for x in (row["plz"], row["city"]) if x)
            body, final, etag, lm, changed = http_get_cond(
                row["watch_url"], row["career_etag"], row["career_last_mod"])
            if not changed:
                return row["id"], [], "unchanged", etag, lm
            jobs = harvest_career(row["watch_url"], row["name"], loc,
                                  body=body, final=final)
            return row["id"], jobs, True, etag, lm
        except Exception:
            return row["id"], [], False, None, None

    def put(cid, j):
        if not career_accept(j):
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
    unchanged = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for cid, jobs, status, etag, lm in ex.map(work, rows):
            # only detected career pages go to the JS-render pass; a homepage
            # with no job links is the normal case, not a script-only page
            if status is True and not jobs and not row_by_id[cid]["is_home"]:
                js_candidates.append(cid)
            if status == "unchanged":
                unchanged += 1
            for j in jobs:
                found += put(cid, j)
            if etag is None:          # fetch failed: keep stored validators
                con.execute("UPDATE companies SET career_watched_at=? WHERE id=?",
                            (now(), cid))
            else:
                con.execute("""UPDATE companies SET career_watched_at=?,
                               career_etag=?, career_last_mod=? WHERE id=?""",
                            (now(), etag, lm, cid))
    if unchanged:
        print(f"  career-page: {unchanged} unchanged (304 skipped)", flush=True)
    # second pass with a real browser: career pages that render their job
    # list client-side yield zero links to plain HTTP (full cloud scan only)
    if js_candidates and os.environ.get("JS_WATCH"):
        try:
            import js_watch
            pairs = [(cid, row_by_id[cid]["watch_url"]) for cid in js_candidates]
            cid_by_url = {}
            for cid, u in pairs:
                cid_by_url.setdefault(u, cid)
            js_found = 0
            for u, final, body in js_watch.fetch_rendered([u for _, u in pairs]):
                row = row_by_id.get(cid_by_url.get(u))
                if row is None:
                    continue
                loc = " ".join(str(x) for x in (row["plz"], row["city"]) if x)
                for j in extract_job_links(body, final, row["name"], loc,
                                           career_page=True):
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
                   "career-page", "academia", "publicsector", "nzz", "ostjob",
                   "jobroom", "jobs.ch", "jobup.ch", "jobagent", "linkedin",
                   "indeed-google", "watch", "generic"]


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
    ap.add_argument("--probe-js", type=int, default=0,
                    help="Playwright-render N sites with no detected career "
                         "page to find JS-only ones (needs playwright)")
    ap.add_argument("--competitions", action="store_true",
                    help="scan espazium competition results and stamp "
                         "offices that won (they hire soon)")
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
                    if is_non_building(title):
                        continue  # buildings-only: software/IT/landscape/... "architect"
                if name in GENERAL_BOARDS and args.arch_only:
                    hay = title + " " + (j.get("company_name") or "")
                    keyword_ok = ARCH_KEYWORDS.search(hay) or \
                        (name == "academia" and ACADEMIA_EXTRA.search(hay))
                    if not keyword_ok:
                        continue
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
    if args.probe_js:
        run_js_probe(con, args.probe_js)
    if args.competitions:
        import competitions
        competitions.scan_competitions(con)
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
