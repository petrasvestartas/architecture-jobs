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
  * the company directory from profile_directory.py (every timber and
    robotics firm in the commercial register plus profile_curated.json),
    read in rotation through each firm's careers page or ATS feed;
  * jobs.ch, job-room.ch keyword searches and LinkedIn guest search, kept to
    titles that classify into one of the categories and score as relevant.

Trade and site-crew roles (carpenter, CNC operator, foreman, installer) are
never kept; timber postings must name an office / engineering role.

    python profile_jobs.py
"""
import concurrent.futures
import datetime
import html
import json
import os
import re
import sys
import time
import urllib.parse

import profile_feedback
import profile_match
from net import http_get, get_json, post_json, strip_tags, extract_workload

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_DIR = os.environ.get("JOBTRACKER_SITE_DIR") or \
    os.path.normpath(os.path.join(HERE, "..", "site"))
STATE = os.path.join(HERE, "profile_state.json")
FORGET_DAYS = 30  # drop first-seen entries for postings gone this long

CATEGORIES = [
    {"key": "robotics", "label": "Robotics"},
    {"key": "timber", "label": "Timber industry"},
    {"key": "software", "label": "Software / geometry"},
    {"key": "research", "label": "Industrial research"},
]

# Firms from the application letters plus their Swiss peers. kind picks the
# fetcher, ref is its argument, cat files every kept posting. Options:
#   only     — big employers with many unrelated roles (ABB, Implenia, ...):
#              keep a posting only if its title classifies into one of these
#              categories, e.g. only timber roles at a general contractor
#   match    — jobsch: substring the posting's company name must contain
#              (the free-text search also returns other firms)
#   path     — page: regex the posting link must match (skips service nav)
#   render   — page: the list is built in JavaScript; render it in Chromium
#   link     — no readable list; shown under "Companies watched" only
ROBO = ("robotics", "software", "research")
COMPANIES = [
    # --- robotics: startups / scale-ups (ATS feeds) ---
    ("ANYbotics", "robotics", "lever", "anybotics", {}),
    ("RIVR", "robotics", "lever", "rivr", {}),
    ("Gravis Robotics", "robotics", "lever", "gravisrobotics", {}),
    ("mimic robotics", "robotics", "lever", "mimic", {}),
    ("Sereact", "robotics", "ashby", "sereact", {}),
    ("Exclaim Robotics", "robotics", "ashby", "exclaim", {}),
    ("Ascento", "robotics", "breezy", "ascento-ag", {}),
    ("Verity", "robotics", "breezy", "verity-ag", {}),
    ("Voliro", "robotics", "bamboo", "voliroar", {}),
    ("Auterion", "robotics", "greenhouse", "auterion", {}),
    ("Flyability", "robotics", "workable", "flyability", {}),
    ("Scandit", "software", "greenhouse", "scandit", {"only": ROBO}),
    ("Embotech", "robotics", "page", "https://embotech.ai/career", {}),
    ("Duatic", "robotics", "page", "https://www.duatic.com/jobs", {"render": True}),
    ("Tethys Robotics", "robotics", "page",
     "https://www.tethys-robotics.ch/company/jobs", {"link": True}),
    ("Hexagon Robotics", "robotics", "page",
     "https://hexagon-robotics.onlyfy.jobs/", {"render": True}),
    ("Sevensense (ABB)", "robotics", "page", "https://www.sevensense.ai/", {"link": True}),
    # --- robotics / automation: industrial (jobs.ch, robotics+software roles) ---
    ("ABB", "robotics", "workday", "abb.wd3/abb/External_Career_Page",
     {"country": "Switzerland", "only": ROBO,
      "home": "https://careers.abb/global/en/search-results?keywords=robotics"}),
    ("ABB Robotics", "robotics", "linkedin", "ABB Robotics", {"match": "abb", "only": ROBO}),
    ("Stäubli", "robotics", "linkedin", "Stäubli robotics",
     {"match": "ubli", "only": ROBO}),
    ("KUKA", "robotics", "linkedin", "KUKA", {"match": "kuka", "only": ROBO}),
    ("Güdel", "robotics", "jobsch", "Güdel", {"match": "güdel", "only": ROBO}),
    ("Hilti", "robotics", "jobsch", "Hilti", {"match": "hilti", "only": ROBO}),
    ("Hilti (LinkedIn)", "robotics", "linkedin", "Hilti robotics software",
     {"match": "hilti", "only": ROBO}),
    ("Schindler", "robotics", "jobsch", "Schindler", {"match": "schindler", "only": ROBO}),
    ("Kistler", "robotics", "jobsch", "Kistler", {"match": "kistler", "only": ROBO}),
    ("Bystronic", "robotics", "jobsch", "Bystronic", {"match": "bystronic", "only": ROBO}),
    ("Mikron", "robotics", "jobsch", "Mikron", {"match": "mikron", "only": ROBO}),
    ("Swisslog", "robotics", "jobsch", "Swisslog", {"match": "swisslog", "only": ROBO}),
    ("Wingtra", "robotics", "jobsch", "Wingtra", {"match": "wingtra", "only": ROBO}),
    ("Leica Geosystems", "software", "jobsch", "Leica Geosystems",
     {"match": "leica", "only": ROBO}),
    # --- timber: builders, engineers, software ---
    ("Blumer Lehmann", "timber", "page",
     "https://www.blumer-lehmann.com/en/careers/job-vacancies.html",
     {"render": True, "path": r"/job-vacancies/."}),
    ("Hector Egger Holzbau", "timber", "page",
     "https://my.jobalino.ch/public/component/jobsfactory?task=framelistjobs"
     "&company_name=hector-egger-holzbau-ag", {"path": r"/public/job/"}),
    ("Balteschwiler", "timber", "page", "https://balteschwiler.ch/unternehmen/jobs/",
     {"path": r"(?i)stellenausschreibung"}),
    ("Neue Holzbau (n'H)", "timber", "page", "https://www.neueholzbau.ch/karriere/stellen",
     {"path": r"ostendis\.com/publication/"}),
    ("Strüby", "timber", "page", "https://www.strueby.ch/unternehmen/jobs/",
     {"path": r"/stellen/."}),
    ("Renggli", "timber", "jobsch", "Renggli", {"match": "renggli"}),
    ("ERNE Holzbau", "timber", "jobsch", "ERNE Holzbau", {"match": "erne", "only": ("timber",)}),
    ("Implenia Holzbau", "timber", "jobsch", "Implenia Holzbau",
     {"match": "implenia", "only": ("timber",)}),
    ("HUSNER Holzbau", "timber", "jobsch", "Husner", {"match": "husner"}),
    ("Künzli Holz", "timber", "jobsch", "Künzli Holz", {"match": "künzli"}),
    ("Blättler Holzbau", "timber", "jobsch", "Blättler Holzbau", {"match": "blättler"}),
    ("Kaufmann Oberholzer", "timber", "jobsch", "Kaufmann Oberholzer",
     {"match": "kaufmann oberholzer"}),
    ("Schilliger Holz", "timber", "jobsch", "Schilliger", {"match": "schilliger"}),
    ("Zaugg", "timber", "jobsch", "Zaugg Holzbau", {"match": "zaugg", "only": ("timber",)}),
    ("Swiss Krono", "timber", "jobsch", "Swiss Krono", {"match": "krono"}),
    ("Gruner (Holzbau)", "timber", "jobsch", "Gruner Holzbau",
     {"match": "gruner", "only": ("timber",)}),
    ("Timbatec", "timber", "jobsch", "Timbatec", {"match": "timbatec"}),
    ("Pirmin Jung", "timber", "page", "https://www.pirminjung.ch/jobs", {"link": True}),
    ("Lignatur", "timber", "page", "https://www.lignatur.ch/unternehmen/jobs-karriere",
     {"link": True}),
    ("SJB Kempter Fitze", "timber", "page", "https://sjb.ch/karriere/arbeiten-bei-sjb/",
     {"link": True}),
    ("Walt Galmarini", "timber", "page", "https://waltgalmarini.ch/en/work-with-us/",
     {"link": True}),
    ("Design-to-Production", "timber", "page", "https://www.designtoproduction.com/",
     {"link": True}),
    ("Technowood", "timber", "page", "https://www.technowood.swiss/", {"link": True}),
    ("cadwork", "timber", "page", "https://cadwork.com/", {"link": True}),
    ("Holzbau Schweiz jobs", "timber", "page",
     "https://www.holzbau-schweiz.ch/de/unser-angebot/jobs/", {"link": True}),
    # --- research ---
    ("Disney Research Studios", "research", "page",
     "https://studios.disneyresearch.com/careers/", {"link": True}),
]

# board keyword searches; the term only finds candidates — each title is
# classified on its own words below, never by the term that found it
BOARD_TERMS = [
    "robotics", "Robotik", "robot software", "ROS", "Robotics Engineer",
    "Roboterprogrammierer", "Robotik Ingenieur", "industrial robot",
    "motion control", "Computer Vision Engineer",
    "Holzbau", "Holzbauingenieur", "Arbeitsvorbereitung Holzbau",
    "Projektleiter Holzbau", "Holzbautechniker", "Holzbau Konstrukteur",
    "Werkplaner Holzbau", "Ingenieurholzbau", "Elementbau Holz", "cadwork",
    "Abbund", "timber",
    "C++", "computational design", "geometry", "computer graphics",
    "CAD software", "DevOps", "Rust", "computer vision",
    "digital fabrication", "research scientist",
]
# job-room.ch is the federal employment service's board (SECO/RAV), where
# employers must report vacancies in some trades and many others post too
JOBROOM_TERMS = ["Holzbau", "Holzbauingenieur", "Ingenieurholzbau", "Robotik",
                 "Robotics", "Roboter", "C++", "Computer Vision", "Geometrie",
                 "computational design", "digitale Fabrikation", "CAD Entwickler"]
LINKEDIN_TERMS = ["robotics software engineer", "robotics engineer",
                  "computational design", "Holzbau", "Holzbauingenieur",
                  "timber construction", "C++ developer geometry"]

CAT_RULES = [  # first match wins
    ("timber", re.compile(
        r"holz|timber|\bwood|\bbois\b|charpent|zimmer(?:mann|ei|leute|in)|"
        r"abbund|brettsperr|\bclt\b|lignum|cadwork|elementbau|leimwerk", re.I)),
    ("robotics", re.compile(
        r"robot|\bros\b|autonom|kinemat|motion planning|motion control|"
        r"perception|mechatron|manipulat|legged|\bslam\b|\bdrone|\buav\b|"
        r"lidar|machine vision|bildverarbeitung", re.I)),
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
    r"sales|verk[aä]uf|vertrieb|account manag|key account|marketing|"
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
    r"\btester\b|\bkoch\b|k[öo]chin|kantine|carpenter|plant operator|"
    r"machine operator|sawmill|s[aä]gewerk|\bbuyer\b|eink[aä]ufer|"
    r"schreiner|joiner|maler\b|assembly technician|"
    # shop-floor and site-crew roles (user, 2026-10-27: "don't search for
    # jobs like carpenter") — CNC operators, foremen, installers, site crews
    r"maschinist|machinist|automatiker|foreman|vorarbeit|construction worker|"
    r"bauarbeit|installer|installateur|\bfachkraft|fachmann|fachfrau|"
    r"logistiker|logistician|mechatroniker|mechatronics technician|bauf[üu]hrer|"
    r"site (?:foreman|manager)|handwerk|craftsm|operator|bediener|"
    r"produktionsmitarbeit|mitarbeiter(?:in)? produktion|staplerfahrer|"
    r"zimmerfrau|"
    r"holzbearbeiter|s[aä]ger\b|spengler|dachdecker|ger[üu]stbau|"
    # back office and interior joinery at timber firms
    r"finanz|rechnungswesen|m[öo]bel|parkett|k[üu]chen", re.I)

# timber firms and the register directory mostly hire trades; there a
# posting must name a professional (office / engineering / lead) role
PROFESSIONAL = re.compile(
    r"ingenieur|engineer|projektleit|project (?:lead|manag)|projektmanag|"
    r"konstrukt|planer|planning|avor|arbeitsvorbereit|work preparation|"
    r"cnc[- ]?programm|programmier|entwickl|developer|software|scientist|"
    r"research|forsch|wissenschaft|techniker|technician|technolog|"
    r"head of|leiter|leitung|lead\b|director|chief|architekt(?!ur)|"
    r"architect(?!ure)|"
    r"digital|\bbim\b|\bcad\b|data|computational|geometr|statik|"
    r"tragwerk|structural|bauphysik|innovation|produktmanag|product manag",
    re.I)
# board searches also surface medicine/biology/physics on generic words
# (robotic surgery, computational biology, quantum geometry)
OFF_DOMAIN = re.compile(
    r"biolog|clinical|klinisch|medizin|medical|surg|chirurg|pharma|"
    r"quantum|metabol|lymph|patholog|radiolog", re.I)

# Industry only (user, 2026-10-27: "remove all university positions in
# ethz zhaw, i want to move to industry"): no universities, universities of
# applied sciences or public research institutes as employer, and no
# academic posts anywhere. Company research labs (Disney, IBM, ABB) stay.
ACADEMIC_EMPLOYER = re.compile(
    r"\beth\b|eth z[uü]rich|\bepfl\b|zhaw|hslu|fhnw|\bbfh\b|fhgr|supsi|"
    r"\bost\b|ostschweizer|fachhochschule|hochschule|universit|\buzh\b|"
    r"\bunibe\b|\bunibas\b|\busi\b|\bempa\b|paul scherrer|\bpsi\b|\bwsl\b|"
    r"eawag|idsia|cscs|wyss zurich|school of (?:engineering|management)|"
    r"haute [ée]cole|polytechni", re.I)
ACADEMIC_TITLE = re.compile(
    r"postdoc|post-doc|\bphd\b|doktorand|professor|professur|"
    r"scientific assistant|research assistant|wissenschaftliche[rn]? "
    r"(?:mitarbeit|assistent)|lecturer|dozent|oberassist|senior assistant|"
    r"lehrbeauftragt|assistenzprofess|tenure", re.I)


def academic(j):
    return bool(ACADEMIC_EMPLOYER.search(j.get("company") or "")
                or ACADEMIC_TITLE.search(j["title"]))

# French-speaking Switzerland is out (user's choice, 2026-10-23): Geneva,
# Vaud, Neuchâtel, Jura, Fribourg and the French-speaking Valais, matched on
# the posting's location, plus French-language ads ("H/F", "ingénieur", ...)
# whatever location they give
ROMANDIE = re.compile(
    r"gen[eè]v|genf|lausanne|vaud|waadt|\bvd\b|neuch[aâ]tel|\bne\b|jura\b|"
    r"del[ée]mont|fribourg|freiburg|\bfr\b|sion\b|sitten|martigny|monthey|"
    r"sierre|morges|nyon|yverdon|vevey|montreux|bulle\b|prilly|renens|"
    r"[ée]cublens|epfl|aigle\b|villeneuve|paudex|carouge|meyrin|thônex|"
    r"thonex|plan-les-ouates|les acacias|la chaux-de-fonds|le locle|"
    r"le mouret|villaz|voll[eè]ges|matran|romont|payerne|gland\b|rolle\b|"
    r"pully|crissier|bussigny|marly\b|granges-paccot|romandie|suisse romande",
    re.I)
FRENCH_AD = re.compile(
    r"\(h\s*/\s*f|\bh/f\b|ing[ée]nieur|chef(?:fe)?\s+de projet|technicien|"
    r"charpent|menuisier|dessinat|responsable\b|collaborat(?:eur|rice)|"
    r"sp[ée]cialiste|conducteur|\bbois\b|\bpour\b|\bavec\b", re.I)


def is_romandie(j):
    return bool(ROMANDIE.search(j.get("location") or "")
                or FRENCH_AD.search(j.get("title") or ""))


def professional(j):
    """Trades are out everywhere; timber postings must also name an
    office/engineering role (CNC operators, crews and foremen are not)."""
    if j["cat"] == "timber" or j.get("directory"):
        return bool(PROFESSIONAL.search(j["title"]))
    return True


# Only postings based in Switzerland (user, 2026-10-27: "remove all non
# suisse based offerings"): no Germany / Austria / Liechtenstein, and no
# "remote" without a Swiss location. A location counts as Swiss when any of
# its parts ("Zurich, Austin, Oxford") names the country, carries a Swiss
# postal code or is a place in the GeoNames postal table (data/CH.txt).
SWISS = re.compile(r"switzerland|schweiz|suisse|svizzera|\bch\b", re.I)
SWISS_PLZ = re.compile(r"(?:^|\bch-?)\s*[1-9]\d{3}\b", re.I)
EXONYMS = {"zurich", "geneva", "lucerne", "basle", "berne", "bale"}
CH_TXT = os.path.join(HERE, "data", "CH.txt")
_places = None


def _fold(s):
    s = s.lower()
    for a, b in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("é", "e"), ("è", "e"),
                 ("à", "a"), ("â", "a"), ("ô", "o"), ("ç", "c")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z]+", " ", s).strip()


def _swiss_places():
    global _places
    if _places is None:
        _places = set(EXONYMS)
        with open(CH_TXT, encoding="utf-8") as f:
            for line in f:
                cols = line.split("\t")
                for name in (cols[2], cols[7]):
                    # "Aarau 1", "Buchs ZH" -> "aarau", "buchs"
                    n = _fold(re.sub(r"\s+\d+$|\s+[A-Z]{2}$", "", name))
                    if n:
                        _places.add(n)
    return _places


def swiss(loc):
    """True for a Swiss location; an empty one is the firm's own (all
    scanned firms and boards are Swiss) and passes."""
    loc = re.sub(r"\(remote\)", " ", loc or "", flags=re.I).strip()
    if not loc or loc.lower() == "unknown":
        return True
    if SWISS.search(loc):
        return True
    places = _swiss_places()
    for part in re.split(r"[,;/|()]| or | and | oder | und ", loc):
        part = part.strip()
        if SWISS_PLZ.match(part):
            return True
        # drop canton suffixes and street addresses: "Roggwil TG",
        # "Stuttgart Schockenriedstr. 17" (the city is the first word)
        part = re.sub(r"\s+[A-Z]{2}$", "", part)
        f = _fold(part)
        if f and (f in places or f.split(" ")[0] in places
                  and len(f.split(" ")[0]) > 3):
            return True
    return False


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

def fetch_lever(name, cat, slug, opt):
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


def fetch_ashby(name, cat, slug, opt):
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


def fetch_breezy(name, cat, slug, opt):
    out = []
    for p in get_json(f"https://{slug}.breezy.hr/json"):
        loc = (p.get("location") or {}).get("name") or ""
        out.append(_job(cat, p.get("name"), name, loc, p.get("url"),
                        "breezy", p.get("published_date")))
    return out


def fetch_greenhouse(name, cat, slug, opt):
    d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    return [_job(cat, p.get("title"), name, (p.get("location") or {}).get("name"),
                 p.get("absolute_url"), "greenhouse", p.get("updated_at"))
            for p in d.get("jobs") or []]


def fetch_bamboo(name, cat, slug, opt):
    d = get_json(f"https://{slug}.bamboohr.com/careers/list")
    out = []
    for p in d.get("result") or []:
        loc = p.get("location") or {}
        loc = ", ".join(x for x in (loc.get("city"), loc.get("state")) if x)
        out.append(_job(cat, p.get("jobOpeningName"), name, loc,
                        f"https://{slug}.bamboohr.com/careers/{p.get('id')}", "bamboohr"))
    return out


def fetch_workable(name, cat, slug, opt):
    d = post_json(f"https://apply.workable.com/api/v3/accounts/{slug}/jobs",
                  {"query": "", "location": [], "department": [], "worktype": [],
                   "remote": []})
    out = []
    for p in d.get("results") or []:
        loc = p.get("location") or {}
        loc = ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
        out.append(_job(cat, p.get("title"), name, loc,
                        f"https://apply.workable.com/{slug}/j/{p.get('shortcode')}/",
                        "workable", p.get("published")))
    return out


def fetch_workday(name, cat, ref, opt):
    """Workday CXS search API; ref is "host/tenant/site". Restricted to one
    country through its facet id, looked up from the first response."""
    host, tenant, site = ref.split("/")
    base = f"https://{host}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    facets = {}
    first = post_json(api, {"appliedFacets": {}, "limit": 1, "offset": 0,
                            "searchText": ""})
    for group in first.get("facets") or []:
        for f in [group] + (group.get("values") or []):
            if f.get("facetParameter") != "locationCountry":
                continue
            for v in f.get("values") or []:
                # descriptors follow Accept-Language (net.py sends de-CH)
                if v.get("descriptor") in (opt["country"], "Schweiz", "Suisse"):
                    facets = {"locationCountry": [v["id"]]}
    if not facets:
        raise RuntimeError(f"no {opt['country']} facet")
    out, offset = [], 0
    while offset < 400:
        d = post_json(api, {"appliedFacets": facets, "limit": 20,
                            "offset": offset, "searchText": ""})
        posts = d.get("jobPostings") or []
        for p in posts:
            out.append(_job(cat, p.get("title"), name,
                            p.get("locationsText") + ", " + opt["country"],
                            f"{base}/{site}{p.get('externalPath')}", "workday"))
        offset += 20
        if not posts or offset >= (d.get("total") or 0):
            break
    return out


def fetch_personio(name, cat, slug, opt):
    body, _ = http_get(f"https://{slug}.jobs.personio.de/xml?language=de",
                       accept="application/xml", tries=2, timeout=20)
    out = []
    for p in re.findall(r"<position>(.*?)</position>", body, re.S):
        def tag(t):
            m = re.search(rf"<{t}>(.*?)</{t}>", p, re.S)
            return strip_tags(re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))) if m else ""
        out.append(_job(cat, tag("name"), name, tag("office"),
                        f"https://{slug}.jobs.personio.de/job/{tag('id')}",
                        "personio", tag("createdAt")))
    return out


def fetch_recruitee(name, cat, slug, opt):
    d = get_json(f"https://{slug}.recruitee.com/api/offers/")
    return [_job(cat, p.get("title"), name,
                 p.get("location") or p.get("city") or "",
                 p.get("careers_url"), "recruitee", p.get("published_at"))
            for p in d.get("offers") or []]


def fetch_smartrecruiters(name, cat, slug, opt):
    d = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
                 "?country=ch&limit=100")
    out = []
    for p in d.get("content") or []:
        loc = p.get("location") or {}
        loc = ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
        out.append(_job(cat, p.get("name"), name, loc,
                        f"https://jobs.smartrecruiters.com/{slug}/{p.get('id')}",
                        "smartrecruiters", p.get("releasedDate")))
    return out


def fetch_teamtailor(name, cat, slug, opt):
    return fetch_page(name, cat, f"https://{slug}.teamtailor.com/jobs",
                      {"path": r"/jobs/\d"})


def fetch_jobsch(name, cat, query, opt):
    """jobs.ch free-text search on the firm's name, kept to that firm."""
    out = _jobcloud("https://www.jobs.ch", "detail_en", "jobs.ch", [query], pages=5)
    return [dict(j, cat=cat, company=j["company"] or name) for j in out
            if opt["match"] in j["company"].lower()]


