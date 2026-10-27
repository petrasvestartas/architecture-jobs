# -*- coding: utf-8 -*-
"""Match profile-scan postings against Petras's CV (cv_petras_vestartas,
2026-10-07): fetch each posting's full description once, find the CV skills
it asks for, and flag ads that demand German beyond B1.

    fit(job) -> {"fit": points, "skills": [labels], "de": bool, "desc": bool}

Results are cached per URL in profile_state.json by profile_jobs.py, so each
posting is fetched once, not every run.
"""
import html
import json
import re
import urllib.parse

from net import http_get, get_json, strip_tags

# (label, pattern, points) — the CV's competences, own tools and stack.
# Core skills (3) are what the CV is built around; supporting ones count less.
CV_SKILLS = [
    ("computational design", r"computational design|computational architect|"
     r"parametric|algorithmic design|generative design|design automation", 3),
    ("geometry", r"geometr|nurbs|b-?rep\b|\bmesh|cgal|opencascade|\boc[c]?t\b|"
     r"libigl|surface modell?ing|3d modell?ing kernel", 3),
    ("timber", r"timber|holzbau|\bholz|\bwood|\bclt\b|glulam|brettsperrholz|"
     r"brettschichtholz|ingenieurholzbau|lignum", 3),
    ("digital fabrication", r"digital(?:e)? fabri|design[- ]to[- ](?:production|"
     r"fabrication)|fabrication[- ]aware|vorfertigung|prefabricat|"
     r"additive manufactur|3d[- ]print", 3),
    ("industrial robots", r"industrial robot|industrieroboter|robot(?:ic)? arm|"
     r"\babb\b|\bkuka\b|st[aä]ubli|fanuc|robotic fabrication|roboterzelle|"
     r"manipulator", 3),
    ("CNC / CAM", r"\bcnc\b|g-?code|5-?ax|5-?achs|\bcam\b|toolpath|abbund|"
     r"hundegger|\bnesting\b|machining", 3),
    ("point clouds / scanning", r"point[- ]cloud|punktwolke|lidar|3d[- ]scan|"
     r"laser scan|photogrammetr|open3d|reality capture", 3),
    ("C++", r"c\+\+", 3),
    ("Rust", r"\brust\b", 2),
    ("Python bindings", r"nanobind|pybind|python bindings|bindings? for python", 2),
    ("Python", r"\bpython\b", 2),
    ("C#", r"\bc#|\.net\b", 1),
    ("Rhino / Grasshopper", r"\brhino|grasshopper|mcneel", 2),
    ("BIM / CAD", r"\bbim\b|\bifc\b|revit|cadwork|\bcad\b|autocad|archicad|"
     r"allplan|dietrich|sema\b", 1),
    ("structural design", r"structural (?:design|engineer|analysis)|tragwerk|"
     r"\bstatik|finite element|\bfem\b|form[- ]?finding|\bshells?\b|vault", 2),
    ("graphics / GPU", r"opengl|webgpu|\bwgpu|vulkan|\bgpu\b|computer graphics|"
     r"rendering engine|shader", 2),
    ("simulation", r"simulation|\bsolver|discrete element|physics engine", 1),
    # ROS case-sensitive: "Ros", "Rosa" in names and addresses are not it
    ("ROS / kinematics", r"(?-i:\bROS ?2?\b)|kinemati|motion planning|"
     r"trajector", 2),
    ("computer vision", r"computer vision|perception|image processing|"
     r"bildverarbeitung|machine vision|3d vision", 1),
    ("open source / CI", r"open[- ]source|github|ci/cd|continuous integration|"
     r"\bcmake\b|\bpypi\b|conda|software packaging", 1),
    ("research", r"research|forschung|\bphd\b|doctorate|promotion|postdoc|"
     r"publication|scientist|wissenschaft", 1),
    # not "Lehre": in German job ads that is an apprenticeship
    ("teaching", r"teaching|lecturer|dozent|\bcourses?\b|unterricht", 1),
    # building architecture only: software/system/firmware architecture is not
    ("architecture / AEC", r"(?<!software)(?<!system)(?<!it-)architekt(?:ur|in|en)?\b|"
     r"architecture (?:firm|office|practice|and construction)|"
     r"architects?,? (?:and|&) engineers|\baec\b|construction industry|"
     r"baubranche|bauindustrie|built environment|building industry", 1),
    ("grants / funding", r"\bgrants?\b|funding|innosuisse|\bsnf\b|\bsnsf\b|"
     r"research proposals?", 1),
]
CV_SKILLS = [(lbl, re.compile(p, re.I), w) for lbl, p, w in CV_SKILLS]
# German beyond the CV's B1: "fliessend Deutsch", "Deutsch C1", "native German"
GERMAN_HIGH = re.compile(
    r"(?:deutsch|german)\w*[^.;\n]{0,60}?(?:muttersprach|native|\bc1\b|\bc2\b|"
    r"verhandlungssicher|fliessend|fließend|fluent|stilsicher|einwandfrei|"
    r"sehr gute?)|(?:muttersprach|native|fliessend|fließend|fluent|"
    r"verhandlungssicher|stilsicher|einwandfrei|sehr gute?)\w*[^.;\n]{0,40}?"
    r"(?:deutsch|german)", re.I)
