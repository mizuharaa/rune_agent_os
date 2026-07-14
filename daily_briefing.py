#!/usr/bin/env python3
"""Daily briefing: what you did since yesterday, and what to pick back up.

Answers one question when you open Rune: "where was I, and what continues?"

  git       commits in THIS repo since the window opened
  github    commits across your other repos (pulse's cached snapshot)
  sessions  Rune missions that ran — and, crucially, the ones left UNFINISHED
  memory    Hermes notes written in the window (the brain, not a log)
  calendar  Outlook via Microsoft Graph (pulse caches it), or a local .ics
  plans     directives queued in the Command Deck that nothing has done yet

OFFLINE BY CONSTRUCTION: every source is a local read (git log, jsonl, json,
.ics on disk). Nothing here makes a network call, so the briefing is always
there the moment the client fires — the network only ever refreshes the caches
that pulse.py already keeps.

Window: yesterday 00:00 -> now. If that's empty (a weekend, a break) it widens
to the last 7 days rather than greeting you with a blank page.

Run:    python daily_briefing.py [--summary]
Import: build() -> dict          (dashboard/serve.py's /api/briefing)
"""
import datetime
import json
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(ROOT, "state", "inbox.jsonl")
EVENTS = os.path.join(ROOT, "state", "events.jsonl")
CEO_DIR = os.path.join(ROOT, "state", "ceo")
SOLVED = os.path.join(ROOT, "hermes", "solved.jsonl")
PULSE_CACHE = os.path.join(ROOT, "state", "pulse-cache.json")
ICS = os.path.join(ROOT, "state", "calendar.ics")

WIDEN_DAYS = 7          # nothing since yesterday? look back this far instead
# roles in these states are work that STOPPED before it was finished — they are
# the whole point of the briefing: what to continue, and why it stopped.
UNFINISHED = ("failed", "blocked", "exhausted", "pending", "working", "review")


def _jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return out


def _dt(s):
    """Parse an ISO-ish timestamp, tolerating a trailing Z. None if unparseable."""
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00").strip()).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _window(days=None):
    """(since, label). Default: yesterday 00:00 — 'what happened since I left'."""
    midnight = datetime.datetime.combine(datetime.date.today(), datetime.time.min)
    if days:
        return midnight - datetime.timedelta(days=days), "last %d days" % days
    return midnight - datetime.timedelta(days=1), "since yesterday"


