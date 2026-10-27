# -*- coding: utf-8 -*-
"""Company directory for the profile scan (profile_jobs.py).

Builds and maintains profile_companies.json: every timber and robotics firm
in the Swiss commercial register (Zefix) whose name says what it does, plus
the hand-researched list in profile_curated.json (firms whose names do not:
Renggli, Hilti, ANYbotics, geometry/CAD software houses, research institutes).
For each firm it finds the website, the careers page and, where the careers
page embeds a known applicant-tracking system (Personio, Lever, Recruitee,
...), that system's machine-readable feed. profile_jobs.py then reads the
firms' own job lists instead of relying on jobs.ch.

French-speaking Switzerland is skipped (user's choice): firms registered in
GE, VD, NE, JU, FR and French-speaking Valais.

Discovery is incremental and resumable; each run handles at most --limit
firms (oldest first), so the daily scan keeps it current:

    python profile_directory.py --limit 300
"""
import argparse
import concurrent.futures
import datetime
import json
import os
import re
import socket
import sys
import unicodedata
import urllib.parse

from net import http_get, post_json, get_json

HERE = os.path.dirname(os.path.abspath(__file__))
DIRECTORY = os.path.join(HERE, "profile_companies.json")
CURATED = os.path.join(HERE, "profile_curated.json")

ZEFIX = "https://www.zefix.admin.ch/ZefixREST/api/v1/firm"
# name substrings searched in the register, per category. "automation" and
# "drohne" names are kept only when the registered purpose mentions robots
# or autonomous machines (most automation firms do building/electrical work)
ZEFIX_TERMS = {
    "timber": ["holzbau", "zimmerei", "leimbau", "holzsystem", "elementbau",
               "holztechnik", "holzingenieur", "holz-ingenieur", "timber"],
    "robotics": ["robot", "drohne", "drone", "automation", "mechatronik",
                 "autonom"],
}
PURPOSE_CHECK = {"automation", "drohne", "drone", "mechatronik", "autonom"}
ROBOT_PURPOSE = re.compile(
    r"robot|autonom|drohne|drone|unbemannt|mechatron|kinemat|\bki\b|"
    r"k[uü]nstliche intelligenz|bildverarbeitung|machine vision", re.I)
# register offices by canton code (from the cantonal excerpt host); the
# French-speaking cantons are out, Valais only by seat (see ROMANDIE_SEAT)
OUT_CANTONS = {"GE", "VD", "NE", "JU", "FR"}
ROMANDIE_SEAT = re.compile(
    r"sion|martigny|monthey|sierre|conthey|sav[iè]se|fully|saxon|riddes|"
    r"vétroz|vetroz|ardon|chamoson|leytron|saillon|bagnes|orsi[eè]res|"
    r"saint-maurice|st-maurice|collombey|vouvry|port-valais|troistorrents|"
    r"val-d'illiez|nendaz|anniviers|crans-montana|lens|chalais|chippis|"
    r"grône|grone|h[ée]r[ée]mence|evol[eè]ne|ayent|arbaz|grimisuat|"
    r"salvan|vernayaz|dor[ée]naz|charrat|bovernier|sembrancher|vollèges|"
    r"evionnaz|massongex|vionnaz|st-gingolph|saint-gingolph|mont-noble|"
    r"vex\b|mase|nax|bramois|uvrier|granges|noës", re.I)
RETRY_DAYS = 60  # re-probe a firm without website/careers after this long
LEGAL = re.compile(
    r"\b(ag|sa|gmbh|sàrl|sarl|sagl|kig|klg|kollektivgesellschaft|"
    r"kommanditgesellschaft|genossenschaft|cooperative|stiftung|"
    r"in liquidation|in liq\.?|& co\.?|und co\.?|ltd\.?|llc|inc\.?|"
    r"einzelunternehmen|holding)\b", re.I)