def fetch_linkedin(name, cat, query, opt):
    out = scan_linkedin([query])
    return [dict(j, cat=cat) for j in out if opt["match"] in j["company"].lower()]


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


# Jobalino-style listings run the whole card into the anchor text:
# "Projektleiter:in Holzbau 80% - 100% Festanstellung Vor Ort Hector Egger ..."
CARD_TAIL = re.compile(r"\s+(?:Festanstellung|Befristet|Tempor[aä]r|Vor Ort|"
                       r"Workload)\b.*$")
# ... and some put the location before or after the title:
# "Zurich Motion Planning Software Engineer", "Staff ML Engineer Zürich | Full-time"
CITY = (r"(?:Z[uü]rich|Munich|M[uü]nchen|Rotterdam|Bern|Basel|Stuttgart|"
        r"Berlin|Boston|Lausanne|Gen[eè]v[ae])(?:,\s*[A-Z][a-z]+(?: [A-Z][a-z]+)*)?")
LEAD_LOC = re.compile(rf"^((?:Multiple locations\s*\([^)]*\)|{CITY}(?:\s+or\s+{CITY})*))\s+(?=\S)")
TRAIL_LOC = re.compile(rf"\s+({CITY}(?:\s+or\s+{CITY})*)$")


def _split_loc(text):
    text = text.split(" | ")[0].strip()
    loc = ""
    m = LEAD_LOC.match(text)
    if m:
        loc, text = m.group(1), text[m.end():]
    m = TRAIL_LOC.search(text)
    if m:
        loc, text = loc or m.group(1), text[:m.start()]
    return text.strip(), loc

