# -*- coding: utf-8 -*-
"""Announce newly found matching jobs as a GitHub issue after a scan.

A new issue in the repo triggers a push notification in the GitHub mobile
app (watch the repo with "All activity"). State lives in notified.json —
on the very first run it is seeded silently with every job already in the
database, so only genuinely new postings are ever announced.

    python notify.py            # normal run (after a scan)
Auth: GITHUB_TOKEN env (CI) or the local `gh` CLI as fallback.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request

import db
import geo

HERE = os.path.dirname(os.path.abspath(__file__))
NOTIFIED = os.path.join(HERE, "notified.json")
REPO = os.environ.get("GITHUB_REPOSITORY", "petrasvestartas/architecture-jobs")

ENTWURF = re.compile(r"entwurf|design\s*architekt|design\s*architect|conception", re.I)

DOG_JOKES = [
    "Why did the dog apply at the architecture office? It's great at fetching approvals.",
    "A dog's favourite architectural style? Bark-oque.",
    "Why do dogs make excellent architects? They never miss a site visit — especially the lampposts.",
    "The dog reviewed the floor plan and approved it: door to the garden, direct axis to the food bowl. Perfect circulation.",
    "Why did the dog love the competition model? Finally, a stick presented properly on a plinth.",
    "A dog's building code has one rule: every window must be low enough to judge the neighbours.",
    "Why do dogs love Swiss architecture? Precise detailing, generous lawns.",
    "The dog rejected the minimalist concept — where is one supposed to bury the bones?",
    "Why was the dog promoted to Bauleiter? Perfect attendance and it already knew everyone on site by smell.",
    "The dog measured the apartment and declared: 100% shared space, as it should be.",
    "Why did the dog fail the acoustics review? It heard the doorbell in a building that has no doorbell.",
    "A dog saw the cantilever and said: nice, but can you fetch from it?",
    "The dog's favourite drawing phase? The walk-through.",
    "Why do dogs hate revolving doors? You can never truly arrive.",
    "The dog approved the courtyard design instantly: sun in the morning, shade at noon, squirrels all day.",
    "Why did the dog sit on the site plan? Someone had to hold it down during the storm of revisions.",
]


def _joke():
    import random
    return random.choice(DOG_JOKES)


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


SITE_URL = "https://petrasvestartas.github.io/architecture-jobs/"


def email_html(heading, items, joke):
    """A tidy, site-styled HTML email. items = [(title, url, meta, entwurf)]."""
    rows = []
    for title, url, meta, entwurf in items:
        tag = ('<span style="background:#e2001a;color:#ffffff;font-size:10px;'
               'font-weight:bold;letter-spacing:1px;padding:2px 6px;'
               'margin-left:6px">ENTWURF</span>') if entwurf else ""
        rows.append(
            '<div style="padding:12px 0;border-bottom:1px solid #e5e5e0">'
            f'<a href="{_esc(url)}" style="font-size:15px;font-weight:bold;'
            f'color:#1a1a1a;text-decoration:none">{_esc(title)}</a>{tag}'
            f'<div style="font-size:13px;color:#555555;margin-top:4px">{_esc(meta)}</div>'
            '</div>')
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;'
        'margin:0 auto;color:#1a1a1a">'
        '<div style="border-bottom:3px solid #e2001a;padding:14px 0 10px">'
        '<span style="display:inline-block;width:12px;height:12px;'
        'background:#e2001a;margin-right:8px"></span>'
        '<span style="font-size:16px;font-weight:bold;letter-spacing:2px;'
        'text-transform:uppercase">Architecture Jobs Switzerland</span></div>'
        f'<p style="font-size:14px;color:#666666;margin:12px 0 4px">{_esc(heading)}</p>'
        + "".join(rows) +
        '<div style="margin:22px 0">'
        f'<a href="{SITE_URL}" style="background:#e2001a;color:#ffffff;'
        'padding:10px 18px;text-decoration:none;font-weight:bold;'
        'font-size:13px;letter-spacing:1px">OPEN ALL JOBS</a></div>'
        '<p style="font-size:12.5px;color:#888888;font-style:italic;'
        'border-top:1px solid #e5e5e0;padding-top:12px;margin-top:8px">'
        f'&#128054; {_esc(joke)}</p></div>')


def _cfg():
    cfg = {"reference_place": "Bern", "notify_radius_km": 40,
           "notify_entwurf_radius_km": 120, "notify_emails": [],
           "notify_email_from": ""}
    path = os.path.join(HERE, "config.json")
    if os.path.exists(path):
        cfg.update(json.load(open(path, encoding="utf-8")))
    return cfg


def send_email(subject, html_body, cfg):
    """Gmail SMTP with an app password from the GMAIL_APP_PASSWORD env
    (repo secret in CI). Silently skipped when not configured."""
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    to = cfg.get("notify_emails") or []
    sender = cfg.get("notify_email_from") or (to[0] if to else "")
    if not pw or not to or not sender:
        print("notify: email not configured (GMAIL_APP_PASSWORD/recipients), "
              "skipping email", flush=True)
        return False
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import formatdate, make_msgid
    text_body = re.sub(r"<br\s*/?>", "\n", html_body)
    text_body = re.sub(r"</(p|li|h\d)>", "\n", text_body)
    text_body = re.sub(r"<[^>]+>", "", text_body)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="gmail.com")
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls()
        s.login(sender, pw)
        s.send_message(msg)
    print(f"notify: email sent to {', '.join(to)}", flush=True)
    return True


def create_issue(title, body):
    payload = {"title": title, "body": body}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/issues",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())["html_url"]
    # local fallback: authenticated gh CLI
    p = subprocess.run(["gh", "issue", "create", "--repo", REPO,
                        "--title", title, "--body", body],
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[:300])
    return p.stdout.strip()


def main():
    cfg = _cfg()
    if "--test" in sys.argv:
        stamp = datetime.datetime.now().strftime("%H:%M")
        ok = send_email(
            f"Test {stamp}: architecture jobs notifications are working",
            email_html(
                "This is a test — from now on, new architecture jobs near "
                "Bern land here, formatted like this:",
                [("Architekt:in Entwurf (80–100%) — example posting",
                  SITE_URL, "Example Architekten AG · 3011 Bern · "
                  "0.4 km · 80-100% · swiss-architects", True)],
                _joke()), cfg)
        sys.exit(0 if ok else 1)
    ref = geo.lookup_place(cfg["reference_place"]) or (46.9481, 7.4474, "BE")
    radius = float(cfg["notify_radius_km"])
    e_radius = float(cfg["notify_entwurf_radius_km"])

    con = db.connect()
    rows = con.execute("""SELECT title, company_name, location, url, source,
                                 workload, lat, lon FROM jobs
                          WHERE active=1 AND dup_of IS NULL""").fetchall()
    con.close()

    if not os.path.exists(NOTIFIED):
        json.dump(sorted(r["url"] for r in rows), open(NOTIFIED, "w"), indent=0)
        print(f"notify: seeded {len(rows)} existing jobs, no announcement", flush=True)
        return

    seen = set(json.load(open(NOTIFIED, encoding="utf-8")))
    fresh = [r for r in rows if r["url"] not in seen]

    matches = []
    for r in fresh:
        d = (geo.haversine_km(ref[0], ref[1], r["lat"], r["lon"])
             if r["lat"] is not None else None)
        entwurf = bool(ENTWURF.search(r["title"] or ""))
        if (d is not None and d <= radius) or \
                (entwurf and (d is None or d <= e_radius)):
            matches.append((r, d, entwurf))

    if matches:
        matches.sort(key=lambda m: (not m[2], m[1] if m[1] is not None else 9e9))
        ref_name = cfg["reference_place"]
        stamp = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
        title = (f"{len(matches)} new architecture job"
                 f"{'s' if len(matches) > 1 else ''} near {ref_name} — {stamp}")
        lines = []
        for r, d, entwurf in matches:
            km = f"{d:.0f} km" if d is not None else "?"
            tag = " **ENTWURF**" if entwurf else ""
            comp = f" — {r['company_name']}" if r["company_name"] else ""
            lines.append(f"- [{r['title']}]({r['url']}){comp} · "
                         f"{r['location'] or '?'} · {km} · {r['source']}{tag}")
        joke = _joke()
        body = ("\n".join(lines)
                + f"\n\n[Open the website]({SITE_URL})"
                + f"\n\n> \U0001F436 {joke}")
        try:
            url = create_issue(title, body)
            print(f"notify: {len(matches)} new matching jobs -> {url}", flush=True)
        except Exception as e:
            print(f"notify: issue creation failed: {e}", flush=True)
        items = []
        for r, d, entwurf in matches:
            km = f"{d:.0f} km" if d is not None else "?"
            meta = " · ".join(x for x in (
                r["company_name"] or "", r["location"] or "?", km,
                (r["workload"] + "%") if r["workload"] else "", r["source"]) if x)
            items.append((r["title"], r["url"], meta, entwurf))
        heading = (f"{len(matches)} new position{'s' if len(matches) > 1 else ''} "
                   f"near {ref_name} — sorted Entwurf first, closest first:")
        try:
            send_email(title, email_html(heading, items, joke), cfg)
        except Exception as e:
            print(f"notify: email failed: {e}", flush=True)
    else:
        print(f"notify: {len(fresh)} new jobs, none within "
              f"{radius:.0f} km / Entwurf {e_radius:.0f} km", flush=True)

    seen.update(r["url"] for r in rows)
    json.dump(sorted(seen), open(NOTIFIED, "w"), indent=0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
