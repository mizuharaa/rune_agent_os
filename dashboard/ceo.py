#!/usr/bin/env python3
"""The CEO pipeline — how a prompt becomes a staffed mission.

  1. refine   Haiku sharpens the operator's raw prompt (keywords, missing
              specifics) — cheap, fast, before anything expensive runs.
  2. recall   Hermes is queried with the refined goal; prior solutions are
              folded into the CEO's brief so nothing gets re-solved.
  3. plan     The CEO (Opus, structured output) decomposes the goal into a
              minimum roster of roles, choosing each role's model and effort:
              Opus for hard implementation, Fable only for frontier-complex
              reasoning, Sonnet for light/logistics, Haiku for mechanical.
  4. execute  Roles run as headless Claude or Codex workers in dependency order.
              Per-role status (pending/working/blocked/review/done/failed)
              is persisted after every transition — the dashboard polls it.
  5. learn    On completion the outcome is written to Hermes, which mirrors
              a card into the Obsidian vault. The flywheel closes itself.

State: one JSON per run in state/ceo/<cid>.json.  Wire: mirror.py events.
Stdlib only, raw urllib against the Anthropic API (repo rule: no deps).
"""
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid

import chat    # API key resolution
import pulse   # account routing for workers
import runtime as agent_runtime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CDIR = os.path.join(ROOT, "state", "ceo")
ADIR = os.path.join(CDIR, "archive")   # finished runs moved here, kept not deleted
MIRROR = os.path.join(ROOT, ".claude", "hooks", "mirror.py")
HERMES = os.path.join(ROOT, "hermes", "hermes.py")
API = "https://api.anthropic.com/v1/messages"
IS_WIN = sys.platform == "win32"
WORKER_TIMEOUT = 1800

# A role that burns its whole turn budget was CUT OFF mid-task — it is not done.
# It used to be recorded as a silent success on half-finished work (the "it died
# out of nowhere and gave no reason" bug: a self-revamp needs far more than 40
# turns). Now we continue the SAME claude session, which keeps its full context,
# so it picks up where it stopped instead of re-doing the work. Only after this
# many auto-continues do we park the role as "exhausted" WITH a stated reason.
# ponytail: fixed budget; make it per-role if a mission ever needs a deeper one.
MAX_CONTINUES = 3
MAX_PLANNER_ATTEMPTS = 2
MAX_TRANSIENT_RETRIES = 2
MAX_RECOVERY_CYCLES = 2
RETRY_BACKOFF_BASE = 0.5

CONTINUE_PROMPT = """You ran out of your turn budget mid-task — this is a \
continuation of that same session, not a new one.

Continue exactly where you left off. Do NOT start over and do NOT redo work you \
already finished. Re-read anything you need, pick up the next unfinished step, \
and carry the task to completion. Finish by reporting what you did."""

# Successful missions move to history when their worker thread finishes.  This
# age window is retained for legacy success records that predate that behavior.
# Recoverable/failed records stay active until the operator resolves or archives
# them, because hiding unfinished work would recreate the silent-death problem.
AUTO_ARCHIVE_DAYS = 7
TERMINAL = ("done", "failed", "rejected", "exhausted", "stopped", "error", "stalled")
SUCCESSFUL = frozenset(("done", "completed", "success", "succeeded", "skipped"))
HISTORY_LIMIT = 50
HISTORY_MAX = 100

REFINER = "claude-haiku-4-5"   # prompt smith: cheap, fast
PLANNER = "claude-opus-4-8"    # the CEO itself: judgment is the product
ROLE_MODELS = ("haiku", "sonnet", "opus", "fable")
CLAUDE_WORKER_MODELS = frozenset(ROLE_MODELS)
CODEX_WORKER_MODELS = frozenset(("gpt-5.6-sol",))

LIVE = {}  # cid -> {"thread","proc","stop","gate":{role_id:(action,feedback)}}
# _save participates in cancellation ordering.  An RLock lets callers that are
# already inspecting LIVE persist state without deadlocking themselves.
LOCK = threading.RLock()

# a worker that dies on a usage/session/rate limit (not a real task failure) —
# used to flag the role so the UI can say "hit a limit, Continue when ready"
LIMIT_RE = agent_runtime.LIMIT_RE

ROUTES = ("answer", "solo", "delegate")

REFINE_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string"},    # the improved, specific prompt
        "keywords": {"type": "string"},  # search keywords for brain recall
        "route": {"type": "string", "enum": list(ROUTES)},
    },
    "required": ["prompt", "keywords", "route"],
    "additionalProperties": False,
}

REFINE_SYSTEM = """You triage raw operator prompts for an agentic OS. Rewrite \
the prompt to be specific and unambiguous: expand vague verbs, name concrete \
deliverables, keep every constraint the operator stated, add nothing they \
didn't ask for. Also produce a short keyword string for searching a knowledge \
base of previously solved problems.

Then pick the cheapest route that can ACTUALLY do the job:
- "answer": pure knowledge/explanation, answerable in one reply from what you \
already know. NO tools, NO files, NO network, NO commands. e.g. 'what does X \
mean', 'explain this concept', 'give me advice'.
- "solo": real work, but one focused task a SINGLE agent can do end-to-end \
with tools — reading/editing files, running commands, fetching from an API or \
GitHub, a scoped fix, a lookup that needs the repo or network. Most work \
belongs here.
- "delegate": genuinely big — several independent workstreams, or a build that \
needs planning plus separate review/verification. Only when one agent working \
sequentially would be clearly worse.

CRITICAL: if doing it requires touching files, running anything, or reaching \
the network, it is NEVER "answer" — it is at least "solo". When unsure between \
solo and delegate, pick "solo"."""

# a chat-only answer uses the cheap assistant, not a Claude Code session. map
# the dropdown's model override to a real id; None lets chat pick Haiku/Sonnet.
DIRECT_MODELS = {"haiku": "claude-haiku-4-5", "sonnet": "claude-sonnet-5",
                 "opus": "claude-opus-4-8", "fable": "claude-fable-5"}

# solo = one Claude Code session, full tools, in this repo. It must DO the work,
# not describe it, and must not delegate (that's the whole point of the mode).
SOLO_BRIEF = """Do this task yourself, now, in this repo.

RULES:
- You have full tools. Actually DO the work — read/edit files, run commands,
  call APIs, fetch what you need. Never just describe what you would do, and
  never emit a command for someone else to run.
- Do NOT delegate. Do NOT spawn subagents or use the Agent/Task tool. You are
  a single agent working alone — that is deliberate.
- Stay scoped to what was asked; don't refactor or build extras around it.
- Finish by reporting concretely what you did and what changed (files, output,
  results). If you couldn't do something, say so plainly.
- Only if you hit something genuinely non-obvious, record it:
  python hermes/hermes.py note "<problem>" "<solution>" --tags solo

TASK:
"""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},      # 2-4 word mission name
        "summary": {"type": "string"},   # one-line what/why
        "roles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},       # short slug e.g. "eng"
                    "title": {"type": "string"},    # e.g. "Backend engineer"
                    "mission": {"type": "string"},  # self-contained brief
                    "model": {"type": "string", "enum": list(ROLE_MODELS)},
                    "turns": {"type": "integer"},   # effort budget 5-80
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "review": {"type": "boolean"},  # park for operator review
                },
                "required": ["id", "title", "mission", "model", "turns",
                             "depends_on", "review"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["name", "summary", "roles"],
    "additionalProperties": False,
}

PLAN_SYSTEM = """You are the CEO of a personal agentic OS. You never do the \
work yourself — you decompose the mission into the MINIMUM roster of roles \
(1-6) and delegate. Each role becomes a headless Claude Code session in this \
repo (full tool access, the .claude/agents roster, skills, the Hermes brain).

Per role you control model and effort deliberately:
- model: "opus" for most hard implementation/reasoning work (the default for \
anything substantial), "fable" ONLY for really, really complex frontier \
thinking, "sonnet" for light or logistics tasks (docs, checks, summaries), \
"haiku" for purely mechanical steps.
- turns: 5-80. Small for lookups, large for builds.
- depends_on: ids of roles whose output this role needs (keep the graph flat \
when possible — independent roles run without waiting).
- review: true ONLY when accepting the completed output would authorize an \
irreversible or outward action (destructive deletion, deploy/publish/send, \
spending, credential/access changes, or a soul write). Ordinary local edits, \
tests, analysis, design judgment, and previewable work are NOT gated. A gated \
role may produce a local report, but dependents wait for operator approval.

Each mission must be a self-contained brief: goal, concrete steps, \
constraints, and a CHECKABLE definition of done.

The brain holds SIGNAL, not a log of every run. Only tell a role to record a \
Hermes note when the work is genuinely worth remembering: a hard concept, a \
rare edge case, something token-expensive, or a point that was confusing to \
get right. For mechanical or obvious steps, do NOT add a note. When one is \
warranted, end that role's brief with: 'If you hit something non-obvious, \
record it: python hermes/hermes.py note "<problem>" "<solution>" --tags mission'.

If prior solutions are provided under "Brain recall", fold them into the \
relevant briefs so workers reuse instead of re-solving."""


