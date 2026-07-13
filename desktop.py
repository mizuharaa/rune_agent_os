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


def port_alive():
    """True if a Rune server is already answering on the port."""
    try:
        with urllib.request.urlopen(URL, timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


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
    else:
        free_port()
        server = start_server()
        if server is None:
            sys.exit(1)
    try:
        open_window(server)
    finally:
        if server and server.poll() is None:
            server.terminate()


if __name__ == "__main__":
    main()
