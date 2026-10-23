# -*- coding: utf-8 -*-
"""Profile job scan: robotics, timber-industry, software and research jobs
that fit Petras's profile (PhD timber structures IBOIS/EPFL, postdoc BRG/ETH,
C++/Python/Rust, COMPAS DevOps, robotic fabrication, geometry kernels).

Deliberately separate from the architecture tracker: it never reads or
writes jobtracker.db, data.js or index.html, and jobfilter.py is not used.
Output is its own pair of files next to the main site:

    profile.js            window.PROFILE = {...}, rendered by profile.html
    profile_state.json    first-seen dates, so new postings can be flagged

Sources:
  * company ATS APIs (Lever / Ashby / Breezy) for the firms applied to;
  * company career pages without an API (anchor text scan);
  * jobs.ch / jobup.ch keyword searches and LinkedIn guest search, kept to
    titles that classify into one of the categories and score as relevant.

    python profile_jobs.py
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.parse

from net import http_get, get_json, strip_tags, extract_workload

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_DIR = os.environ.get("JOBTRACKER_SITE_DIR") or \
    os.path.normpath(os.path.join(HERE, "..", "site"))
STATE = os.path.join(HERE, "profile_state.json")
FORGET_DAYS = 30  # drop first-seen entries for postings gone this long

CATEGORIES = [
    {"key": "robotics", "label": "Robotics"},
    {"key": "timber", "label": "Timber industry"},
    {"key": "software", "label": "Software / geometry"},
    {"key": "research", "label": "Research"},
]

# firms from the application letters (+ a few close peers). kind decides the
# fetcher; cat is the category every posting of that firm is filed under.
COMPANIES = [
    ("ANYbotics", "robotics", "lever", "anybotics"),
    ("RIVR", "robotics", "lever", "rivr"),
    ("Gravis Robotics", "robotics", "lever", "gravisrobotics"),
    ("mimic robotics", "robotics", "lever", "mimic"),
    ("Sereact", "robotics", "ashby", "sereact"),
    ("Ascento", "robotics", "breezy", "ascento-ag"),
    ("Duatic", "robotics", "page", "https://www.duatic.com/jobs"),
    ("Tethys Robotics", "robotics", "page",
     "https://www.tethys-robotics.ch/company/jobs"),
    ("Exclaim Robotics", "robotics", "page", "https://exclaimrobotics.com/careers/"),
    ("Hexagon Robotics", "robotics", "page", "https://robotics.hexagon.com/careers/"),
    ("Verity", "robotics", "page", "https://www.verity.net/careers"),
    ("Voliro", "robotics", "page", "https://voliro.com/careers/"),
    ("Blumer Lehmann", "timber", "page",
     "https://www.blumer-lehmann.com/de/karriere/offene-stellen"),
    ("Balteschwiler", "timber", "page", "https://balteschwiler.ch/unternehmen/jobs/"),
    ("Walt Galmarini", "timber", "page", "https://waltgalmarini.ch/en/work-with-us/"),
    ("Pirmin Jung", "timber", "page", "https://www.pirminjung.ch/jobs"),
    ("Hector Egger Holzbau", "timber", "page", "https://www.hector-egger.ch/de/jobs"),
    ("Lignatur", "timber", "page", "https://www.lignatur.ch/unternehmen/jobs-karriere"),
    ("Design-to-Production", "timber", "page", "https://www.designtoproduction.com/"),
    ("Disney Research Studios", "research", "page",
     "https://studios.disneyresearch.com/careers/"),
]

# board keyword searches; the term only finds candidates — each title is
# classified on its own words below, never by the term that found it
BOARD_TERMS = [
    "robotics", "Robotik", "robot software", "ROS",
    "Holzbau", "Holzbauingenieur", "Arbeitsvorbereitung Holzbau",
    "timber", "construction bois",
    "C++", "computational design", "geometry", "computer graphics",
    "CAD software", "DevOps", "Rust", "computer vision",
    "digital fabrication", "research scientist",
]
LINKEDIN_TERMS = ["robotics software engineer", "computational design",
                  "Holzbau", "C++ developer geometry"]

CAT_RULES = [  # first match wins
    ("timber", re.compile(
        r"holz|timber|\bwood|\bbois\b|charpent|zimmer(?:mann|ei|leute|in)|"
        r"abbund|brettsperr|\bclt\b|lignum", re.I)),
    ("robotics", re.compile(
        r"robot|\bros\b|autonom|kinemat|motion planning|perception|"
        r"mechatron|manipulat|legged|\bslam\b|\bdrone|\buav\b", re.I)),
    ("research", re.compile(
        r"research|forsch|scientist|postdoc|post-doc|wissenschaftl|"
        r"senior assistant|oberassist|lecturer|dozent", re.I)),
    ("software", re.compile(
        r"software|developer|entwickler|programm|engineer.*(?:c\+\+|python|rust)|"
        r"c\+\+|\brust\b|devops|\bci/cd|geometr|graphics|grafik(?:programm)|"
        r"computational|\bcad\b|\bbim\b|webgpu|opengl|vulkan|\bgpu\b|"
        r"computer vision|\b3d\b|digital fabrication|digitale fabrikation", re.I)),
]

# relevance to the profile; board postings need a minimum score to show
SCORE_WORDS = [
    (r"computational|geometr|digital(?:e)? fabri|fabrication", 4),
    (r"holzbau|timber|holz|\bbois\b|charpent", 4),
    (r"robot|kinemat|motion planning|autonomy", 3),
    (r"c\+\+|\brust\b|webgpu|opengl|vulkan|graphics|\bcnc\b", 3),
    (r"\bcad\b|\bbim\b|\b3d\b|computer vision|perception|mesh|simulation|"
     r"software|programm|technical staff|reinforcement learning|backend|"
     r"devops|ci/cd|\bavor\b|arbeitsvorbereit", 2),
    (r"infrastructure|python|\bros\b|machine learning|\bml\b", 1),
    (r"research|scientist|forsch|wissenschaftl", 1),
    (r"senior|lead|principal|head", 1),
]
SCORE_WORDS = [(re.compile(p, re.I), w) for p, w in SCORE_WORDS]
MIN_BOARD_SCORE = 3
# the page hides postings below this by default ("show weak matches" toggle)
WEAK_SCORE = 2

# never a fit: apprenticeships, internships, non-technical functions
EXCLUDE = re.compile(
    r"lehrling|lernende|lehrstelle|\bapprenti|\bauszubild|praktik|"
    r"\bintern(?:ship)?\b|werkstudent|working student|stagiaire|\bstage\b|"
    r"sales|verkauf|vertrieb|account manag|key account|marketing|"
    r"buchhalt|accounting|finance|controller|\bhr\b|human resources|"
    r"recruit|talent acqui|personal(?:fach|ber)|business develop|"
    r"kaufm|kauffrau|kaufmann|sachbearbeit|assistenz|office manag|"
    r"customer (?:service|success)|deal desk|partner manag|"
    r"chauffeur|fahrer|lagerist|logistik|reinig|hauswart|"
    r"spontan|unsolicited|initiativ|speculative|general application|"
    r"procurement|purchasing|einkauf|supply chain|financing|"
    r"operations manager|teleoperation|phd (?:student|position)|doktorand|"
    # manual trades: the profile is engineering / development / lead roles
    r"zimmer(?:mann|in|leute)|charpentier|menuisier|monteur|montage|"
    r"hilfs|arbeiter|polier|holzbearbeit|holzwurm|dachdecker|allrounder|"
    r"zuschnitt|schalungsbau|mitarbeitende?r? robotic|servicetechni|"
    r"elektroinstall|elektriker|elektroniker|mechaniker|test operator|"
    r"production technician|field technician|\blehre\b|"
    r"aussendienst|berater|technico-commercial|kalkulator|"
    r"responsable du d[ée]partement|tempor|zwischenjahr|d[ée]coupe|"
    r"\btester\b", re.I)
# board searches also surface medicine/biology/physics on generic words
# (robotic surgery, computational biology, quantum geometry)
OFF_DOMAIN = re.compile(
    r"biolog|clinical|klinisch|medizin|medical|surg|chirurg|pharma|"
    r"quantum|metabol|lymph|patholog|radiolog", re.I)

# keep postings in or near Switzerland, or remote
NEAR = re.compile(
    r"switzerland|schweiz|suisse|svizzera|\bch\b|z[uü]rich|bern|basel|"
    r"lausanne|gen[eè]v|genf|luzern|lucerne|\bzug\b|winterthur|st\.?\s?gallen|"
    r"schlieren|d[uü]bendorf|baden|aarau|biel|fribourg|neuch[aâ]tel|lugano|"
    r"chur|thun|wallisellen|opfikon|oerlikon|altendorf|gossau|"
    r"remote|europe|emea|germany|deutschland|stuttgart|m[uü]nchen|munich|"
    r"austria|[öo]sterreich|wien|vienna|liechtenstein|vaduz", re.I)


def classify(title, default=None):
    for key, rx in CAT_RULES:
        if rx.search(title):
            return key
    return default


def score(text):
    return sum(w for rx, w in SCORE_WORDS if rx.search(text))


def _job(cat, title, company, location, url, source, posted=""):
    title = re.sub(r"\s+", " ", strip_tags(title or "")).strip()
    return {"cat": cat, "title": title, "company": (company or "").strip(),
            "location": re.sub(r"\s+", " ", (location or "")).strip(),
            "url": url, "source": source, "posted": (posted or "")[:10],
            "workload": extract_workload(title)}


# ---------- company ATS APIs ----------

def fetch_lever(name, cat, slug):
    out = []
    for p in get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json"):
        c = p.get("categories") or {}
        loc = ", ".join(c.get("allLocations") or [c.get("location") or ""])
        posted = ""
        if p.get("createdAt"):
            posted = datetime.datetime.fromtimestamp(
                p["createdAt"] / 1000, datetime.timezone.utc).date().isoformat()
        out.append(_job(cat, p.get("text"), name, loc, p.get("hostedUrl"),
                        "lever", posted))
    return out


def fetch_ashby(name, cat, slug):
    d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    out = []
    for p in d.get("jobs") or []:
        if p.get("isListed") is False:
            continue
        loc = p.get("location") or ""
        if p.get("isRemote"):
            loc += " (remote)"
        out.append(_job(cat, p.get("title"), name, loc, p.get("jobUrl"),
                        "ashby", p.get("publishedAt")))
    return out


def fetch_breezy(name, cat, slug):
    out = []
    for p in get_json(f"https://{slug}.breezy.hr/json"):
        loc = (p.get("location") or {}).get("name") or ""
        out.append(_job(cat, p.get("name"), name, loc, p.get("url"),
                        "breezy", p.get("published_date")))
    return out


# ---------- career pages without an API ----------

ANCHOR = re.compile(r'<a\b[^>]*?href\s*=\s*["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>',
                    re.S | re.I)
# anchor text that reads like a position title
ROLE = re.compile(  # engineer(?!ing): "Structural Engineering" is nav
    r"engineer(?!ing)|ingenieur(?!wesen|b[uü]ro)|ing[ée]nieur|developer|entwickler|scientist|"
    r"researcher|projektleit|project (?:lead|manag)|techniker|technician|"
    r"konstrukteur|zeichner|zimmer(?:mann|in|leute)|polier|vorarbeit|"
    r"arbeitsvorbereit|planer|programm|specialist|spezialist|architect|"
    r"architekt|manager|leiter|lead\b|head of|\(m/w|\(w/m|\(m/f|\(f/m|"
    r"\(all genders|\d{2,3}\s*%", re.I)
NAV_TEXT = re.compile(
    r"^(?:jobs?|karriere|careers?|stellen|offene stellen|apply|bewerben|"
    r"mehr|more|read more|details?|learn more|alle jobs|all jobs|"
    r"open positions|team|about|kontakt|contact)$", re.I)


def fetch_page(name, cat, url):
    body, final = http_get(url, tries=2, timeout=20)
    out, seen = [], set()
    for m in ANCHOR.finditer(body):
        text = re.sub(r"\s+", " ", strip_tags(m.group(2))).strip()
        if not (8 <= len(text) <= 140) or NAV_TEXT.match(text):
            continue
        if not ROLE.search(text):
            continue
        href = urllib.parse.urljoin(final, m.group(1).strip())
        if not href.startswith("http") or href.rstrip("/") == final.rstrip("/"):
            continue
        if re.search(r"linkedin\.com|facebook|instagram|xing\.com|mailto:", href):
            continue
        if href in seen:
            continue
        seen.add(href)
        out.append(_job(cat, text, name, "", href, "career page"))
    return out


FETCHERS = {"lever": fetch_lever, "ashby": fetch_ashby,
            "breezy": fetch_breezy, "page": fetch_page}


def scan_companies():
    jobs, watch = [], []
    for name, cat, kind, ref in COMPANIES:
        home = ref if kind == "page" else {
            "lever": f"https://jobs.lever.co/{ref}",
            "ashby": f"https://jobs.ashbyhq.com/{ref}",
            "breezy": f"https://{ref}.breezy.hr/"}[kind]
        try:
            found = FETCHERS[kind](name, cat, ref)
            kept = []
            for j in found:
                if not j["title"] or not j["url"] or EXCLUDE.search(j["title"]):
                    continue
                # API postings carry locations; page anchors usually do not
                if j["location"] and not NEAR.search(j["location"]):
                    continue
                kept.append(j)
            jobs += kept
            watch.append({"name": name, "cat": cat, "url": home,
                          "status": "ok", "count": len(kept)})
        except Exception as e:
            watch.append({"name": name, "cat": cat, "url": home,
                          "status": "failed", "count": 0,
                          "detail": str(e)[:120]})
        time.sleep(0.3)
    return jobs, watch


# ---------- job boards ----------

def _jobcloud(base, link_key, source):
    out, seen = [], set()
    for term in BOARD_TERMS:
        for page in (1, 2, 3):
            q = urllib.parse.urlencode({"query": term, "rows": 20, "page": page})
            try:
                d = get_json(f"{base}/api/v1/public/search?{q}")
            except Exception:
                break
            for doc in d.get("documents") or []:
                jid = doc.get("job_id") or doc.get("slug")
                links = doc.get("_links") or {}
                url = None
                for key in (link_key, "detail_en", "detail_de", "detail_fr"):
                    if key in links:
                        url = links[key].get("href")
                        break
                if not jid or jid in seen or not url:
                    continue
                seen.add(jid)
                out.append(_job(None, doc.get("title"), doc.get("company_name"),
                                doc.get("place"), url, source,
                                doc.get("publication_date")))
            if page >= (d.get("num_pages") or 1):
                break
        time.sleep(0.4)
    return out


LINKEDIN_CARD = re.compile(
    r'base-card__full-link[^"]*"\s+href="([^"]+)".*?'
    r'base-search-card__title[^>]*>\s*(.*?)\s*</h3>.*?'
    r'(?:base-search-card__subtitle[^>]*>.*?<a[^>]*>\s*(.*?)\s*</a>|'
    r'hidden-nested-link[^>]*>\s*(.*?)\s*</a>).*?'
    r'job-search-card__location[^>]*>\s*(.*?)\s*</span>(?:.*?datetime="([^"]+)")?',
    re.S)


def scan_linkedin():
    out = []
    for term in LINKEDIN_TERMS:
        q = urllib.parse.urlencode({"keywords": term, "location": "Switzerland",
                                    "f_TPR": "r1209600"})  # last 14 days
        try:
            body, _ = http_get("https://www.linkedin.com/jobs-guest/jobs/api/"
                               "seeMoreJobPostings/search?" + q, tries=1)
        except Exception:
            continue
        for m in LINKEDIN_CARD.finditer(body):
            out.append(_job(None, m.group(2), strip_tags(m.group(3) or m.group(4)),
                            m.group(5), m.group(1).split("?")[0], "linkedin",
                            m.group(6)))
        time.sleep(1.5)
    return out


def scan_boards():
    raw = (_jobcloud("https://www.jobs.ch", "detail_en", "jobs.ch")
           + _jobcloud("https://www.jobup.ch", "detail_fr", "jobup.ch")
           + scan_linkedin())
    kept = []
    for j in raw:
        if (not j["title"] or EXCLUDE.search(j["title"])
                or OFF_DOMAIN.search(j["title"])):
            continue
        cat = classify(j["title"])
        # "software" is the broadest bucket (every bank has DevOps roles),
        # so it needs one point more than the others
        need = MIN_BOARD_SCORE + (cat == "software")
        if not cat or score(j["title"]) < need:
            continue
        j["cat"] = cat
        kept.append(j)
    return kept


# ---------- output ----------

def _key(j):
    t = re.sub(r"[^a-z0-9]+", "", j["title"].lower())
    c = re.sub(r"[^a-z0-9]+", "", j["company"].lower())
    return c + "|" + t


def main():
    started = datetime.datetime.now().astimezone()
    today = started.date().isoformat()
    company_jobs, watch = scan_companies()
    print(f"profile: {len(company_jobs)} company postings from "
          f"{sum(w['status'] == 'ok' for w in watch)}/{len(watch)} firms", flush=True)
    board_jobs = []
    try:
        board_jobs = scan_boards()
    except Exception as e:
        print(f"profile: boards failed: {e}", flush=True)
    print(f"profile: {len(board_jobs)} board postings", flush=True)

    # company feeds first: the same role on a board is a duplicate of it
    jobs, keys = [], set()
    for j in company_jobs + board_jobs:
        k = _key(j)
        if k in keys:
            continue
        keys.add(k)
        j["score"] = score(j["title"] + " " + j["company"])
        jobs.append(j)

    try:
        state = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    for j in jobs:
        s = state.setdefault(j["url"], {"first_seen": today})
        s["last_seen"] = today
        j["first_seen"] = s["first_seen"]
    cutoff = (started.date() - datetime.timedelta(days=FORGET_DAYS)).isoformat()
    state = {u: s for u, s in state.items() if s.get("last_seen", "") >= cutoff}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=0, sort_keys=True)

    jobs.sort(key=lambda j: (j["first_seen"], j["score"]), reverse=True)
    payload = {"generated": started.isoformat(timespec="minutes"),
               "categories": CATEGORIES, "weak_score": WEAK_SCORE,
               "jobs": jobs, "watch": watch}
    os.makedirs(SITE_DIR, exist_ok=True)
    with open(os.path.join(SITE_DIR, "profile.js"), "w", encoding="utf-8") as f:
        f.write("window.PROFILE = ")
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    by_cat = {}
    for j in jobs:
        by_cat[j["cat"]] = by_cat.get(j["cat"], 0) + 1
    print(f"profile: wrote {len(jobs)} jobs {by_cat}", flush=True)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
