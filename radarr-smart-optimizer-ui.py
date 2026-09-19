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


def queue_records():
    """Return current Radarr download queue rows for the dashboard."""
    data = radarr_get("/queue?page=1&pageSize=100&sortKey=timeleft&sortDirection=ascending")
    return data.get("records", [])


def queue_health(rows):
    """Classify queue rows conservatively from Radarr's own status fields."""
    result = []
    for row in rows:
        status = str(row.get("status") or "").lower()
        tracked = str(row.get("trackedDownloadStatus") or "").lower()
        messages = row.get("statusMessages") or []
        message_parts = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            title = str(m.get("title") or "")
            details = m.get("messages") or []
            if isinstance(details, list):
                detail_text = " ".join(
                    str(x.get("message") or "") if isinstance(x, dict) else str(x)
                    for x in details
                )
            elif isinstance(details, dict):
                detail_text = str(details.get("message") or details)
            else:
                detail_text = str(details)
            message_parts.append((title + " " + detail_text).strip())
        message_text = " ".join(x for x in message_parts if x).strip()
        size = float(row.get("size") or 0)
        left = float(row.get("sizeleft") or 0)
        progress = max(0.0, min(100.0, ((size - left) / size * 100.0) if size else 0.0))
        attention = tracked in ("warning", "error") or status in ("warning", "failed")
        result.append({
            "title": row.get("title") or ("Movie ID %s" % row.get("movieId")),
            "movie_id": row.get("movieId"),
            "status": row.get("status") or row.get("trackedDownloadStatus") or "unknown",
            "tracked": row.get("trackedDownloadStatus") or "",
            "progress": progress,
            "timeleft": row.get("timeleft") or "—",
            "message": message_text,
            "attention": attention,
        })
    return result


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
:root{color-scheme:dark;--bg:#0b0e13;--panel:#121720;--panel2:#161c26;--line:#242b36;--text:#f3f4f6;--muted:#8993a4;--accent:#7dd3fc;--accent2:#a78bfa;--good:#86efac;--bad:#fda4af;--warn:#fde68a}
html,body{margin:0;min-height:100%;background:var(--bg);color:var(--text)}
body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:radial-gradient(circle at 18% -10%,rgba(59,130,246,.10),transparent 32rem),radial-gradient(circle at 90% 0%,rgba(168,85,247,.08),transparent 28rem),var(--bg)}
a{color:inherit}
.shell{max-width:1220px;margin:0 auto;padding:30px 28px 54px}
.topbar{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:28px}
.brand{display:flex;align-items:center;gap:13px}.mark{width:42px;height:42px;border-radius:12px;display:grid;place-items:center;font-size:20px;font-weight:800;background:linear-gradient(145deg,#2563eb,#7c3aed);box-shadow:inset 0 1px rgba(255,255,255,.22),0 8px 24px rgba(37,99,235,.18)}
.brandcopy h1{margin:0;font-size:1.15rem;letter-spacing:-.025em}.brandcopy div{font-size:.76rem;color:var(--muted);margin-top:2px}
.nav{display:flex;align-items:center;gap:8px}.navchip,.status{height:34px;display:inline-flex;align-items:center;gap:8px;padding:0 11px;border-radius:9px;border:1px solid var(--line);background:#10151d;color:#b8c0cc;font-size:.76rem}
.dot{width:7px;height:7px;border-radius:999px;background:var(--good);box-shadow:0 0 10px rgba(134,239,172,.55)}
.hero{display:flex;justify-content:space-between;align-items:flex-end;gap:24px;margin-bottom:18px}
.hero h2{font-size:1.75rem;line-height:1.1;letter-spacing:-.04em;margin:0 0 7px}.hero p{margin:0;color:var(--muted);font-size:.86rem}
.actions{display:flex;gap:8px;flex-wrap:wrap}.actions form{margin:0}
button{height:36px;padding:0 13px;border-radius:9px;border:1px solid #334155;background:#172033;color:#e5e7eb;font-weight:700;font-size:.78rem;cursor:pointer}
button:hover:not(:disabled){background:#1d2940}button.live{background:#2a1720;border-color:#5f2437;color:#fecdd3}button.live:hover:not(:disabled){background:#351b27}button:disabled{opacity:.38;cursor:not-allowed}
.notice{margin:0 0 16px;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:#10151d;color:var(--muted);font-size:.78rem}.notice.bad{color:var(--bad)}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}
.stat{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:13px;padding:16px;min-height:120px}
.stathead{display:flex;align-items:center;justify-content:space-between;gap:12px;color:#aab3c1;font-size:.75rem}.stathead span:first-child{display:flex;align-items:center;gap:8px}.mini{width:24px;height:24px;border-radius:7px;display:grid;place-items:center;background:#0f141c;border:1px solid #222a35;color:#cbd5e1;font-size:.74rem}
.value{font-size:1.85rem;font-weight:760;letter-spacing:-.045em;margin-top:18px}.good{color:var(--good)}.bad{color:var(--bad)}.muted{color:var(--muted)}.sub{font-size:.73rem;color:var(--muted);margin-top:6px}
.layout{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(280px,.8fr);gap:16px}
.panel{background:linear-gradient(180deg,#141a23,#10151c);border:1px solid var(--line);border-radius:13px;overflow:hidden}
.panel+.panel{margin-top:16px}.layout .panel+.panel{margin-top:0}
.panelhead{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;padding:16px 17px 13px;border-bottom:1px solid var(--line)}.panelhead h3{font-size:.9rem;margin:0;letter-spacing:-.015em}.panelhead p{font-size:.73rem;color:var(--muted);margin:4px 0 0}.badge{font-size:.64rem;padding:5px 7px;border-radius:999px;border:1px solid #2a3340;color:#93a4b8;background:#0e131a;white-space:nowrap}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:12px 17px;border-bottom:1px solid #1f2630;font-size:.79rem}th{font-size:.62rem;color:#667085;text-transform:uppercase;letter-spacing:.09em;background:#0f141b}tr:last-child td{border-bottom:0}td:first-child{max-width:520px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sidecontent{padding:16px}.metricline{display:flex;align-items:center;justify-content:space-between;padding:11px 0;border-bottom:1px solid #202731;font-size:.78rem}.metricline:last-child{border-bottom:0}.metricline span:first-child{color:var(--muted)}.metricline b{font-size:.8rem}
pre{margin:0;white-space:pre-wrap;word-break:break-word;max-height:305px;overflow:auto;background:#0c1117;padding:15px 17px;color:#bbc5d3;font:11.5px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}
.footer{padding-top:22px;text-align:center;font-size:.68rem;color:#4c5667}
.toolbar{display:flex;gap:10px;align-items:center;margin-bottom:16px}.searchbox{position:relative;flex:1}.searchbox input{width:100%;height:40px;border-radius:10px;border:1px solid var(--line);background:#0f141b;color:var(--text);padding:0 14px 0 38px;outline:none;font-size:.8rem}.searchbox input:focus{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.10)}.searchicon{position:absolute;left:13px;top:10px;color:#64748b}.queueitem{padding:14px 17px;border-bottom:1px solid #1f2630}.queueitem:last-child{border-bottom:0}.qtop{display:flex;justify-content:space-between;gap:12px;align-items:center}.qtitle{font-size:.8rem;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.qmeta{font-size:.7rem;color:var(--muted);margin-top:5px}.progress{height:5px;background:#0b1016;border-radius:99px;overflow:hidden;margin-top:10px}.progress span{display:block;height:100%;background:linear-gradient(90deg,#3b82f6,#8b5cf6);border-radius:99px}.attention{color:var(--warn)}.empty{padding:22px 17px;color:var(--muted);font-size:.78rem}.sectiontabs{display:flex;gap:5px;margin-bottom:12px}.tab{font-size:.72rem;padding:6px 9px;border-radius:8px;background:#10151d;border:1px solid var(--line);color:#8e99aa}.tab.active{color:#e5e7eb;background:#17202c}.kpi{font-size:.66rem;color:#667085;text-transform:uppercase;letter-spacing:.08em}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}.layout{grid-template-columns:1fr}.hero{align-items:flex-start;flex-direction:column}.topbar{align-items:flex-start;flex-direction:column}.nav{width:100%;justify-content:space-between}}
@media(max-width:520px){.shell{padding:22px 14px 40px}.grid{grid-template-columns:1fr}.hero h2{font-size:1.45rem}th,td{padding:11px 12px}}
"""

def page():
    state = load_state()
    today = time.strftime("%Y-%m-%d")
    used = int((state.get("daily") or {}).get(today, {}).get("searches", 0))
    error = ""
    try:
        records = history_records()
        upgrades = completed_upgrades(records)
        queue = queue_health(queue_records())
    except Exception as exc:
        upgrades, queue = [], []
        error = str(exc)
    saved = sum(x["saved"] for x in upgrades)
    positive = sum(1 for x in upgrades if x["saved"] > 0)
    total_before = sum(x["old"] for x in upgrades)
    reduction_pct = (saved / total_before * 100.0) if total_before else 0.0
    attention = [x for x in queue if x["attention"]]
    last_date = upgrades[0]["date"][:10] if upgrades else "—"
    with job_lock:
        snap = dict(job)

    rows = ""
    for x in upgrades[:20]:
        delta = gib(x["saved"])
        cls = "good" if delta >= 0 else "bad"
        rows += "<tr class='filterrow' data-search='%s'><td>%s</td><td>%.2f GiB</td><td>%.2f GiB</td><td class='%s'>%+.2f GiB</td></tr>" % (
            html.escape(x["title"].lower(), quote=True), html.escape(x["title"]), gib(x["old"]), gib(x["new"]), cls, delta)
    if not rows:
        rows = "<tr><td colspan='4' class='muted'>No completed upgrade pairs found in the loaded history window.</td></tr>"

    qrows = ""
    for x in queue[:20]:
        cls = "attention" if x["attention"] else ""
        note = x["message"] or ("Time left: %s" % x["timeleft"])
        qrows += """<div class="queueitem filterrow" data-search="%s"><div class="qtop"><div class="qtitle">%s</div><div class="%s">%s</div></div><div class="qmeta">%.1f%% · %s</div><div class="progress"><span style="width:%.1f%%"></span></div></div>""" % (
            html.escape(x["title"].lower(), quote=True), html.escape(x["title"]), cls,
            "Needs attention" if x["attention"] else html.escape(str(x["status"])),
            x["progress"], html.escape(note), x["progress"])
    if not qrows:
        qrows = "<div class='empty'>Nothing is currently in Radarr's download queue.</div>"

    actions = """<div class="actions">
      <form method="post" action="/run"><input type="hidden" name="mode" value="dry"><button %s>Dry run</button></form>
      <form method="post" action="/run"><input type="hidden" name="mode" value="live"><button class="live" %s>Optimize now</button></form>
      </div>""" % ("" if ENABLE_ACTIONS and not snap["running"] else "disabled",
                    "" if ENABLE_ACTIONS and not snap["running"] else "disabled")
    output = html.escape(snap.get("output") or "No UI-started run yet.")
    status = "Running %s…" % snap["mode"] if snap["running"] else "Idle"
    warning = "" if ENABLE_ACTIONS else "<div class='notice'>Read-only mode is active. Smart retry controls will only be enabled after we validate queue detection and candidate selection.</div>"
    err = ("<div class='notice bad'>Radarr API error: %s</div>" % html.escape(error)) if error else ""

    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#0b0e13"><title>Radarr Smart Optimizer</title><style>%s</style></head>
<body><div class="shell">
<div class="topbar"><div class="brand"><div class="mark">R</div><div class="brandcopy"><h1>Radarr Smart Optimizer</h1><div>Library optimization dashboard</div></div></div><div class="nav"><span class="navchip">Overview</span><span class="status"><span class="dot"></span>%s</span></div></div>
<div class="hero"><div><h2>Overview</h2><p>See savings, active downloads, problem jobs and optimizer activity in one place.</p></div>%s</div>
%s%s
<div class="toolbar"><div class="searchbox"><span class="searchicon">⌕</span><input id="librarySearch" autocomplete="off" placeholder="Search releases and current downloads…"></div></div>
<div class="grid">
<div class="stat"><div class="stathead"><span><span class="mini">↘</span>Storage saved</span></div><div class="value %s">%+.2f GiB</div><div class="sub">Observed across loaded upgrade history</div></div>
<div class="stat"><div class="stathead"><span><span class="mini">✓</span>Space reductions</span></div><div class="value">%d</div><div class="sub">Observed upgrades that ended smaller</div></div>
<div class="stat"><div class="stathead"><span><span class="mini">↓</span>Active downloads</span></div><div class="value">%d</div><div class="sub">%d currently need attention</div></div>
<div class="stat"><div class="stathead"><span><span class="mini">⌕</span>Searches today</span></div><div class="value">%d</div><div class="sub">Optimizer state counter</div></div>
</div>
<div class="layout"><div>
<div class="panel"><div class="panelhead"><div><h3>Recent file changes</h3><p>Observed Radarr upgrade pairs. These are not all necessarily optimizer-triggered.</p></div><span class="badge">HISTORY</span></div>
<table><thead><tr><th>Release</th><th>Before</th><th>After</th><th>Change</th></tr></thead><tbody>%s</tbody></table></div>
<div class="panel"><div class="panelhead"><div><h3>Optimizer activity</h3><p>Output from runs started through this dashboard.</p></div><span class="badge">ACTIVITY</span></div><pre>%s</pre></div>
</div>
<div>
<div class="panel"><div class="panelhead"><div><h3>Download radar</h3><p>Live Radarr queue with problem jobs surfaced automatically.</p></div><span class="badge">%d ACTIVE</span></div>%s</div>
<div class="panel"><div class="panelhead"><div><h3>Optimizer intelligence</h3><p>Useful context without pretending Radarr history equals optimizer success.</p></div><span class="badge">SUMMARY</span></div><div class="sidecontent">
<div class="metricline"><span>Observed net reduction</span><b class="%s">%.1f%%</b></div>
<div class="metricline"><span>Smaller replacements</span><b>%d</b></div>
<div class="metricline"><span>Downloads needing attention</span><b class="%s">%d</b></div>
<div class="metricline"><span>Last observed upgrade</span><b>%s</b></div>
<div class="metricline"><span>Engine</span><b>%s</b></div>
<div class="metricline"><span>UI mode</span><b>%s</b></div>
</div></div></div></div>
<div class="footer">Radarr Smart Optimizer · storage intelligence, not another Radarr replacement</div>
</div>
<script>
const box=document.getElementById('librarySearch');
box.addEventListener('input',()=>{const q=box.value.trim().toLowerCase();document.querySelectorAll('.filterrow').forEach(el=>{el.style.display=!q||((el.dataset.search||'').includes(q))?'':'none';});});
</script></body></html>""" % (
        CSS, html.escape(status), actions, warning, err,
        "good" if saved >= 0 else "bad", gib(saved), positive,
        len(queue), len(attention), used, rows, output, len(queue), qrows,
        "good" if reduction_pct >= 0 else "bad", reduction_pct, positive,
        "bad" if attention else "good", len(attention), html.escape(last_date),
        html.escape(status), "Actions enabled" if ENABLE_ACTIONS else "Read-only")


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
