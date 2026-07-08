#!/usr/bin/env python3
"""Maestro dashboard server. Zero dependencies: stdlib http.server + ctypes.

GET  /...                serves the repo root (dashboard + live state), no-store.
GET  /api/instances      managed Claude Code windows Maestro launched, with liveness.
GET  /api/integrations   configured MCPs, hooks, agents, skills.
POST /api/message        queue a directive -> state/inbox.jsonl + wire.
POST /api/spawn          launch a session on this repo:
                         mode "tab"        -> own titled console window (focusable/closable)
                         mode "background" -> headless claude -p, log in state/spawn-logs/
POST /api/focus  {sid}   bring that window to the foreground (Win32).
POST /api/close  {sid}   taskkill that window's process tree.

Binds 127.0.0.1 only — that IS the boundary; anyone who can POST here can spawn
permission-skipping agents. Never bind 0.0.0.0.

Usage: python dashboard/serve.py [port]     (default 8817)
"""
import collections
import ctypes
import datetime
import json
import os
import re
import subprocess
import sys
import uuid
from ctypes import wintypes
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIRROR = os.path.join(ROOT, ".claude", "hooks", "mirror.py")
INBOX = os.path.join(ROOT, "state", "inbox.jsonl")
WINDOWS = os.path.join(ROOT, "state", "windows.json")
LOGS = os.path.join(ROOT, "state", "spawn-logs")
CREATE_NEW_CONSOLE = 0x00000010
IS_WIN = sys.platform == "win32"


def emit(**kv):
    args = []
    for k, v in kv.items():
        args += ["--" + k, str(v)]
    subprocess.run([sys.executable, MIRROR] + args, capture_output=True)


# ---------------------------------------------------------------- window mgmt
def load_windows():
    try:
        with open(WINDOWS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"windows": []}


def save_windows(doc):
    with open(WINDOWS, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)


def pid_alive(pid):
    if not IS_WIN or not pid:
        return False
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFO
    if not h:
        return False
    code = wintypes.DWORD()
    ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(h)
    return code.value == 259  # STILL_ACTIVE


def _foreground(hwnd):
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    try:
        user32.SwitchToThisWindow(hwnd, True)  # ponytail: undocumented but the
    except Exception:                          # only call that foregrounds from
        user32.SetForegroundWindow(hwnd)       # a background process. Upgrade to
    return True                                # AttachThreadInput if it regresses.


def focus_by(pid, needle):
    """Foreground the console window for this instance. We launch each tab via
    conhost.exe, so the window's owning process IS the pid we tracked — match on
    that (Claude rewrites the console title after launch, so title-match alone
    misses). Title substring is kept as a fallback."""
    if not IS_WIN:
        return False
    user32 = ctypes.windll.user32
    by_pid, by_title = [], []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if pid and wpid.value == pid:
            by_pid.append(hwnd)
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if needle and needle.lower() in buf.value.lower():
            by_title.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    hits = by_pid or by_title
    return _foreground(hits[0]) if hits else False


