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
  4. execute  Roles run as headless `claude -p` workers in dependency order.
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
import threading
import time
import urllib.request
import uuid

import chat    # API key resolution
import pulse   # account routing for workers

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

CONTINUE_PROMPT = """You ran out of your turn budget mid-task — this is a \
continuation of that same session, not a new one.

Continue exactly where you left off. Do NOT start over and do NOT redo work you \
already finished. Re-read anything you need, pick up the next unfinished step, \
and carry the task to completion. Finish by reporting what you did."""

# finished missions older than this auto-archive out of the active list to keep
# the UI lean; a Done/failed mission can also be archived by hand any time.
AUTO_ARCHIVE_DAYS = 7
TERMINAL = ("done", "failed", "rejected", "exhausted", "stopped", "error", "stalled")

REFINER = "claude-haiku-4-5"   # prompt smith: cheap, fast
PLANNER = "claude-opus-4-8"    # the CEO itself: judgment is the product
ROLE_MODELS = ("haiku", "sonnet", "opus", "fable")

LIVE = {}  # cid -> {"thread","proc","stop","gate":{role_id:(action,feedback)}}
LOCK = threading.Lock()

# a worker that dies on a usage/session/rate limit (not a real task failure) —
# used to flag the role so the UI can say "hit a limit, Continue when ready"
LIMIT_RE = re.compile(
    r"rate.?limit|usage limit|session limit|quota|overloaded|"
    r"\b429\b|too many requests|reset[s]? at|try again later", re.I)

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
- review: true when the operator should approve the output before dependents \
run (destructive changes, outward-facing deliverables, judgment calls) — \
also use it for anything the operator will want to preview.

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


