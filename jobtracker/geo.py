# -*- coding: utf-8 -*-
"""Offline geocoding for Swiss locations, backed by the bundled GeoNames
postal-code table (data/CH.txt). Lets the UI compute distances from any
user-chosen reference point instead of a precomputed km-to-Zurich column.

    python geo.py    # backfill lat/lon on jobs + companies, print coverage
"""
import math
import os
import re

import db

HERE = os.path.dirname(os.path.abspath(__file__))
CH_TXT = os.path.join(HERE, "data", "CH.txt")

_plz = None    # "3011" -> (lat, lon, name, canton)
_names = None  # folded place name -> (lat, lon, plz, canton)

# words that carry no place information in job-ad location strings
NOISE = {
    "region", "umgebung", "raum", "grossraum", "greater", "bei", "area",
    "agglomeration", "stadt", "city", "kanton", "canton", "de", "du",
    "schweiz", "switzerland", "suisse", "svizzera", "ch", "ganze",
    "deutschschweiz", "romandie", "mittelland", "oder", "und",
}

# spellings that differ from the GeoNames place names
ALIASES = {
    "berne": "bern", "geneva": "geneve", "genf": "geneve",
    "lucerne": "luzern", "basle": "basel", "bienne": "biel",
    "sankt gallen": "st gallen", "coire": "chur", "soleure": "solothurn",
    "argovie": "aarau", "turgovia": "frauenfeld",
}

# canton names that are not themselves city names -> canton capital
CANTON_TO_CAPITAL = {
    "aargau": "aarau", "graubuenden": "chur", "grisons": "chur",
    "thurgau": "frauenfeld", "wallis": "sion", "valais": "sion",
    "ticino": "bellinzona", "tessin": "bellinzona",
    "vaud": "lausanne", "waadt": "lausanne",
    "basel-landschaft": "liestal", "baselland": "liestal",
    "uri": "altdorf", "obwalden": "sarnen", "nidwalden": "stans",
    "neuenburg": "neuchatel", "freiburg": "fribourg", "jura": "delemont",
}


def _fold(s):
    s = str(s).lower().strip()
    s = (s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
         .replace("é", "e").replace("è", "e").replace("ê", "e").replace("ë", "e")
         .replace("à", "a").replace("â", "a").replace("ô", "o").replace("î", "i")
         .replace("û", "u").replace("ç", "c").replace("ß", "ss"))
    s = re.sub(r"[^a-z0-9\- ]+", " ", s)
    return " ".join(s.split())


