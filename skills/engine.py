#!/usr/bin/env python3
"""Skill earn/prune engine. Skills are earned after 3 real uses; skills that
stop serving the current goal decay and archive (2 strikes at /goal or prune).

Usage:
  python skills/engine.py list
  python skills/engine.py add NAME "DESC" [--branch B] [--trigger /verb]
  python skills/engine.py use NAME
  python skills/engine.py goal ["NEW GOAL TEXT"]
  python skills/engine.py link NAME
  python skills/engine.py prune
  python skills/engine.py review     (weekly cadence: list + prune preview)
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(HERE, "registry.json")
EARN_AT = 3
STRIKES = 2


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def load():
    with open(REGISTRY, encoding="utf-8") as f:
        return json.load(f)


def save(reg):
    reg["updated"] = now()
    with open(REGISTRY, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2)


def status_for(s):
    if s.get("status") == "archived":
        return "archived"
    u = s.get("uses", 0)
    return "active" if u >= EARN_AT else ("learning" if u > 0 else "candidate")


def cmd_list(reg):
    goal = reg.get("goal", "(none)")
    print("GOAL: " + goal)
    buckets = {"active": [], "learning": [], "candidate": [], "archived": []}
    for name, s in sorted(reg["skills"].items()):
        buckets[status_for(s)].append((name, s))
    labels = [("active", "EARNED"), ("learning", "LEARNING"),
              ("candidate", "CANDIDATE"), ("archived", "ARCHIVED")]
    for key, label in labels:
        if not buckets[key]:
            continue
        print("\n%s:" % label)
        for name, s in buckets[key]:
            goals = ", ".join(s.get("goals", [])) or "-"
            print("  %-18s uses=%d/%d  branch=%-11s trigger=%-16s goals: %s"
                  % (name, s.get("uses", 0), EARN_AT, s.get("branch", "-"),
                     s.get("trigger", "-"), goals))


def cmd_add(reg, name, desc, branch, trigger):
    if name in reg["skills"]:
        print("exists: " + name)
        return
    reg["skills"][name] = {
        "status": "candidate", "uses": 0, "desc": desc, "branch": branch,
        "trigger": trigger, "goals": [], "decay": 0,
        "created": now(), "last_used": None,
    }
    folder = os.path.join(HERE, name)
    os.makedirs(folder, exist_ok=True)
    card = os.path.join(folder, "SKILL.md")
    if not os.path.exists(card):
        with open(card, "w", encoding="utf-8") as f:
            f.write("# %s\n\n%s\n\nTrigger: %s\nStatus: candidate (earned after %d uses "
                    "via `python skills/engine.py use %s`)\n\n## Process\n\n1. TODO\n"
                    % (name, desc, trigger, EARN_AT, name))
    save(reg)
    print("added candidate '%s' -> %s" % (name, card))


def cmd_use(reg, name):
    s = reg["skills"].get(name)
    if not s:
        print("unknown skill: " + name)
        return 1
    s["uses"] = s.get("uses", 0) + 1
    s["last_used"] = now()
    s["decay"] = 0
    before = s.get("status")
    s["status"] = status_for({**s, "status": "live"})  # recompute; use revives archived
    save(reg)
    tag = " -- EARNED" if s["status"] == "active" and before != "active" else ""
    print("%s uses=%d/%d status=%s%s" % (name, s["uses"], EARN_AT, s["status"], tag))
    return 0


def cmd_goal(reg, text):
    if text:
        reg["goal"] = text
        save(reg)
        print("goal set: " + text)
        print("(skills not linked to it will decay on prune -- link with "
              "`python skills/engine.py link NAME`)")
    else:
        print("goal: " + reg.get("goal", "(none)"))


def cmd_link(reg, name):
    s = reg["skills"].get(name)
    if not s:
        print("unknown skill: " + name)
        return 1
    goal = reg.get("goal")
    if goal and goal not in s.setdefault("goals", []):
        s["goals"].append(goal)
    s["decay"] = 0
    save(reg)
    print("%s linked to goal: %s" % (name, goal))
    return 0


def cmd_prune(reg, dry=False):
    goal = reg.get("goal")
    if not goal:
        print("no current goal; nothing to prune against")
        return
    changed = []
    for name, s in reg["skills"].items():
        if status_for(s) == "archived":
            continue
        if goal in s.get("goals", []):
            continue
        s["decay"] = s.get("decay", 0) + 1
        if s["decay"] >= STRIKES:
            s["status"] = "archived"
            changed.append((name, "ARCHIVED (decay %d/%d)" % (s["decay"], STRIKES)))
        else:
            changed.append((name, "decay %d/%d" % (s["decay"], STRIKES)))
    if dry:
        print("prune preview vs goal '%s':" % goal)
    if not changed:
        print("all live skills serve the goal; nothing decayed")
    for name, what in changed:
        print("  %-18s %s" % (name, what))
    if not dry:
        save(reg)


def main(argv):
    reg = load()
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        cmd_list(reg)
    elif cmd == "add":
        opts = {"--branch": "misc", "--trigger": "-"}
        pos = []
        i = 1
        while i < len(argv):
            if argv[i] in opts:
                opts[argv[i]] = argv[i + 1]
                i += 2
            else:
                pos.append(argv[i])
                i += 1
        cmd_add(reg, pos[0], pos[1] if len(pos) > 1 else "", opts["--branch"], opts["--trigger"])
    elif cmd == "use":
        return cmd_use(reg, argv[1])
    elif cmd == "goal":
        cmd_goal(reg, " ".join(argv[1:]))
    elif cmd == "link":
        return cmd_link(reg, argv[1])
    elif cmd == "prune":
        cmd_prune(reg)
    elif cmd == "review":
        cmd_list(reg)
        print("\n--- weekly review: prune preview (run `prune` to apply) ---")
        cmd_prune(reg, dry=True)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
