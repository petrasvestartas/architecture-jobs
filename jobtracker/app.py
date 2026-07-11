# -*- coding: utf-8 -*-
"""Job application tracker — local website.

Run:  python app.py   then open http://127.0.0.1:5050
"""
import datetime
import json
import subprocess
import sys
import threading
import os

from flask import Flask, jsonify, render_template, request

import db

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)

_search_state = {"running": False, "started": None, "log": ""}


@app.after_request
def cors(resp):
    # the static site (file://) calls /api/search from a "null" origin
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/companies")
def companies():
    q = (request.args.get("q") or "").strip().lower()
    canton = request.args.get("canton") or ""
    applied = request.args.get("applied")  # '1', '0' or ''
    arch_only = request.args.get("arch_only") == "1"
    max_km = request.args.get("max_km")

    sql = """
      SELECT c.*, (SELECT COUNT(*) FROM jobs j WHERE j.company_id = c.id AND j.active=1) job_count
      FROM companies c WHERE c.status = 'ok'
    """
    params = []
    if q:
        sql += " AND (LOWER(c.name) LIKE ? OR LOWER(c.city) LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    if canton:
        sql += " AND c.canton = ?"
        params.append(canton)
    if applied in ("0", "1"):
        sql += " AND c.applied = ?"
        params.append(int(applied))
    if arch_only:
        sql += " AND c.specialisations LIKE '%Architecture%'"
    if max_km:
        sql += " AND c.distance_km <= ?"
        params.append(float(max_km))
    sql += " ORDER BY c.distance_km IS NULL, c.distance_km, c.name LIMIT 3000"

    con = db.connect()
    rows = [dict(r) for r in con.execute(sql, params)]
    con.close()
    return jsonify(rows)


@app.route("/api/jobs")
def jobs():
    company_id = request.args.get("company_id")
    con = db.connect()
    if company_id:
        rows = con.execute(
            "SELECT * FROM jobs WHERE company_id=? AND active=1 ORDER BY posted_at DESC",
            (company_id,)).fetchall()
    else:
        rows = con.execute(
            "SELECT j.*, c.name matched_company FROM jobs j "
            "LEFT JOIN companies c ON c.id = j.company_id "
            "WHERE j.active=1 ORDER BY j.posted_at DESC LIMIT 2000").fetchall()
    out = [dict(r) for r in rows]
    con.close()
    return jsonify(out)


@app.route("/api/applied", methods=["POST"])
def set_applied():
    data = request.get_json(force=True)
    cid = int(data["company_id"])
    applied = 1 if data.get("applied") else 0
    con = db.connect()
    con.execute("UPDATE companies SET applied=?, applied_at=? WHERE id=?",
                (applied, datetime.datetime.now().isoformat(timespec="seconds") if applied else None, cid))
    con.commit()
    row = dict(con.execute("SELECT id, applied, applied_at FROM companies WHERE id=?", (cid,)).fetchone())
    con.close()
    return jsonify(row)


@app.route("/api/notes", methods=["POST"])
def set_notes():
    data = request.get_json(force=True)
    con = db.connect()
    con.execute("UPDATE companies SET notes=? WHERE id=?",
                (data.get("notes") or "", int(data["company_id"])))
    con.commit()
    con.close()
    return jsonify({"ok": True})


def _run_search():
    _search_state["running"] = True
    _search_state["started"] = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        # boards + the 150 nearest companies' own career pages
        p = subprocess.run([sys.executable, os.path.join(HERE, "scraper.py"),
                            "--career-watch", "150"],
                           capture_output=True, text=True, timeout=1800,
                           encoding="utf-8", errors="replace")
        _search_state["log"] = (p.stdout or "") + (p.stderr or "")
        pub = subprocess.run([sys.executable, os.path.join(HERE, "publish_web.py")],
                             capture_output=True, text=True, timeout=300,
                             encoding="utf-8", errors="replace")
        _search_state["log"] += (pub.stdout or "") + (pub.stderr or "")
    except Exception as e:
        _search_state["log"] = f"search failed: {e}"
    finally:
        _search_state["running"] = False


@app.route("/api/search", methods=["POST"])
def search():
    if _search_state["running"]:
        return jsonify({"status": "already-running"}), 409
    threading.Thread(target=_run_search, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/search/status")
def search_status():
    con = db.connect()
    last = con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    total = con.execute("SELECT COUNT(*) c FROM jobs WHERE active=1").fetchone()["c"]
    applied = con.execute("SELECT COUNT(*) c FROM companies WHERE applied=1").fetchone()["c"]
    con.close()
    return jsonify({
        "running": _search_state["running"],
        "last_run": dict(last) if last else None,
        "active_jobs": total,
        "applied_companies": applied,
    })


if __name__ == "__main__":
    db.init()
    app.run(host="127.0.0.1", port=5050, debug=False)