_browser = None


def _render(url):
    """Page HTML after JavaScript ran, via the Chromium that the scan workflow
    installs. None when Playwright is unavailable (the plain fetch is used)."""
    global _browser
    try:
        if _browser is None:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            _browser = pw.chromium.launch()
        page = _browser.new_page()
        try:
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass  # chatty pages never go idle; the DOM is there anyway
            return page.content(), page.url
        finally:
            page.close()
    except ImportError:
        return None


def fetch_page(name, cat, url, opt):
    got = _render(url) if opt.get("render") else None
    body, final = got or http_get(url, tries=2, timeout=20)
    return page_jobs(body, final, name, cat, opt)


# opt "strict" (directory firms: small sites whose menus are full of
# "Architektur", "Planung", "Für Architekten"): the anchor must read like an
# ad — a gender tag or workload, or a link into a jobs/careers path
AD_TAG = re.compile(r"\((?:[mwfdxa]\s*/\s*)*[mwfdxa]\)|\b[mwfd]/[mwfd]\b|"
                    r":in\b|\*in\b|/-?in\b|\(alle|\(all |%", re.I)
CAREER_PATH = re.compile(r"job|stelle|karriere|career|inserat|vakan|vacan|offene",
                         re.I)
OLD_AD = re.compile(r"\b20(?:0\d|1\d|2[0-4])\b")  # "06.2020 - Projektleiter gesucht"
NOT_AD = re.compile(r"^(?:f[uü]r|routenplaner|job-quiz|quiz)\b", re.I)