# ------------------------------------------------------------------ this repo
def _commits(since):
    """Commits in THIS repo since the window opened. git log never touches the
    network, so it works offline by construction."""
    try:
        out = subprocess.run(
            ["git", "log", "--since=" + since.isoformat(),
             "--pretty=format:%h|%ct|%an|%s"],
            cwd=ROOT, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    commits = []
    for line in out.stdout.splitlines():
        h, _, rest = line.partition("|")
        ct, _, rest = rest.partition("|")
        author, _, msg = rest.partition("|")
        if h:
            commits.append({"hash": h, "ts": int(ct or 0), "author": author, "msg": msg})
    return commits


# -------------------------------------------------------------------- github
def _github(since):
    """Your other repos, from the snapshot pulse.py already caches to disk. Read
    only — offline this serves the last good copy and says how old it is."""
    try:
        snap = json.load(open(PULSE_CACHE, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"error": "no cached GitHub data yet — start the dashboard once "
                         "with a token in state/pulse.json"}
    g = snap.get("github") or {}
    if not g or g.get("error"):
        return {"error": g.get("error") or "GitHub not connected (state/pulse.json)"}
    recent = [c for c in (g.get("commits") or [])
              if (_dt(c.get("ts")) or datetime.datetime.min) >= since]
    asof = snap.get("asof") or 0
    return {"user": g.get("user", ""), "commits": recent,
            "today": g.get("today", 0), "streak": g.get("streak", 0),
            "asof": asof,
            # the cache is refreshed every 45s while the dashboard runs; if it's
            # older than an hour we're almost certainly offline — say so.
            "stale": (datetime.datetime.now().timestamp() - asof) > 3600}


# ------------------------------------------------------------------ sessions
def _sessions(since):
    """Rune missions that ran in the window. The unfinished roles carry their
    reason (out of turns, failed, blocked) so 'continue' has clear directions."""
    out = []
    for base in (CEO_DIR, os.path.join(CEO_DIR, "archive")):
        if not os.path.isdir(base):
            continue
        for fn in os.listdir(base):
            if not fn.endswith(".json"):
                continue
            try:
                o = json.load(open(os.path.join(base, fn), encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            when = _dt(o.get("updated")) or _dt(o.get("started"))
            if not when or when < since:
                continue
            todo = [{"id": r.get("id", ""), "title": r.get("title", ""),
                     "status": r.get("status", ""),
                     "why": r.get("detail") or ""}
                    for r in (o.get("roles") or []) if r.get("status") in UNFINISHED]
            out.append({"cid": o.get("cid", ""), "name": o.get("name", ""),
                        "goal": o.get("goal", ""), "status": o.get("status", ""),
                        "detail": o.get("detail") or "",
                        "cost": o.get("cost") or 0, "ts": (o.get("updated") or ""),
                        "unfinished": todo})
    out.sort(key=lambda s: s["ts"], reverse=True)
    return out


# -------------------------------------------------------------------- memory
def _memory(since):
    """Hermes notes written in the window — what Rune actually LEARNED. This is
    the 'check memory' half of the briefing: it reads the brain, not a log."""
    notes = []
    for n in _jsonl(SOLVED):
        when = _dt(n.get("ts"))
        if when and when >= since:
            notes.append({"id": n.get("id", ""), "problem": n.get("problem", ""),
                          "solution": n.get("solution", ""),
                          "tags": n.get("tags") or [], "ts": n.get("ts", "")})
    notes.sort(key=lambda n: n["ts"], reverse=True)
    return notes


# ------------------------------------------------------------------ calendar
# Rune has no calendar OAuth and doesn't need one: every calendar (Google,
# Outlook, Apple) publishes a secret .ics URL. Drop the file at state/calendar.ics
# — or put {"calendar": {"ics_url": "..."}} in state/pulse.json and the dashboard
# refreshes that file for you. We only ever PARSE the local copy, so the briefing
# still has your day when the network is gone.
# ponytail: enough .ics for DTSTART/SUMMARY; use the `ics` package if RRULE
# (recurring events) ever actually matters.
def _ics_events(text, day0, day1):
    """VEVENTs falling on day0..day1 (dates). Unfolds RFC5545 continuation lines."""
    text = re.sub(r"\r?\n[ \t]", "", text)          # unfold
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        m = re.search(r"^DTSTART[^:\r\n]*:([0-9T]+)", block, re.M)
        s = re.search(r"^SUMMARY[^:\r\n]*:(.*)$", block, re.M)
        if not m:
            continue
        raw = m.group(1)
        try:
            d = datetime.date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
        except ValueError:
            continue
        if not (day0 <= d <= day1):
            continue
        t = raw[9:13] if "T" in raw and len(raw) >= 13 else ""
        events.append({"date": d.isoformat(),
                       "time": ("%s:%s" % (t[:2], t[2:4])) if t else "all-day",
                       "summary": (s.group(1).strip() if s else "(untitled)")[:100]})
    events.sort(key=lambda e: (e["date"], e["time"]))
    return events


def _calendar(since):
    """Outlook first (Microsoft Graph, cached to disk by pulse), .ics as fallback.
    Both are read from disk — the briefing never waits on Microsoft to load."""
    try:
        snap = json.load(open(PULSE_CACHE, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        snap = {}
    o = snap.get("outlook") or {}
    if o.get("events") is not None:
        asof = snap.get("asof") or 0
        return {"events": o["events"], "source": "outlook", "asof": asof,
                "stale": (datetime.datetime.now().timestamp() - asof) > 3600}
    if os.path.exists(ICS):
        try:
            text = open(ICS, encoding="utf-8", errors="ignore").read()
        except OSError as e:
            return {"events": [], "error": "calendar unreadable: %s" % e}
        return {"events": _ics_events(text, since.date(),
                                      datetime.date.today() + datetime.timedelta(days=7)),
                "source": "ics", "asof": int(os.path.getmtime(ICS))}
    return {"events": [], "error": "no calendar — connect Outlook (add outlook.client_id "
                                   "to state/pulse.json, then: python dashboard/pulse.py "
                                   "--outlook-login), or drop an .ics at state/calendar.ics"}


CAL_MAX_AGE = 3600      # re-pull the .ics at most hourly; the file is the truth


def refresh_calendar():
    """Pull the subscribed .ics to disk. The ONLY networked thing in this file,
    it is never called by build() — pulse's background loop calls it, and a
    failure just leaves the last good copy in place (that's the offline story)."""
    import urllib.request
    try:
        if os.path.exists(ICS) and \
                datetime.datetime.now().timestamp() - os.path.getmtime(ICS) < CAL_MAX_AGE:
            return False                     # fresh enough, don't touch the network
    except OSError:
        pass
    try:
        cfg = json.load(open(os.path.join(ROOT, "state", "pulse.json"), encoding="utf-8"))
        url = (cfg.get("calendar") or {}).get("ics_url")
    except (OSError, json.JSONDecodeError):
        return False
    if not url:
        return False
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            data = r.read()
        if b"BEGIN:VCALENDAR" not in data[:2000]:
            return False
        tmp = ICS + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, ICS)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------- plans
def _plans():
    """Queued directives, cross-referenced against directive-done events so a
    finished one doesn't still show as pending."""
    done_ids = set()
    for e in _jsonl(EVENTS):
        if e.get("event") == "directive-done":
            done_ids.update(re.findall(r"[0-9a-f]{8}", str(e.get("detail", ""))))
    return [{"id": d.get("id", ""), "text": d.get("text", ""), "ts": d.get("ts", ""),
             "done": d.get("id", "") in done_ids} for d in _jsonl(INBOX)]


def build(days=None):
    since, label = _window(days)
    commits, sessions = _commits(since), _sessions(since)
    memory, gh = _memory(since), _github(since)
    # a blank briefing is a useless briefing: if the default window caught
    # nothing (weekend, a few days off), widen once and say that's what we did.
    if days is None and not (commits or sessions or memory or gh.get("commits")):
        wide = build(days=WIDEN_DAYS)
        if wide["commits"] or wide["sessions"] or wide["memory"]:
            return wide
    plans = _plans()
    pending = [p for p in plans if not p["done"]]
    # the point of the whole card: what stopped mid-flight and why
    continues = [{"cid": s["cid"], "name": s["name"], "status": s["status"],
                  "why": s["detail"] or (s["unfinished"][0]["why"] if s["unfinished"] else ""),
                  "roles": s["unfinished"]}
                 for s in sessions if s["unfinished"]]
    return {
        "date": datetime.date.today().isoformat(),
        "since": since.isoformat(timespec="seconds"),
        "window": label,
        "commits": commits,
        "commit_count": len(commits),
        "github": gh,
        "sessions": sessions,
        "continues": continues,
        "memory": memory,
        "calendar": _calendar(since),
        "plans": plans,
        "plans_pending": len(pending),
    }


def render_text(b):
    L = ["Daily briefing — %s (%s)" % (b["date"], b["window"]), ""]
    L.append("Git — %d commit(s) in this repo:" % b["commit_count"])
    L += ["  %s %s" % (c["hash"], c["msg"]) for c in b["commits"]] or ["  (none)"]
    gh = b["github"]
    L.append("")
    if gh.get("error"):
        L.append("GitHub — %s" % gh["error"])
    else:
        L.append("GitHub — %d commit(s) across your repos%s:"
                 % (len(gh["commits"]), " [cached, offline]" if gh.get("stale") else ""))
        L += ["  %s: %s" % (c["repo"], c["msg"]) for c in gh["commits"]] or ["  (none)"]
    L += ["", "Sessions — %d mission(s) ran:" % len(b["sessions"])]
    L += ["  [%s] %s — %s%s" % (s["cid"], s["name"], s["status"],
                                (" · " + s["detail"]) if s["detail"] else "")
          for s in b["sessions"]] or ["  (none)"]
    L += ["", "PICK BACK UP — %d unfinished:" % len(b["continues"])]
    for c in b["continues"]:
        L.append("  [%s] %s (%s)" % (c["cid"], c["name"], c["status"]))
        L += ["      - %s: %s%s" % (r["title"], r["status"],
                                    (" — " + r["why"]) if r["why"] else "")
              for r in c["roles"]]
    if not b["continues"]:
        L.append("  (nothing left hanging)")
    L += ["", "Memory — %d Hermes note(s) learned:" % len(b["memory"])]
    L += ["  [%s] %s" % (n["id"], n["problem"][:90]) for n in b["memory"]] or ["  (none)"]
    cal = b["calendar"]
    L.append("")
    if cal.get("error"):
        L.append("Calendar — %s" % cal["error"])
    else:
        L.append("Calendar — %d event(s):" % len(cal["events"]))
        L += ["  %s %s  %s" % (e["date"], e["time"], e["summary"])
              for e in cal["events"]] or ["  (none)"]
    pending = [p for p in b["plans"] if not p["done"]]
    L += ["", "Plans — %d pending directive(s):" % len(pending)]
    L += ["  [%s] %s" % (p["id"], p["text"]) for p in pending] or ["  (none queued)"]
    return "\n".join(L)


def render_summary(b):
    return "%s (%s): %d commit(s), %d session(s), %d to continue, %d note(s), %d plan(s) pending" % (
        b["date"], b["window"], b["commit_count"], len(b["sessions"]),
        len(b["continues"]), len(b["memory"]), b["plans_pending"])


if __name__ == "__main__":
    import sys
    # notes and commit messages carry arrows/dashes; the Windows console is cp1252
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    if "--selfcheck" in sys.argv:
        # the .ics parser is the only real logic here — pin it (folded line,
        # all-day vs timed, and a date outside the window that must NOT appear)
        ics = ("BEGIN:VCALENDAR\r\n"
               "BEGIN:VEVENT\r\nDTSTART:20260714T093000Z\r\nSUMMARY:Standup\r\n"
               "END:VEVENT\r\n"
               # RFC5545 folds mid-token: CRLF+space is removed, not turned into
               # a space. "Long fol" + "ded title" => "Long folded title".
               "BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20260713\r\nSUMMARY:Long fol\r\n"
               " ded title\r\nEND:VEVENT\r\n"
               "BEGIN:VEVENT\r\nDTSTART:20200101T000000Z\r\nSUMMARY:Ancient\r\n"
               "END:VEVENT\r\nEND:VCALENDAR\r\n")
        evs = _ics_events(ics, datetime.date(2026, 7, 13), datetime.date(2026, 7, 14))
        assert [e["summary"] for e in evs] == ["Long folded title", "Standup"], evs
        assert evs[0]["time"] == "all-day" and evs[1]["time"] == "09:30", evs
        assert _dt("2026-07-14T10:52:59") and _dt("garbage") is None
        b = build()
        assert {"continues", "memory", "github", "calendar", "sessions"} <= set(b)
        print("daily_briefing.py OK —", render_summary(b))
    else:
        b = build()
        print(render_summary(b) if "--summary" in sys.argv else render_text(b))
