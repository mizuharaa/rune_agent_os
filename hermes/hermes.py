#!/usr/bin/env python3
"""Hermes, the knowledge flywheel: solve once, note it, query it, never re-solve.

Usage:
  python hermes/hermes.py query "TEXT"            exit 0 = hit, 1 = miss
  python hermes/hermes.py note "PROBLEM" "SOLUTION" [--tags a,b] [--source S]
  python hermes/hermes.py stale ID
  python hermes/hermes.py list

Every note appends to hermes/solved.jsonl AND mirrors a markdown card into the
Obsidian vault (AIOS/Hermes/) so the browsable brain grows.
"""
import datetime
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOLVED = os.path.join(HERE, "solved.jsonl")
HIT_AT = 0.34

sys.path.insert(0, os.path.join(ROOT, "memory"))
from pipeline import vault_path  # single source of truth for the vault location


def load():
    rows = []
    if os.path.exists(SOLVED):
        with open(SOLVED, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
    return rows


def save(rows):
    with open(SOLVED, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def brain_folder():
    vp = vault_path()
    if not vp or not os.path.isdir(vp):
        return None
    folder = os.path.join(vp, "Maestro", "Hermes")
    os.makedirs(folder, exist_ok=True)
    return folder


def card_path(row):
    folder = brain_folder()
    if not folder:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", row["problem"].lower()).strip("-")[:60]
    return os.path.join(folder, "%s-%s.md" % (row["id"], slug))


def write_index(rows):
    """Regenerate the browsable MOC for the Maestro section of the brain."""
    folder = brain_folder()
    if not folder:
        return
    stale_n = sum(1 for r in rows if r.get("stale"))
    lines = [
        "---", "generated: " + datetime.datetime.now().isoformat(timespec="seconds"), "---", "",
        "# Maestro · Hermes — solved problems", "",
        "%d solved · %d stale. One card per problem, solved exactly once." % (len(rows), stale_n), "",
    ]
    for r in sorted(rows, key=lambda r: r.get("ts", ""), reverse=True):
        name = os.path.splitext(os.path.basename(card_path(r)))[0]
        flag = " ⚠ STALE" if r.get("stale") else ""
        tags = " ".join("#" + t for t in r.get("tags", []))
        lines.append("- [[%s|%s]] — %s %s%s" % (name, r["problem"], (r.get("ts") or "")[:10], tags, flag))
    with open(os.path.join(folder, "_index.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def write_card(row):
    path = card_path(row)
    if not path:
        print("WARN: vault unreachable, card not mirrored (jsonl still updated)")
        return None
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "---\nid: %s\ndate: %s\ntags: [%s]\nsource: %s\nstale: %s\n---\n\n"
            "# %s\n\n**Solution:**\n\n%s\n"
            % (row["id"], row["ts"], ", ".join(row.get("tags", [])),
               row.get("source", "?"), str(row.get("stale", False)).lower(),
               row["problem"], row["solution"])
        )
    return path


def cmd_note(problem, solution, tags, source):
    row = {
        "id": hashlib.sha1((problem + solution).encode("utf-8")).hexdigest()[:7],
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "problem": problem,
        "solution": solution,
        "tags": [t for t in (tags or "").split(",") if t],
        "source": source or "conductor",
        "stale": False,
    }
    with open(SOLVED, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    path = write_card(row)
    write_index(load())
    print("noted [%s] %s" % (row["id"], problem[:70]))
    if path:
        print("card: %s" % path)
    return 0


def cmd_query(text):
    q = tokens(text)
    if not q:
        print("MISS (empty query)")
        return 1
    scored = []
    for r in load():
        doc = tokens(r["problem"]) | tokens(r["solution"]) | tokens(" ".join(r.get("tags", [])))
        score = len(q & doc) / len(q)
        if r.get("stale"):
            score *= 0.5
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    hits = [(s, r) for s, r in scored if s >= HIT_AT]
    if not hits:
        print("MISS: no prior solution for %r" % text)
        print("(solve it, then: python hermes/hermes.py note \"<problem>\" \"<solution>\" ...)")
        return 1
    for s, r in hits[:3]:
        flag = " [STALE]" if r.get("stale") else ""
        print("HIT %.2f [%s]%s %s" % (s, r["id"], flag, r["problem"]))
        print("    -> %s" % r["solution"])
    return 0


def cmd_stale(rid):
    rows = load()
    for r in rows:
        if r["id"] == rid:
            r["stale"] = True
            save(rows)
            write_card(r)
            write_index(rows)
            print("marked stale: [%s] %s" % (rid, r["problem"][:70]))
            return 0
    print("no such id: " + rid)
    return 1


def main(argv):
    cmd = argv[0] if argv else "list"
    if cmd == "query":
        return cmd_query(" ".join(argv[1:]))
    if cmd == "note":
        opts = {"--tags": "", "--source": ""}
        pos = []
        i = 1
        while i < len(argv):
            if argv[i] in opts:
                opts[argv[i]] = argv[i + 1]
                i += 2
            else:
                pos.append(argv[i])
                i += 1
        if len(pos) < 2:
            print(__doc__)
            return 1
        return cmd_note(pos[0], pos[1], opts["--tags"], opts["--source"])
    if cmd == "stale":
        return cmd_stale(argv[1])
    if cmd == "list":
        for r in load():
            flag = " [STALE]" if r.get("stale") else ""
            print("[%s]%s %s" % (r["id"], flag, r["problem"]))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
