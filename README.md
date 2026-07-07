# AIOS — Agentic OS

Claude Code as a personal AI operating system. One **conductor** that keeps
memory fresh, reuses hard-won skills, spawns specialist agents only when
needed, and never re-solves a solved problem. AIOS is a **substrate** — other
assistants/consumers run on top of it through the surfaces below; it knows
nothing about them.

Zero dependencies beyond Python 3 stdlib. MIT.

## Quickstart

```
python bootstrap.py          # verify every layer (11 checks)
python bootstrap.py boot     # run the session boot sequence
python dashboard/serve.py    # mission control -> http://127.0.0.1:8817/dashboard/
```

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
| Hermes | `hermes/` | The flywheel: `hermes.py note|query|stale`. Notes append to `solved.jsonl` AND mirror as markdown cards into the vault (`AIOS/Hermes/`). Query before hard work; note after. |
| Dashboard | `dashboard/` | Single-file vanilla HTML/CSS/JS mission control + stdlib server. Polls the wire so every Claude Code tab shows up in one live workspace: conductor loop, sessions + subagents + stages, skill tree with earn progress, Hermes search, event stream. |

## The wire

Everything observable flows through `state/events.jsonl`. Hooks write it
automatically inside this repo; scripts and commands write it via
`python .claude/hooks/mirror.py --stage build --detail "..."`. If it's not on
the wire, it didn't happen.

## Surface for consumers

A consumer integrates by: reading/writing the wire (`mirror.py`), querying the
flywheel (`hermes.py query`), recording skill use (`skills/engine.py use`), and
respecting the guard. Nothing in AIOS may reference a consumer.