def integrations():
    out = {"mcp": [], "hooks": [], "agents": [], "skills": {}}
    try:
        mj = json.load(open(os.path.join(ROOT, ".mcp.json"), encoding="utf-8"))
        for name, cfg in mj.get("mcpServers", {}).items():
            cmd = (cfg.get("command", "") + " " + " ".join(cfg.get("args", []))).strip()
            out["mcp"].append({"name": name, "command": cmd})
    except (OSError, json.JSONDecodeError):
        pass
    try:
        sj = json.load(open(os.path.join(ROOT, ".claude", "settings.json"), encoding="utf-8"))
        for ev, arr in sj.get("hooks", {}).items():
            n = sum(len(g.get("hooks", [])) for g in arr)
            out["hooks"].append({"event": ev, "count": n})
    except (OSError, json.JSONDecodeError):
        pass
    adir = os.path.join(ROOT, ".claude", "agents")
    if os.path.isdir(adir):
        out["agents"] = sorted(f[:-3] for f in os.listdir(adir) if f.endswith(".md"))
    try:
        reg = json.load(open(os.path.join(ROOT, "skills", "registry.json"), encoding="utf-8"))
        out["skills"] = dict(collections.Counter(s["status"] for s in reg["skills"].values()))
    except (OSError, json.JSONDecodeError):
        pass
    return out


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")  # hermes c9d5a2f
        super().end_headers()

    def log_message(self, fmt, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/instances":
            doc = load_windows()
            for w in doc["windows"]:
                w["alive"] = pid_alive(w.get("pid"))
            return self._json(200, doc)
        if self.path == "/api/integrations":
            return self._json(200, integrations())
        return super().do_GET()

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "bad json"})
        route = {
            "/api/message": self.api_message, "/api/spawn": self.api_spawn,
            "/api/focus": self.api_focus, "/api/close": self.api_close,
        }.get(self.path)
        if not route:
            return self._json(404, {"error": "unknown endpoint"})
        return route(data)

    def api_message(self, data):
        text = (data.get("text") or "").strip()
        if not text:
            return self._json(400, {"error": "empty directive"})
        row = {"id": uuid.uuid4().hex[:8],
               "ts": datetime.datetime.now().isoformat(timespec="seconds"), "text": text}
        with open(INBOX, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        emit(session="operator", event="directive", detail=("[%s] %s" % (row["id"], text))[:200])
        return self._json(200, {"ok": True, "id": row["id"]})

    def api_spawn(self, data):
        mission = (data.get("mission") or "").strip()
        mode = data.get("mode", "tab")
        if not mission:
            return self._json(400, {"error": "empty mission"})
        # conscious spend: least privilege covers the MODEL and the turn budget,
        # not just tools. Default = inherit the config model; pick a smaller model
        # for mechanical work, a bigger one only for hard reasoning.
        model = {"": "", "default": "", "haiku": "haiku", "sonnet": "sonnet",
                 "opus": "opus"}.get(str(data.get("model", "")).lower(), "")
        try:
            budget = max(1, min(100, int(data.get("budget") or 40)))
        except (TypeError, ValueError):
            budget = 40
        safe = re.sub(r'[&|<>^%"\r\n]', " ", mission).strip()
        sid = uuid.uuid4().hex[:8]
        title = "MAESTRO " + sid
        if mode == "background":
            os.makedirs(LOGS, exist_ok=True)
            log = open(os.path.join(LOGS, sid + ".log"), "w", encoding="utf-8")
            argv = ["claude", "-p", mission, "--dangerously-skip-permissions", "--max-turns", str(budget)]
            if model:
                argv += ["--model", model]
            p = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, shell=IS_WIN)
        else:
            mode = "tab"
            # conhost.exe hosts the window itself, so p.pid owns the HWND (used by
            # /api/focus) and it forces a real console even if Windows Terminal is
            # the default terminal (which would otherwise share one window).
            mflag = (" --model " + model) if model else ""
            cmd = 'conhost.exe cmd /k title %s && claude --dangerously-skip-permissions%s "%s"' % (title, mflag, safe)
            p = subprocess.Popen(cmd, cwd=ROOT,
                                 creationflags=CREATE_NEW_CONSOLE if IS_WIN else 0)
        doc = load_windows()
        doc["windows"].append({
            "sid": sid, "title": title, "mission": mission[:200], "mode": mode,
            "model": model or "default", "budget": budget if mode == "background" else None,
            "pid": p.pid, "started": datetime.datetime.now().isoformat(timespec="seconds")})
        save_windows(doc)
        emit(session="operator", event="user-spawn", agent=mode,
             detail=("[%s] model=%s budget=%s · %s"
                     % (sid, model or "default", budget if mode == "background" else "-", mission))[:200])
        return self._json(200, {"ok": True, "id": sid, "mode": mode, "model": model or "default"})

    def api_focus(self, data):
        sid = data.get("sid", "")
        w = next((x for x in load_windows()["windows"] if x["sid"] == sid), None)
        if not w:
            return self._json(404, {"error": "unknown instance"})
        ok = focus_by(w.get("pid"), w.get("title"))
        return self._json(200 if ok else 409,
                          {"ok": ok, "error": None if ok else "window not found (closed?)"})

    def api_close(self, data):
        sid = data.get("sid", "")
        doc = load_windows()
        w = next((x for x in doc["windows"] if x["sid"] == sid), None)
        if not w:
            return self._json(404, {"error": "unknown instance"})
        if w.get("pid"):
            subprocess.run(["taskkill", "/PID", str(w["pid"]), "/T", "/F"],
                           capture_output=True, shell=IS_WIN)
        doc["windows"] = [x for x in doc["windows"] if x["sid"] != sid]
        save_windows(doc)
        emit(session="operator", event="instance-close", detail="closed %s" % sid)
        return self._json(200, {"ok": True})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8817
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("Maestro: http://127.0.0.1:%d/dashboard/" % port)
    print("serving %s (Ctrl+C to stop)" % ROOT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
