# -*- coding: utf-8 -*-
""""Not a fit" feedback from the profile page.

profile.html keeps the postings Petras marks "Not a fit" in the browser and
sends them as a GitHub issue (label profile-feedback) through a prefilled
new-issue link. This script, run by the scan workflow before
profile_jobs.py, folds those issues into profile_rejected.json and closes
them. profile_jobs.py then drops the rejected postings and every posting
with a near-identical title (see similar()).

Only issues opened by the repository owner count: the repo is public.

    GH_TOKEN=... python profile_feedback.py
"""
import datetime
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REJECTED = os.path.join(HERE, "profile_rejected.json")
OWNER = os.environ.get("PROFILE_FEEDBACK_OWNER", "petrasvestartas")
LABEL = "profile-feedback"
# issue body lines: "- <url> | <title> | <company>"
LINE = re.compile(r"^\s*[-*]\s*(https?://\S+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*$")

# words that say nothing about what the job is
STOP = {"m", "w", "d", "f", "x", "a", "h", "in", "innen", "und", "and", "or",
        "oder", "of", "the", "for", "fur", "de", "du", "la", "le", "mit",
        "with", "senior", "junior", "sr", "jr", "lead", "staff", "principal",
        "all", "alle", "genders", "per", "sofort", "nach", "vereinbarung",
        "zurich", "zuerich", "switzerland", "schweiz", "ch", "remote"}


def tokens(title):
    s = title.lower()
    for a, b in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("é", "e"), ("è", "e")):
        s = s.replace(a, b)
    s = re.sub(r"\d+\s*(?:-|–|bis)?\s*\d*\s*%", " ", s)  # workload
    return {t for t in re.split(r"[^a-z0-9+#]+", s) if t and t not in STOP
            and not t.isdigit()}


def similar(title, rejected_titles):
    """Near-identical role: Jaccard >= 0.6 on the title's content words."""
    a = tokens(title)
    if not a:
        return False
    for t in rejected_titles:
        b = tokens(t)
        if b and len(a & b) / len(a | b) >= 0.6:
            return True
    return False


def load():
    try:
        return json.load(open(REJECTED, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout


def main():
    try:
        issues = json.loads(gh("issue", "list", "--label", LABEL, "--state",
                               "open", "--limit", "50", "--json",
                               "number,body,author"))
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"feedback: cannot list issues: {e}", flush=True)
        return 0
    rejected = load()
    today = datetime.date.today().isoformat()
    added = 0
    for issue in issues:
        if (issue.get("author") or {}).get("login") != OWNER:
            continue
        for line in (issue.get("body") or "").splitlines():
            m = LINE.match(line)
            if m and m.group(1) not in rejected:
                rejected[m.group(1)] = {"title": m.group(2),
                                        "company": m.group(3), "date": today}
                added += 1
        gh("issue", "close", str(issue["number"]), "--comment",
           "Recorded in jobtracker/profile_rejected.json; these postings and "
           "near-identical ones are no longer offered.")
    with open(REJECTED, "w", encoding="utf-8") as f:
        json.dump(rejected, f, ensure_ascii=False, indent=0, sort_keys=True)
    print(f"feedback: {added} postings marked not a fit "
          f"({len(rejected)} in total)", flush=True)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
