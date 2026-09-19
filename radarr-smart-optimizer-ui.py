#!/usr/bin/env python3
"""Optional lightweight web UI for Radarr Smart Optimizer.

Standard library only. The optimizer remains fully usable without this file.
"""

import html
import json
import os
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OPTIMIZER = os.environ.get("RADARR_OPTIMIZER_SCRIPT", os.path.join(BASE_DIR, "radarr-smart-optimizer.py"))
STATE_FILE = os.environ.get("RADARR_OPTIMIZER_STATE", os.path.join(BASE_DIR, "radarr-smart-optimizer-state.json"))
RADARR_URL = os.environ.get("RADARR_URL", "http://127.0.0.1:7878").rstrip("/")
API_KEY = os.environ.get("RADARR_KEY", "").strip()
HOST = os.environ.get("RADARR_UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("RADARR_UI_PORT", "8788"))
ENABLE_ACTIONS = os.environ.get("RADARR_UI_ENABLE_ACTIONS", "0").lower() in ("1", "true", "yes")
HISTORY_PAGES = max(1, min(20, int(os.environ.get("RADARR_UI_HISTORY_PAGES", "5"))))
MAX_OUTPUT = 50000

job_lock = threading.Lock()
job = {"running": False, "mode": None, "started": None, "finished": None, "returncode": None, "output": ""}


def radarr_get(path):
    if not API_KEY:
        raise RuntimeError("RADARR_KEY is not configured")
    req = urllib.request.Request(
        RADARR_URL + "/api/v3" + path,
        headers={"X-Api-Key": API_KEY, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"daily": {}, "movies": {}}


def history_records():
    records = []
    for page in range(1, HISTORY_PAGES + 1):
        data = radarr_get("/history?page=%d&pageSize=100&sortKey=date&sortDirection=descending" % page)
        batch = data.get("records", [])
        records.extend(batch)
        if len(batch) < 100:
            break
    return records


def completed_upgrades(records):
    """Pair Upgrade deletion -> subsequent import for the same movie.

    This reports observed Radarr history, not predicted optimizer savings.
    """
    pending = {}
    upgrades = []
    # API records are newest first; process oldest first.
    for event in reversed(records):
        movie_id = event.get("movieId")
        etype = event.get("eventType")
        data = event.get("data") or {}
        if etype == "movieFileDeleted" and data.get("reason") == "Upgrade":
            try:
                pending[movie_id] = {
                    "old": int(data.get("size") or 0),
                    "deleted": event.get("date"),
                    "old_path": event.get("sourceTitle") or "",
                }
            except (TypeError, ValueError):
                pass
        elif etype == "downloadFolderImported" and movie_id in pending:
            try:
                new_size = int(data.get("size") or 0)
            except (TypeError, ValueError):
                new_size = 0
            old = pending.pop(movie_id)
            if old["old"] > 0 and new_size > 0:
                upgrades.append({
                    "movie_id": movie_id,
                    "date": event.get("date") or "",
                    "title": event.get("sourceTitle") or ("Movie ID %s" % movie_id),
                    "old": old["old"],
                    "new": new_size,
                    "saved": old["old"] - new_size,
                })
    return sorted(upgrades, key=lambda x: x["date"], reverse=True)


def gib(n):
    return n / (1024.0 ** 3)


def run_optimizer(live):
    with job_lock:
        if job["running"]:
            return False
        job.update(running=True, mode="live" if live else "dry-run", started=time.time(),
                   finished=None, returncode=None, output="")
    def worker():
        cmd = ["python3", OPTIMIZER] + (["--live"] if live else [])
        env = os.environ.copy()
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, env=env, timeout=60 * 60)
            output = proc.stdout[-MAX_OUTPUT:]
            rc = proc.returncode
        except Exception as exc:
            output, rc = "ERROR: %s" % exc, -1
        with job_lock:
            job.update(running=False, finished=time.time(), returncode=rc, output=output)
    threading.Thread(target=worker, daemon=True).start()
    return True


CSS = """
body{font-family:system-ui,-apple-system,sans-serif;background:#111827;color:#e5e7eb;margin:0}
main{max-width:1100px;margin:auto;padding:28px}.top{display:flex;justify-content:space-between;gap:16px;align-items:center}
h1{margin:0 0 4px}.muted{color:#9ca3af}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin:24px 0}
.card,.panel{background:#1f2937;border:1px solid #374151;border-radius:14px;padding:18px}.big{font-size:2rem;font-weight:750}
.good{color:#6ee7b7}.bad{color:#fca5a5}button{background:#2563eb;color:white;border:0;border-radius:9px;padding:10px 14px;font-weight:650;cursor:pointer}
button.live{background:#b91c1c}button:disabled{opacity:.45;cursor:not-allowed}form{display:inline-block;margin-right:8px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid #374151;font-size:.92rem}th{color:#9ca3af}
pre{white-space:pre-wrap;word-break:break-word;max-height:380px;overflow:auto;background:#0b1020;padding:14px;border-radius:10px}
code{color:#bfdbfe}.note{font-size:.9rem}.right{text-align:right}
"""

def page():
    state = load_state()
    today = time.strftime("%Y-%m-%d")
    used = int((state.get("daily") or {}).get(today, {}).get("searches", 0))
    error = ""
    try:
        records = history_records()
        upgrades = completed_upgrades(records)
    except Exception as exc:
        upgrades = []
        error = str(exc)
    saved = sum(x["saved"] for x in upgrades)
    positive = sum(1 for x in upgrades if x["saved"] > 0)
    with job_lock:
        snap = dict(job)
    rows = ""
    for x in upgrades[:20]:
        delta = gib(x["saved"])
        cls = "good" if delta >= 0 else "bad"
        rows += "<tr><td>%s</td><td>%.2f GiB</td><td>%.2f GiB</td><td class='%s'>%+.2f GiB</td></tr>" % (
            html.escape(x["title"]), gib(x["old"]), gib(x["new"]), cls, delta)
    if not rows:
        rows = "<tr><td colspan='4' class='muted'>No completed upgrade pairs found in the loaded history window.</td></tr>"
    actions = """
      <form method="post" action="/run"><input type="hidden" name="mode" value="dry"><button %s>Run dry-run</button></form>
      <form method="post" action="/run"><input type="hidden" name="mode" value="live"><button class="live" %s>Optimize now (LIVE)</button></form>
    """ % ("" if ENABLE_ACTIONS and not snap["running"] else "disabled",
           "" if ENABLE_ACTIONS and not snap["running"] else "disabled")
    output = html.escape(snap.get("output") or "No UI-started run yet.")
    status = "Running %s…" % snap["mode"] if snap["running"] else "Idle"
    warning = "" if ENABLE_ACTIONS else "<p class='note muted'>Run buttons are disabled by default. Set <code>RADARR_UI_ENABLE_ACTIONS=1</code> only if you want the UI to launch optimizer runs.</p>"
    err = ("<p class='bad'>Radarr history error: %s</p>" % html.escape(error)) if error else ""
    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Radarr Smart Optimizer</title><style>%s</style></head>
<body><main><div class="top"><div><h1>Radarr Smart Optimizer</h1><div class="muted">Optional lightweight dashboard</div></div><div>%s</div></div>
%s%s
<div class="cards">
<div class="card"><div class="muted">Observed space change</div><div class="big %s">%+.2f GiB</div><div class="note muted">Across loaded completed upgrades</div></div>
<div class="card"><div class="muted">Completed upgrades</div><div class="big">%d</div><div class="note muted">%d reduced storage</div></div>
<div class="card"><div class="muted">Searches today</div><div class="big">%d</div><div class="note muted">From optimizer state</div></div>
<div class="card"><div class="muted">UI job</div><div class="big" style="font-size:1.3rem">%s</div><div class="note muted">Background optimizer continues independently</div></div>
</div>
<div class="panel"><h2>Recent completed upgrades</h2><p class="note muted">Calculated from Radarr history by pairing an Upgrade deletion with its subsequent import. It may include upgrades initiated outside this optimizer.</p>
<table><thead><tr><th>Release</th><th>Old</th><th>New</th><th>Change</th></tr></thead><tbody>%s</tbody></table></div>
<div class="panel" style="margin-top:14px"><h2>Last UI-started run</h2><pre>%s</pre></div>
</main></body></html>""" % (CSS, actions, warning, err, "good" if saved >= 0 else "bad", gib(saved), len(upgrades), positive, used, html.escape(status), rows, output)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?", 1)[0] != "/":
            self.send_error(404); return
        body = page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/run" or not ENABLE_ACTIONS:
            self.send_error(403); return
        length = min(int(self.headers.get("Content-Length", "0")), 4096)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        mode = (form.get("mode") or [""])[0]
        if mode not in ("dry", "live"):
            self.send_error(400); return
        run_optimizer(mode == "live")
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, fmt, *args):
        print("[ui] " + fmt % args)


if __name__ == "__main__":
    print("Radarr Smart Optimizer UI")
    print("Listening on http://%s:%d" % (HOST, PORT))
    print("Actions:", "ENABLED" if ENABLE_ACTIONS else "disabled (read-only)")
    if HOST not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: UI has no built-in authentication; expose only on a trusted LAN/reverse proxy.")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