def _ad_like(text, href):
    if OLD_AD.search(text) or NOT_AD.match(text) or not PROFESSIONAL.search(text):
        return False
    return bool(AD_TAG.search(text)
                or CAREER_PATH.search(urllib.parse.urlparse(href).path))


def page_jobs(body, final, name, cat, opt):
    path = re.compile(opt["path"]) if opt.get("path") else None
    out, seen = [], set()
    for m in ANCHOR.finditer(body):
        text = re.sub(r"\s+", " ", strip_tags(m.group(2))).strip()
        workload = extract_workload(text)
        text, loc = _split_loc(CARD_TAIL.sub("", text))
        if not (8 <= len(text) <= 140) or NAV_TEXT.match(text):
            continue
        # hrefs can carry entities ("80&ndash;100-441.html")
        href = urllib.parse.urljoin(final, html.unescape(m.group(1).strip()))
        if path:
            if not path.search(href):
                continue
        elif not ROLE.search(text):
            continue
        if opt.get("strict") and not _ad_like(text, href):
            continue
        if not href.startswith("http") or href.rstrip("/") == final.rstrip("/"):
            continue
        if re.search(r"linkedin\.com|facebook|instagram|xing\.com|mailto:", href):
            continue
        if href in seen:
            continue
        seen.add(href)
        j = _job(cat, text, name, loc, href, "career page")
        j["workload"] = j["workload"] or workload
        out.append(j)
    return out