# the score bands profile.html shows
STRONG, GOOD = 12, 7

JSONLD = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>",
                    re.S | re.I)


def _text(s):
    return re.sub(r"\s+", " ", strip_tags(html.unescape(s or ""))).strip()


def _jsonld(body):
    for m in JSONLD.finditer(body):
        try:
            x = json.loads(m.group(1).strip())
        except ValueError:
            continue
        objs = x if isinstance(x, list) else (x.get("@graph") or [x])
        for o in objs:
            if isinstance(o, dict) and o.get("@type") == "JobPosting" \
                    and o.get("description"):
                return _text(o["description"])
    return ""


def description(job):
    """Full posting text, best effort: the ATS / board API where the page is
    an empty JavaScript shell, else JSON-LD, else the page's visible text."""
    url, src = job["url"], job["source"]
    p = urllib.parse.urlparse(url)
    if src == "job-room":
        ad = get_json("https://www.job-room.ch/jobadservice/api/jobAdvertisements/"
                      + p.path.rstrip("/").rsplit("/", 1)[-1], timeout=20)
        descs = (ad.get("jobContent") or {}).get("jobDescriptions") or []
        return " ".join(_text(d.get("title")) + " " + _text(d.get("description"))
                        for d in descs)
    if src == "greenhouse":
        m = re.search(r"greenhouse\.io/([^/]+)/jobs/(\d+)", url)
        if m:
            d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}"
                         f"/jobs/{m.group(2)}", timeout=20)
            return _text(d.get("content"))
    if src == "workable":
        m = re.search(r"apply\.workable\.com/([^/]+)/j/([^/]+)", url)
        if m:
            d = get_json(f"https://apply.workable.com/api/v2/accounts/{m.group(1)}"
                         f"/jobs/{m.group(2)}", timeout=20)
            return _text(" ".join(str(d.get(k) or "") for k in
                                  ("description", "requirements", "benefits")))
    if src == "bamboohr":
        d = get_json(url.rstrip("/") + "/detail", timeout=20)
        r = (d.get("result") or {}).get("jobOpening") or {}
        return _text(r.get("description"))
    body, _ = http_get(url, tries=1, timeout=20)
    got = _jsonld(body)
    if got:
        return got
    body = re.sub(r"<(script|style|noscript|nav|header|footer)\b.*?</\1>", " ",
                  body, flags=re.S | re.I)
    return _text(body)


def fit(job):
    try:
        desc = description(job)
    except Exception:
        desc = ""
    text = job["title"] + " " + desc
    skills, pts = [], 0
    for lbl, rx, w in CV_SKILLS:
        if rx.search(text):
            skills.append(lbl)
            # asked for in the title itself: a central part of the role
            pts += w * 2 if rx.search(job["title"]) else w
    return {"fit": pts, "skills": skills,
            "de": bool(GERMAN_HIGH.search(desc)), "desc": len(desc) > 300}
