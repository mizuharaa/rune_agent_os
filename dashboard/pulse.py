#!/usr/bin/env python3
"""Pulse: the outside-world strip for the dashboard (stdlib only).

Reads gitignored state/pulse.json for credentials (values copied from the
owner's env — never paths, never committed) and serves one cached snapshot:

  claude   token usage + countdown to the 5h-window reset, per account
           (approximation: window starts at the first message seen in the
           last 5h across that account's transcript files)
  github   recent commits via the events API
  gmail    unread count + latest subjects via IMAP (app password)
  spotify  now playing via refresh-token flow; "not connected" until
           client_id/client_secret/refresh_token exist in pulse.json

A daemon thread refreshes every 45s so requests never block on the network.
Each service degrades to {"error": ...} independently.
"""
import base64
import datetime
import email.header
import imaplib
import json
import os
import threading
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "state", "pulse.json")
WINDOW = 5 * 3600
SNAP = {"asof": 0}
LOCK = threading.Lock()


def _cfg():
    try:
        return json.load(open(CFG, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _http(url, headers=None, data=None, timeout=8):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ---------------------------------------------------------------- claude usage
def _email(base):
    """The logged-in account email, read from <config-dir>/.claude.json (or the
    sibling <dir>.json used by the default ~/.claude). Best-effort, never raises."""
    if not base:
        return None
    for cand in (os.path.join(base, ".claude.json"), base.rstrip("/\\") + ".json"):
        try:
            with open(cand, encoding="utf-8") as f:
                oa = (json.load(f).get("oauthAccount") or {})
            if oa.get("emailAddress"):
                return oa["emailAddress"]
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    return None


def _claude_account(acct):
    base = acct.get("dir", "")
    email = acct.get("email") or _email(base)
    d = os.path.join(base, "projects")
    if not os.path.isdir(d):
        return {"name": acct.get("name", "?"), "email": email, "dir": base,
                "error": "no transcript dir"}
    now = time.time()
    # scan ~10.5h back so the CURRENT 5h window can be anchored correctly (the
    # old code took min(ts in last 5h), which slides forward as messages age out
    # so the countdown never actually reaches a reset).
    scan = 2 * WINDOW + 1800
    files = []
    for dirpath, _dirs, fns in os.walk(d):
        for fn in fns:
            if fn.endswith(".jsonl"):
                p = os.path.join(dirpath, fn)
                try:
                    mt = os.path.getmtime(p)
                except OSError:
                    continue
                if now - mt < scan:
                    files.append(p)
    events = []  # (ts, tokens_in, tokens_out) for messages within the scan
    for p in files[:80]:
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if '"usage"' not in line or '"timestamp"' not in line:
                        continue
                    try:
                        j = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = j.get("timestamp") or ""
                    try:
                        t = datetime.datetime.fromisoformat(
                            ts.replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        continue
                    if now - t > scan or t > now + 300:
                        continue
                    u = (j.get("message") or {}).get("usage") or {}
                    if not u:
                        continue
                    events.append((t,
                                   (u.get("input_tokens") or 0)
                                   + (u.get("cache_creation_input_tokens") or 0),
                                   u.get("output_tokens") or 0))
        except OSError:
            continue
    out = {"name": acct.get("name", "?"), "email": email, "dir": base, "msgs": 0,
           "tokens_in": 0, "tokens_out": 0, "limit_tokens": acct.get("limit_tokens")}
    if not events:
        return out
    events.sort()
    # the 5h window is anchored to its first message and resets exactly 5h later;
    # the next message after that starts a fresh window. Chain the blocks to find
    # the one NOW falls in — its start is fixed, so the countdown is real.
    ws = events[0][0]
    for t, _ti, _to in events:
        if t - ws >= WINDOW:
            ws = t
    if now - ws >= WINDOW:
        return out  # last window elapsed with no new activity -> clear, no reset
    ti = to = msgs = 0
    for t, a, b in events:
        if t >= ws:
            msgs += 1
            ti += a
            to += b
    out.update(msgs=msgs, tokens_in=ti, tokens_out=to, reset_at=int(ws + WINDOW))
    if acct.get("limit_tokens"):
        out["pct"] = min(100, round((ti + to) / acct["limit_tokens"] * 100))
    return out


# --- accurate per-account tracking via the server's own rate-limit headers ----
# Transcripts don't record WHICH account sent each message, so scanning a config
# dir mis-attributes usage when accounts are swapped in one terminal. Instead we
# capture each account's OAuth token as it's seen, then ask Anthropic for that
# account's TRUE unified 5h/7d window (utilization + reset). Server-authoritative,
# per-account, independent of how terminals/dirs are arranged.
SEEN = os.path.join(ROOT, "state", "claude-seen.json")  # gitignored (holds tokens)
RL_CACHE = {}            # account key -> (data, fetched_at)
RL_TTL = 180             # re-probe the server at most every 3 min per account


def _creds(base):
    """{token, expires_ms, email, uuid} for the account currently logged into
    config dir `base` (from its .credentials.json + .claude.json)."""
    try:
        oa = (json.load(open(os.path.join(base, ".credentials.json"),
                              encoding="utf-8")).get("claudeAiOauth") or {})
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not oa.get("accessToken"):
        return {}
    out = {"token": oa["accessToken"], "expires_ms": oa.get("expiresAt")}
    for cand in (os.path.join(base, ".claude.json"), base.rstrip("/\\") + ".json"):
        try:
            a = (json.load(open(cand, encoding="utf-8")).get("oauthAccount") or {})
            if a.get("emailAddress"):
                out["email"], out["uuid"] = a["emailAddress"], a.get("accountUuid")
                break
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    return out


def _load_seen():
    try:
        return json.load(open(SEEN, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _capture_seen(cfg):
    """Snapshot every account currently visible (default ~/.claude + configured
    dirs) keyed by accountUuid, so accounts swapped through one terminal
    accumulate and each keeps its own token. Returns the merged seen-map."""
    seen = _load_seen()
    now = int(time.time())
    dirs = {os.path.expanduser("~/.claude")}
    for a in cfg.get("claude_accounts") or []:
        if a.get("dir"):
            dirs.add(a["dir"])
    names = cfg.get("account_names") or {}  # optional {email: friendly name} override
    changed = False
    for d in dirs:
        c = _creds(d)
        uid = c.get("uuid")
        if not (uid and c.get("token")):
            continue
        email = c.get("email")
        rec = seen.get(uid, {"first_seen": now})
        rec.update(email=email, token=c["token"], expires_ms=c.get("expires_ms"),
                   dir=d, last_seen=now,
                   # name by EMAIL, stable when accounts are swapped through one dir
                   name=names.get(email) or (email.split("@")[0] if email else "account"))
        seen[uid] = rec
        changed = True
    if changed:
        try:
            os.makedirs(os.path.dirname(SEEN), exist_ok=True)
            tmp = SEEN + ".tmp"
            json.dump(seen, open(tmp, "w", encoding="utf-8"), indent=1)
            os.replace(tmp, SEEN)
        except OSError:
            pass
    return seen


def _ratelimit(token, key):
    """Ask the server for this account's real unified 5h/7d window. Throttled per
    key. Sends a 1-token message (the only call that returns the headers) —
    negligible spend."""
    hit = RL_CACHE.get(key)
    if hit and time.time() - hit[1] < RL_TTL:
        return hit[0]
    body = json.dumps({"model": "claude-haiku-4-5", "max_tokens": 1,
                       "messages": [{"role": "user", "content": "."}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
        headers={"content-type": "application/json", "authorization": "Bearer " + token,
                 "anthropic-version": "2023-06-01", "anthropic-beta": "oauth-2025-04-20"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            out = _parse_rl(r.headers)
    except urllib.error.HTTPError as e:
        # a 429 (window exhausted) STILL carries the unified reset/utilization
        # headers — read them so an at-limit account shows its real reset.
        out = _parse_rl(e.headers)
        if out.get("reset_at") is None:
            out = {"error": "re-open this account to refresh" if e.code == 401 else "http %d" % e.code}
    except Exception as e:
        out = {"error": type(e).__name__}
    RL_CACHE[key] = (out, time.time())
    return out


def _parse_rl(h):
    def num(k):
        try:
            return float(h.get(k))
        except (TypeError, ValueError):
            return None
    u5, u7 = num("anthropic-ratelimit-unified-5h-utilization"), \
        num("anthropic-ratelimit-unified-7d-utilization")
    return {"reset_at": int(num("anthropic-ratelimit-unified-5h-reset") or 0) or None,
            "reset7d": int(num("anthropic-ratelimit-unified-7d-reset") or 0) or None,
            "pct": None if u5 is None else min(100, round(u5 * 100)),
            "pct7d": None if u7 is None else min(100, round(u7 * 100)),
            "status": h.get("anthropic-ratelimit-unified-5h-status")}


def _claude(cfg):
    seen = _capture_seen(cfg)
    if not seen:  # nothing captured yet -> fall back to transcript scan
        accts = cfg.get("claude_accounts") or []
        return {"accounts": [_claude_account(a) for a in accts]} if accts \
            else {"error": "no Claude account seen yet — open a Claude Code session"}
    out = []
    for uid, r in sorted(seen.items(), key=lambda kv: -(kv[1].get("last_seen") or 0)):
        exp = r.get("expires_ms")
        stale = exp and exp / 1000 < time.time()
        rl = {"error": "re-open this account to refresh"} if stale or not r.get("token") \
            else _ratelimit(r["token"], uid)
        out.append({"name": r.get("name") or "account", "email": r.get("email"),
                    "dir": r.get("dir"), "reset_at": rl.get("reset_at"),
                    "pct": rl.get("pct"), "pct7d": rl.get("pct7d"),
                    "reset7d": rl.get("reset7d"), "error": rl.get("error")})
    return {"accounts": out}


# ---------------------------------------------------------------- github
# The public events API now returns TRIMMED PushEvent payloads (SHAs only, no
# commits[]/size), so counting commits from events yields 0. Source of truth is
# the GraphQL contribution calendar (accurate daily counts + a year of graph
# data); recent commit messages come from the REST commits endpoint of the
# most-recently-pushed repo. Needs a token with read:user scope.
_GQL_CAL = """query($l:String!){user(login:$l){contributionsCollection{
  contributionCalendar{totalContributions weeks{contributionDays{date contributionCount}}}}}}"""


def _github_calendar(user, hdr):
    """Year of daily contribution counts via GraphQL. Returns (days, total) where
    days is a flat [{date, count}] list, or ([], 0) if unavailable (no token)."""
    if "Authorization" not in hdr:
        return [], 0  # GraphQL requires auth; skip cleanly on public-only config
    body = json.dumps({"query": _GQL_CAL, "variables": {"l": user}}).encode()
    try:
        d = _http("https://api.github.com/graphql",
                  dict(hdr, **{"Content-Type": "application/json"}), body)
    except Exception:
        return [], 0
    if d.get("errors"):
        return [], 0
    cal = (((d.get("data") or {}).get("user") or {}).get("contributionsCollection")
           or {}).get("contributionCalendar") or {}
    days = [{"date": dd["date"], "count": dd["contributionCount"]}
            for w in cal.get("weeks", []) for dd in w.get("contributionDays", [])]
    return days, cal.get("totalContributions", 0)


def _github(cfg):
    g = cfg.get("github") or {}
    user, token = g.get("user"), g.get("token")
    if not user:
        return {"error": "not connected"}
    hdr = {"User-Agent": "rune-pulse", "Accept": "application/vnd.github+json"}
    if token:
        hdr["Authorization"] = "Bearer " + token
    days, total_year = _github_calendar(user, hdr)
    today_key = datetime.date.today().isoformat()
    today = next((d["count"] for d in reversed(days) if d["date"] == today_key), 0)
    # streak: consecutive days up to and including today with >0 contributions
    streak = 0
    for d in reversed(days):
        if d["date"] > today_key:
            continue
        if d["count"] > 0:
            streak += 1
        elif d["date"] != today_key:  # today at 0 doesn't break a prior streak
            break
    # recent commit messages from the most-recently-pushed repo (events still
    # carry repo + timestamp reliably, just not the commit bodies)
    commits, repos = [], []
    try:
        evs = _http("https://api.github.com/users/%s/events?per_page=30" % user, hdr)
        for e in evs:
            if e.get("type") == "PushEvent":
                r = (e.get("repo") or {}).get("name")
                if r and r not in repos:
                    repos.append(r)
    except Exception:
        pass
    for full in repos[:2]:
        try:
            cs = _http("https://api.github.com/repos/%s/commits?per_page=6" % full, hdr)
        except Exception:
            continue
        repo = full.split("/")[-1]
        for c in cs:
            cm = c.get("commit") or {}
            commits.append({"repo": repo,
                            "msg": (cm.get("message") or "").split("\n")[0][:80],
                            "ts": (cm.get("author") or {}).get("date", "")})
        if len(commits) >= 8:
            break
    commits.sort(key=lambda c: c["ts"], reverse=True)
    return {"user": user, "today": today, "year": total_year,
            "streak": streak, "days": days, "commits": commits[:8]}


# ---------------------------------------------------------------- gmail
def _decode(s):
    try:
        return "".join(t.decode(enc or "utf-8", "replace") if isinstance(t, bytes) else t
                       for t, enc in email.header.decode_header(s))
    except Exception:
        return s


def _gmail(cfg):
    g = cfg.get("gmail") or {}
    addr, pw = g.get("email"), (g.get("app_password") or "").replace(" ", "")
    if not addr or not pw:
        return {"error": "not connected"}
    m = imaplib.IMAP4_SSL("imap.gmail.com", timeout=8)
    try:
        m.login(addr, pw)
        m.select("INBOX", readonly=True)
        _typ, data = m.search(None, "UNSEEN")
        ids = (data[0] or b"").split()
        subs = []
        for i in ids[-3:][::-1]:
            _t, md = m.fetch(i, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])")
            raw = (md[0][1] if md and md[0] else b"").decode("utf-8", "replace")
            subj = frm = ""
            for ln in raw.splitlines():
                if ln.lower().startswith("subject:"):
                    subj = _decode(ln[8:].strip())
                elif ln.lower().startswith("from:"):
                    frm = _decode(ln[5:].strip()).split("<")[0].strip(' "')
            subs.append({"from": frm[:40], "subject": subj[:80]})
        return {"email": addr, "unread": len(ids), "latest": subs}
    finally:
        try:
            m.logout()
        except Exception:
            pass


# ---------------------------------------------------------------- spotify
def _sp_token(cfg=None):
    """(access_token, error) via the stored refresh token."""
    s = (cfg or _cfg()).get("spotify") or {}
    cid, sec, rt = s.get("client_id"), s.get("client_secret"), s.get("refresh_token")
    if not (cid and sec and rt):
        return None, "not connected"
    auth = base64.b64encode(("%s:%s" % (cid, sec)).encode()).decode()
    tok = _http("https://accounts.spotify.com/api/token",
                {"Authorization": "Basic " + auth,
                 "Content-Type": "application/x-www-form-urlencoded"},
                urllib.parse.urlencode({"grant_type": "refresh_token",
                                        "refresh_token": rt}).encode())
    return tok.get("access_token"), None


def _spotify(cfg):
    at, err = _sp_token(cfg)
    if err:
        return {"error": err}
    req = urllib.request.Request(
        "https://api.spotify.com/v1/me/player/currently-playing",
        headers={"Authorization": "Bearer " + at})
    with urllib.request.urlopen(req, timeout=8) as r:
        if r.status == 204:
            return {"playing": False}
        j = json.load(r)
    item = j.get("item") or {}
    return {"playing": bool(j.get("is_playing")),
            "track": item.get("name", ""),
            "artist": ", ".join(a["name"] for a in item.get("artists", [])),
            "art": ((item.get("album") or {}).get("images") or [{}])[-1].get("url", ""),
            "progress_ms": j.get("progress_ms"),
            "duration_ms": (item.get("duration_ms") or None)}


def spotify_ctl(action, pos_ms=None):
    """Playback control: next / prev / seek / toggle. Needs the
    user-modify-playback-state scope — older tokens get a friendly error."""
    at, err = _sp_token()
    if err:
        return {"error": err}
    base = "https://api.spotify.com/v1/me/player"
    if action == "toggle":
        playing = (get().get("spotify") or {}).get("playing")
        method, url = "PUT", base + ("/pause" if playing else "/play")
    elif action == "next":
        method, url = "POST", base + "/next"
    elif action == "prev":
        method, url = "POST", base + "/previous"
    elif action == "seek":
        method, url = "PUT", base + "/seek?position_ms=%d" % max(0, int(pos_ms or 0))
    else:
        return {"error": "unknown action"}
    req = urllib.request.Request(url, data=b"", method=method,
                                 headers={"Authorization": "Bearer " + at})
    try:
        urllib.request.urlopen(req, timeout=8)
    except urllib.error.HTTPError as e:
        # the token is freshly refreshed, so 401 here = missing scope
        # ("Permissions missing"), not an expired token; 403 similar.
        if e.code in (401, 403):
            return {"error": "controls need permission — hit reconnect on the Spotify card"}
        if e.code == 404:
            return {"error": "no active Spotify device"}
        return {"error": "spotify %d" % e.code}
    except Exception as e:
        return {"error": type(e).__name__}
    # reflect the change immediately instead of waiting for the 7s loop
    try:
        v = _spotify(_cfg())
        with LOCK:
            if SNAP:
                SNAP["spotify"] = v
    except Exception:
        v = {}
    return {"ok": True, "spotify": v}


# -------------------------------------------------- spotify OAuth (code flow)
SPOTIFY_SCOPE = "user-read-currently-playing user-read-playback-state user-modify-playback-state"
# Spotify requires the redirect URI to EXACTLY match one registered in the app,
# and loopback must be the literal 127.0.0.1 (not localhost). Fixed + overridable.
SPOTIFY_REDIRECT_DEFAULT = "http://127.0.0.1:8817/api/spotify/callback"


def spotify_redirect():
    return (_cfg().get("spotify") or {}).get("redirect_uri") or SPOTIFY_REDIRECT_DEFAULT


def spotify_authorize_url(redirect_uri):
    """The consent URL to send the user to, or None if no client_id configured."""
    s = _cfg().get("spotify") or {}
    if not s.get("client_id"):
        return None
    return "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": s["client_id"], "response_type": "code",
        "redirect_uri": redirect_uri, "scope": SPOTIFY_SCOPE})


def spotify_exchange(code, redirect_uri):
    """Exchange an auth code for a refresh token and persist it. Returns an
    error string, or None on success."""
    s = _cfg().get("spotify") or {}
    cid, sec = s.get("client_id"), s.get("client_secret")
    if not (cid and sec):
        return "client_id / client_secret missing in state/pulse.json"
    auth = base64.b64encode(("%s:%s" % (cid, sec)).encode()).decode()
    try:
        tok = _http("https://accounts.spotify.com/api/token",
                    {"Authorization": "Basic " + auth,
                     "Content-Type": "application/x-www-form-urlencoded"},
                    urllib.parse.urlencode({"grant_type": "authorization_code",
                                            "code": code, "redirect_uri": redirect_uri}).encode())
    except urllib.error.HTTPError as e:
        return "token exchange %s: %s" % (e.code, e.read().decode("utf-8", "ignore")[:160])
    except Exception as e:
        return type(e).__name__ + ": " + str(e)[:160]
    rt = tok.get("refresh_token")
    if not rt:
        return "no refresh_token in Spotify response"
    cfg = _cfg()
    cfg.setdefault("spotify", {})["refresh_token"] = rt
    tmp = CFG + ".tmp"
    json.dump(cfg, open(tmp, "w", encoding="utf-8"), indent=2)
    os.replace(tmp, CFG)
    _refresh()  # reflect it on the dashboard immediately
    return None


# ---------------------------------------------------------------- codex
# OpenAI Codex CLI account. Auth lives in ~/.codex/auth.json (chatgpt OAuth);
# the id_token JWT carries the email + plan. Rate-limit state is read from the
# newest session rollout's token_count events — Codex records a rate_limits
# snapshot there (primary=5h window, secondary=weekly), so no network probe
# is needed; the card shows the last snapshot and how old it is.
def _jwt_claims(tok):
    try:
        seg = tok.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg))
    except Exception:
        return {}


def _codex_dir(cfg):
    return (cfg.get("codex") or {}).get("dir") or os.path.expanduser("~/.codex")


def _codex(cfg):
    base = _codex_dir(cfg)
    auth_path = os.path.join(base, "auth.json")
    try:
        auth = json.load(open(auth_path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"error": "not connected", "hint": "run `codex login` in a terminal"}
    toks = auth.get("tokens") or {}
    claims = _jwt_claims(toks.get("id_token") or "")
    oai = claims.get("https://api.openai.com/auth") or {}
    out = {"email": claims.get("email"), "mode": auth.get("auth_mode"),
           "plan": oai.get("chatgpt_plan_type"),
           "last_refresh": auth.get("last_refresh")}
    # newest rollout that carries a rate_limits snapshot
    sess = os.path.join(base, "sessions")
    files = []
    for dirpath, _dirs, fns in os.walk(sess):
        for fn in fns:
            if fn.endswith(".jsonl"):
                p = os.path.join(dirpath, fn)
                try:
                    files.append((os.path.getmtime(p), p))
                except OSError:
                    continue
    for _mt, p in sorted(files, reverse=True)[:5]:
        snap = None
        try:
            for line in open(p, encoding="utf-8", errors="ignore"):
                if '"rate_limits"' in line:
                    snap = line  # keep the LAST snapshot in the file
        except OSError:
            continue
        if not snap:
            continue
        try:
            j = json.loads(snap)
        except json.JSONDecodeError:
            continue
        pay = j.get("payload") or {}
        rl = pay.get("rate_limits") or {}
        try:
            ts = datetime.datetime.fromisoformat(
                (j.get("timestamp") or "").replace("Z", "+00:00")).timestamp()
        except ValueError:
            ts = _mt
        out["plan"] = rl.get("plan_type") or out["plan"]
        out["asof"] = int(ts)
        cred = rl.get("credits") or {}
        if cred:
            out["credits"] = ("unlimited" if cred.get("unlimited")
                              else "available" if cred.get("has_credits") else "exhausted")
        for key, name in (("primary", ""), ("secondary", "7d")):
            w = rl.get(key)
            if not w:
                continue
            if w.get("used_percent") is not None:
                out["pct" + name] = min(100, round(w["used_percent"]))
            if w.get("resets_in_seconds"):
                out["reset_at" + ("7d" if name else "")] = int(ts + w["resets_in_seconds"])
        break
    return out


# ---------------------------------------------------------------- loop
def _refresh():
    cfg = _cfg()
    snap = {"asof": int(time.time())}
    for key, fn in (("claude", _claude), ("codex", _codex), ("github", _github),
                    ("gmail", _gmail), ("spotify", _spotify)):
        try:
            snap[key] = fn(cfg)
        except Exception as e:
            snap[key] = {"error": type(e).__name__ + ": " + str(e)[:120]}
    with LOCK:
        SNAP.clear()
        SNAP.update(snap)


def _loop():
    while True:
        _refresh()
        time.sleep(45)


def _loop_spotify():
    """Now-playing changes every few minutes — refresh it on its own fast cadence
    so the card feels live, instead of waiting up to 45s for the full loop."""
    while True:
        time.sleep(7)
        try:
            v = _spotify(_cfg())
        except Exception as e:
            v = {"error": type(e).__name__}
        with LOCK:
            if SNAP:  # don't create a lone-key snapshot before the first full refresh
                SNAP["spotify"] = v


def get():
    with LOCK:
        return dict(SNAP)


# -------------------------------------------------- account routing (spawn/orch)
def accounts():
    return _cfg().get("claude_accounts") or []


def dir_for(name):
    """The CLAUDE_CONFIG_DIR for an account display-name — resolves via the
    seen-cache (email-based names) first, then configured accounts. '' if unknown."""
    for r in _load_seen().values():
        if r.get("name") == name and r.get("dir"):
            return r["dir"]
    for a in accounts():
        if a.get("name") == name:
            return a.get("dir") or ""
    return ""


def least_used():
    """Name of the account with the most headroom — judged on BOTH windows
    (max of 5h and 7d utilization; an account at 0% of 5h but 97% of 7d is
    nearly exhausted, not free). The orchestrator delegates to it."""
    def load(a):
        p5 = a.get("pct") if a.get("pct") is not None else 999
        p7 = a.get("pct7d") if a.get("pct7d") is not None else 0
        return max(p5, p7)
    accs = [a for a in ((get().get("claude") or {}).get("accounts") or [])
            if not a.get("error") and a.get("name")]
    if accs:
        return min(accs, key=load)["name"]
    cfg = accounts()
    return cfg[0]["name"] if cfg else ""


threading.Thread(target=_loop, daemon=True).start()
threading.Thread(target=_loop_spotify, daemon=True).start()

if __name__ == "__main__":
    _refresh()
    print(json.dumps(get(), indent=2)[:3000])
