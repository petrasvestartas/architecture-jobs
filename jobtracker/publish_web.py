# -*- coding: utf-8 -*-
"""Publish the static site to GitHub Pages.

Copies site files into the local publishing repo and pushes. Safe to run
after every scan: does nothing when there are no changes or when the repo
is missing (e.g. on the Mac, where publishing is not set up).

    python publish_web.py
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, "..", "site"))
REPO = r"C:\Users\Petras\tools\architecture-jobs"
FILES = ["index.html", "data.js", "jobs-simple.html",
         "manifest.webmanifest", "icon.svg", "sw.js"]


def run(args):
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main():
    if not os.path.isdir(os.path.join(REPO, ".git")):
        print(f"publish: repo not found at {REPO}, skipping", flush=True)
        return 0
    # the cloud CI also pushes to this repo; generated files are
    # last-writer-wins, so sync to origin first instead of merging
    run(["git", "fetch", "origin"])
    run(["git", "reset", "--hard", "origin/main"])
    for name in FILES:
        src = os.path.join(SITE, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(REPO, "public", name))
    # user-maintained inputs dropped into the Drive folder propagate to the
    # repo so the cloud scans honor them too
    for name in ("manual_jobs.json", "watch_sites.json"):
        src = os.path.join(HERE, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(REPO, "jobtracker", name))
    if not run(["git", "status", "--porcelain"]).stdout.strip():
        print("publish: no changes", flush=True)
        return 0
    run(["git", "add", "-A"])
    import datetime
    stamp = datetime.datetime.now().isoformat(timespec="minutes")
    c = run(["git", "commit", "-m", f"Data refresh {stamp}"])
    if c.returncode != 0:
        print("publish: commit failed:", c.stderr.strip()[:300], flush=True)
        return 1
    p = run(["git", "push"])
    if p.returncode != 0:
        print("publish: push failed:", p.stderr.strip()[:300], flush=True)
        return 1
    print("publish: pushed to GitHub Pages "
          "(https://petrasvestartas.github.io/architecture-jobs/)", flush=True)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
