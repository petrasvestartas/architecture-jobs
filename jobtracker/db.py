# -*- coding: utf-8 -*-
"""SQLite database for the Swiss architecture job tracker."""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobtracker.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY,
  hash TEXT UNIQUE,
  name TEXT NOT NULL,
  website TEXT, canton TEXT, street TEXT, plz TEXT, city TEXT,
  distance_km REAL, email TEXT, phone TEXT,
  branches TEXT, specialisations TEXT,
  rating INTEGER, status TEXT, sia_url TEXT,
  career_url TEXT, career_checked_at TEXT,
  applied INTEGER DEFAULT 0, applied_at TEXT, notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  title TEXT NOT NULL,
  company_name TEXT,
  company_id INTEGER REFERENCES companies(id),
  location TEXT,
  url TEXT UNIQUE NOT NULL,
  posted_at TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  active INTEGER DEFAULT 1,
  lat REAL, lon REAL, canton TEXT, workload TEXT,
  fingerprint TEXT, dup_of INTEGER
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  started TEXT, finished TEXT, source_stats TEXT, ok INTEGER
);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_jobs_active ON jobs(active);
"""

# columns added after the first release; migrate() adds them to existing DBs
MIGRATIONS = {
    "jobs": [("lat", "REAL"), ("lon", "REAL"), ("canton", "TEXT"),
             ("workload", "TEXT"), ("fingerprint", "TEXT"), ("dup_of", "INTEGER")],
    "companies": [("lat", "REAL"), ("lon", "REAL"), ("career_watched_at", "TEXT"),
                  ("geo_precision", "TEXT")],
}


def migrate(con):
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for name, typ in cols:
            if name not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
    con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_fingerprint ON jobs(fingerprint)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_dup ON jobs(dup_of)")
    con.commit()


def connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init():
    con = connect()
    con.executescript(SCHEMA)
    migrate(con)
    con.commit()
    con.close()


NORMALIZE_STRIP = (
    " ag", " gmbh", " sa", " s.a.", " sarl", " sàrl", " sagl", " ltd", " llc",
    " & co", " + co", " and partner", " und partner", " et associes",
)


def normalize_name(name):
    """Normalize a company name for fuzzy matching."""
    if not name:
        return ""
    s = str(name).lower().strip()
    for ch in ".,'\"()®™":
        s = s.replace(ch, "")
    s = (s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
         .replace("é", "e").replace("è", "e").replace("ê", "e")
         .replace("à", "a").replace("â", "a").replace("ç", "c"))
    s = " ".join(s.split())
    changed = True
    while changed:
        changed = False
        for suf in NORMALIZE_STRIP:
            if s.endswith(suf):
                s = s[: -len(suf)].strip()
                changed = True
    return s


WORKLOAD_RE = r"\(?\s*\d{2,3}\s*(?:[-–—]|bis)?\s*\d{0,3}\s*%\s*\)?"
GENDER_RE = r"\((?:m|w|f|d|h|x)(?:\s*/\s*(?:m|w|f|d|h|x)){0,3}\)|\*in\b|:in\b|/-?in\b"


def normalize_title(title):
    """Normalize a job title for cross-board dedup fingerprints."""
    import re
    if not title:
        return ""
    s = str(title).lower().strip()
    s = re.sub(WORKLOAD_RE, " ", s)
    s = re.sub(GENDER_RE, " ", s)
    s = (s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
         .replace("é", "e").replace("è", "e").replace("ê", "e")
         .replace("à", "a").replace("â", "a").replace("ç", "c"))
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(s.split())


if __name__ == "__main__":
    init()
    print("DB initialized at", DB_PATH)