def emit(cid, detail, event="ceo"):
    subprocess.run([sys.executable, MIRROR, "--session", cid, "--event", event,
                    "--detail", detail[:200]], capture_output=True)


# thresholds for "worth a brain note" — tuned so cheap mechanical successes are
# skipped and hard/rare/expensive/failed work is kept. Bump these if the brain
# still fills with noise; lower them if genuine learnings get dropped.
WORTH_COST = 0.15    # dollars: token-heavy runs
WORTH_TURNS = 40     # a single role that ran deep


def _worth_remembering(o):
    """The brain records signal, not every run. Keep a note only when a mission
    was actually instructive: it failed (failures teach most), was token-heavy,
    needed several coordinated roles, leaned on hard reasoning (opus/fable), or
    ran a role deep. Skip cheap, mechanical, first-try successes."""
    roles = o.get("roles") or []
    if any(agent_runtime.compact_recovery_evidence(r, learnable_only=True)
           for r in roles):
        return True
    if o.get("status") in ("failed", "error"):
        return True
    if (o.get("cost") or 0) >= WORTH_COST:
        return True
    if len([r for r in roles if r.get("status") != "skipped"]) >= 2:
        return True
    if any(r.get("model") in ("opus", "fable") for r in roles):
        return True
    if any((r.get("turns") or 0) >= WORTH_TURNS for r in roles):
        return True
    return False