def _save(o):
    o["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    tmp = _path(o["cid"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path(o["cid"]))


def _age_days(o):
    ts = o.get("updated") or o.get("started") or ""
    try:
        return (datetime.datetime.now()
                - datetime.datetime.fromisoformat(ts)).total_seconds() / 86400
    except ValueError:
        return 0.0


def _archive_file(cid):
    """Move a run out of the active dir into state/ceo/archive/ (kept, not deleted).
    list_all only scans the top level, so archived runs drop out of the UI."""
    src = _path(cid)
    if not os.path.exists(src):
        return False
    os.makedirs(ADIR, exist_ok=True)
    os.replace(src, os.path.join(ADIR, cid + ".json"))
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
                o = json.load(open(os.path.join(CDIR, fn), encoding="utf-8"))
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
            if (not o["live"] and o.get("status") in TERMINAL
                    and _age_days(o) >= AUTO_ARCHIVE_DAYS):
                _archive_file(o["cid"])
                continue
            out.append(o)
    out.sort(key=lambda o: o.get("started", ""), reverse=True)
    return out


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


def plan_and_start(text, opts=None):
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
    o = {"cid": cid, "name": text[:40], "summary": "", "goal": text[:1000],
         "refined": refined[:4000], "keywords": keywords[:300], "recall": False,
         "roles": [], "route": route,
         "opts": {"model": force_model, "turns": force_turns,
                  "gate": bool(opts.get("gate"))},
         "account_pref": str(opts.get("account") or "auto"),
         "status": "running", "cost": 0,
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


def _plan_then_run(cid):
    """Thread: brain recall -> CEO staffs the roles -> run them. Slow work that
    used to block the HTTP request now lives here."""
    try:
        o = json.load(open(_path(cid), encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    ov = o.get("opts") or {}
    force_model, force_turns, gate_all = ov.get("model"), ov.get("turns"), ov.get("gate")
    try:
        recall = _recall(o.get("keywords") or o["goal"])
        brief = "MISSION:\n" + o["refined"]
        if recall:
            brief += "\n\n## Brain recall — solved before, reuse don't re-solve:\n" + recall
        p = _api(PLANNER, PLAN_SYSTEM, brief, PLAN_SCHEMA, max_tokens=8000, timeout=180)
        roles = [] if p.get("error") else (p.get("roles") or [])
        if not roles:
            o["status"] = "error"
            o["detail"] = (p.get("error") or "CEO returned no roles")[:300]
            _save(o)
            emit(cid, "CEO planning failed: " + o["detail"][:120])
            return
        seen = set()
        for i, role in enumerate(roles[:6]):
            rid = re.sub(r"[^a-z0-9\-]", "", str(role.get("id") or "").lower()) or "r%d" % i
            while rid in seen:
                rid += "x"
            seen.add(rid)
            role["id"] = rid
            role["model"] = role.get("model") if role.get("model") in ROLE_MODELS else "opus"
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
        o.update(name=(p.get("name") or o["goal"][:40])[:40],
                 summary=(p.get("summary") or "")[:300],
                 recall=bool(recall), roles=roles[:6], status="running")
        _save(o)
        emit(cid, "CEO staffed '%s': %s" % (o["name"], ", ".join(
            "%s(%s/%dt)" % (r["id"], r["model"], r["turns"]) for r in o["roles"])))
    except Exception as e:
        o["status"] = "error"
        o["detail"] = repr(e)[:300]
        _save(o)
        emit(cid, "CEO planning crashed: " + repr(e)[:120])
        with LOCK:
            LIVE.pop(cid, None)
        return
    _run(cid)   # same thread: staffing flows straight into execution


def _worker(cid, role, context, cfg_dir, resume_sid=""):
    """One role = one headless claude -p run under this run's account.

    resume_sid continues THAT claude session (its context, its files read, its
    plan) instead of starting a fresh one — how a role that ran out of turns
    picks up where it left off rather than starting the task over."""
    if resume_sid:
        prompt = CONTINUE_PROMPT
    else:
        prompt = role["mission"]
        if context:
            prompt += "\n\n## Output from roles you depend on:\n" + context[:6000]
    argv = ["claude", "-p", "--output-format", "json",
            "--max-turns", str(role["turns"]),
            "--model", role["model"], "--dangerously-skip-permissions"]
    if resume_sid:
        argv += ["--resume", resume_sid]
    env = dict(os.environ, MAESTRO_SID=cid)
    if cfg_dir:
        env["CLAUDE_CONFIG_DIR"] = cfg_dir
    p = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding="utf-8",
                         shell=IS_WIN, env=env)
    with LOCK:
        if cid in LIVE:
            LIVE[cid]["proc"] = p
    try:
        out, err = p.communicate(prompt, timeout=WORKER_TIMEOUT)
    except subprocess.TimeoutExpired:
        p.kill()
        return {"is_error": True, "result": "timed out after %ss" % WORKER_TIMEOUT}
    finally:
        with LOCK:
            if cid in LIVE:
                LIVE[cid]["proc"] = None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"is_error": True, "result": (out or err or "no output").strip()[:2000]}


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
    o = json.load(open(_path(cid), encoding="utf-8"))
    # "auto" picks the account with the most headroom; else the operator's choice
    pref = o.get("account_pref") or "auto"
    acct = pulse.least_used() if pref == "auto" else pref
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
            with LOCK:
                if LIVE.get(cid, {}).get("stop"):
                    o["status"] = "stopped"
                    _save(o)
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
            while True:  # redo loop
                role["status"] = "working"
                o["status"] = "running"
                _save(o)
                emit(cid, "role %s working (%s, %dt): %s"
                     % (role["id"], role["model"], role["turns"], role["title"]))
                ctx = "\n\n".join("### %s (%s)\n%s" % (roles[d]["title"], d,
                                                       roles[d]["result"][:1500])
                                  for d in role["depends_on"] if roles[d].get("result"))
                t0 = time.time()
                # a Continue on an exhausted role resumes its old claude session
                # (context intact) instead of re-running the mission from zero
                sid = role.pop("continue_from", "")
                w = _worker(cid, role, "" if sid else ctx, cfg_dir, resume_sid=sid)
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
                    w = _worker(cid, role, "", cfg_dir, resume_sid=sid)
                    spend += w.get("total_cost_usd") or 0
                    sid = w.get("session_id") or sid
                role["session"] = sid
                role["continues"] = cont
                role["secs"] = round(time.time() - t0)
                role["cost"] = round(spend, 4)
                o["cost"] = round(sum(r.get("cost") or 0 for r in o["roles"]), 4)
                ran_out = w.get("subtype") == "error_max_turns"
                role["result"] = str(w.get("result") or "")[:6000] or (
                    "(no final message — used all %d turns)" % role["turns"])
                if ran_out:
                    # STILL unfinished after the continue budget. Park it with a
                    # stated reason — never report half-done work as success.
                    role["status"] = "exhausted"
                    role["detail"] = (
                        "ran out of turns: %d turn budget × %d run(s). The task was "
                        "cut off, not finished. Continue resumes this same session."
                        % (role["turns"], cont + 1))
                    _save(o)
                    emit(cid, "role %s EXHAUSTED: %s" % (role["id"], role["detail"]))
                    break
                if w.get("is_error"):
                    role["status"] = "failed"
                    # distinguish a usage/session-limit stop from a real failure:
                    # limits are transient — the operator just Continues later.
                    role["limit"] = bool(LIMIT_RE.search(role["result"]))
                    role["detail"] = ("hit a usage/session limit — transient, Continue when it resets"
                                      if role["limit"] else role["result"][:300])
                    _save(o)
                    emit(cid, "role %s %s: %s" % (role["id"],
                         "hit a limit" if role["limit"] else "FAILED", role["result"][:120]))
                    break
                if not role.get("review"):
                    role["status"] = "done"
                    _save(o)
                    emit(cid, "role %s done ($%s, %ss)" % (role["id"], role["cost"], role["secs"]))
                    break
                verdict, feedback = _wait_gate(cid, o, role)
                if verdict == "stop":
                    o["status"] = "stopped"
                    _save(o)
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
                emit(cid, "role %s redo: %s" % (role["id"], (feedback or "")[:100]))
        # a mission is only "done" when every role actually finished. Exhausted
        # roles are unfinished work, not success — they get their own status so
        # the card says why and offers Continue instead of claiming completion.
        stuck = [r for r in o["roles"] if r["status"] in ("failed", "blocked", "exhausted")]
        if not stuck:
            o["status"] = "done"
            o["detail"] = ""
        elif all(r["status"] == "exhausted" for r in stuck):
            o["status"] = "exhausted"
            o["detail"] = ("out of turns before finishing — Continue picks each role "
                           "up in its own session, right where it stopped")
        else:
            o["status"] = "failed"
            o["detail"] = "; ".join("%s: %s" % (r["id"], r.get("detail") or r["status"])
                                    for r in stuck)[:300]
        _save(o)
        emit(cid, "mission %s: %s ($%s)%s" % (o["status"], o["name"], o["cost"],
                                              " — " + o["detail"] if o.get("detail") else ""))
        # 5. learn — but only when it's worth remembering (signal, not a log).
        # Trivial cheap successes are skipped so the brain stays high-signal.
        if _worth_remembering(o):
            outcome = "; ".join("%s=%s" % (r["id"], r["status"]) for r in o["roles"])
            last = next((r["result"] for r in reversed(o["roles"]) if r.get("result")), "")
            subprocess.run([sys.executable, HERMES, "note", o["goal"][:180],
                            ("%s. %s" % (outcome, last))[:400],
                            "--tags", "mission,ceo", "--source", "ceo:" + cid],
                           capture_output=True)
        else:
            emit(cid, "skipped brain note (routine run — brain holds signal, not logs)")
    except Exception as e:  # never leave a run stuck at "running"
        o["status"] = "error"
        o["detail"] = repr(e)[:300]
        _save(o)
        emit(cid, "CEO run crashed: " + repr(e)[:120])
    finally:
        with LOCK:
            LIVE.pop(cid, None)


def resume(cid):
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
        o = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "mission file unreadable"
    reset = 0
    for r in o["roles"]:
        if r["status"] in ("failed", "blocked", "working", "review", "exhausted"):
            # a role that was cut off mid-task still has its claude session: hand
            # it back so it CONTINUES (context intact) instead of starting over.
            # A role that genuinely failed re-runs fresh from its mission.
            if r["status"] in ("exhausted", "working") and r.get("session"):
                r["continue_from"] = r["session"]
                r["continues"] = 0        # fresh continue budget for this attempt
            else:
                r["result"] = ""          # drop stale error text; re-run fresh
            r["status"] = "pending"
            r.pop("limit", None)
            r.pop("detail", None)
            reset += 1
    if not reset:
        return "nothing to resume — every role is already done or skipped"
    o["status"] = "running"
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
    with LOCK:
        st = LIVE.get(cid)
        if not st:
            return "not running (finished or server restarted)"
        if act == "stop":
            st["stop"] = True
            if st.get("proc"):
                try:
                    st["proc"].kill()
                except OSError:
                    pass
            return None
        if act in ("approve", "redo", "skip"):
            st["gate"][role_id] = (act, feedback)
            return None
    return "unknown action"


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

    def _worker(cid, role, context, cfg_dir, resume_sid=""):   # never runs claude
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
    got = json.load(open(_path("t1"), encoding="utf-8"))
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
    assert json.load(open(_path("t1"), encoding="utf-8"))["resumes"] == 1
    print("ceo.py OK — key present:", bool(chat._api_key()))
