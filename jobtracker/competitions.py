# -*- coding: utf-8 -*-
"""Competition-result tracker: offices that just won (or placed in) an
architecture competition are prime speculative-application targets — they
staff up before any job ad appears.

Scans espazium.ch's Wettbewerbe articles (the publisher of TEC21/Tracés/
Archi; freely accessible, unlike konkurado's paywalled lists), matches the
article text against the known-office list by normalized name, and stamps
matched companies with won_at/won_ref. The website badges them and the
career watchdog re-checks them daily.

Run via  python scraper.py --competitions  (the 05:30 cloud full scan).
"""
import re
import sys
import datetime

import db
from net import http_get, strip_tags

SECTIONS = [
    "https://www.espazium.ch/de/aktuelles/wettbewerbe",
    "https://www.espazium.ch/fr/actualites/concours",
]
ARTICLE = re.compile(r'href="(/(?:de/aktuelles|fr/actualites)/[a-z0-9-]+)"')
SKIP_SLUG = re.compile(r"interview|magazin|manifest|espazium-|newsletter", re.I)


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _norm_text(s):
    """Same folding as db.normalize_name, applied to running text."""
    s = s.lower()
    for ch in ".,'\"()®™":
        s = s.replace(ch, "")
    s = (s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
         .replace("é", "e").replace("è", "e").replace("ê", "e")
         .replace("à", "a").replace("â", "a").replace("ç", "c"))
    return " " + " ".join(s.split()) + " "


def scan_competitions(con, pages=1, verbose=True):
    """Fetch recent competition articles, match office names, stamp winners.
    Idempotent: an article only ever adds stamps, never clears them."""
    # offices with a usable (distinctive) normalized name: single short
    # tokens like "atelier" would match everything
    companies = []
    for r in con.execute("SELECT id, name FROM companies WHERE status='ok'"):
        n = db.normalize_name(r["name"])
        if len(n) >= 8 or (len(n.split()) >= 2 and len(n) >= 6):
            companies.append((r["id"], " " + n + " "))

    seen = {r["won_ref"] for r in
            con.execute("SELECT DISTINCT won_ref FROM companies "
                        "WHERE won_ref IS NOT NULL")}
    stamped = 0
    for section in SECTIONS:
        try:
            body, final = http_get(section, tries=2, timeout=20)
        except Exception as e:
            if verbose:
                print(f"  competitions: {section} failed ({e})", flush=True)
            continue
        links = []
        for m in ARTICLE.finditer(body):
            path = m.group(1)
            if SKIP_SLUG.search(path) or path.endswith(("/wettbewerbe", "/concours")):
                continue
            url = "https://www.espazium.ch" + path
            if url not in links:
                links.append(url)
        for url in links[:25]:
            if url in seen:
                continue  # already processed this article
            try:
                art, _ = http_get(url, tries=1, timeout=20)
            except Exception:
                continue
            text = _norm_text(strip_tags(art))
            hits = [cid for cid, n in companies if n in text]
            for cid in hits:
                con.execute("""UPDATE companies SET won_at=?, won_ref=?
                               WHERE id=? AND (won_at IS NULL OR won_at < ?)""",
                            (now(), url, cid, now()))
            stamped += len(hits)
            if verbose and hits:
                names = [con.execute("SELECT name FROM companies WHERE id=?",
                                     (cid,)).fetchone()["name"] for cid in hits[:4]]
                print(f"  competitions: {url.rsplit('/', 1)[-1][:46]} -> "
                      f"{', '.join(names)}", flush=True)
    con.commit()
    if verbose:
        print(f"  competitions: stamped {stamped} office mentions", flush=True)
    return stamped


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    db.init()
    con = db.connect()
    scan_competitions(con)
    con.close()