def _api(model, system, user, schema, max_tokens=4000, timeout=120):
    """One structured-output Messages call. Returns parsed dict or {"error"}."""
    key = chat._api_key()
    if not key:
        return {"error": "no ANTHROPIC_API_KEY (set it in the environment or .env)"}
    body = {"model": model, "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user[:12000]}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}}}
    req = urllib.request.Request(API, data=json.dumps(body).encode("utf-8"), headers={
        "content-type": "application/json", "x-api-key": key,
        "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        return {"error": "API %s: %s" % (e.code, e.read().decode("utf-8", "ignore")[:200])}
    except Exception as e:
        return {"error": type(e).__name__ + ": " + str(e)[:200]}
    txt = "".join(b.get("text", "") for b in (data.get("content") or [])
                  if b.get("type") == "text")
    try:
        return json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
    except (json.JSONDecodeError, ValueError):
        return {"error": "malformed JSON from " + model}


def _recall(text):
    """Prior solutions from the brain (Hermes -> Obsidian), or ''."""
    try:
        r = subprocess.run([sys.executable, HERMES, "query", text[:300]],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()[:1500]
    except Exception:
        pass
    return ""


def _path(cid):
    return os.path.join(CDIR, cid + ".json")


def _load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _save(o):
    # Stop is a monotonic transition while the owning thread is alive.  Hold
    # the same lock used by action(): either this write lands first and action
    # writes stopped afterward, or it observes stop and cannot resurrect the
    # mission with a late worker/planner completion.
    with LOCK:
        live = LIVE.get(o.get("cid")) or {}
        thread = live.get("thread")
        thread_alive = not thread or not hasattr(thread, "is_alive") or thread.is_alive()
        if live.get("stop") and thread_alive:
            o["status"] = "stopped"
            o["detail"] = "stopped by operator"
            o["next_action"] = "Resume to continue unfinished roles, or archive this mission."
            for role in o.get("roles") or []:
                if role.get("status") in ("working", "retrying", "repairing"):
                    role["status"] = "stopped"
                    role["detail"] = "stopped by operator"
                    role["next_action"] = "Resume this role when ready."
        now = datetime.datetime.now().isoformat(timespec="seconds")
        # Completion time is an immutable lifecycle boundary.  `updated` can
        # still change for metadata and must not be used to date a finished run.
        if str(o.get("status") or "").lower() in SUCCESSFUL:
            o.setdefault("finished_at", now)
        o["updated"] = now
        tmp = _path(o["cid"]) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(o, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _path(o["cid"]))


def _stopped(cid):
    with LOCK:
        return bool(LIVE.get(cid, {}).get("stop"))


def _drop_live(cid):
    """Forget a planner thread that ended before handing off to _run."""
    with LOCK:
        LIVE.pop(cid, None)


def _stop_state(o, detail="stopped by operator"):
    """Persist cancellation immediately; a killed worker must never look failed."""
    o["status"] = "stopped"
    o["detail"] = detail
    o["next_action"] = "Resume to continue unfinished roles, or archive this mission."
    for role in o.get("roles") or []:
        if role.get("status") in ("working", "retrying", "repairing"):
            role["status"] = "stopped"
            role["detail"] = detail
            role["next_action"] = "Resume this role when ready."
    _save(o)


def _wait_retry(cid, retry_number):
    return agent_runtime.wait_backoff(
        lambda: _stopped(cid), retry_number, base=RETRY_BACKOFF_BASE)


def _record_attempt(role, w, classification, secs, kind="worker"):
    """Keep bounded recovery evidence instead of overwriting the last failure."""
    attempts = role.setdefault("attempts", [])
    attempts.append({
        "attempt": role.get("attempt", len(attempts) + 1),
        "kind": kind,
        "status": "done" if classification == "success" else "failed",
        "classification": classification,
        "detail": agent_runtime.safe_excerpt(w.get("result"), 500),
        "session": w.get("session_id") or "",
        "secs": round(secs),
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
    })
    del attempts[:-12]


def _age_days(o):
    ts = o.get("finished_at") or o.get("updated") or o.get("started") or ""
    try:
        return (datetime.datetime.now()
                - datetime.datetime.fromisoformat(ts)).total_seconds() / 86400
    except ValueError:
        return 0.0


def _archive_file(cid):
    """Move a run out of the active dir into state/ceo/archive/ (kept, not deleted).
    list_all only scans the top level, so archived runs drop out of the UI."""
    src = _path(cid)
    dst = os.path.join(ADIR, cid + ".json")
    if not os.path.exists(src):
        # Archive is an idempotent lifecycle transition.  A double click or a
        # retry after a lost HTTP response must not turn success into an error.
        return os.path.isfile(dst)
    os.makedirs(ADIR, exist_ok=True)
    os.replace(src, dst)
    return True


def archive(cid):
    """Manual archive (the mission card's Archive button). Refuses a live run."""
    with LOCK:
        st = LIVE.get(cid)
        if st and st["thread"].is_alive():
            return "can't archive a running mission — stop it first"
    return None if _archive_file(cid) else "no such mission"


def list_all():
    out = []
    if os.path.isdir(CDIR):
        for fn in os.listdir(CDIR):
            if not fn.endswith(".json"):
                continue
            try:
                o = _load_json(os.path.join(CDIR, fn))
            except (OSError, json.JSONDecodeError):
                continue
            with LOCK:
                o["live"] = o["cid"] in LIVE and LIVE[o["cid"]]["thread"].is_alive()
            if o.get("status") in ("running", "review", "planning") and not o["live"]:
                # the run's thread is gone but the file says running: the server
                # process died under it (window closed, restart, or Rune rewrote
                # its own backend). Say so — a stalled run used to give no reason.
                o["status"] = "stalled"
                o["detail"] = ("the Rune server stopped while this was running (app "
                               "window closed, restart, or Rune rewrote its own "
                               "backend) — Continue picks it up where it left off")
            # auto-archive: finished, not live, and past the window -> move it out
            # so the active list stays short. Runs the sweep for free on the poll.
            if (not o["live"] and str(o.get("status") or "").lower() in SUCCESSFUL
                    and _age_days(o) >= AUTO_ARCHIVE_DAYS):
                _archive_file(o["cid"])
                continue
            out.append(o)
    out.sort(key=lambda o: o.get("started", ""), reverse=True)
    return out


def list_history(limit=HISTORY_LIMIT):
    """Return bounded, newest-first mission history without deleting evidence.

    Archived records are the durable source.  Recent successful top-level
    records are included too so the API cannot briefly lose a completion
    between its final save and the worker thread's archive cleanup.
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = HISTORY_LIMIT
    limit = max(0, min(limit, HISTORY_MAX))
    if not limit:
        return []

    by_cid = {}
    locations = ((CDIR, False), (ADIR, True))
    for directory, archived in locations:
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if not name.endswith(".json"):
                continue
            try:
                run = _load_json(os.path.join(directory, name))
            except (OSError, json.JSONDecodeError):
                continue
            status = str(run.get("status") or "").lower()
            if not archived and status not in SUCCESSFUL:
                continue
            cid = str(run.get("cid") or name[:-5])
            live = False
            if not archived:
                with LOCK:
                    state = LIVE.get(cid) or {}
                    thread = state.get("thread")
                    live = bool(thread and thread.is_alive())
            item = dict(run, cid=cid, archived=archived, live=live)
            # Prefer the archived copy if a stale duplicate exists in both
            # locations; it is the authoritative lifecycle destination.
            if cid not in by_cid or archived:
                by_cid[cid] = item

    out = list(by_cid.values())
    out.sort(key=lambda run: str(run.get("finished_at") or run.get("updated") or
                                 run.get("started") or ""), reverse=True)
    return out[:limit]


EFFORT_TURNS = {"quick": 15, "standard": 40, "deep": 80}


# Not every prompt needs a Haiku rewrite. A long, detailed prompt is already
# specific — re-writing it burns a call and can dilute the operator's wording.
# Only short/terse prompts get refined; the rest go to the CEO verbatim.
REFINE_MAX_CHARS = 180
# anything that has to touch files / run something / hit the network is real
# work — it needs a Claude Code session with tools, never a chat-only answer.
WORK_RE = re.compile(
    r"\b(fix|build|creat|add|implement|refactor|writ|run|test|deploy|audit|"
    r"migrat|updat|remov|delet|revamp|orchestrat|automat|debug|investigat|"
    r"scan|generat|set ?up|install|configur|clean|optimi[sz]|rename|wire|"
    r"fetch|pull|clone|push|commit|search|find|check|review|analy[sz]|"
    r"refresh|sync|verify|prioriti[sz]|plan)\w*\b", re.I)
ASK_RE = re.compile(
    r"^\s*(what|who|whom|whose|when|where|why|how|which|is|are|was|were|does|do|did|"
    r"can|could|should|would|explain|describe|summar|define|tell me)\b", re.I)


def _classify(text):
    """Free, local triage — no API call. Returns (needs_refine, route_guess).

    needs_refine: only short/terse prompts benefit from the Haiku rewrite.
    route_guess: a pure question with no work verbs can be answered from
    knowledge; anything else is real work, so default to a solo Claude Code
    run (tools, one agent) rather than the full CEO roster. Used as-is when we
    skip the refiner; otherwise the model's judgment wins."""
    long_prompt = len(text) >= REFINE_MAX_CHARS
    work = bool(WORK_RE.search(text))
    ask = bool(ASK_RE.match(text))
    if ask and not work and not long_prompt:
        return True, "answer"
    return (not long_prompt), "solo"


def plan_and_start(text, opts=None, source=None, workdir=None,
                   safe_permissions=False):
    """Intake. Returns fast — the CEO's planning call (which can take minutes)
    runs on a thread, so the HTTP request never hangs (a long synchronous plan
    was what produced "Failed to fetch" in the browser).

    opts (from the Run-it dropdown):
      mode    "auto" (route per prompt) | answer | solo | delegate
      refine  "auto" (only short/vague prompts) | off (never rewrite my prompt)
      model   "auto" (CEO decides) | haiku|sonnet|opus|fable (force all roles)
      effort  "auto" | quick|standard|deep (force every role's turn budget)
      account "auto" (least used) | an account display-name
      gate    True -> operator reviews every role before it runs

    Three real routes:
      answer   chat-only reply (no tools) — pure knowledge questions
      solo     ONE Claude Code session with full tools, no CEO, no subagents
      delegate the CEO staffs a roster of roles
    Returns (result, None) or (None, error); result["kind"] is "answer" or
    "mission" (solo and delegate both produce a mission card)."""
    opts = opts if isinstance(opts, dict) else {}
    mode = opts.get("mode") if opts.get("mode") in ("auto",) + ROUTES else "auto"
    refine_off = str(opts.get("refine") or "auto") == "off"
    force_model = opts.get("model") if opts.get("model") in ROLE_MODELS else None
    force_turns = EFFORT_TURNS.get(opts.get("effort"))
    text = (text or "").strip()
    if not text:
        return None, "empty goal"
    run_dir = os.path.normpath(os.path.realpath(workdir or ROOT))
    if not os.path.isdir(run_dir):
        return None, "working directory is unavailable"
    needs_refine, route = _classify(text)
    refined, keywords = text, text
    # 1. Haiku prompt smith — ONLY for short/vague prompts, and only when its
    # routing judgment can still matter (never when the operator forced a mode
    # AND turned refinement off, or in answer mode where there's nothing to plan).
    if needs_refine and not refine_off and mode != "answer":
        r = _api(REFINER, REFINE_SYSTEM, text, REFINE_SCHEMA, max_tokens=1500, timeout=45)
        if not r.get("error"):
            refined = r.get("prompt") or text
            keywords = r.get("keywords") or text
            if r.get("route") in ROUTES:
                route = r["route"]        # the model's judgment beats the heuristic
    if mode != "auto":
        route = mode                       # an explicit choice always wins
    # 2. answer: chat only, no tools, no agents. Cheap questions.
    if route == "answer":
        ans = chat.ask(refined, model=DIRECT_MODELS.get(force_model))
        if ans.get("error"):
            return None, ans["error"]
        return {"kind": "answer", "reply": ans.get("reply") or "(no reply)",
                "model": ans.get("model"), "goal": text[:1000]}, None
    os.makedirs(CDIR, exist_ok=True)
    cid = uuid.uuid4().hex[:8]
    briefing_source = (isinstance(source, dict) and
                       source.get("kind") == "daily_briefing")
    prompt_limit = 12000 if briefing_source else 4000
    goal_limit = prompt_limit if briefing_source else 1000
    o = {"cid": cid, "name": text[:40], "summary": "", "goal": text[:goal_limit],
         "refined": refined[:prompt_limit], "keywords": keywords[:300], "recall": False,
         "roles": [], "route": route,
         "source": dict(source) if isinstance(source, dict) else None,
         "workdir": run_dir, "safe_permissions": bool(safe_permissions),
         "permission_mode": "safe" if safe_permissions else "skip",
         "opts": {"model": force_model, "turns": force_turns,
                   "gate": bool(opts.get("gate"))},
         "account_pref": str(opts.get("account") or "auto"),
         "status": "running", "cost": 0, "auto_recover": True,
         "planning_attempt": 0, "planning_history": [], "next_action": "",
         "started": datetime.datetime.now().isoformat(timespec="seconds")}
    # 3. solo: run the prompt in ONE Claude Code session with full tools — no
    # planning call, no roster, no subagents. This is "just run it" mode.
    if route == "solo":
        recall = _recall(keywords)
        brief = SOLO_BRIEF + refined
        if recall:
            brief += ("\n\n## Brain recall — solved before, reuse don't re-solve:\n"
                      + recall)
        o.update(name=text[:40], summary="Single Claude Code session — no delegation.",
                 recall=bool(recall),
                 roles=[{"id": "solo", "title": "Direct run (no delegation)",
                         "mission": brief, "model": force_model or "sonnet",
                         "provider": "claude",
                         "turns": force_turns or 40, "depends_on": [],
                         "review": bool(opts.get("gate")), "status": "pending",
                         "result": "", "secs": 0, "cost": 0}])
        _save(o)
        t = threading.Thread(target=_run, args=(cid,), daemon=True)
        with LOCK:
            LIVE[cid] = {"thread": t, "proc": None, "stop": False, "gate": {}}
        t.start()
        emit(cid, "solo run (%s, %dt, no subagents): %s"
             % (o["roles"][0]["model"], o["roles"][0]["turns"], text[:100]))
        return dict(o, kind="mission"), None
    # 4. delegate: create the card NOW (status "planning") and hand the slow
    # recall + CEO plan to a thread, so the HTTP request never hangs.
    o["status"] = "planning"
    _save(o)
    t = threading.Thread(target=_plan_then_run, args=(cid,), daemon=True)
    with LOCK:
        LIVE[cid] = {"thread": t, "proc": None, "stop": False, "gate": {}}
    t.start()
    emit(cid, "CEO planning: " + text[:120])
    return dict(o, kind="mission"), None


def _source_identity(source, exact_snapshot=True):
    """Comparable identity for an authoritative persisted-plan launch."""
    source = source if isinstance(source, dict) else {}
    keys = ["kind", "source_date", "batch_id", "priority_id"]
    if exact_snapshot:
        keys.append("snapshot")
    return tuple(str(source.get(key) or "") for key in keys)


def _source_run_active(run):
    """Whether starting another copy would overlap unresolved execution."""
    cid = str(run.get("cid") or "")
    with LOCK:
        live = LIVE.get(cid)
        if live and live.get("thread") and live["thread"].is_alive():
            return True
    return str(run.get("status") or "").lower() in (
        "review", "waiting_permission", "waiting-permission")


def find_source_run(source, exact_snapshot=True, active_only=False):
    """Find the newest persisted run for a briefing card or exact snapshot."""
    wanted = _source_identity(source, exact_snapshot=exact_snapshot)
    if not wanted[0] or not os.path.isdir(CDIR):
        return None
    matches = []
    # Active lookup is deliberately top-level/LIVE only.  A normal exact-source
    # lookup additionally sees archived successes so clearing the UI cannot make
    # a completed briefing card launch again without explicit rerun=True.
    locations = [(CDIR, False)]
    if exact_snapshot and not active_only:
        locations.append((ADIR, True))
    for directory, archived in locations:
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if not name.endswith(".json"):
                continue
            try:
                run = _load_json(os.path.join(directory, name))
            except (OSError, json.JSONDecodeError):
                continue
            if archived and str(run.get("status") or "").lower() not in SUCCESSFUL:
                continue
            if (_source_identity(run.get("source"), exact_snapshot=exact_snapshot) == wanted
                    and (not active_only or _source_run_active(run))):
                run["archived"] = archived
                matches.append(run)
    if not matches:
        return None
    matches.sort(key=lambda run: str(run.get("updated") or run.get("started") or ""),
                 reverse=True)
    run = matches[0]
    with LOCK:
        live = LIVE.get(str(run.get("cid") or ""))
        run["live"] = bool(not run.get("archived") and live and live.get("thread")
                           and live["thread"].is_alive())
    return run


def _start_direct_briefing(text, source, workdir, roles):
    """Persist and start an explicit provider-aware briefing mission."""
    if not isinstance(roles, list) or not 2 <= len(roles) <= 4:
        return None, "direct briefing execution requires 2-4 validated roles"
    prepared = []
    seen = set()
    previous = ""
    for index, item in enumerate(roles, 1):
        if not isinstance(item, dict):
            return None, "direct briefing role %d is invalid" % index
        role_id = str(item.get("id") or "").strip()
        if (not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", role_id) or
                role_id in seen):
            return None, "direct briefing role %d has an invalid id" % index
        try:
            provider = _provider_for_model(item.get("model"))
        except ValueError as exc:
            return None, str(exc)
        if str(item.get("provider") or "") != provider:
            return None, "direct briefing role %d has corrupt provider metadata" % index
        title = str(item.get("title") or "").strip()[:80]
        assignment = str(item.get("mission") or "").strip()[:2000]
        deliverable = str(item.get("deliverable") or "").strip()[:1000]
        if not title or not assignment or not deliverable:
            return None, "direct briefing role %d is incomplete" % index
        try:
            turns = int(item.get("turns"))
        except (TypeError, ValueError):
            return None, "direct briefing role %d has an invalid effort budget" % index
        if not 1 <= turns <= 100:
            return None, "direct briefing role %d has an invalid effort budget" % index
        mission = (text + "\n\n## YOUR DIRECT SAVED ASSIGNMENT\nRole: " + title +
                   "\nMission: " + assignment + "\nDeliverable: " + deliverable +
                   "\nExecute this assignment with the saved provider/model. Do not "
                   "restaff it onto another provider. Inspect existing work from earlier "
                   "roles before making changes.")
        prepared.append({
            "id": role_id, "title": title, "mission": mission[:14000],
            "deliverable": deliverable, "model": str(item.get("model")),
            "provider": provider, "effort": str(item.get("effort") or ""),
            "turns": turns, "depends_on": [previous] if previous else [],
            "review": False, "status": "pending", "result": "", "secs": 0,
            "cost": 0,
        })
        seen.add(role_id)
        previous = role_id
    os.makedirs(CDIR, exist_ok=True)
    cid = uuid.uuid4().hex[:8]
    o = {
        "cid": cid, "name": (prepared[0]["title"] or text[:40])[:40],
        "summary": "Saved briefing cards running directly through their selected providers.",
        "goal": text[:12000], "refined": text[:12000], "keywords": "",
        "recall": False, "roles": prepared, "route": "direct",
        "source": dict(source), "workdir": workdir,
        "safe_permissions": False, "permission_mode": "skip",
        "providers": sorted({role["provider"] for role in prepared}),
        "opts": {"model": None, "turns": None, "gate": False},
        "account_pref": "auto", "status": "running", "cost": 0,
        "auto_recover": True, "planning_attempt": 0, "planning_history": [],
        "next_action": "Executing the first saved provider role.",
        "started": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    _save(o)
    thread = threading.Thread(target=_run, args=(cid,), daemon=True)
    with LOCK:
        LIVE[cid] = {"thread": thread, "proc": None, "stop": False, "gate": {}}
    thread.start()
    emit(cid, "direct briefing (%s): %s" %
         ("+".join(o["providers"]), text[:100]))
    return dict(o, kind="mission"), None


def start_briefing_mission(text, source, workdir, rerun=False,
                           permission_mode="safe", roles=None):
    """Idempotently launch one server-resolved Daily Briefing priority.

    The lock spans lookup and mission persistence.  ``plan_and_start`` is forced
    down its no-refiner delegated route, so it performs no blocking model call
    before saving the source-tagged run.  A second HTTP thread therefore sees
    and reuses the same cid instead of launching duplicate work.
    """
    if permission_mode not in ("safe", "skip"):
        return None, "permission_mode must be safe or skip"
    if not isinstance(source, dict) or source.get("kind") != "daily_briefing":
        return None, "invalid briefing source"
    if any(not str(source.get(key) or "") for key in
           ("batch_id", "priority_id", "snapshot")):
        return None, "incomplete briefing source"
    run_dir = os.path.normpath(os.path.realpath(workdir or ""))
    if not os.path.isdir(run_dir):
        return None, "briefing repository is unavailable"
    with LOCK:
        # Never overlap two runs for the same logical card, even if its agent
        # dropdowns changed and therefore produced a new snapshot hash.
        active = find_source_run(source, exact_snapshot=False, active_only=True)
        if active is not None:
            return dict(active, kind="mission", reused=True), None
        existing = find_source_run(source)
        if existing is not None and not rerun:
            return dict(existing, kind="mission", reused=True), None
        if permission_mode == "skip":
            out, err = _start_direct_briefing(text, source, run_dir, roles)
            if err:
                return None, err
            return dict(out, reused=False), None
        out, err = plan_and_start(
            text,
            {"mode": "delegate", "refine": "off", "model": "auto",
             "effort": "auto", "account": "auto", "gate": False},
            source=source, workdir=run_dir, safe_permissions=True)
        if err:
            return None, err
        return dict(out, reused=False), None


def _plan_then_run(cid):
    """Thread: brain recall -> CEO staffs the roles -> run them. Slow work that
    used to block the HTTP request now lives here. Planning is retryable and a
    Stop received during the API call/backoff wins before any roles can start."""
    try:
        o = _load_json(_path(cid))
    except (OSError, json.JSONDecodeError):
        _drop_live(cid)
        return
    ov = o.get("opts") or {}
    force_model, force_turns, gate_all = ov.get("model"), ov.get("turns"), ov.get("gate")
    try:
        if _stopped(cid):
            _stop_state(o)
            _drop_live(cid)
            return
        recall = _recall(o.get("keywords") or o["goal"])
        brief = "MISSION:\n" + o["refined"]
        if recall:
            brief += "\n\n## Brain recall — solved before, reuse don't re-solve:\n" + recall
        p, roles = {}, []
        final_classification = "task"
        for attempt in range(1, MAX_PLANNER_ATTEMPTS + 1):
            if _stopped(cid):
                _stop_state(o)
                _drop_live(cid)
                return
            o["status"] = "planning"
            o["planning_attempt"] = attempt
            o["next_action"] = "The CEO is preparing a bounded staffing plan."
            _save(o)
            if _stopped(cid):
                _stop_state(o)
                _drop_live(cid)
                return
            p = _api(PLANNER, PLAN_SYSTEM, brief, PLAN_SCHEMA,
                     max_tokens=8000, timeout=180)
            if _stopped(cid):
                _stop_state(o)
                _drop_live(cid)
                return
            if not isinstance(p, dict):
                p = {"error": "malformed planner response"}
            raw_roles = None if p.get("error") else p.get("roles")
            malformed_roles = (raw_roles is not None and
                               (not isinstance(raw_roles, list) or
                                any(not isinstance(role, dict) for role in raw_roles)))
            roles = (raw_roles if isinstance(raw_roles, list) and
                     not malformed_roles else []) or []
            error = ("" if roles else p.get("error") or
                     ("CEO returned malformed roles" if malformed_roles else
                      "CEO returned no roles"))
            classification = ("success" if roles else
                              agent_runtime.classify_failure(error, True))
            final_classification = classification
            o.setdefault("planning_history", []).append({
                "attempt": attempt,
                "status": "done" if roles else "failed",
                "classification": classification,
                "detail": agent_runtime.safe_excerpt(error, 300),
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            })
            del o["planning_history"][:-8]
            if roles:
                break
            o["detail"] = agent_runtime.safe_excerpt(error, 300)
            if classification == "permission":
                o["status"] = "waiting_permission"
                o["next_action"] = (
                    "Provide the named credential or approval explicitly, then Resume planning.")
                _save(o)
                emit(cid, "CEO planning paused for operator permission: " +
                     o["detail"][:100])
                _drop_live(cid)
                return
            # Capacity/transport failures are retryable.  An empty or malformed
            # structured plan is also safe to ask the planner for again; other
            # task/API errors stop immediately instead of retrying blindly.
            retryable_plan = (
                classification in ("transient", "transient_limit") or
                not p.get("error") or
                "malformed" in str(p.get("error") or "").lower())
            if retryable_plan and attempt < MAX_PLANNER_ATTEMPTS:
                o["next_action"] = "Planning failed transiently; retrying automatically."
                _save(o)
                emit(cid, "CEO planning attempt %d/%d failed — retrying: %s" %
                     (attempt, MAX_PLANNER_ATTEMPTS, o["detail"][:100]))
                if not _wait_retry(cid, attempt):
                    _stop_state(o)
                    _drop_live(cid)
                    return
            else:
                break
        if not roles:
            o["status"] = ("waiting_permission" if final_classification == "permission"
                           else "error")
            o["detail"] = agent_runtime.safe_excerpt(
                p.get("error") or "CEO returned no roles", 300)
            o["next_action"] = (
                "Provide the named credential or approval explicitly, then Resume planning."
                if final_classification == "permission" else
                "Retry planning; no repository role was started.")
            _save(o)
            emit(cid, "CEO planning failed: " + o["detail"][:120])
            _drop_live(cid)
            return
        seen = set()
        for i, role in enumerate(roles[:6]):
            rid = re.sub(r"[^a-z0-9\-]", "", str(role.get("id") or "").lower()) or "r%d" % i
            while rid in seen:
                rid += "x"
            seen.add(rid)
            role["id"] = rid
            role["model"] = role.get("model") if role.get("model") in ROLE_MODELS else "opus"
            role["provider"] = "claude"
            try:
                role["turns"] = max(5, min(80, int(role.get("turns") or 30)))
            except (TypeError, ValueError):
                role["turns"] = 30
            # operator overrides from the Run-it dropdown beat the CEO's choices
            if force_model:
                role["model"] = force_model
            if force_turns:
                role["turns"] = force_turns
            if gate_all:
                role["review"] = True
            role["depends_on"] = [d for d in (role.get("depends_on") or [])
                                  if d in seen and d != rid]
            role.update(status="pending", result="", secs=0, cost=0)
        if _stopped(cid):
            _stop_state(o)
            _drop_live(cid)
            return
        o.update(name=(p.get("name") or o["goal"][:40])[:40],
                  summary=(p.get("summary") or "")[:300],
                  recall=bool(recall), roles=roles[:6], status="running",
                  detail="", next_action="Executing the first runnable role.")
        _save(o)
        if _stopped(cid):
            _stop_state(o)
            _drop_live(cid)
            return
        emit(cid, "CEO staffed '%s': %s" % (o["name"], ", ".join(
            "%s(%s/%dt)" % (r["id"], r["model"], r["turns"]) for r in o["roles"])))
    except Exception as e:
        if _stopped(cid):
            _stop_state(o)
            _drop_live(cid)
            return
        o["status"] = "error"
        o["detail"] = repr(e)[:300]
        _save(o)
        emit(cid, "CEO planning crashed: " + repr(e)[:120])
        _drop_live(cid)
        return
    _run(cid)   # same thread: staffing flows straight into execution


def _provider_for_model(model):
    """Return the allowlisted CLI provider for a persisted worker model."""
    model = str(model or "").strip().lower()
    if model in CLAUDE_WORKER_MODELS:
        return "claude"
    if model in CODEX_WORKER_MODELS:
        return "codex"
    raise ValueError("unsupported worker model: %s" % (model or "(empty)"))


def _worker_argv(role, permission_mode="safe", workdir=ROOT, resume_sid="",
                 output_path=""):
    """Build an argv-only, provider-aware headless worker command.

    The model and provider are allowlisted and must agree. Permission bypass is
    enabled only for an explicit skip mission and never for a recovery role.
    Prompt text is deliberately absent from argv and is supplied over stdin.
    """
    if permission_mode not in ("safe", "skip"):
        raise ValueError("permission_mode must be safe or skip")
    model = str(role.get("model") or "").strip().lower()
    provider = _provider_for_model(model)
    saved_provider = str(role.get("provider") or provider).strip().lower()
    if saved_provider != provider:
        raise ValueError("worker provider does not match its model")
    recovery = bool(role.get("recovery"))
    bypass = permission_mode == "skip" and not recovery
    if provider == "claude":
        try:
            turns = max(1, min(100, int(role.get("turns") or 40)))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid Claude worker turn budget") from exc
        argv = ["claude", "-p", "--output-format", "json",
                "--max-turns", str(turns), "--model", model]
        if bypass:
            argv.append("--dangerously-skip-permissions")
        else:
            argv += ["--permission-mode", "auto"]
        if resume_sid:
            argv += ["--resume", str(resume_sid)]
        return argv
    if not output_path:
        raise ValueError("Codex workers require an output path")
    argv = ["codex", "exec"]
    if resume_sid:
        argv.append("resume")
    argv += ["--json", "-o", os.path.normpath(output_path), "-m", model]
    if bypass:
        argv.append("--yolo")
    else:
        # `exec` has no interactive approval channel. Keep recovery contained in
        # the workspace sandbox and make denials visible to the worker without
        # granting the native yolo/bypass policy.
        argv += ["--sandbox", "workspace-write", "-c", 'approval_policy="never"']
    if resume_sid:
        argv += [str(resume_sid), "-"]
    else:
        argv += ["-C", os.path.normpath(workdir), "-"]
    return argv


def _codex_result(stdout, stderr, output_path, returncode):
    """Normalize Codex JSONL/final-message output into the Claude worker shape."""
    events = []
    for line in str(stdout or "").splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(event, dict):
            events.append(event)
    session_id = ""
    failure = ""
    for event in events:
        kind = str(event.get("type") or "")
        if kind == "thread.started":
            thread = event.get("thread") if isinstance(event.get("thread"), dict) else {}
            session_id = str(event.get("thread_id") or thread.get("id") or "")
        if kind in ("turn.failed", "error"):
            failure = str(event.get("message") or event.get("error") or failure)
    result = ""
    try:
        with open(output_path, encoding="utf-8") as handle:
            result = handle.read().strip()
    except OSError:
        pass
    if not result:
        for event in reversed(events):
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            if (str(event.get("type") or "") == "item.completed" and
                    str(item.get("type") or "") == "agent_message"):
                result = str(item.get("text") or item.get("content") or "").strip()
                if result:
                    break
    is_error = bool(returncode) or bool(failure) or not bool(result)
    if not result and is_error:
        result = (failure or str(stderr or "") or str(stdout or "") or
                  "Codex worker returned no final message (exit %s)" % returncode).strip()[:6000]
    return {"is_error": is_error, "result": result, "session_id": session_id,
            "total_cost_usd": 0, "provider": "codex"}


def _worker(cid, role, context, cfg_dir, resume_sid="", workdir=ROOT,
            safe_permissions=False):
    """Run one Claude or Codex role headlessly with stop/resume tracking."""
    prompt = CONTINUE_PROMPT if resume_sid else role["mission"]
    if context and not resume_sid:
        prompt += "\n\n## Output from roles you depend on:\n" + context[:6000]
    provider = _provider_for_model(role.get("model"))
    permission_mode = "safe" if safe_permissions else "skip"
    temp = tempfile.TemporaryDirectory(prefix="rune-codex-worker-") \
        if provider == "codex" else None
    output_path = os.path.join(temp.name, "final.txt") if temp else ""
    argv = _worker_argv(role, permission_mode, workdir, resume_sid, output_path)
    env = dict(os.environ, MAESTRO_SID=cid)
    if cfg_dir and provider == "claude":
        env["CLAUDE_CONFIG_DIR"] = cfg_dir
    p = None
    try:
        try:
            p = subprocess.Popen(
                argv, cwd=workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", shell=IS_WIN,
                env=env, start_new_session=not IS_WIN,
                creationflags=((getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) |
                                getattr(subprocess, "CREATE_NO_WINDOW", 0))
                               if IS_WIN else 0))
        except (OSError, subprocess.SubprocessError) as exc:
            return {"is_error": True, "result": "%s CLI failed to start: %s" %
                    (provider.title(), str(exc)[:500]), "provider": provider}
        with LOCK:
            if cid in LIVE:
                LIVE[cid]["proc"] = p
                stop_after_spawn = bool(LIVE[cid].get("stop"))
            else:
                stop_after_spawn = False
        if stop_after_spawn:
            agent_runtime.terminate_process_tree(p)
            try:
                p.communicate(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
            return {"is_error": True, "result": "stopped by operator",
                    "provider": provider}
        try:
            out, err = p.communicate(prompt, timeout=WORKER_TIMEOUT)
        except subprocess.TimeoutExpired:
            agent_runtime.terminate_process_tree(p)
            try:
                p.communicate(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
            return {"is_error": True, "result": "timed out after %ss" % WORKER_TIMEOUT,
                    "provider": provider}
        if provider == "codex":
            return _codex_result(out, err, output_path, getattr(p, "returncode", 0))
        try:
            result = json.loads(out)
            if not isinstance(result, dict):
                raise ValueError("Claude returned a non-object envelope")
            result.setdefault("provider", "claude")
            return result
        except (TypeError, ValueError, json.JSONDecodeError):
            return {"is_error": True,
                    "result": (out or err or "no output").strip()[:2000],
                    "provider": "claude"}
    finally:
        with LOCK:
            if cid in LIVE and (p is None or LIVE[cid].get("proc") is p):
                LIVE[cid]["proc"] = None
        if temp:
            temp.cleanup()


def _run_recovery(cid, o, role, failure, cfg_dir, cycle, workdir=ROOT,
                  safe_permissions=False):
    """Run one local/reversible recovery supervisor, never an approval bypass."""
    prompt, blocked = agent_runtime.build_recovery_prompt(
        role.get("mission") or "", failure, cycle, MAX_RECOVERY_CYCLES)
    rec = {
        "cycle": cycle,
        "failure_class": agent_runtime.classify_failure(failure, True),
        "failure": agent_runtime.safe_excerpt(failure, 300),
        "status": "blocked" if blocked else "working",
        "detail": blocked or "Recovery supervisor is inspecting the local failure.",
        "verification": "not-run",
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    history = role.setdefault("recovery_history", [])
    history.append(rec)
    del history[:-6]
    if blocked:
        role["status"] = "waiting_permission"
        role["detail"] = blocked
        role["next_action"] = "Operator decision required; automatic recovery is paused."
        o["status"] = "waiting_permission"
        o["detail"] = "%s: %s" % (role["id"], blocked)
        o["next_action"] = role["next_action"]
        _save(o)
        emit(cid, "role %s recovery paused: %s" % (role["id"], blocked))
        return "blocked", ""

    role["status"] = "repairing"
    role["detail"] = "bounded recovery cycle %d/%d" % (cycle, MAX_RECOVERY_CYCLES)
    role["next_action"] = "Recovery supervisor will verify a minimal local fix."
    o["status"] = "running"
    o["next_action"] = role["next_action"]
    _save(o)
    if _stopped(cid):
        rec.update(status="stopped", detail="stopped by operator")
        _stop_state(o)
        return "stopped", ""
    emit(cid, "role %s recovery cycle %d/%d" %
         (role["id"], cycle, MAX_RECOVERY_CYCLES))
    fixer = {
        "id": role["id"] + "-recovery-%d" % cycle,
        "title": "Recovery supervisor / fixer",
        "mission": prompt,
        "model": "sonnet" if role.get("model") in ("haiku", "sonnet") else role.get("model", "sonnet"),
        "turns": max(10, min(30, int(role.get("turns") or 30) // 2)),
        "recovery": True,
    }
    t0 = time.time()
    w = _worker(cid, fixer, "", cfg_dir, workdir=workdir,
                safe_permissions=safe_permissions)
    rec["secs"] = round(time.time() - t0)
    rec["cost"] = round(w.get("total_cost_usd") or 0, 4)
    role["cost"] = round((role.get("cost") or 0) + rec["cost"], 4)
    report = str(w.get("result") or "")[:3000]
    classification = agent_runtime.classify_failure(
        report, bool(w.get("is_error")), w.get("subtype") or "")
    # A successful-looking fixer can still explicitly discover that a protected
    # operator decision is required. Treat that as a gate, never as success.
    rec["repair_class"] = classification
    rec["repair_summary"] = agent_runtime.safe_excerpt(report, 320)
    if _stopped(cid):
        rec.update(status="stopped", detail="stopped by operator")
        _stop_state(o)
        return "stopped", ""
    if classification == "permission":
        rec.update(status="blocked", detail="operator permission or credentials are required")
        role["status"] = "waiting_permission"
        role["detail"] = rec["detail"]
        role["next_action"] = "Resolve the permission explicitly, then Resume."
        o["status"] = "waiting_permission"
        o["detail"] = "%s: %s" % (role["id"], rec["detail"])
        o["next_action"] = role["next_action"]
        _save(o)
        return "blocked", ""
    if w.get("is_error"):
        rec.update(status="failed", detail=agent_runtime.safe_excerpt(report, 300))
        role["detail"] = "recovery cycle %d failed: %s" % (cycle, rec["detail"])
        role["next_action"] = ("Trying the final bounded recovery cycle." if
                               cycle < MAX_RECOVERY_CYCLES else
                               "Inspect the recovery evidence and retry manually.")
        _save(o)
        return "failed", report
    rec.update(status="repaired", detail="minimal local repair reported; original role will verify",
               verification="pending-original-rerun")
    role["detail"] = rec["detail"]
    role["next_action"] = "Re-running the original role to verify completion."
    _save(o)
    return "repaired", report


def _wait_gate(cid, o, role):
    """Park a review-gated role until the operator acts (approve/redo/skip)."""
    role["status"] = "review"
    o["status"] = "review"
    _save(o)
    emit(cid, "role %s awaiting review: %s" % (role["id"], role["title"]))
    while True:
        with LOCK:
            st = LIVE.get(cid) or {}
            if st.get("stop"):
                return ("stop", "")
            v = st.get("gate", {}).pop(role["id"], None)
        if v:
            return v
        time.sleep(0.5)


def _run(cid):
    o = _load_json(_path(cid))
    workdir = os.path.normpath(os.path.realpath(o.get("workdir") or ROOT))
    safe_permissions = bool(o.get("safe_permissions"))
    # Claude roles use the selected Claude account. Pure Codex missions do not
    # claim or expose an unrelated Claude account in Activity.
    has_claude = any(_provider_for_model(role.get("model")) == "claude"
                     for role in o.get("roles") or [])
    pref = o.get("account_pref") or "auto"
    acct = (pulse.least_used() if pref == "auto" else pref) if has_claude else ""
    cfg_dir = pulse.dir_for(acct) if acct else ""
    if acct:
        o["account"] = acct
        _save(o)
        emit(cid, "delegating to account: " + acct)
    roles = {r["id"]: r for r in o["roles"]}
    try:
        # dependency-ordered pass; repeats until no runnable role remains.
        # ponytail: sequential execution — parallel threads per role when a
        # real mission is actually bottlenecked by it.
        while True:
            if _stopped(cid):
                _stop_state(o)
                return
            runnable = next(
                (r for r in o["roles"] if r["status"] == "pending"
                 and all(roles[d]["status"] in ("done", "skipped")
                         for d in r["depends_on"] if d in roles)), None)
            if not runnable:
                # anything still pending has a failed/blocked dependency — name it,
                # so a blocked role never sits there without a reason
                for r in o["roles"]:
                    if r["status"] == "pending":
                        r["status"] = "blocked"
                        bad = [d for d in r["depends_on"] if d in roles
                               and roles[d]["status"] not in ("done", "skipped")]
                        r["detail"] = "blocked: %s didn't finish (%s)" % (
                            ", ".join(bad) or "a dependency",
                            ", ".join(roles[d]["status"] for d in bad) or "unfinished")
                break
            role = runnable
            transient_retries = 0
            recovery_cycles = len(role.get("recovery_history") or [])
            recovery_context = ""
            while True:  # worker retry / recovery / human-redo loop
                if _stopped(cid):
                    _stop_state(o)
                    return
                role["status"] = "working"
                role["attempt"] = (role.get("attempt") or 0) + 1
                role["detail"] = "attempt %d is running" % role["attempt"]
                role["next_action"] = "Wait for the role report."
                o["status"] = "running"
                o["next_action"] = "%s is working." % role["title"]
                _save(o)
                if _stopped(cid):
                    _stop_state(o)
                    return
                emit(cid, "role %s attempt %d working (%s, %dt): %s"
                     % (role["id"], role["attempt"], role["model"],
                        role["turns"], role["title"]))
                contexts = ["### %s (%s)\n%s" % (roles[d]["title"], d,
                                                  roles[d]["result"][:1500])
                            for d in role["depends_on"] if roles[d].get("result")]
                if recovery_context:
                    contexts.append("### Bounded recovery report\n" + recovery_context[:2000])
                ctx = "\n\n".join(contexts)
                t0 = time.time()
                # A Continue on an exhausted role resumes its prior provider session
                # (context intact) instead of re-running the mission from zero
                sid = role.pop("continue_from", "")
                w = _worker(cid, role, "" if sid else ctx, cfg_dir, resume_sid=sid,
                            workdir=workdir, safe_permissions=safe_permissions)
                spend = w.get("total_cost_usd") or 0
                sid = w.get("session_id") or sid
                # ran out of turns => cut off mid-task, NOT finished. Continue the
                # same session until it lands or the continue budget runs out.
                cont = role.get("continues") or 0
                while (w.get("subtype") == "error_max_turns" and sid
                       and cont < MAX_CONTINUES):
                    with LOCK:
                        if LIVE.get(cid, {}).get("stop"):
                            break
                    cont += 1
                    role["continues"] = cont
                    role["session"] = sid
                    _save(o)
                    emit(cid, "role %s ran out of %d turns — auto-continuing the "
                         "same session (%d/%d)" % (role["id"], role["turns"],
                                                   cont, MAX_CONTINUES))
                    w = _worker(cid, role, "", cfg_dir, resume_sid=sid,
                                workdir=workdir, safe_permissions=safe_permissions)
                    spend += w.get("total_cost_usd") or 0
                    sid = w.get("session_id") or sid
                role["session"] = sid
                role["continues"] = cont
                elapsed = time.time() - t0
                role["secs"] = round((role.get("secs") or 0) + elapsed)
                role["cost"] = round((role.get("cost") or 0) + spend, 4)
                o["cost"] = round(sum(r.get("cost") or 0 for r in o["roles"]), 4)
                ran_out = w.get("subtype") == "error_max_turns"
                role["result"] = str(w.get("result") or "")[:6000] or (
                    "(no final message — used all %d turns)" % role["turns"])
                classification = agent_runtime.classify_failure(
                    role["result"], bool(w.get("is_error")), w.get("subtype") or "")
                _record_attempt(role, w, classification, elapsed)
                if _stopped(cid):
                    _stop_state(o)
                    return
                if ran_out:
                    # STILL unfinished after the continue budget. Park it with a
                    # stated reason — never report half-done work as success.
                    role["status"] = "exhausted"
                    role["detail"] = (
                        "ran out of turns: %d turn budget × %d run(s). The task was "
                         "cut off, not finished. Continue resumes this same session."
                         % (role["turns"], cont + 1))
                    role["next_action"] = "Continue resumes this exact worker session."
                    _save(o)
                    emit(cid, "role %s EXHAUSTED: %s" % (role["id"], role["detail"]))
                    break
                if w.get("is_error"):
                    role["last_failure_class"] = classification
                    role["last_failure"] = agent_runtime.safe_excerpt(role["result"], 500)
                    role["limit"] = classification == "transient_limit"
                    if classification == "task" and role.get("recovery_history"):
                        latest = role["recovery_history"][-1]
                        if latest.get("verification") == "pending-original-rerun":
                            latest["verification"] = "failed-original-rerun"
                            latest["status"] = "verification-failed"
                    if classification in ("transient", "transient_limit") \
                            and transient_retries < MAX_TRANSIENT_RETRIES:
                        transient_retries += 1
                        role["status"] = "retrying"
                        role["detail"] = "%s failure; bounded retry %d/%d" % (
                            classification, transient_retries, MAX_TRANSIENT_RETRIES)
                        role["next_action"] = "Retrying automatically after a short backoff."
                        o["next_action"] = role["next_action"]
                        _save(o)
                        emit(cid, "role %s %s — retry %d/%d" %
                             (role["id"], classification, transient_retries,
                              MAX_TRANSIENT_RETRIES))
                        if not _wait_retry(cid, transient_retries):
                            _stop_state(o)
                            return
                        recovery_context = ""
                        continue
                    if classification == "permission":
                        role["status"] = "waiting_permission"
                        role["detail"] = "operator permission or credentials are required"
                        role["next_action"] = "Resolve the permission explicitly, then Resume."
                        o["status"] = "waiting_permission"
                        o["detail"] = "%s: %s" % (role["id"], role["detail"])
                        o["next_action"] = role["next_action"]
                        _save(o)
                        emit(cid, "role %s waiting for operator permission" % role["id"])
                        break
                    if classification == "task" and recovery_cycles < MAX_RECOVERY_CYCLES:
                        recovery_cycles += 1
                        state, report = _run_recovery(
                            cid, o, role, role["result"], cfg_dir, recovery_cycles,
                            workdir=workdir, safe_permissions=safe_permissions)
                        o["cost"] = round(sum(r.get("cost") or 0 for r in o["roles"]), 4)
                        if state == "stopped":
                            return
                        if state == "blocked":
                            break
                        recovery_context = report
                        # Whether the fixer landed or itself failed, the original
                        # role is the verifier. It inspects existing work and either
                        # completes or produces evidence for the next bounded cycle.
                        continue
                    role["status"] = "failed"
                    role["detail"] = (
                        "transient retry budget exhausted: " + role["last_failure"]
                        if classification in ("transient", "transient_limit") else
                        "recovery budget exhausted: " + role["last_failure"])
                    role["next_action"] = "Inspect attempt/recovery history, then Resume or archive."
                    _save(o)
                    emit(cid, "role %s %s: %s" % (role["id"],
                         "retry budget exhausted" if role["limit"] else "FAILED",
                         role["detail"][:120]))
                    break
                # A successful original rerun is the verification step for the
                # latest recovery cycle. Keep only compact, secret-safe evidence.
                if role.get("recovery_history"):
                    latest = role["recovery_history"][-1]
                    if latest.get("verification") == "pending-original-rerun":
                        latest["verification"] = "passed-original-rerun"
                        latest["status"] = "verified"
                        # A brain note is a reusable recipe, not a process log.
                        # Require a successful fixer, actual original-role
                        # verification, and enough concrete evidence to be more
                        # useful than "fixed it" before marking it learnable.
                        repair = latest.get("repair_summary") or ""
                        latest["learnable"] = bool(
                            latest.get("repair_class") == "success" and
                            len(repair) >= 40 and len(repair.split()) >= 6)
                        role["recovery_summary"] = agent_runtime.compact_recovery_evidence(role)
                        emit(cid, "role %s recovery verified: %s" %
                             (role["id"], role["recovery_summary"][:120]))
                role.pop("limit", None)
                role["detail"] = ""
                role["next_action"] = ("Awaiting operator approval." if role.get("review")
                                       else "Role complete.")
                if not role.get("review"):
                    role["status"] = "done"
                    _save(o)
                    emit(cid, "role %s done ($%s, %ss)" % (role["id"], role["cost"], role["secs"]))
                    break
                verdict, feedback = _wait_gate(cid, o, role)
                if verdict == "stop":
                    _stop_state(o)
                    return
                if verdict == "approve":
                    role["status"] = "done"
                    _save(o)
                    emit(cid, "role %s approved by operator" % role["id"])
                    break
                if verdict == "skip":
                    role["status"] = "skipped"
                    _save(o)
                    break
                # redo: feedback becomes an addendum to the mission
                role["mission"] += "\n\nOPERATOR FEEDBACK (address this): " + (feedback or "revise")
                transient_retries = 0
                recovery_context = ""
                emit(cid, "role %s redo: %s" % (role["id"], (feedback or "")[:100]))
        # a mission is only "done" when every role actually finished. Exhausted
        # roles are unfinished work, not success — they get their own status so
        # the card says why and offers Continue instead of claiming completion.
        waiting = [r for r in o["roles"] if r["status"] == "waiting_permission"]
        stuck = [r for r in o["roles"] if r["status"] in
                 ("failed", "blocked", "exhausted", "waiting_permission")]
        if not stuck:
            o["status"] = "done"
            o["detail"] = ""
            o["next_action"] = "Mission complete; archive when no longer needed."
        elif waiting:
            o["status"] = "waiting_permission"
            o["detail"] = "; ".join("%s: %s" %
                                     (r["id"], r.get("detail") or "operator action required")
                                     for r in waiting)[:300]
            o["next_action"] = "Resolve the named permission explicitly, then Resume."
        elif all(r["status"] == "exhausted" for r in stuck):
            o["status"] = "exhausted"
            o["detail"] = ("out of turns before finishing — Continue picks each role "
                            "up in its own session, right where it stopped")
            o["next_action"] = "Continue resumes exhausted sessions without starting over."
        else:
            o["status"] = "failed"
            o["detail"] = "; ".join("%s: %s" % (r["id"], r.get("detail") or r["status"])
                                     for r in stuck)[:300]
            o["next_action"] = "Inspect attempt/recovery evidence, then Resume or archive."
        _save(o)
        emit(cid, "mission %s: %s ($%s)%s" % (o["status"], o["name"], o["cost"],
                                              " — " + o["detail"] if o.get("detail") else ""))
        # 5. learn — but only when it's worth remembering (signal, not a log).
        # Trivial cheap successes are skipped so the brain stays high-signal.
        if _worth_remembering(o):
            outcome = "; ".join("%s=%s" % (r["id"], r["status"]) for r in o["roles"])
            recovery_parts = [
                agent_runtime.compact_recovery_evidence(r, learnable_only=True)
                for r in o["roles"] if r.get("recovery_history")]
            recovery = "; ".join(part for part in recovery_parts if part)
            had_recovery = any(r.get("recovery_history") for r in o["roles"])
            last = next((r["result"] for r in reversed(o["roles"]) if r.get("result")), "")
            evidence = (recovery or
                        ("bounded recovery ended without a verified reusable recipe"
                         if had_recovery else agent_runtime.safe_excerpt(last, 320)))
            try:  # learning is best-effort and can never turn a done run into error
                subprocess.run([sys.executable, HERMES, "note",
                                agent_runtime.safe_excerpt(o["goal"], 180),
                                ("%s. %s" % (outcome, evidence))[:500],
                                "--tags", "mission,ceo,recovery" if recovery else "mission,ceo",
                                "--source", "ceo:" + cid],
                               capture_output=True, timeout=20)
            except Exception as learn_error:
                emit(cid, "brain note failed (mission unaffected): " +
                     type(learn_error).__name__)
        else:
            emit(cid, "skipped brain note (routine run — brain holds signal, not logs)")
    except Exception as e:  # never leave a run stuck at "running"
        if _stopped(cid):
            _stop_state(o)
        else:
            o["status"] = "error"
            o["detail"] = repr(e)[:300]
            _save(o)
            emit(cid, "CEO run crashed: " + repr(e)[:120])
    finally:
        completed = str(o.get("status") or "").lower() in SUCCESSFUL
        with LOCK:
            LIVE.pop(cid, None)
            if completed:
                try:
                    _archive_file(cid)
                except OSError as archive_error:
                    # The final active JSON remains intact if the move fails;
                    # archival housekeeping can never turn success into error.
                    emit(cid, "history archive failed (mission preserved): " +
                         type(archive_error).__name__)


def _resume_review_then_run(cid, role_id):
    """Re-create an in-memory review wait after a server restart, without
    re-running the already completed gated role."""
    o = _load_json(_path(cid))
    role = next((r for r in o.get("roles") or [] if r.get("id") == role_id), None)
    if not role:
        return
    verdict, feedback = _wait_gate(cid, o, role)
    if verdict == "stop":
        _stop_state(o)
        with LOCK:
            LIVE.pop(cid, None)
        return
    if verdict == "approve":
        role["status"] = "done"
    elif verdict == "skip":
        role["status"] = "skipped"
    else:
        role["mission"] += "\n\nOPERATOR FEEDBACK (address this): " + (feedback or "revise")
        role["status"] = "pending"
    _save(o)
    _run(cid)


def resume(cid, automatic=False):
    """Pick a stopped / failed / stalled mission back up. Every non-terminal role
    (failed, blocked, working, review) resets to pending and re-runs; done and
    skipped work is kept, so a mission that got 3/6 through a session limit
    continues from role 4 instead of restarting. Only when not already live."""
    path = _path(cid)
    if not os.path.exists(path):
        return "no such mission"
    with LOCK:
        st = LIVE.get(cid)
        if st and st["thread"].is_alive():
            return "already running"
    try:
        o = _load_json(path)
    except (OSError, json.JSONDecodeError):
        return "mission file unreadable"
    # Planning failures have no roles. The old resume path iterated an empty
    # list and claimed there was nothing to resume; safely re-plan instead.
    if not o.get("roles") and o.get("route") == "delegate":
        o["status"] = "planning"
        o["detail"] = ""
        o["next_action"] = "Retrying the bounded CEO staffing plan."
        o["resumes"] = o.get("resumes", 0) + 1
        _save(o)
        t = threading.Thread(target=_plan_then_run, args=(cid,), daemon=True)
        with LOCK:
            LIVE[cid] = {"thread": t, "proc": None, "stop": False, "gate": {}}
        t.start()
        emit(cid, "planning resumed (#%d); no repository role had started" % o["resumes"])
        return None

    # A persisted review is already-completed work awaiting a verdict. Rebuild
    # only its wait loop; never auto-approve it and never rerun it on boot.
    review = next((r for r in o.get("roles") or [] if r.get("status") == "review"), None)
    if review:
        if automatic:
            return "operator review is still required"
        o["status"] = "review"
        o["next_action"] = "Approve, redo, or skip the gated role."
        _save(o)
        t = threading.Thread(target=_resume_review_then_run,
                             args=(cid, review["id"]), daemon=True)
        with LOCK:
            LIVE[cid] = {"thread": t, "proc": None, "stop": False, "gate": {}}
        t.start()
        return None

    reset = 0
    for r in o["roles"]:
        if r["status"] in ("failed", "blocked", "working", "retrying", "repairing",
                            "waiting_permission", "exhausted", "stopped"):
            # A role cut off mid-task still has its provider session: hand
            # it back so it CONTINUES (context intact) instead of starting over.
            # A role that genuinely failed re-runs fresh from its mission.
            if r["status"] in ("exhausted", "working", "stopped") and r.get("session"):
                r["continue_from"] = r["session"]
                r["continues"] = 0        # fresh continue budget for this attempt
            else:
                r["result"] = ""          # drop stale error text; re-run fresh
            r["status"] = "pending"
            r.pop("limit", None)
            r.pop("detail", None)
            r["next_action"] = "Role queued after resume."
            reset += 1
    if not reset:
        return "nothing to resume — every role is already done or skipped"
    o["status"] = "running"
    o["detail"] = ""
    o["next_action"] = "Resuming unfinished roles; completed roles are retained."
    o["resumes"] = o.get("resumes", 0) + 1
    _save(o)
    t = threading.Thread(target=_run, args=(cid,), daemon=True)
    with LOCK:
        LIVE[cid] = {"thread": t, "proc": None, "stop": False, "gate": {}}
    t.start()
    kept = len([r for r in o["roles"] if r["status"] in ("done", "skipped")])
    emit(cid, "resumed (#%d) — re-running %d role(s), keeping %d completed"
         % (o["resumes"], reset, kept))
    return None


def action(cid, role_id, act, feedback=""):
    """Operator verdicts: approve | redo | skip (per role), stop, resume, archive."""
    if act == "resume":
        return resume(cid)  # valid precisely when the mission is NOT live
    if act == "archive":
        return archive(cid)
    proc = None
    with LOCK:
        st = LIVE.get(cid)
        if not st:
            return "not running (finished or server restarted)"
        if act == "stop":
            st["stop"] = True
            proc = st.get("proc")
        elif act in ("approve", "redo", "skip"):
            st["gate"][role_id] = (act, feedback)
            return None
        else:
            return "unknown action"
    if act == "stop":
        agent_runtime.terminate_process_tree(proc)
        try:
            o = _load_json(_path(cid))
            _stop_state(o)
        except (OSError, json.JSONDecodeError):
            pass
        return None
    return "unknown action"


def recover_stalled_on_boot():
    """Resume only crash-interrupted planning/working states marked recoverable.

    Review and permission waits are deliberately left gated. New missions carry
    ``auto_recover``; legacy files without it are never restarted implicitly.
    Returns the ids successfully scheduled, primarily for diagnostics/tests.
    """
    recovered = []
    if not os.path.isdir(CDIR):
        return recovered
    for fn in os.listdir(CDIR):
        if not fn.endswith(".json"):
            continue
        try:
            o = _load_json(os.path.join(CDIR, fn))
        except (OSError, json.JSONDecodeError):
            continue
        if not o.get("auto_recover"):
            continue
        states = {r.get("status") for r in o.get("roles") or []}
        if states & {"review", "waiting_permission"}:
            continue
        interrupted = (o.get("status") == "planning" or
                       (o.get("status") in ("running", "stalled") and
                        bool(states & {"working", "retrying", "repairing"})) or
                       (o.get("route") == "direct" and o.get("status") == "running" and
                        bool(states & {"pending"})))
        if interrupted and resume(o.get("cid") or fn[:-5], automatic=True) is None:
            recovered.append(o.get("cid") or fn[:-5])
    return recovered


if __name__ != "__main__" and os.environ.get("RUNE_DISABLE_BOOT_RECOVERY") != "1":
    recover_stalled_on_boot()


if __name__ == "__main__":
    # self-check: schemas serialize, model tiers sane, no API call needed
    json.dumps(REFINE_SCHEMA), json.dumps(PLAN_SCHEMA)
    assert "opus" in ROLE_MODELS and "fable" in ROLE_MODELS
    assert "fold" in PLAN_SYSTEM.lower() and "hermes" in PLAN_SYSTEM.lower()
    # the brain gate: keep signal, drop noise
    trivial = {"status": "done", "cost": 0.05,
               "roles": [{"model": "haiku", "turns": 8, "status": "done"}]}
    hard = {"status": "done", "cost": 0.05,
            "roles": [{"model": "opus", "turns": 30, "status": "done"}]}
    failed = {"status": "failed", "cost": 0.01,
              "roles": [{"model": "haiku", "turns": 5, "status": "failed"}]}
    assert not _worth_remembering(trivial), "cheap mechanical success should be skipped"
    assert _worth_remembering(hard), "opus reasoning should be kept"
    assert _worth_remembering(failed), "failures should be kept"
    # archive age math: an 8-day-old run is past the 7-day window, a fresh one isn't
    old = {"updated": (datetime.datetime.now() - datetime.timedelta(days=8)).isoformat()}
    assert _age_days(old) >= AUTO_ARCHIVE_DAYS and _age_days({"updated": ""}) == 0.0

    # ---- the regression that started all this: a role that burns its whole turn
    # budget was reported as a SILENT SUCCESS on half-finished work. It must now
    # auto-continue the same session, then park as "exhausted" with a reason.
    import tempfile
    CDIR = tempfile.mkdtemp()                      # run against a scratch dir
    calls = []

    def _worker(cid, role, context, cfg_dir, resume_sid="", workdir=ROOT,
                safe_permissions=False):   # never runs claude
        calls.append(resume_sid)
        return {"is_error": True, "subtype": "error_max_turns", "result": "",
                "session_id": "sess1", "total_cost_usd": 0.01}

    def emit(*a, **kw):
        pass

    pulse.least_used = lambda: ""
    role = {"id": "solo", "title": "T", "mission": "m", "model": "sonnet", "turns": 5,
            "depends_on": [], "review": False, "status": "pending", "result": "",
            "secs": 0, "cost": 0}
    _save({"cid": "t1", "name": "t", "goal": "g", "roles": [role], "status": "running",
           "cost": 0, "started": datetime.datetime.now().isoformat()})
    LIVE["t1"] = {"thread": threading.current_thread(), "proc": None,
                  "stop": False, "gate": {}}
    _run("t1")
    got = _load_json(_path("t1"))
    r = got["roles"][0]
    assert r["status"] == "exhausted", "out of turns was reported as %r" % r["status"]
    assert got["status"] == "exhausted" and r["detail"] and got["detail"]
    assert calls == [""] + ["sess1"] * MAX_CONTINUES, calls   # continued, not restarted
    assert r["cost"] == round(0.01 * (1 + MAX_CONTINUES), 4)  # every attempt billed
    # Continue = resume that same claude session, not re-run the mission from zero
    assert resume("t1") is None
    with LOCK:
        t = LIVE["t1"]["thread"]
    t.join(timeout=10)
    assert calls[1 + MAX_CONTINUES] == "sess1", "Continue restarted instead of resuming"
    assert _load_json(_path("t1"))["resumes"] == 1
    print("ceo.py OK — key present:", bool(chat._api_key()))
