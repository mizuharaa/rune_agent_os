#!/usr/bin/env python3
"""Launch Rune as a local desktop app: ensure a clean server on the port, open
the console in a chromeless app window (no tabs, no address bar), and shut the
server down when you close it.

  python desktop.py         (or: pythonw desktop.py  for no console window)

Self-healing on purpose: this repo modifies itself, and repeated start/stop
churn used to leave a stale server bound to the port (silent round-robin on old
code) or hand the Edge app window off to a dead instance — the classic "nothing
comes up". So we reuse a HEALTHY server if one is already answering, otherwise
free the port of any stale listener and start fresh, detect a server that dies
on startup (and print why), and always fall back to the default browser so a
window ALWAYS appears.

ponytail: Edge --app mode is the zero-dependency native-window path — Edge ships
with Windows 11, reuses the exact dashboard, needs no build step. Upgrade to
pywebview (pip) only for a true borderless window with Python<->JS calls.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8817
URL = "http://127.0.0.1:%d/dashboard/" % PORT
IS_WIN = sys.platform == "win32"

EDGE_CANDIDATES = [
    os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
    os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
]


def find_browser():
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


BACKEND_FILES = [os.path.join(HERE, "dashboard", n) for n in
                 ("serve.py", "ceo.py", "chat.py", "orchestrator.py", "pulse.py", "askpass.py")
                 ] + [os.path.join(HERE, "daily_briefing.py")]


def port_alive():
    """True if a Rune server is already answering on the port AND is running
    current code. A server that started before the newest edit to any backend
    .py file is stale: those routes don't hot-reload (unlike index.html), so a
    naive '/' ping would happily reuse a process missing a just-added route
    (see hermes note 6e781f0 — that's exactly how the briefing card went
    offline). Treat a stale server as not-alive so main() kills and restarts it."""
    try:
        with urllib.request.urlopen(URL, timeout=1) as r:
            if r.status != 200:
                return False
        with urllib.request.urlopen("http://127.0.0.1:%d/api/version" % PORT, timeout=1) as r:
            boot = json.loads(r.read()).get("boot", 0)
    except (OSError, ValueError):
        return False
    newest = max((os.path.getmtime(f) for f in BACKEND_FILES if os.path.exists(f)), default=0)
    return newest <= boot


def live_missions():
    """How many CEO missions are running RIGHT NOW in the server on the port.
    Missions live in that process's threads, so killing it kills them mid-task
    with nothing written down — the other half of 'it died and gave no reason'.
    Nothing answering / can't tell => 0 (never block a restart on a guess)."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/ceo" % PORT, timeout=2) as r:
            return sum(1 for m in json.loads(r.read()).get("runs", []) if m.get("live"))
    except (OSError, ValueError):
        return 0


def free_port():
    """Kill any process LISTENING on PORT so we always run current code on a
    clean port — never let a stale server silently round-robin with the new one
    (the SO_REUSEADDR hazard called out in HANDOFF.md)."""
    if not IS_WIN:
        return
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
    except OSError:
        return
    pids = set()
    for line in out.splitlines():
        if ("127.0.0.1:%d" % PORT in line or "0.0.0.0:%d" % PORT in line) and "LISTENING" in line:
            pids.add(line.split()[-1])
    for pid in pids:
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, shell=True)


def start_server():
    """Start serve.py, capturing output. Returns the Popen, or None if it never
    came up (after printing the reason from its log)."""
    logpath = os.path.join(HERE, "state", "desktop.log")
    os.makedirs(os.path.dirname(logpath), exist_ok=True)
    log = open(logpath, "w", encoding="utf-8")
    srv = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "dashboard", "serve.py"), str(PORT)],
        stdout=log, stderr=subprocess.STDOUT)
    for _ in range(75):                      # ~15s
        if srv.poll() is not None:           # serve.py exited on startup
            break
        if port_alive():
            return srv
        time.sleep(0.2)
    # never answered — surface the server's own error instead of a blank window
    log.flush()
    detail = ""
    try:
        detail = open(logpath, encoding="utf-8").read()[-1200:]
    except OSError:
        pass
    print("Rune server did not come up on port %d.\n%s" % (PORT, detail or "(no output)"))
    if srv.poll() is None:
        srv.terminate()
    return None


def open_window(server):
    """Open the chromeless app window; if the app launch hands off to an existing
    browser and exits immediately (no window), fall back to a normal tab so the
    user ALWAYS gets a visible window."""
    browser = find_browser()
    if not browser:
        webbrowser.open(URL)
        print("Opened Rune in your default browser:\n  " + URL)
        if server:
            server.wait()
        return
    prof = os.path.join(HERE, "state", ".appwindow")
    t0 = time.time()
    subprocess.run([browser, "--app=" + URL, "--window-size=1480,940",
                    "--no-first-run", "--no-default-browser-check",
                    "--user-data-dir=" + prof])
    if time.time() - t0 < 3:   # returned instantly => handoff/no window
        print("App window handed off to a running browser — opening a tab instead.")
        webbrowser.open(URL)
        if server:
            server.wait()


def main():
    if port_alive():
        print("Rune is already serving on %d — opening the window." % PORT)
        server = None            # reuse it; don't spawn a duplicate, don't kill on exit
    elif live_missions():
        # the server is stale (Rune edited its own backend) but is RUNNING a
        # mission. Restarting would kill it mid-task for no stated reason. Keep
        # it: the missions finish, and the next launch picks up the new code.
        n = live_missions()
        print("Server on %d is running %d live mission(s) — keeping it (restart "
              "would kill them). Relaunch once they finish to load new code." % (PORT, n))
        server = None
    else:
        free_port()
        server = start_server()
        if server is None:
            sys.exit(1)
    try:
        open_window(server)
    finally:
        # closing the window must never kill a mission that's still working
        if server and server.poll() is None:
            n = live_missions()
            if n:
                print("Window closed, but %d mission(s) are still running — leaving "
                      "the server up so they finish. Ctrl+C here to force-stop." % n)
                server.wait()
            else:
                server.terminate()


if __name__ == "__main__":
    main()