def _load():
    global _plz, _names
    if _plz is not None:
        return
    _plz, _names = {}, {}
    rows = []
    with open(CH_TXT, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 11 or p[0] != "CH":
                continue
            plz, name, canton = p[1], p[2], p[4]
            try:
                lat, lon = float(p[9]), float(p[10])
            except ValueError:
                continue
            _plz.setdefault(plz, (lat, lon, name, canton))
            rows.append((plz, name, canton, lat, lon))
    # lowest PLZ first: for same-named places the central-city postal code
    # (8001 Zürich, 3000 Bern) wins over outskirts like 8058 Flughafen
    rows.sort(key=lambda r: r[0])
    # pass 1: exact folded names claim their key first ("Zürich" beats
    # variants derived from "Zürich (Kreis 11)" or de-umlauting)
    for plz, name, canton, lat, lon in rows:
        _names.setdefault(_fold(name), (lat, lon, plz, canton))
    # pass 2: head of compound names ("Zürich (Kreis 1)", "Biel/Bienne"),
    # canton-suffix stems ("Rapperswil BE" -> "rapperswil"), and de-umlauted
    # spellings as fallbacks; split raw name BEFORE folding
    canton_suffix = re.compile(
        r"^(.*\S)\s+(ag|ai|ar|be|bl|bs|fr|ge|gl|gr|ju|lu|ne|nw|ow|sg|sh|so|"
        r"sz|tg|ti|ur|vd|vs|zg|zh)$")
    for plz, name, canton, lat, lon in rows:
        folded = _fold(name)
        variants = {folded.replace("ue", "u").replace("ae", "a").replace("oe", "o")}
        head = _fold(name.split("(")[0].split("/")[0])
        if head:
            variants.add(head)
            variants.add(head.replace("ue", "u").replace("ae", "a").replace("oe", "o"))
        m = canton_suffix.match(folded)
        if m and m.group(2) == canton.lower():
            variants.add(m.group(1))
        for v in variants:
            _names.setdefault(v, (lat, lon, plz, canton))


def lookup_place(name):
    """Fold + alias a bare place name; return (lat, lon, canton) or None."""
    _load()
    v = _fold(name)
    v = ALIASES.get(v, v)
    v = CANTON_TO_CAPITAL.get(v, v)
    hit = _names.get(v)
    return (hit[0], hit[1], hit[3]) if hit else None


def geocode(location):
    """Geocode a free-text job location string. Returns (lat, lon, canton)
    or None. Never guesses: unresolved strings stay un-geocoded."""
    if not location:
        return None
    _load()
    s = str(location)
    m = re.search(r"\b([1-9]\d{3})\b", s)
    if m and m.group(1) in _plz:
        lat, lon, _, canton = _plz[m.group(1)]
        return lat, lon, canton
    parts = re.split(r"[,/|;•()]|\s+und\s+|\s+oder\s+|\s+ou\s+|\s+et\s+|\s+or\s+", s)
    tokens = []
    for part in parts:
        folded = _fold(part)
        if not folded:
            continue
        tokens.append(folded)
        cleaned = " ".join(w for w in folded.split() if w not in NOISE)
        if cleaned and cleaned != folded:
            tokens.append(cleaned)
    for tok in sorted(set(tokens), key=len, reverse=True):
        hit = lookup_place(tok)
        if hit:
            return hit
    return None


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def places():
    """All known places as [name, plz, lat, lon, canton] for the UI picker."""
    _load()
    out, seen = [], set()
    with open(CH_TXT, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 11 or p[0] != "CH":
                continue
            name = p[2].split("(")[0].split("/")[0].strip()
            key = (name, p[1])
            if key in seen:
                continue
            seen.add(key)
            try:
                out.append([name, p[1], round(float(p[9]), 4), round(float(p[10]), 4), p[4]])
            except ValueError:
                continue
    return out


def backfill(con):
    """Fill missing lat/lon on jobs (from location text) and companies
    (from plz, then city). Cheap: only touches NULL rows."""
    jn = cn = 0
    for r in con.execute("SELECT id, location FROM jobs "
                         "WHERE lat IS NULL AND location IS NOT NULL AND location != ''").fetchall():
        hit = geocode(r["location"])
        if hit:
            con.execute("UPDATE jobs SET lat=?, lon=?, canton=? WHERE id=?",
                        (hit[0], hit[1], hit[2], r["id"]))
            jn += 1
    for r in con.execute("SELECT id, plz, city FROM companies WHERE lat IS NULL").fetchall():
        _load()
        hit = None
        if r["plz"] and str(r["plz"]).strip() in _plz:
            lat, lon, _, canton = _plz[str(r["plz"]).strip()]
            hit = (lat, lon, canton)
        elif r["city"]:
            hit = lookup_place(r["city"])
        if hit:
            con.execute("UPDATE companies SET lat=?, lon=? WHERE id=?",
                        (hit[0], hit[1], r["id"]))
            cn += 1
    # jobs whose location text could not be geocoded but that are matched to
    # a known firm inherit the firm's coordinates and address
    con.execute("""
        UPDATE jobs SET
          lat = (SELECT c.lat FROM companies c WHERE c.id = jobs.company_id),
          lon = (SELECT c.lon FROM companies c WHERE c.id = jobs.company_id),
          canton = COALESCE(NULLIF(jobs.canton, ''),
                   (SELECT c.canton FROM companies c WHERE c.id = jobs.company_id))
        WHERE jobs.lat IS NULL AND jobs.company_id IS NOT NULL""")
    con.execute("""
        UPDATE jobs SET location =
          (SELECT TRIM(COALESCE(c.plz || ' ', '') || COALESCE(c.city, ''))
           FROM companies c WHERE c.id = jobs.company_id)
        WHERE (jobs.location IS NULL OR jobs.location = '')
          AND jobs.company_id IS NOT NULL""")
    con.commit()
    return jn, cn


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for probe in ("Bern", "8001 Zürich", "Zurich", "Genève", "La Chaux-de-Fonds",
                  "Region Basel", "Bern, Bern, Switzerland", "Lausanne / Genf",
                  "Kanton Aargau", "Espace Mittelland"):
        print(f"  geocode({probe!r}) -> {geocode(probe)}")
    db.init()
    con = db.connect()
    jn, cn = backfill(con)
    jt = con.execute("SELECT COUNT(*) c FROM jobs WHERE lat IS NOT NULL").fetchone()["c"]
    ja = con.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
    ct = con.execute("SELECT COUNT(*) c FROM companies WHERE lat IS NOT NULL").fetchone()["c"]
    ca = con.execute("SELECT COUNT(*) c FROM companies").fetchone()["c"]
    print(f"backfilled {jn} jobs, {cn} companies | coverage: jobs {jt}/{ja}, companies {ct}/{ca}")
    con.close()
