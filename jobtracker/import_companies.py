# -*- coding: utf-8 -*-
"""Import the corrected SIA company list (xlsx) into the tracker DB.

Re-runnable: upserts by SIA hash, never overwrites `applied`/`notes`.
"""
import os
import pandas as pd
import db

XLSX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "sia_companies_zurich_proximity_v2.xlsx")


def main():
    db.init()
    con = db.connect()
    xl = pd.ExcelFile(XLSX)
    main_df = xl.parse("companies")
    missing_df = xl.parse("missing_from_file")

    def upsert(row, sia_url, rating=None, status="ok"):
        h = str(sia_url).rsplit("/", 1)[-1]
        vals = dict(
            hash=h, name=row.get("name"), website=row.get("website") or "",
            canton=row.get("canton") or "", street=row.get("street") or "",
            plz=str(row.get("plz") or ""), city=row.get("city") or "",
            distance_km=row.get("distance_km_zurich"),
            email=row.get("email") or "", phone=row.get("phone") or "",
            branches=row.get("branches") or "",
            specialisations=row.get("specialisations") or "",
            rating=rating, status=status, sia_url=sia_url,
        )
        vals = {k: (None if pd.isna(v) else v) if not isinstance(v, str) else v
                for k, v in vals.items()}
        con.execute("""
            INSERT INTO companies (hash, name, website, canton, street, plz, city,
                distance_km, email, phone, branches, specialisations, rating, status, sia_url)
            VALUES (:hash,:name,:website,:canton,:street,:plz,:city,
                :distance_km,:email,:phone,:branches,:specialisations,:rating,:status,:sia_url)
            ON CONFLICT(hash) DO UPDATE SET
                name=excluded.name, website=excluded.website, canton=excluded.canton,
                street=excluded.street, plz=excluded.plz, city=excluded.city,
                distance_km=excluded.distance_km, email=excluded.email, phone=excluded.phone,
                branches=excluded.branches, specialisations=excluded.specialisations,
                rating=excluded.rating, status=excluded.status, sia_url=excluded.sia_url
        """, vals)

    for _, r in main_df.iterrows():
        upsert(r, r["sia_url"], rating=int(r["architecture_rating"]) if pd.notna(r.get("architecture_rating")) else None,
               status=r.get("status") or "ok")
    for _, r in missing_df.iterrows():
        upsert(r, r["sia_url"], rating=None, status="ok")

    con.commit()
    n = con.execute("SELECT COUNT(*) c FROM companies").fetchone()["c"]
    print(f"companies in DB: {n}")
    con.close()


if __name__ == "__main__":
    main()
