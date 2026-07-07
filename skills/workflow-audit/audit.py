#!/usr/bin/env python3
"""Workflow audit: read the wire (state/events.jsonl), summarize activity, and
flag repeated manual command patterns as automation candidates (>=3 repeats)."""
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVENTS = os.path.join(ROOT, "state", "events.jsonl")


def main():
    rows = []
    with open(EVENTS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    by_event = collections.Counter(r.get("event", "?") for r in rows)
    sessions = collections.Counter(r.get("session", "?") for r in rows)
    # first two tokens of each shell detail = the "verb" of the workflow
    cmds = collections.Counter(
        " ".join(str(r.get("detail", "")).split()[:2])
        for r in rows if r.get("event") == "tool" and r.get("detail")
    )
    print("events: %d  |  sessions: %d" % (len(rows), len(sessions)))
    print("\nby event type:")
    for ev, n in by_event.most_common():
        print("  %-14s %d" % (ev, n))
    repeats = [(c, n) for c, n in cmds.most_common() if n >= 3 and c]
    print("\nautomation candidates (same verb >=3x):")
    if not repeats:
        print("  none yet -- wire needs more history")
    for c, n in repeats:
        print("  %-40s x%d  -> consider: python skills/engine.py add <name> ... (see skills/automation)" % (c, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
