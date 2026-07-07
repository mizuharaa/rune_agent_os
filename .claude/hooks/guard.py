#!/usr/bin/env python3
"""PreToolUse gate. Blocks gated action classes unless state/approvals.json
holds an unexpired token for that class. Exit 2 = block; stderr goes to Claude.
Fails closed: anything matching a gate without a token does not run."""
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APPROVALS = os.path.join(ROOT, "state", "approvals.json")

GATES = {
    "destructive-delete": r"rm\s+-\w*[rf]|Remove-Item\b.*-(Recurse|Force)|rmdir\s+/s|del\s+/[sfq]|git\s+(reset\s+--hard|clean\s+-\w*f|push\b[^|;&]*(--force|\s-f\b))|DROP\s+(TABLE|DATABASE)|\bmkfs\b|format\s+[a-z]:",
    "deploy": r"\bgh\s+release\b|\bvercel\b|\bnetlify\b|\bfly\s+deploy\b|\bkubectl\s+apply\b|\bterraform\s+apply\b|\bdocker\s+push\b|\bdeploy\b",
    "external-send": r"\b(curl|wget|Invoke-RestMethod|Invoke-WebRequest)\b[^|;]*(-X\s*(POST|PUT|DELETE)|--data\b|\s-d\s|-Method\s+(Post|Put|Delete))|\bsendmail\b|\btwilio\b",
    "spend": r"\bnpm\s+publish\b|\btwine\s+upload\b|\bstripe\b|\baws\s+\S+\s+(create|run-instances)|\bgcloud\b.*\bcreate\b",
}


def gate_for(data):
    """Return (action, evidence) if this tool call is gated, else (None, None)."""
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        p = (ti.get("file_path") or ti.get("notebook_path") or "").replace("\\", "/").lower()
        if "/soul/" in p or p.startswith("soul/"):
            return "soul-write", p
    cmd = ti.get("command", "")
    if cmd:
        if re.search(r"soul[/\\]", cmd) and re.search(
            r"(>>?|Set-Content|Out-File|Add-Content|sed\s+-i|\btee\b)", cmd
        ):
            return "soul-write", cmd
        for name, rx in GATES.items():
            if re.search(rx, cmd, re.I):
                return name, cmd
    return None, None


def approved(action):
    try:
        with open(APPROVALS, encoding="utf-8") as f:
            tokens = json.load(f).get("tokens", [])
    except (OSError, json.JSONDecodeError):
        return False  # ponytail: unreadable approvals = no approvals (fail closed)
    now = time.time()
    return any(
        t.get("action") in (action, "*") and t.get("expires", 0) > now for t in tokens
    )


def main():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    action, evidence = gate_for(data)
    if not action or approved(action):
        return 0
    sys.stderr.write(
        "AIOS GUARD: blocked gated action '%s'.\n"
        "Evidence: %s\n"
        "If Daniel approved this, mint a token: "
        "python .claude/hooks/approve.py %s --minutes 15\n"
        % (action, str(evidence)[:200], action)
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
