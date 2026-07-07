#!/usr/bin/env python3
"""Non-rot memory pipeline. Every note carries source + freshness; no naked facts.

Usage:
  python memory/pipeline.py vault                 verify the Obsidian wire reads
  python memory/pipeline.py write "TEXT" --source S [--topic T] [--ttl-days N]
  python memory/pipeline.py list
  python memory/pipeline.py dedup                 drop exact duplicates
  python memory/pipeline.py consolidate           merge same-topic notes
  python memory/pipeline.py archive               stale + unreferenced -> archive.jsonl
"""
import datetime
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NOTES = os.path.join(HERE, "notes.jsonl")
ARCHIVE = os.path.join(HERE, "archive.jsonl")


def vault_path():
    with open(os.path.join(HERE, "OBSIDIAN.md"), encoding="utf-8") as f:
        for line in f:
            if line.lower().startswith("vault:"):
                return line.split(":", 1)[1].strip()
    return None


def load_notes(path=NOTES):
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
    return rows


def save_notes(rows, path=NOTES):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def cmd_vault():
    vp = vault_path()
    if not vp or not os.path.isdir(vp):
        print("FAIL: vault not readable at %r" % vp)
        return 1
    mds = []
    for dirpath, _dirs, files in os.walk(vp):
        if os.sep + ".obsidian" in dirpath:
            continue
        for fn in files:
            if fn.endswith(".md"):
                p = os.path.join(dirpath, fn)
                mds.append((os.path.getmtime(p), p))
    mds.sort(reverse=True)
    print("vault OK: %s" % vp)
    print("markdown notes: %d" % len(mds))
    print("most recent:")
    for _mt, p in mds[:3]:
        print("  " + os.path.relpath(p, vp))
    return 0


def cmd_write(text, source, topic, ttl_days):
    if not source:
        print("REFUSED: no naked facts -- every write needs --source")
        return 1
    ts = datetime.datetime.now()
    row = {
        "id": hashlib.sha1(text.encode("utf-8")).hexdigest()[:8],
        "ts": ts.isoformat(timespec="seconds"),
        "source": source,
        "topic": topic or "general",
        "text": text,
        "fresh_until": (ts + datetime.timedelta(days=ttl_days)).strftime("%Y-%m-%d"),
    }
    with open(NOTES, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("noted [%s] topic=%s fresh_until=%s" % (row["id"], row["topic"], row["fresh_until"]))
    return 0


def norm(text):
    return re.sub(r"\s+", " ", text.strip().lower())


def cmd_dedup():
    rows = load_notes()
    seen, kept, dropped = set(), [], 0
    for r in rows:
        h = norm(r.get("text", ""))
        if h in seen:
            dropped += 1
        else:
            seen.add(h)
            kept.append(r)
    save_notes(kept)
    print("dedup: kept %d, dropped %d" % (len(kept), dropped))
    return 0


def cmd_consolidate():
    rows = load_notes()
    by_topic = {}
    for r in rows:
        by_topic.setdefault(r.get("topic", "general"), []).append(r)
    out, merged = [], 0
    for topic, group in by_topic.items():
        if len(group) == 1:
            out.append(group[0])
            continue
        group.sort(key=lambda r: r.get("ts", ""))
        newest = group[-1]
        newest["text"] = " | ".join(r["text"] for r in group)
        newest["source"] = "; ".join(sorted({r.get("source", "?") for r in group}))
        merged += len(group) - 1
        out.append(newest)
    save_notes(out)
    print("consolidate: %d notes merged into their topics; %d notes remain" % (merged, len(out)))
    return 0


def referenced_blob():
    blob = ""
    for p in (os.path.join(ROOT, "hermes", "solved.jsonl"),
              os.path.join(ROOT, "skills", "registry.json")):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                blob += f.read().lower()
    return blob


def cmd_archive():
    rows = load_notes()
    today = datetime.date.today().isoformat()
    blob = referenced_blob()
    keep, arch = [], []
    for r in rows:
        stale = r.get("fresh_until", "9999") < today
        refd = r.get("topic", "").lower() in blob or norm(r.get("text", ""))[:40] in blob
        (arch if stale and not refd else keep).append(r)
    if arch:
        with open(ARCHIVE, "a", encoding="utf-8") as f:
            for r in arch:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    save_notes(keep)
    print("archive: %d moved to archive.jsonl, %d kept (stale-but-referenced notes are kept)" % (len(arch), len(keep)))
    return 0


def main(argv):
    cmd = argv[0] if argv else "vault"
    if cmd == "vault":
        return cmd_vault()
    if cmd == "write":
        opts = {"--source": None, "--topic": None, "--ttl-days": "90"}
        pos = []
        i = 1
        while i < len(argv):
            if argv[i] in opts:
                opts[argv[i]] = argv[i + 1]
                i += 2
            else:
                pos.append(argv[i])
                i += 1
        return cmd_write(" ".join(pos), opts["--source"], opts["--topic"], int(opts["--ttl-days"]))
    if cmd == "list":
        for r in load_notes():
            print("[%s] %s (%s, fresh<=%s) %s" % (r["id"], r["topic"], r["source"], r["fresh_until"], r["text"][:80]))
        return 0
    if cmd == "dedup":
        return cmd_dedup()
    if cmd == "consolidate":
        return cmd_consolidate()
    if cmd == "archive":
        return cmd_archive()
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