FETCHERS = {"lever": fetch_lever, "ashby": fetch_ashby, "breezy": fetch_breezy,
            "greenhouse": fetch_greenhouse, "bamboo": fetch_bamboo,
            "workable": fetch_workable, "workday": fetch_workday,
            "personio": fetch_personio, "recruitee": fetch_recruitee,
            "smartrecruiters": fetch_smartrecruiters,
            "teamtailor": fetch_teamtailor, "jobsch": fetch_jobsch,
            "linkedin": fetch_linkedin, "page": fetch_page}
HOME = {
    "lever": "https://jobs.lever.co/{}", "ashby": "https://jobs.ashbyhq.com/{}",
    "breezy": "https://{}.breezy.hr/", "greenhouse": "https://boards.greenhouse.io/{}",
    "bamboo": "https://{}.bamboohr.com/careers", "workable": "https://apply.workable.com/{}/",
    "workday": "https://{}",
    "personio": "https://{}.jobs.personio.de/", "recruitee": "https://{}.recruitee.com/",
    "smartrecruiters": "https://careers.smartrecruiters.com/{}",
    "teamtailor": "https://{}.teamtailor.com/jobs",
    "jobsch": "https://www.jobs.ch/en/vacancies/?term={}",
    "linkedin": "https://www.linkedin.com/jobs/search/?location=Switzerland&keywords={}",
    "page": "{}",
}


