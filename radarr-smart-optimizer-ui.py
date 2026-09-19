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
*{box-sizing:border-box}
:root{color-scheme:dark;--bg:#070b14;--surface:rgba(17,24,39,.72);--surface2:rgba(30,41,59,.62);--line:rgba(148,163,184,.14);--text:#f8fafc;--muted:#94a3b8;--accent:#8b5cf6;--accent2:#22d3ee;--good:#34d399;--bad:#fb7185}
html{background:var(--bg)}
body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--text);margin:0;min-height:100vh;background:radial-gradient(circle at 10% 0%,rgba(139,92,246,.17),transparent 34rem),radial-gradient(circle at 90% 8%,rgba(34,211,238,.12),transparent 30rem),linear-gradient(180deg,#080d18 0%,#070b14 65%)}
body:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.16;background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px);background-size:32px 32px;mask-image:linear-gradient(to bottom,black,transparent 70%)}
main{position:relative;max-width:1180px;margin:auto;padding:42px 28px 60px}
.top{display:flex;justify-content:space-between;gap:24px;align-items:center;margin-bottom:28px}
.brand{display:flex;gap:15px;align-items:center}.logo{width:48px;height:48px;border-radius:15px;display:grid;place-items:center;font-size:24px;background:linear-gradient(135deg,rgba(139,92,246,.95),rgba(34,211,238,.85));box-shadow:0 12px 35px rgba(34,211,238,.12),inset 0 1px rgba(255,255,255,.3)}
h1{font-size:clamp(1.65rem,3vw,2.35rem);letter-spacing:-.045em;margin:0 0 4px;font-weight:780}h2{font-size:1rem;margin:0;letter-spacing:-.02em}
.muted{color:var(--muted)}.eyebrow{text-transform:uppercase;letter-spacing:.14em;font-size:.68rem;font-weight:750;color:#a5b4fc;margin-bottom:6px}
.status{display:inline-flex;align-items:center;gap:8px;padding:9px 13px;border:1px solid var(--line);background:rgba(15,23,42,.65);backdrop-filter:blur(16px);border-radius:999px;font-size:.82rem;color:#cbd5e1}.dot{width:8px;height:8px;border-radius:50%;background:var(--good);box-shadow:0 0 14px var(--good)}
.actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end}.actions form{margin:0}
button{appearance:none;border:1px solid rgba(255,255,255,.12);border-radius:11px;padding:10px 14px;font-weight:700;cursor:pointer;color:white;background:linear-gradient(180deg,rgba(99,102,241,.95),rgba(79,70,229,.9));box-shadow:0 8px 24px rgba(79,70,229,.18);transition:.18s ease}
button:hover:not(:disabled){transform:translateY(-1px);filter:brightness(1.08)}button.live{background:linear-gradient(180deg,#f43f5e,#be123c);box-shadow:0 8px 24px rgba(244,63,94,.15)}button:disabled{opacity:.35;cursor:not-allowed;box-shadow:none}
.notice{margin:0 0 18px;padding:11px 14px;border:1px solid var(--line);border-radius:11px;background:rgba(15,23,42,.48);font-size:.82rem;color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:0 0 18px}
.card,.panel{position:relative;overflow:hidden;background:linear-gradient(145deg,rgba(30,41,59,.72),rgba(15,23,42,.66));border:1px solid var(--line);border-radius:18px;box-shadow:0 18px 50px rgba(0,0,0,.18);backdrop-filter:blur(18px)}
.card{padding:20px;min-height:145px}.card:after{content:"";position:absolute;width:110px;height:110px;border-radius:50%;right:-48px;top:-52px;background:radial-gradient(circle,rgba(139,92,246,.18),transparent 70%)}
.label{display:flex;align-items:center;gap:8px;font-size:.78rem;font-weight:650;color:#a8b3c7}.icon{width:28px;height:28px;border-radius:9px;display:grid;place-items:center;background:rgba(148,163,184,.09);font-size:.85rem}
.big{font-size:2rem;line-height:1.05;font-weight:790;letter-spacing:-.045em;margin-top:18px}.good{color:var(--good)}.bad{color:var(--bad)}.note{font-size:.78rem;line-height:1.5}.sub{margin-top:7px}
.panel{padding:0;margin-top:14px}.panel-head{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;padding:20px 20px 14px}.panel-head p{margin:5px 0 0}.pill{padding:6px 9px;border:1px solid var(--line);border-radius:999px;color:#a5b4fc;background:rgba(99,102,241,.08);font-size:.7rem;font-weight:700;white-space:nowrap}
.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:13px 20px;border-top:1px solid var(--line);font-size:.84rem}th{color:#64748b;text-transform:uppercase;letter-spacing:.08em;font-size:.65rem;font-weight:750;background:rgba(2,6,23,.16)}td:first-child{max-width:560px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
pre{margin:0;border-top:1px solid var(--line);white-space:pre-wrap;word-break:break-word;max-height:330px;overflow:auto;background:rgba(2,6,23,.42);padding:18px 20px;color:#cbd5e1;font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}
code{color:#c4b5fd}.footer{text-align:center;color:#475569;font-size:.72rem;margin-top:24px}
@media(max-width:850px){.cards{grid-template-columns:repeat(2,1fr)}.top{align-items:flex-start;flex-direction:column}.actions{justify-content:flex-start}}
@media(max-width:520px){main{padding:25px 15px 40px}.cards{grid-template-columns:1fr}.card{min-height:125px}.panel-head{flex-direction:column}.big{font-size:1.8rem}th,td{padding:12px 14px}}
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
      <div class="actions">
      <form method="post" action="/run"><input type="hidden" name="mode" value="dry"><button %s>Dry run</button></form>
      <form method="post" action="/run"><input type="hidden" name="mode" value="live"><button class="live" %s>Optimize now</button></form>
      </div>
    """ % ("" if ENABLE_ACTIONS and not snap["running"] else "disabled",
           "" if ENABLE_ACTIONS and not snap["running"] else "disabled")
    output = html.escape(snap.get("output") or "No UI-started run yet.")
    status = "Running %s…" % snap["mode"] if snap["running"] else "Idle"
    warning = "" if ENABLE_ACTIONS else "<div class='notice'>Dashboard is in <strong>read-only mode</strong>. Optimizer actions stay disabled until explicitly enabled.</div>"
    err = ("<div class='notice bad'>Radarr history error: %s</div>" % html.escape(error)) if error else ""
    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#070b14"><title>Radarr Smart Optimizer</title><style>%s</style></head>
<body><main>
<div class="top"><div class="brand"><div class="logo">✦</div><div><div class="eyebrow">Library intelligence</div><h1>Radarr Smart Optimizer</h1><div class="muted note">A quiet overview of what your optimizer is doing.</div></div></div><div><div class="status"><span class="dot"></span>%s</div>%s</div></div>
%s%s
<div class="cards">
<div class="card"><div class="label"><span class="icon">↘</span>Storage saved</div><div class="big %s">%+.2f GiB</div><div class="note muted sub">Observed across loaded upgrades</div></div>
<div class="card"><div class="label"><span class="icon">✓</span>Optimized</div><div class="big">%d</div><div class="note muted sub">%d upgrades reduced storage</div></div>
<div class="card"><div class="label"><span class="icon">⌕</span>Searches today</div><div class="big">%d</div><div class="note muted sub">Optimizer state counter</div></div>
<div class="card"><div class="label"><span class="icon">◉</span>Engine</div><div class="big" style="font-size:1.45rem">%s</div><div class="note muted sub">Scheduled optimizer runs independently</div></div>
</div>
<div class="panel"><div class="panel-head"><div><h2>Recent optimizations</h2><p class="note muted">Completed Radarr upgrade pairs with their real file-size change.</p></div><span class="pill">RADARR HISTORY</span></div>
<div class="table-wrap"><table><thead><tr><th>Release</th><th>Before</th><th>After</th><th>Saved</th></tr></thead><tbody>%s</tbody></table></div></div>
<div class="panel"><div class="panel-head"><div><h2>Optimizer activity</h2><p class="note muted">Output from runs started through this dashboard.</p></div><span class="pill">LIVE LOG</span></div><pre>%s</pre></div>
<div class="footer">Radarr Smart Optimizer · lightweight optional dashboard</div>
</main></body></html>""" % (CSS, html.escape(status), actions, warning, err, "good" if saved >= 0 else "bad", gib(saved), len(upgrades), positive, used, html.escape(status), rows, output)


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
