#!/usr/bin/env python3
"""Launch Maestro as a local desktop app: start the server, open the console in a
chromeless app window (no tabs, no address bar), and shut the server down when
you close it.

  python desktop.py         (or: pythonw desktop.py  for no console window)

ponytail: Edge --app mode is the zero-dependency native-window path — Edge ships
with Windows 11, reuses the exact dashboard, and needs no build step. Upgrade to
pywebview (pip) only if you want a true borderless window with Python<->JS calls.
"""
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8817
URL = "http://127.0.0.1:%d/dashboard/" % PORT

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


def main():
    server = subprocess.Popen([sys.executable, os.path.join(HERE, "dashboard", "serve.py"), str(PORT)])
    for _ in range(60):  # wait for the port to answer
        try:
            urllib.request.urlopen(URL, timeout=1)
            break
        except OSError:
            time.sleep(0.2)
    browser = find_browser()
    try:
        if browser:
            prof = os.path.join(HERE, "state", ".appwindow")
            subprocess.run([browser, "--app=" + URL, "--window-size=1480,940",
                            "--user-data-dir=" + prof])
        else:
            print("No Edge/Chrome found; open this in any browser:\n  " + URL)
            server.wait()
    finally:
        server.terminate()


if __name__ == "__main__":
    main()