def scan_companies():
    jobs, watch = [], []
    for name, cat, kind, ref, opt in COMPANIES:
        home = opt.get("home") or HOME[kind].format(
            urllib.parse.quote(ref, safe=":/?&=")
            if kind in ("page", "workday") else urllib.parse.quote(ref))
        if opt.get("link"):
            watch.append({"name": name, "cat": cat, "url": home, "status": "link"})
            continue
        try:
            found = FETCHERS[kind](name, cat, ref, opt)
            kept = []
            for j in found:
                if not j["title"] or not j["url"] or EXCLUDE.search(j["title"]):
                    continue
                # API postings carry locations; page anchors usually do not
                if not swiss(j["location"]):
                    continue
                if is_romandie(j):
                    continue
                if opt.get("only") and classify(j["title"]) not in opt["only"]:
                    continue
                if opt.get("only"):
                    j["cat"] = classify(j["title"])
                if not professional(j):
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


# ---------- company directory (profile_directory.py) ----------
# Every timber / robotics firm in the commercial register plus the curated
# list, with the careers page or ATS feed found for each. Too many to read
# every run: each run reads ROTATE of them, least recently read first, and
# keeps what every firm last showed in profile_found.json.

DIRECTORY = os.path.join(HERE, "profile_companies.json")
FOUND = os.path.join(HERE, "profile_found.json")
ROTATE = int(os.environ.get("PROFILE_ROTATE") or 200)
STALE_DAYS = 4  # postings of a firm not read for this long drop out


