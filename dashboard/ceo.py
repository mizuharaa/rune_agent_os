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
MIRROR = os.path.join(ROOT, ".claude", "hooks", "mirror.py")
HERMES = os.path.join(ROOT, "hermes", "hermes.py")
API = "https://api.anthropic.com/v1/messages"
IS_WIN = sys.platform == "win32"
WORKER_TIMEOUT = 1800

REFINER = "claude-haiku-4-5"   # prompt smith: cheap, fast
PLANNER = "claude-opus-4-8"    # the CEO itself: judgment is the product
ROLE_MODELS = ("haiku", "sonnet", "opus", "fable")

LIVE = {}  # cid -> {"thread","proc","stop","gate":{role_id:(action,feedback)}}
LOCK = threading.Lock()

REFINE_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string"},    # the improved, specific prompt
        "keywords": {"type": "string"},  # search keywords for brain recall
    },
    "required": ["prompt", "keywords"],
    "additionalProperties": False,
}

REFINE_SYSTEM = """You improve raw operator prompts before they reach the CEO \
of an agentic OS. Rewrite the prompt to be specific and unambiguous: expand \
vague verbs, name concrete deliverables, keep every constraint the operator \
stated, add nothing they didn't ask for. Also produce a short keyword string \
for searching a knowledge base of previously solved problems."""

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
constraints, and a CHECKABLE definition of done. End every mission with: \
'When done, record what you learned: python hermes/hermes.py note \
"<problem>" "<solution>" --tags mission'.

If prior solutions are provided under "Brain recall", fold them into the \
relevant briefs so workers reuse instead of re-solving."""


def emit(cid, detail, event="ceo"):
    subprocess.run([sys.executable, MIRROR, "--session", cid, "--event", event,
                    "--detail", detail[:200]], capture_output=True)


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
            if o.get("status") in ("running", "review") and not o["live"]:
                o["status"] = "stalled"  # server restarted mid-run
            out.append(o)
    out.sort(key=lambda o: o.get("started", ""), reverse=True)
    return out


def plan_and_start(text):
    """The whole intake: refine -> recall -> CEO plan -> launch. Synchronous
    (the command bar shows a spinner); execution then runs on a thread.
    Returns (run-dict, None) or (None, error)."""
    text = (text or "").strip()
    if not text:
        return None, "empty goal"
    # 1. Haiku prompt smith — degrade gracefully to the raw prompt
    r = _api(REFINER, REFINE_SYSTEM, text, REFINE_SCHEMA, max_tokens=1500, timeout=45)
    refined = r.get("prompt") if isinstance(r.get("prompt"), str) and r.get("prompt") else text
    keywords = r.get("keywords") or text
    # 2. Brain recall — requery learnt knowledge before planning
    recall = _recall(keywords)
    # 3. CEO plan (Opus, structured output)
    brief = "MISSION (refined by intake):\n" + refined
    if recall:
        brief += "\n\n## Brain recall — solved before, reuse don't re-solve:\n" + recall
    p = _api(PLANNER, PLAN_SYSTEM, brief, PLAN_SCHEMA, max_tokens=8000, timeout=180)
    if p.get("error"):
        return None, p["error"]
    roles = p.get("roles") or []
    if not roles:
        return None, "CEO returned no roles"
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
        role["depends_on"] = [d for d in (role.get("depends_on") or []) if d in seen and d != rid]
        role.update(status="pending", result="", secs=0, cost=0)
    os.makedirs(CDIR, exist_ok=True)
    cid = uuid.uuid4().hex[:8]
    o = {"cid": cid, "name": (p.get("name") or text[:40])[:40],
         "summary": (p.get("summary") or "")[:300],
         "goal": text[:1000], "refined": refined[:4000],
         "recall": bool(recall), "roles": roles[:6],
         "status": "running", "cost": 0,
         "started": datetime.datetime.now().isoformat(timespec="seconds")}
    _save(o)
    t = threading.Thread(target=_run, args=(cid,), daemon=True)
    with LOCK:
        LIVE[cid] = {"thread": t, "proc": None, "stop": False, "gate": {}}
    t.start()
    emit(cid, "CEO staffed '%s': %s" % (o["name"], ", ".join(
        "%s(%s/%dt)" % (r["id"], r["model"], r["turns"]) for r in o["roles"])))
    return o, None


def _worker(cid, role, context, cfg_dir):
    """One role = one headless claude -p run under this run's account."""
    prompt = role["mission"]
    if context:
        prompt += "\n\n## Output from roles you depend on:\n" + context[:6000]
    argv = ["claude", "-p", "--output-format", "json",
            "--max-turns", str(role["turns"]),
            "--model", role["model"], "--dangerously-skip-permissions"]
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
    acct = pulse.least_used()
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
                # anything still pending has a failed/blocked dependency
                for r in o["roles"]:
                    if r["status"] == "pending":
                        r["status"] = "blocked"
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
                w = _worker(cid, role, ctx, cfg_dir)
                role["secs"] = round(time.time() - t0)
                role["cost"] = round(w.get("total_cost_usd") or 0, 4)
                o["cost"] = round(sum(r.get("cost") or 0 for r in o["roles"]), 4)
                ran_out = w.get("subtype") == "error_max_turns"
                role["result"] = str(w.get("result") or "")[:6000] or (
                    "(no final message — used all %d turns)" % role["turns"])
                if w.get("is_error") and not ran_out:
                    role["status"] = "failed"
                    _save(o)
                    emit(cid, "role %s FAILED: %s" % (role["id"], role["result"][:120]))
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
        failed = [r for r in o["roles"] if r["status"] in ("failed", "blocked")]
        o["status"] = "failed" if failed else "done"
        _save(o)
        emit(cid, "mission %s: %s ($%s)" % (o["status"], o["name"], o["cost"]))
        # 5. learn — the outcome lands in Hermes, which mirrors into Obsidian
        outcome = "; ".join("%s=%s" % (r["id"], r["status"]) for r in o["roles"])
        last = next((r["result"] for r in reversed(o["roles"]) if r.get("result")), "")
        subprocess.run([sys.executable, HERMES, "note", o["goal"][:180],
                        ("%s. %s" % (outcome, last))[:400],
                        "--tags", "mission,ceo", "--source", "ceo:" + cid],
                       capture_output=True)
    except Exception as e:  # never leave a run stuck at "running"
        o["status"] = "error"
        o["detail"] = repr(e)[:300]
        _save(o)
        emit(cid, "CEO run crashed: " + repr(e)[:120])
    finally:
        with LOCK:
            LIVE.pop(cid, None)


def action(cid, role_id, act, feedback=""):
    """Operator verdicts: approve | redo | skip (per role), stop (whole run)."""
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
    print("ceo.py OK — key present:", bool(chat._api_key()))
