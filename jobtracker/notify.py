# -*- coding: utf-8 -*-
"""Daily e-mail digest of new architecture (buildings) jobs near the
reference place.

Sent once, early morning, by the 05:30 cloud full scan only — never after
the frequent light scans — and only when there is at least one genuinely
new building-architecture posting within range. State lives in
notified.json: on the very first run it is seeded silently with every job
already in the database, so the first digest never dumps the whole backlog.

    python notify.py            # send the digest (e-mail only)
    python notify.py --dry-run  # print what would be sent, send nothing
    python notify.py --test     # send a sample e-mail to verify delivery
E-mail: Gmail SMTP with GMAIL_APP_PASSWORD (repo secret) to config recipients.
"""
import datetime
import json
import os
import re
import sys

import db
import geo
from jobfilter import is_non_building

HERE = os.path.dirname(os.path.abspath(__file__))
NOTIFIED = os.path.join(HERE, "notified.json")
REPO = os.environ.get("GITHUB_REPOSITORY", "petrasvestartas/architecture-jobs")

ENTWURF = re.compile(r"entwurf|design\s*architekt|design\s*architect|conception", re.I)

DOG_JOKES = [
    "What do you call a dog that does magic? A labracadabrador.",
    "My dog used to chase people on a bike. It got so bad we had to take his bike away.",
    "I named my dog Five Miles, so I can tell people I walk Five Miles every single day.",
    "What do you call a dog with no legs? Doesn't matter — he won't come anyway.",
    "Why did the dog cross the road twice? He was trying to fetch a boomerang.",
    "A dog walks into a bar... nothing. He's a good boy. He waited outside.",
    "What do you call a dog detective? Sherlock Bones.",
    "What did the dog say when he sat on sandpaper? Ruff.",
    "Why did the broke dog chase his own tail? He was trying to make ends meet.",
    "What's a dog's favourite instrument? The trom-bone.",
    "Where do dogs refuse to shop? The flea market.",
    "What kind of dog does Dracula have? A bloodhound.",
    "Why do dogs float so well? Because they're good buoys.",
    "What do you get when you cross a dog with a calculator? A friend you can count on.",
    "Where does a dog leave his car? In the barking lot.",
    "My dog knows 'sit', 'stay' and 'roll over' in three languages. Still unemployed. The market is brutal.",
]


def _joke():
    import random
    return random.choice(DOG_JOKES)


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


SITE_URL = "https://petrasvestartas.github.io/architecture-jobs/"


def _health_note():
    """One line naming sources that collapsed against their own history.

    Without it a board can die quietly and the digest just gets shorter —
    which reads as "a slow week", not "we stopped scanning a third of the
    market". Only shown when something is actually wrong.
    """
    try:
        con = db.connect()
        row = con.execute("SELECT source_stats FROM runs "
                          "ORDER BY id DESC LIMIT 1").fetchone()
        con.close()
        health = (json.loads(row["source_stats"] or "{}") or {}).get("_health")
    except Exception:
        return ""
    if not health:
        return ""
    names = ", ".join(sorted(health))
    return ('<p style="font-size:12.5px;color:#b00020;margin-top:14px;'
            'border-top:1px solid #e5e5e0;padding-top:12px">'
            f'&#9888; Not scanning right now: <b>{_esc(names)}</b>. '
            'The Job Websites tab has a "search manually" link for each.</p>')


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
        '<p style="font-size:12px;color:#888888;margin-top:14px">'
        'Website password: <b>lina</b></p>'
        + _health_note() +
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
    dry = "--dry-run" in sys.argv
    ref = geo.lookup_place(cfg["reference_place"]) or (46.9481, 7.4474, "BE")
    radius = float(cfg["notify_radius_km"])
    e_radius = float(cfg["notify_entwurf_radius_km"])
    ref_name = cfg["reference_place"]

    con = db.connect()
    rows = con.execute("""SELECT title, company_name, location, url, source,
                                 workload, lat, lon FROM jobs
                          WHERE active=1 AND dup_of IS NULL""").fetchall()
    con.close()

    if not os.path.exists(NOTIFIED) and not dry:
        json.dump(sorted(r["url"] for r in rows), open(NOTIFIED, "w"), indent=0)
        print(f"notify: seeded {len(rows)} existing jobs, no digest", flush=True)
        return

    seen = set(json.load(open(NOTIFIED, encoding="utf-8"))) \
        if os.path.exists(NOTIFIED) else set()
    fresh = [r for r in rows if r["url"] not in seen]

    matches = []
    for r in fresh:
        if is_non_building(r["title"] or ""):
            continue  # buildings only — no software/landscape/... "architect"
        d = (geo.haversine_km(ref[0], ref[1], r["lat"], r["lon"])
             if r["lat"] is not None else None)
        entwurf = bool(ENTWURF.search(r["title"] or ""))
        if (d is not None and d <= radius) or \
                (entwurf and (d is None or d <= e_radius)):
            matches.append((r, d, entwurf))

    if matches:
        matches.sort(key=lambda m: (not m[2], m[1] if m[1] is not None else 9e9))
        stamp = datetime.datetime.now().strftime("%d.%m.%Y")
        title = (f"{len(matches)} new architecture job"
                 f"{'s' if len(matches) > 1 else ''} near {ref_name} — {stamp}")
        items = []
        for r, d, entwurf in matches:
            km = f"{d:.0f} km" if d is not None else "?"
            meta = " · ".join(x for x in (
                r["company_name"] or "", r["location"] or "?", km,
                (r["workload"] + "%") if r["workload"] else "", r["source"]) if x)
            items.append((r["title"], r["url"], meta, entwurf))
        heading = (f"{len(matches)} new position{'s' if len(matches) > 1 else ''} "
                   f"near {ref_name} — sorted Entwurf first, closest first:")
        if dry:
            print(f"[dry-run] would e-mail: {title}", flush=True)
            for t, u, m, entwurf in items:
                print(f"  {'[ENTWURF] ' if entwurf else ''}{t} · {m}", flush=True)
            return
        try:
            send_email(title, email_html(heading, items, _joke()), cfg)
        except Exception as e:
            print(f"notify: email failed: {e}", flush=True)
    else:
        print(f"notify: {len(fresh)} new jobs, none building & in range "
              f"({radius:.0f} km / Entwurf {e_radius:.0f} km)", flush=True)

    if not dry:
        seen.update(r["url"] for r in rows)
        json.dump(sorted(seen), open(NOTIFIED, "w"), indent=0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