def _name_key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _read_firm(e):
    import scraper  # career sublink heuristics shared with the architecture scan
    feed = e.get("feed")
    if feed and feed[0] in FETCHERS:
        return FETCHERS[feed[0]](e["name"], e["cat"], feed[1], {})
    body, final = http_get(e["careers"], tries=2, timeout=20)
    jobs = page_jobs(body, final, e["name"], e["cat"], {"strict": True})
    if not jobs:  # teaser page: the list sits one link further (or at an ATS)
        for u, _ in scraper._career_sublinks(body, final):
            try:
                b2, f2 = http_get(u, tries=1, timeout=15)
            except Exception:
                continue
            jobs += page_jobs(b2, f2, e["name"], e["cat"], {"strict": True})
    return jobs


def _keep_firm_job(j, e):
    if (not j["title"] or not j["url"] or EXCLUDE.search(j["title"])
            or OFF_DOMAIN.search(j["title"]) or is_romandie(j)):
        return False
    if not swiss(j["location"]):
        return False
    found_cat = classify(j["title"])
    # a register firm named "... Automation" also hires electricians and
    # PLC programmers for buildings: there only robotics/software titles count
    if e.get("source") == "zefix" and e["cat"] != "timber" and not found_cat:
        return False
    # big software / research employers (Google, ETH, Empa) list hundreds of
    # unrelated roles: there the title itself must score for the profile
    if e["cat"] in ("software", "research") and score(j["title"]) < 2:
        return False
    j["cat"] = found_cat or e["cat"]
    j["directory"] = True
    return professional(j)


def scan_directory(started):
    try:
        firms = json.load(open(DIRECTORY, encoding="utf-8"))
    except (OSError, ValueError):
        return [], [], {}
    try:
        found = json.load(open(FOUND, encoding="utf-8"))
    except (OSError, ValueError):
        found = {}
    known = [_name_key(c[0].split(" (")[0]) for c in COMPANIES]
    readable = {k: e for k, e in firms.items()
                if e.get("status") == "ok" and (e.get("careers") or e.get("feed"))
                and not any(_name_key(e["name"]).startswith(n) for n in known)}
    order = sorted(readable, key=lambda k: (found.get(k) or {}).get("checked", ""))
    batch = order[:ROTATE]
    now = started.isoformat(timespec="minutes")

    def read(k):
        try:
            return k, [j for j in _read_firm(readable[k])
                       if _keep_firm_job(j, readable[k])], None
        except Exception as ex:
            return k, None, str(ex)[:120]

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        for k, jobs, err in pool.map(read, batch):
            prev = found.get(k) or {}
            if jobs is None:  # failed: keep what it showed last time
                found[k] = dict(prev, checked=now, status="failed", detail=err)
            else:
                found[k] = {"checked": now, "status": "ok", "jobs": jobs}
    stale = (started - datetime.timedelta(days=STALE_DAYS)).isoformat(timespec="minutes")
    found = {k: v for k, v in found.items() if k in readable}
    with open(FOUND, "w", encoding="utf-8") as f:
        json.dump(found, f, ensure_ascii=False, indent=0, sort_keys=True)

    jobs, watch = [], []
    for k, v in found.items():
        if v.get("checked", "") < stale or not v.get("jobs"):
            continue
        e = readable[k]
        jobs += v["jobs"]
        watch.append({"name": e["name"], "cat": e["cat"],
                      "url": e.get("careers") or e.get("website"),
                      "status": "ok", "count": len(v["jobs"]), "dir": True})
    by_cat = {}
    for e in firms.values():
        if e.get("status") != "off-topic":
            by_cat[e["cat"]] = by_cat.get(e["cat"], 0) + 1
    stats = {"firms": sum(by_cat.values()), "by_cat": by_cat,
             "readable": len(readable),
             "read_recently": sum(1 for v in found.values()
                                  if v.get("checked", "") >= stale),
             "read_now": len(batch)}
    print(f"profile: directory read {len(batch)} of {len(readable)} firms, "
          f"{len(jobs)} postings", flush=True)
    return jobs, watch, stats


# ---------- job boards ----------

def _jobcloud(base, link_key, source, terms, pages=3):
    out, seen = [], set()
    for term in terms:
        for page in range(1, pages + 1):
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


def scan_linkedin(terms):
    out = []
    for term in terms:
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


