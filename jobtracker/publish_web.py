# -*- coding: utf-8 -*-
"""Publish the static site to GitHub Pages.

Two layouts are supported, detected automatically:

*in-repo* (Linux/macOS, the `run_local.sh` setup)
    The clone is the working copy: the scan writes straight into
    `<repo>/public` and `<repo>/jobtracker/jobtracker.db`, so there is
    nothing to copy — commit what is already there and push.

*out-of-repo* (the older Windows/Google Drive setup)
    The pipeline lives in `G:\\My Drive\\job\\jobtracker` and writes the
    site to a sibling `site/` folder, while the publishing clone sits
    somewhere else. The generated files are copied into the clone first.

Overrides: JOBTRACKER_REPO (publishing clone), JOBTRACKER_SITE_DIR (where
export_site.py wrote the site).

Safe to run after every scan: does nothing when there are no changes or
when the repo is missing.

    python publish_web.py
"""
import datetime
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# the Windows box published from a clone kept outside the Drive folder
WINDOWS_REPO = r"C:\Users\Petras\tools\architecture-jobs"
FILES = ["index.html", "data.js", "jobs-simple.html", "jobtracker-phone.html",
         "manifest.webmanifest", "icon.svg", "sw.js"]
INPUTS = ("manual_jobs.json", "watch_sites.json")


def _git_root(path):
    """The work tree containing `path`, or "" when it is not in a clone."""
    try:
        out = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


REPO = os.environ.get("JOBTRACKER_REPO") or _git_root(HERE) or WINDOWS_REPO
SITE = os.environ.get("JOBTRACKER_SITE_DIR") or \
    os.path.normpath(os.path.join(HERE, "..", "site"))


def run(args):
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _inside(child, parent):
    """True when `child` sits within `parent`."""
    try:
        rel = os.path.relpath(os.path.realpath(child), os.path.realpath(parent))
    except ValueError:
        return False  # different drives on Windows
    return not rel.startswith(os.pardir)


def main():
    if not os.path.isdir(os.path.join(REPO, ".git")):
        print(f"publish: repo not found at {REPO}, skipping", flush=True)
        return 0
    in_repo = _inside(SITE, REPO)

    if in_repo:
        # The scan already wrote its results into the clone. Rebasing onto
        # origin here would throw them away, so the sync happens *before*
        # the scan (run_local.sh) and this step only commits and pushes.
        print(f"publish: in-repo layout ({REPO})", flush=True)
    else:
        # the cloud CI also pushes to this clone; generated files are
        # last-writer-wins, so sync to origin first instead of merging
        run(["git", "fetch", "origin"])
        run(["git", "reset", "--hard", "origin/main"])
        for name in FILES:
            src = os.path.join(SITE, name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(REPO, "public", name))
        # user-maintained inputs dropped into the Drive folder propagate to
        # the repo so the cloud scans honor them too
        for name in INPUTS:
            src = os.path.join(HERE, name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(REPO, "jobtracker", name))

    if not run(["git", "status", "--porcelain"]).stdout.strip():
        print("publish: no changes", flush=True)
        return 0
    run(["git", "add", "-A"])
    stamp = datetime.datetime.now().isoformat(timespec="minutes")
    c = run(["git", "commit", "-m", f"Data refresh {stamp}"])
    if c.returncode != 0:
        print("publish: commit failed:", c.stderr.strip()[:300], flush=True)
        return 1

    # the cloud scan pushes to main as well; on rejection replay our commit
    # on top of theirs. During a rebase "theirs" is the commit being
    # replayed (ours) — generated files and the DB are last-writer-wins.
    for attempt in range(1, 6):
        if run(["git", "push"]).returncode == 0:
            print("publish: pushed to GitHub Pages "
                  "(https://petrasvestartas.github.io/architecture-jobs/)",
                  flush=True)
            return 0
        print(f"publish: push rejected (attempt {attempt}) — re-syncing",
              flush=True)
        if run(["git", "pull", "--rebase", "-X", "theirs", "--autostash",
                "origin", "main"]).returncode != 0:
            run(["git", "rebase", "--abort"])
    print("publish: could not push; the next scan will retry", flush=True)
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
