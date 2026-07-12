# -*- coding: utf-8 -*-
"""Address-precise coordinates for offices via the official Swiss GeoAdmin
geocoder (api3.geo.admin.ch, free, no key). One-off/occasional:

    python geo_precise.py            # geocode all not-yet-precise offices
    python geo_precise.py --limit 50 # test run

Companies get geo_precision='address' when an address match is found; rows
without a match keep their PLZ-centroid coordinates (geo_precision='plz').
"""
import json
import sys
import time
import urllib.parse

import db
from net import http_get

API = ("https://api3.geo.admin.ch/rest/services/api/SearchServer"
       "?type=locations&origins=address&limit=1&searchText=")


def lookup(street, plz, city):
    q = " ".join(str(x).strip() for x in (street, plz, city) if x and str(x).strip())
    if not q:
        return None
    try:
        body, _ = http_get(API + urllib.parse.quote(q), accept="application/json",
                           tries=1, timeout=15)
        results = json.loads(body).get("results") or []
        if not results:
            return None
        a = results[0].get("attrs") or {}
        lat, lon = a.get("lat"), a.get("lon")
        if lat and lon and 45.6 < float(lat) < 48.0 and 5.8 < float(lon) < 10.7:
            return float(lat), float(lon)
    except Exception:
        return None
    return None


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    db.init()
    con = db.connect()
    rows = con.execute("""
        SELECT id, name, street, plz, city FROM companies
        WHERE status='ok' AND street != '' AND street IS NOT NULL
          AND (geo_precision IS NULL OR geo_precision != 'address')
        ORDER BY id""").fetchall()
    if limit:
        rows = rows[:limit]
    print(f"geocoding {len(rows)} office addresses via geo.admin.ch...", flush=True)
    hit = miss = 0
    for i, r in enumerate(rows, 1):
        res = lookup(r["street"], r["plz"], r["city"])
        if res:
            con.execute("UPDATE companies SET lat=?, lon=?, geo_precision='address' "
                        "WHERE id=?", (res[0], res[1], r["id"]))
            hit += 1
        else:
            con.execute("UPDATE companies SET geo_precision='plz' WHERE id=?",
                        (r["id"],))
            miss += 1
        if i % 100 == 0:
            con.commit()
            print(f"  {i}/{len(rows)} ({hit} precise)", flush=True)
        time.sleep(0.12)
    con.commit()
    print(f"done: {hit} address-precise, {miss} kept PLZ centroid", flush=True)
    con.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