def scan_jobroom(terms):
    from sources import jobroom
    out = []
    for term in terms:
        try:
            data = post_json(jobroom.API.format(page=0, size=jobroom.SIZE),
                             jobroom._body(term), headers=jobroom.HEADERS)
        except Exception:
            continue
        for w in data if isinstance(data, list) else []:
            j = jobroom._parse_item(w)
            if j:
                out.append(_job(None, j["title"], j["company_name"],
                                j["location"], j["url"], "job-room",
                                j["posted_at"]))
        time.sleep(0.8)
    return out


def scan_boards():
    # no jobup.ch: it is the French-speaking region's board (see ROMANDIE)
    raw = (_jobcloud("https://www.jobs.ch", "detail_en", "jobs.ch", BOARD_TERMS)
           + scan_jobroom(JOBROOM_TERMS) + scan_linkedin(LINKEDIN_TERMS))
    kept = []
    for j in raw:
        if (not j["title"] or EXCLUDE.search(j["title"])
                or OFF_DOMAIN.search(j["title"]) or is_romandie(j)
                or not swiss(j["location"])):
            continue
        cat = classify(j["title"])
        # "software" is the broadest bucket (every bank has DevOps roles),
        # so it needs one point more than the others
        need = MIN_BOARD_SCORE + (cat == "software")
        if not cat or score(j["title"]) < need:
            continue
        j["cat"] = cat
        if professional(j):
            kept.append(j)
    return kept


# ---------- CV match (profile_match.py) ----------

MATCH_TRIES = 3  # postings whose description would not load are retried


def match_cv(jobs, state):
    """Score every posting against the CV from its full description. Each
    URL is fetched once; the result lives in its profile_state.json entry."""
    todo = [j for j in jobs
            if "fit" not in state[j["url"]]
            or (not state[j["url"]]["fit"].get("desc")
                and state[j["url"]]["fit"].get("tries", 0) < MATCH_TRIES)]

    def one(j):
        return j["url"], profile_match.fit(j)

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        for url, f in pool.map(one, todo):
            f["tries"] = state[url].get("fit", {}).get("tries", 0) + 1
            state[url]["fit"] = f
    for j in jobs:
        f = state[j["url"]]["fit"]
        j["fit"], j["skills"], j["de"] = f["fit"], f["skills"], f["de"]
    print(f"profile: matched {len(todo)} new postings against the CV", flush=True)


# ---------- output ----------

def _key(j):
    t = re.sub(r"[^a-z0-9]+", "", j["title"].lower())
    c = re.sub(r"[^a-z0-9]+", "", j["company"].lower())
    return c + "|" + t


def main():
    started = datetime.datetime.now().astimezone()
    today = started.date().isoformat()
    try:
        company_jobs, watch = scan_companies()
    finally:
        if _browser is not None:
            _browser.close()
    read = [w for w in watch if w["status"] != "link"]
    print(f"profile: {len(company_jobs)} company postings from "
          f"{sum(w['status'] == 'ok' for w in read)}/{len(read)} firms", flush=True)
    for w in read:
        if w["status"] == "failed":
            print(f"  {w['name']}: {w['detail']}", flush=True)
    dir_jobs, dir_watch, dir_stats = [], [], {}
    try:
        dir_jobs, dir_watch, dir_stats = scan_directory(started)
    except Exception as e:
        print(f"profile: directory failed: {e}", flush=True)
    board_jobs = []
    try:
        board_jobs = scan_boards()
    except Exception as e:
        print(f"profile: boards failed: {e}", flush=True)
    print(f"profile: {len(board_jobs)} board postings", flush=True)

    # company feeds first: the same role on a board is a duplicate of it
    # "Not a fit" from the page (profile_feedback.py): those postings and
    # near-identical titles are never offered again
    rejected = profile_feedback.load()
    rejected_titles = [r["title"] for r in rejected.values() if r.get("title")]
    jobs, keys, dropped = [], set(), 0
    for j in company_jobs + dir_jobs + board_jobs:
        k = _key(j)
        if k in keys:
            continue
        keys.add(k)
        # applied here so stored results from older runs are filtered too
        if not swiss(j["location"]) or academic(j):
            continue
        if j["url"] in rejected or profile_feedback.similar(j["title"],
                                                           rejected_titles):
            dropped += 1
            continue
        j = dict(j)
        j.pop("directory", None)
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
    match_cv(jobs, state)
    cutoff = (started.date() - datetime.timedelta(days=FORGET_DAYS)).isoformat()
    state = {u: s for u, s in state.items() if s.get("last_seen", "") >= cutoff}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=0, sort_keys=True)

    jobs.sort(key=lambda j: (j["first_seen"], j["score"]), reverse=True)
    payload = {"generated": started.isoformat(timespec="minutes"),
               "categories": CATEGORIES, "weak_score": WEAK_SCORE,
               "fit_bands": [profile_match.STRONG, profile_match.GOOD],
               "jobs": jobs, "watch": watch + dir_watch,
               "directory": dir_stats,
               "rejected": {"count": len(rejected), "dropped": dropped}}
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
