#!/usr/bin/env python3
"""AIOS dashboard server. Zero dependencies: stdlib http.server, serves the repo
root so the dashboard can read live state, with caching disabled so polling
always sees fresh events.

Usage: python dashboard/serve.py [port]     (default 8817)
"""
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def end_headers(self):
        # ponytail: no-store beats cache-busting query strings (hermes c9d5a2f)
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass  # polling every 2.5s would flood the terminal


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8817
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("AIOS mission control: http://127.0.0.1:%d/dashboard/" % port)
    print("serving %s (Ctrl+C to stop)" % ROOT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