# words that never identify a firm on their own
GENERIC = {"holzbau", "zimmerei", "holz", "bau", "und", "the", "der", "die",
           "das", "von", "zum", "zur", "am", "an", "im", "in", "de", "du",
           "la", "le", "les", "et", "robotics", "robotik", "robot", "robots",
           "automation", "technik", "technologies", "technology", "tech",
           "systems", "system", "solutions", "swiss", "schweiz", "suisse",
           "leimbau", "elementbau", "holztechnik", "timber", "engineering",
           "drohnen", "drones", "drone", "mechatronik", "group", "gruppe",
           "partner", "söhne", "soehne", "sohn", "gebr", "gebrüder", "ing",
           "dipl", "innenausbau", "schreinerei", "bedachungen", "fenster",
           "treppen", "sägerei", "saegerei", "co", "cie", "services",
           "service", "ch", "team", "werk", "werkstatt", "zimmerei-"}
# a homepage only counts for a firm when it also mentions the trade
TRADE = {
    "timber": re.compile(r"holz|zimmer|timber|\bbois\b|legno", re.I),
    "robotics": re.compile(r"robot|automati|drohne|drone|autonom|mechatron", re.I),
}

# ---------- applicant-tracking systems ----------
# (kind, regex on the careers page HTML or its final URL) — the first group
# is the account slug the fetchers in profile_jobs.py take
ATS = [
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]+)")),
    ("greenhouse", re.compile(
        r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?"
        r"([A-Za-z0-9_-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)")),
    ("workable", re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)")),
    ("personio", re.compile(r"([A-Za-z0-9-]+)\.jobs\.personio\.(?:de|com)")),
    ("recruitee", re.compile(r"([A-Za-z0-9-]+)\.recruitee\.com")),
    ("smartrecruiters", re.compile(
        r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_-]+)")),
    ("breezy", re.compile(r"([A-Za-z0-9-]+)\.breezy\.hr")),
    ("bamboo", re.compile(r"([A-Za-z0-9-]+)\.bamboohr\.com")),
    ("teamtailor", re.compile(r"([A-Za-z0-9-]+)\.teamtailor\.com")),
]
ATS_SKIP = {"www", "api", "boards", "app", "jobs", "careers", "embed", "assets",
            "static", "cdn", "help", "support", "blog", "v1", "js"}


def detect_feed(html, url):
    for kind, rx in ATS:
        for m in rx.finditer(url + " " + html):
            slug = urllib.parse.unquote(m.group(1)).strip(".")
            if slug.lower() not in ATS_SKIP and len(slug) >= 2:
                return [kind, slug]
    return None


# ---------- register ----------

def _canton(firm):
    host = urllib.parse.urlparse(firm.get("cantonalExcerptWeb") or "").netloc
    m = re.match(r"([a-z]{2})\.chregister\.ch$", host)
    if m:
        return {"vo": "VS"}.get(m.group(1), m.group(1).upper())
    if "fr.ch" in host:
        return "FR"
    if "vd.ch" in host:
        return "VD"
    if "ne.ch" in host:
        return "NE"
    if "ge.ch" in host:
        return "GE"
    return ""


def zefix_firms():
    """{uid: entry} for every active register firm matching ZEFIX_TERMS."""
    found = {}
    for cat, terms in ZEFIX_TERMS.items():
        for term in terms:
            try:
                d = post_json(ZEFIX + "/search.json",
                              {"name": term, "languageKey": "de",
                               "maxEntries": 3000, "offset": 0})
            except Exception as e:
                print(f"  zefix {term}: {e}", flush=True)
                continue
            for f in d.get("list") or []:
                if f.get("status") != "EXISTIEREND" or f["uid"] in found:
                    continue
                canton = _canton(f)
                seat = f.get("legalSeat") or ""
                if canton in OUT_CANTONS or (canton == "VS" and
                                             ROMANDIE_SEAT.search(seat)):
                    continue
                found[f["uid"]] = {"name": f["name"], "cat": cat,
                                   "uid": f["uid"], "ehraid": f.get("ehraid"),
                                   "seat": seat, "canton": canton,
                                   "source": "zefix",
                                   "check_purpose": term in PURPOSE_CHECK}
    return found


def purpose_ok(entry):
    try:
        d = get_json(f"{ZEFIX}/{entry['ehraid']}.json", tries=2, timeout=20)
    except Exception:
        return None  # unknown: ask again next run
    return bool(ROBOT_PURPOSE.search((d.get("purpose") or "") + " " + entry["name"]))


