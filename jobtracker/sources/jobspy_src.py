"""Source: Indeed CH + Google Jobs via the python-jobspy package.

Library wrapper, not site probing: jobspy does its own HTTP. We run two
separate scrape_jobs() calls (one per site) so a failure/zero-result on one
site never loses the other's rows, then map the pandas DataFrame rows onto
the tracker's dict schema.

Known state as of 2026-07-11: Indeed CH works well; Google Jobs returns a
JS-only shell to jobspy's non-JS client ("initial cursor not found"), so the
google leg usually yields 0 rows. It is kept in anyway — harmless, and it
starts contributing again if jobspy/Google behavior changes.
"""

import sys


import re
import time

from net import http_get, get_json, post_json, strip_tags, extract_workload  # noqa: F401

SEARCH_TERM = "architekt"
GOOGLE_SEARCH_TERM = "architekt jobs schweiz"
RESULTS_WANTED = 150
HOURS_OLD = 168  # last 7 days

CANTONS = {
    "AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR", "JU", "LU",
    "NE", "NW", "OW", "SG", "SH", "SO", "SZ", "TG", "TI", "UR", "VD", "VS",
    "ZG", "ZH",
}

_WS = re.compile(r"\s+")


def _clean(s):
    return _WS.sub(" ", strip_tags(str(s))).strip()


def _cell(row, key, isna):
    """Safe DataFrame cell -> cleaned str ('' for missing/NaN)."""
    val = row.get(key)
    if val is None or isna(val):
        return ""
    return _clean(val)


def _split_location(loc):
    """'Zürich, ZH, CH' -> ('Zürich, ZH', 'ZH'); strips CH/Switzerland suffix."""
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    while parts and parts[-1].upper() in ("CH", "SWITZERLAND", "SCHWEIZ", "SUISSE", "SVIZZERA"):
        parts.pop()
    canton = ""
    if parts and parts[-1].upper() in CANTONS:
        canton = parts[-1].upper()
    return ", ".join(parts), canton


def scan_indeed_google():
    try:
        import pandas as pd
        from jobspy import scrape_jobs
    except ImportError as e:
        print(f"[jobspy] python-jobspy/pandas not installed, skipping source: {e}", file=sys.stderr)
        return []

    frames = []
    calls = [
        dict(
            site_name=["indeed"],
            search_term=SEARCH_TERM,
            location="Switzerland",
            country_indeed="Switzerland",
            results_wanted=RESULTS_WANTED,
            hours_old=HOURS_OLD,
            verbose=0,
        ),
        dict(
            site_name=["google"],
            search_term=SEARCH_TERM,
            google_search_term=GOOGLE_SEARCH_TERM,
            results_wanted=RESULTS_WANTED,
            hours_old=HOURS_OLD,
            verbose=0,
        ),
    ]
    for i, kw in enumerate(calls):
        try:
            df = scrape_jobs(**kw)
            if df is not None and len(df):
                frames.append(df)
        except Exception as e:  # rate limits, layout changes: tolerate per-site
            print(f"[jobspy] {kw['site_name']} failed: {e}", file=sys.stderr)
        if i < len(calls) - 1:
            time.sleep(1.0)  # politeness gap between the two sites

    if not frames:
        return []

    jobs, seen = [], set()
    for df in frames:
        for _, row in df.iterrows():
            title = _cell(row, "title", pd.isna)
            url = _cell(row, "job_url", pd.isna)
            if not title or not url.startswith(("http://", "https://")):
                continue
            url = url.split("#", 1)[0]
            if url in seen:
                continue
            seen.add(url)

            location, canton = _split_location(_cell(row, "location", pd.isna))

            posted_at = ""
            raw_date = row.get("date_posted")
            if raw_date is not None and not pd.isna(raw_date):
                posted_at = str(raw_date)[:10]
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", posted_at):
                    posted_at = ""

            job = {
                "title": title,
                "company_name": _cell(row, "company", pd.isna),
                "location": location,
                "url": url,
                "posted_at": posted_at,
            }
            if canton:
                job["canton"] = canton
            workload = extract_workload(title)
            if workload:
                job["workload"] = workload
            jobs.append(job)
    return jobs


if __name__ == "__main__":
    import json

    sys.stdout.reconfigure(encoding="utf-8")
    jobs = scan_indeed_google()
    print(json.dumps({"count": len(jobs), "sample": jobs[:3]}, ensure_ascii=False, indent=1))
