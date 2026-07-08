# Maestro — Agentic OS

Claude Code as a personal AI operating system. One **conductor** that keeps
memory fresh, reuses hard-won skills, spawns specialist agents only when
needed, and never re-solves a solved problem. A maestro doesn't play the
instruments — it conducts the orchestra; that's the whole operating model.
Maestro is a **substrate** — other assistants/consumers run on top of it through
the surfaces below; it knows nothing about them.

Zero dependencies beyond Python 3 stdlib. MIT.

## Quickstart

```
python bootstrap.py          # verify every layer (12 checks)
python bootstrap.py boot     # run the session boot sequence
python desktop.py            # Maestro as a local app window (no browser chrome)
python dashboard/serve.py    # or just the server -> http://127.0.0.1:8817/dashboard/
```

`desktop.py` opens the console in a chromeless Edge/Chrome app window and shuts
the server down when you close it — a local app, no tabs or address bar.

## Is it working? (the 60-second health check)

1. `python bootstrap.py` — 12 PASS lines = every layer verified end to end.
2. Open the console — the sidebar **System** card shows wire/guard state live,
   and the dashboard's wire card ticks in real time: a stale clock = dead wire.
3. From **Instances**, launch a window ("New window" opens a real
   `claude --dangerously-skip-permissions` terminal on this repo). Within
   seconds it appears under Managed windows with working Focus/Close — that's
   the whole loop: spawn → hooks → wire → console.
4. Ask the brain something it knows: type in the Brain search
   (or `python hermes/hermes.py query "hook block tool call"`) — a HIT proves
   the flywheel reads.

Open the folder in Claude Code and it becomes the conductor: `CLAUDE.md` is the
boot sequence, `soul/soul.md` is the identity.

## The layers

| Layer | Where | What |
|---|---|---|
| 0 Soul | `soul/soul.md` | Identity, mission, beliefs. Hand-edited only — the guard blocks automated writes. Drift tracked in `soul/CHANGELOG.md`. |
| 1 Inventory | `CLAUDE.md` | The map + boot sequence every session runs. |
| 2 Rules | `.claude/hooks/` | `guard.py` blocks gated actions (destructive deletes, deploys, external sends, spending, soul writes) unless `state/approvals.json` holds a token (`approve.py`). `mirror.py` mirrors every event to `state/events.jsonl` — the single wire. |
| 3 Skills | `skills/` | `engine.py`: skills are **earned after 3 uses**, decay and archive when they stop serving the current `/goal`. Four real skills incl. `loop-engineering/loop.py`, a critic→doer loop with an iteration budget. |
| 4 Agents | `.claude/agents/` | Nine specialists (ceo, eng-manager, designer, reviewer, qa-lead, security-officer, release-engineer, doc-engineer, hermes), spawn-on-demand via `/spawn` — minimum roster, collected reports, closed loops. Mission loop: `/office-hours → /plan-ceo-review → /plan-eng-review → build → /review → /qa → /ship` (ship ends with a mandatory Hermes reflect). |
| 5 Wires | `memory/` | `OBSIDIAN.md` points at the vault; `pipeline.py` enforces source + freshness on every write (no naked facts) and deduplicates/consolidates/archives. |
| Hermes | `hermes/` | The flywheel: `hermes.py note|query|stale`. Notes append to `solved.jsonl` AND mirror as markdown cards into the Obsidian vault (`Maestro/Hermes/` + auto-regenerated `_index.md` MOC). Query before hard work; note after. |
| Console | `dashboard/` | Single-file vanilla HTML/CSS/JS + stdlib server, peach-sunset theme, left-navbar SPA. Overview (KPIs, activity chart, events donut, feed), **Instances** (launch + focus/close real Claude Code windows), Skills, Brain, **Brain Graph** (orbiting node view of Hermes), Integrations (MCPs/hooks/agents/skills), Audit, Guard. The launcher POSTs to `/api/spawn` — mode `tab` opens a real focusable terminal (via `conhost.exe`, foregrounded by pid), `background` runs headless; both take a **conscious model + turn budget**. Also `/api/message` (directive inbox), `/api/focus`, `/api/close`. Server binds 127.0.0.1 only — that is the security boundary. |

## The wire

Everything observable flows through `state/events.jsonl`. Hooks write it
automatically inside this repo; scripts and commands write it via
`python .claude/hooks/mirror.py --stage build --detail "..."`. If it's not on
the wire, it didn't happen.

## Surface for consumers

A consumer integrates by: reading/writing the wire (`mirror.py`), querying the
flywheel (`hermes.py query`), recording skill use (`skills/engine.py use`), and
respecting the guard. Nothing in AIOS may reference a consumer.