# ---------- websites ----------

def _ascii(s, umlaut):
    s = s.lower()
    if umlaut:
        for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue")):
            s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s


def _tokens(name, umlaut=True):
    s = LEGAL.sub(" ", name)
    s = re.sub(r"[&+/,.()'’\"]", " ", s)
    return [t for t in re.split(r"[\s]+", _ascii(s, umlaut)) if t]


def domain_guesses(name):
    """Plausible .ch domains for a register name, most specific first."""
    out = []
    for umlaut in (True, False):
        toks = [re.sub(r"[^a-z0-9-]", "", t) for t in _tokens(name, umlaut)]
        toks = [t for t in toks if t]
        if not toks:
            continue
        distinct = [t for t in toks if t not in GENERIC and len(t) >= 3]
        cands = ["-".join(toks), "".join(toks)]
        if len(toks) > 2:
            cands += ["-".join(toks[:2]), "".join(toks[:2])]
        if distinct:
            d = distinct[0]
            cands += [d, d + "-holzbau", "holzbau-" + d, d + "holzbau",
                      d + "-ag", d + "ag"]
        for c in cands:
            c = c.strip("-")
            if 3 <= len(c) <= 50 and c not in out:
                out.append(c)
    return [c + ".ch" for c in out][:12]


def _resolves(host):
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        try:
            socket.getaddrinfo("www." + host, 443)
            return True
        except OSError:
            return False


def _mentions(body, entry):
    """Homepage belongs to the firm: names its distinctive word and trade."""
    text = _ascii(re.sub(r"<[^>]+>", " ", body), True)
    words = [t for t in _tokens(entry["name"]) if t not in GENERIC and len(t) >= 3]
    # every one of the first two distinctive words: "Albert und Peter
    # Hauser Holzbau" is not albert.ch unless that page also says "peter"
    if words and not all(w in text for w in words[:2]):
        return False
    rx = TRADE.get(entry["cat"])
    return bool(rx.search(body)) if rx else True


TRADE_DOMAIN = re.compile(r"holz|zimmer|timber|robot|automat|drohn|drone|"
                          r"mechatron|bau", re.I)


def plausible(dom, body, entry):
    """A bare-name domain (nussbaum.ch, albert.ch) often belongs to another
    firm of that name: accept it only when the homepage names the firm's
    seat. Domains with the trade or two name words in them pass on _mentions."""
    if not _mentions(body, entry):
        return False
    stem = dom[:-3]
    if TRADE_DOMAIN.search(stem) or "-" in stem:
        return True
    words = [t for t in _tokens(entry["name"]) if t not in GENERIC and len(t) >= 3]
    if len(words) >= 2 and "".join(words[:2]) in stem:
        return True
    seat = _ascii(re.sub(r"\s*\(.*", "", entry.get("seat") or ""), True)
    text = _ascii(re.sub(r"<[^>]+>", " ", body), True)
    return bool(seat) and seat in text


def find_website(entry):
    for dom in domain_guesses(entry["name"]):
        if not _resolves(dom):
            continue
        for url in ("https://www." + dom, "https://" + dom):
            try:
                body, final = http_get(url, tries=1, timeout=12)
            except Exception:
                continue
            if plausible(dom, body, entry):
                p = urllib.parse.urlparse(final)
                return f"{p.scheme}://{p.netloc}/", body, final
            break
    return "", None, None


# ---------- careers pages ----------

def find_careers(website, body=None, final=None):
    """(careers_url, feed) for a homepage; feed is [kind, slug] or None."""
    import scraper  # career-link heuristics shared with the architecture scan
    if body is None:
        body, final = http_get(website, tries=1, timeout=15)
    feed = detect_feed(body, final)
    if feed:
        return final, feed
    # only links whose path names jobs/careers ("/team", "/buero" are not)
    cands = [c for c in scraper._career_candidates(body, final)
             if c[0] >= 1 and scraper.CAREER_WORD.search(
                 urllib.parse.urlparse(c[3]).path)]
    url = cands[0][3] if cands else ""
    if not url:
        url = scraper._career_fallback(final)
    if not url:
        return "", None
    try:
        b2, f2 = http_get(url, tries=1, timeout=15)
    except Exception:
        return url, None
    feed = detect_feed(b2, f2)
    if not feed:  # the careers page links out to the ATS listing
        for u, ats in scraper._career_sublinks(b2, f2):
            feed = detect_feed("", u) if ats else None
            if feed:
                break
    return f2, feed


# ---------- directory ----------

def _key(entry):
    return entry.get("uid") or "curated:" + re.sub(
        r"[^a-z0-9]+", "", _ascii(entry["name"], True))


def load():
    try:
        return json.load(open(DIRECTORY, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(d):
    with open(DIRECTORY, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=0, sort_keys=True)


def curated():
    try:
        rows = json.load(open(CURATED, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for r in rows:
        e = {"name": r["name"], "cat": r["cat"], "source": "curated",
             "seat": r.get("city") or "", "website": r.get("website") or "",
             "careers": r.get("careers") or ""}
        out[_key(e)] = e
    return out


def probe(entry, today):
    """One discovery step for a firm; returns the updated entry."""
    e = dict(entry)
    try:
        if e.get("check_purpose"):
            ok = purpose_ok(e)
            if ok is None:
                return e
            e["check_purpose"] = False
            if not ok:
                e["status"] = "off-topic"
                e["probed"] = today
                return e
        body = final = None
        if not e.get("website"):
            e["website"], body, final = find_website(e)
            if not e["website"]:
                e["status"] = "no website"
                e["probed"] = today
                return e
        careers = e.get("careers") or ""
        if careers:  # curated: careers known, only look for a feed
            try:
                b, f = http_get(careers, tries=1, timeout=15)
                e["feed"] = detect_feed(b, f)
            except Exception:
                e["feed"] = None
        else:
            e["careers"], e["feed"] = find_careers(e["website"], body, final)
        e["status"] = "ok" if (e["careers"] or e.get("feed")) else "no careers page"
    except Exception as ex:
        e["status"] = "error: " + str(ex)[:80]
    e["probed"] = today
    return e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=300,
                    help="firms to probe this run (oldest first)")
    ap.add_argument("--no-register", action="store_true",
                    help="skip the Zefix refresh (probe only)")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()
    today = datetime.date.today().isoformat()
    d = load()

    fresh = curated()
    if not args.no_register:
        reg = zefix_firms()
        print(f"directory: {len(reg)} register firms", flush=True)
        fresh.update(reg)
    for k, e in fresh.items():
        if k not in d:
            d[k] = e
        elif e.get("source") == "curated":  # the curated list wins on facts
            for f in ("name", "cat", "seat"):
                d[k][f] = e[f]
            for f in ("website", "careers"):
                if e[f] and e[f] != d[k].get(f):
                    d[k][f] = e[f]
                    d[k].pop("probed", None)
    if not args.no_register and fresh:
        # firms that left the register (deleted, in liquidation) drop out
        for k in [k for k, e in d.items()
                  if e.get("source") == "zefix" and k not in fresh]:
            del d[k]

    retry = (datetime.date.today() - datetime.timedelta(days=RETRY_DAYS)).isoformat()
    todo = [k for k, e in d.items()
            if not e.get("probed")
            or (e.get("status") != "ok" and e.get("status") != "off-topic"
                and e["probed"] < retry)
            or (e.get("status") == "ok" and e["probed"] < retry)]
    todo.sort(key=lambda k: (d[k].get("source") != "curated",
                             d[k].get("probed") or ""))
    todo = todo[:args.limit]
    print(f"directory: probing {len(todo)} of {len(d)} firms", flush=True)
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        for k, e in zip(todo, pool.map(lambda k: probe(d[k], today), todo)):
            d[k] = e
    save(d)
    st = {}
    for e in d.values():
        s = e.get("status") or "pending"
        s = "error" if s.startswith("error") else s
        st[s] = st.get(s, 0) + 1
    feeds = sum(1 for e in d.values() if e.get("feed"))
    print(f"directory: {len(d)} firms {st}, {feeds} with an ATS feed", flush=True)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
