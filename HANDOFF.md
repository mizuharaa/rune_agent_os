# Rune — agent handoff

Written 2026-07-09 (commit `83a6d28`, "Console v5") for the next agent picking up this repo.

## Console v6 addendum (2026-07-13) — Rune

- **Renamed Maestro → Rune** (Daniel asked for a memorable 1-syllable name).
  `MAESTRO_SID`/`MAESTRO_SSH_*` env vars and the `maestro_agent_os` remote
  keep their old names on purpose (running hooks / GitHub). The Obsidian
  section moved: all cards migrated from `Maestro/` to `Rune/Hermes` +
  `Rune/Knowledge`, index regenerated, recall verified.
- **CEO pipeline** (`dashboard/ceo.py`, replaces `mission.py` + `/api/mission`):
  command bar → Haiku prompt-refiner → Hermes recall → CEO (Opus structured
  output) staffs 1-6 roles with per-role model (opus default for hard work,
  fable only for frontier-complex, sonnet light, haiku mechanical) + turns +
  depends_on + review gates. Roles run as headless `claude -p` on the
  least-used account; state in `state/ceo/<cid>.json`; dashboard "Running
  tasks" card expands to per-role status (pending/working/blocked/review/
  done/failed) with Approve/Redo/Skip on gated roles. On finish the outcome is
  written to Hermes → mirrored into Obsidian. Verified end-to-end (a real
  mission planned by Opus, executed by a haiku role).
- **Accounts row**: white cards + brand logos + hover lift. New **Codex card**
  reads `~/.codex/auth.json` (email/plan from the id_token JWT) and the newest
  session rollout's `rate_limits` snapshot for 5h/weekly cooldowns — zero
  network probes. Claude cards now show the 7d reset countdown too.
  Removed: weather chip, MCP tool cards, the pulse-row Spotify card.
- **Vinyl player** is minimizable (toggle top-right, persisted in
  localStorage `rune-vinyl-min`; collapsed = one now-playing line).
- **Instances → Agent console**; session feed now shows only sessions Rune
  didn't launch (redundancy cut). Orchestrator loops unchanged.
Read `soul/soul.md` and `CLAUDE.md` first — this file is the operational map on top of them.

## What Rune is

Claude Code as a personal AI operating system for **Daniel (Khang Daniel Tran)**. One
*conductor* that keeps memory fresh, reuses hard-won skills, spawns specialist agents only
when needed, and never re-solves a solved problem. It is a **substrate**: a future consumer
runs on top of it (Daniel's memory holds its name). Rune must contain **zero references
to that consumer** — `bootstrap.py`'s hygiene check greps EVERY file (including code
comments — it has caught a comment once) and fails the build if the name appears.
Naming history: AIOS → EMBER → Rune (run new product names past Daniel).

Location: `C:\Users\user\OneDrive\Desktop\Python Env\agentic_os` (note the SPACE in the
path — it has broken SSH_ASKPASS once already; see Conventions). MIT. Python 3 stdlib only —
no dependencies, no build step. Remote: `github.com/mizuharaa/maestro_agent_os`, in sync.

## Status (as of commit 83a6d28, 2026-07-09)

`python bootstrap.py` → **12/12 checks pass.** Everything below is committed and pushed.

Verified end-to-end this session: the **mission command bar** (goal → Sonnet intake →
clarify-or-launch → orchestrator loop → opus critic → Hermes note on accept; a real test
mission ran for $0.10 on auto-routed account), **server-accurate account pulse** (4 accounts
live), **Spotify OAuth + now-playing + vinyl player**, weather, chat assistant, plum retheme,
dendrogram skill tree, admin add-skill.

Skill registry goal: `stand up Rune v1` — 4/9 earned (automation, web-design,
loop-engineering, skill-creation), learning (3d-interaction, orchestration, workflow-audit),
candidate (design-intelligence — seeded via the new admin endpoint), archived (vault-gardening).

## Run + verify

```
python bootstrap.py          # 12-check verification — run this first, always
python desktop.py            # LOCAL app (chromeless Edge) — the intended entry point
python dashboard/serve.py    # or just the server -> http://127.0.0.1:8817/dashboard/
```

Local app, not a webapp. Binds `127.0.0.1` only; that IS the security boundary — anyone who
can POST to it can spawn permission-skipping agents. Never bind `0.0.0.0`.

⚠️ **Restart discipline:** `SO_REUSEADDR` lets multiple `serve.py` processes bind :8817
simultaneously; requests then round-robin across stale copies and *new routes 404
intermittently* (burned an hour once). Always kill ALL pids on the port before starting one:
`netstat -ano | grep 127.0.0.1:8817.*LISTEN` → `taskkill /PID <each> /F` → start exactly one.

## What was added in Console v5 (this session's work)

| Piece | Where | The point |
|---|---|---|
| **Mission command bar** | `index.html` (cmd*), `dashboard/mission.py`, `serve.py api_mission` | Top-center "Tell Rune what to do…" with typewriter placeholder examples. One Sonnet-5 structured-output call either asks ONE clarifying question back (bar becomes a mini-conversation) or returns a launch brief {mission, name, turns, rounds, model, dir} with conscious spend baked in, then auto-starts the orchestrator (opus critic, account="auto"). Split-button dropdown (model/effort/rounds/critic/account/gate-verdicts) overrides any auto choice; white dot on the chevron = tuned. |
| **Learning loop closed** | `orchestrator.py` accept path | On accept, the orchestrator itself writes a Hermes note (mission + outcome), AND every intake brief ends by instructing the worker to write one. Missions now feed the brain both ways without anyone remembering to. |
| **Accurate account pulse** | `dashboard/pulse.py` | Transcripts can't attribute usage when accounts are swapped in one terminal (they don't record the account). Real method: capture each account's OAuth token from `<config>/.credentials.json` into gitignored `state/claude-seen.json` (keyed by accountUuid), probe `POST /v1/messages` (1 token, `anthropic-beta: oauth-2025-04-20`), read `anthropic-ratelimit-unified-5h/7d-{reset,utilization}` headers. A 429 still carries them. `least_used()` scores **max(5h, 7d)** — 5h-only picked an account at 100% weekly once. |
| **Per-account spawning** | `serve.py`, `orchestrator.py` | Launch form + orchestrator take an account; spawns set `CLAUDE_CONFIG_DIR`. "auto" = `pulse.least_used()`. |
| **Spotify: OAuth + player** | `pulse.py`, `serve.py`, `index.html` | Full code flow (`/api/spotify/login` → consent → callback saves refresh token; redirect URI is FIXED `http://127.0.0.1:8817/api/spotify/callback` — Spotify requires exact match, loopback must be 127.0.0.1 not localhost). 7s now-playing loop. `spotify_ctl()`: next/prev/seek/toggle via `/api/spotify/ctl`. |
| **Vinyl mini-player** | sidebar, `index.html` (.vinyl), `dashboard/lofi.jpg` | Lofi starry-mountain backdrop (local file), record face = album cover with grooves/sheen, spinning; SVG tonearm morphs down when playing; interactive seek bar (click-to-seek, hover knob, mm:ss), prev/play/next. Progress advances via a local 1s ticker resynced ONLY when the server snapshot changes (resyncing every poll snaps the bar backwards). No `filter:` on animated elements — that was the lag. |
| **Chat assistant** | `dashboard/chat.py`, FAB bottom-right | "Ask Rune" bubble: Haiku for light questions, Sonnet for heavy (regex routing), raw urllib, context = soul + skills + wire + Hermes. Key from env or gitignored `.env` (VALUE copied in; never a consumer path). |
| **Plum retheme** | whole `index.html` | Green → royal plum (#5c1346) via scripted hue-rotation of the entire palette (~318°), preserving lightness relationships. Per-service brand cards allowed (Claude orange / Gmail red / GitHub ink / Spotify green). |
| **Pulse row** | dashboard top | Brand cards with emails auto-read from each config's `.claude.json`, setup buttons (copy `$env:CLAUDE_CONFIG_DIR=…; claude`), carousel arrows (rAF easeOutCubic tween — `scrollTo({smooth})` is a no-op under scroll-snap), content-signature guard so the 2.5s poll doesn't reset scroll/detach clicks. |
| **Skill tree** | skills tab | Dendrogram: root → genre branches → skill leaves with CSS connector lines, progress-ring nodes, stat tiles (earned/in-progress/XP + %), detail panel on click, admin "Add a skill" form → `POST /api/skill` → `engine.py add`. **LIGHT theme — Daniel rejected the dark version as unreadable slop.** |
| **Misc** | — | Weather chip in topbar (open-meteo; replaced the "all clear" dot). Performance line graph (wire events/hour, 12h). Hover-left-edge reveals navbar, auto-collapses (burger pins). iPhone-style timer roll (only on minute change). Dead instances auto-pruned from `/api/instances`. SSH_ASKPASS now uses the Windows 8.3 short path (see Conventions). |

## Known issues / what's left

- **Spotify controls need a re-auth Daniel must do**: current token lacks
  `user-modify-playback-state` (Spotify says 401 "Permissions missing" — not 403). UI shows a
  clean toast. He must add `http://127.0.0.1:8817/api/spotify/callback` to his Spotify app's
  Redirect URIs, then hit **reconnect** on the Spotify card (SPOTIFY_SCOPE already includes
  modify). Until then: seek/skip buttons toast the hint; now-playing works fine.
- **SSH fix is unverified end-to-end.** Root cause found and fixed (Win32-OpenSSH execs
  SSH_ASKPASS unquoted; space in "Python Env" broke it → 8.3 short path via GetShortPathNameW).
  But no valid credentials to `ptlab@100.115.46.66` were available to test a real login. If it
  still denies WITHOUT the `'…\Python' is not recognized` lines, the saved password itself is
  wrong — that's on Daniel's side.
- **`workingwdaniel` account token is stale** ("re-open this account to refresh") — tokens
  expire; the card says what to do. The seen-cache self-heals when he opens that account again.
- **Orchestrations don't survive a server restart** (thread dies; JSON stays, shows stalled).
  With the command bar now auto-launching loops this hurts more than before — see Ideas.
- **Mission intake trusts its own `dir`** — it only launches into a dir that exists, but an
  existing-but-wrong dir would still be used. Low risk, worth a confirm-chip in the bar.
- **Clarify-loop history is in-memory only** (CMD.hist in the page) — a reload mid-clarify
  drops the exchange.
- **Structured output + Sonnet 5**: adaptive thinking tokens count against `max_tokens`
  (mission.py uses 3500 and slices `{…}` out of the text). Don't lower it back.
- **Windows-only** instance management (ctypes PID/focus/conhost). Unchanged.
- **Vault graph caps at 240 notes** (vault ~100 now). Unchanged.
- **Weekly (7d) limits bite**: one test burned a mission on an account at 100%/7d before
  `least_used` was fixed. Watch the 7d chip on the account cards.

## Ideas for what to do next (ranked by leverage)

1. **Mission bar → status inline.** After launch, stream the loop's wire events (round, verdict,
   cost) into the cmdflow area instead of "watch it →". The data is already on the wire under
   the oid; it's a filter + poll.
2. **Orchestration resume.** Persist enough loop state (`--resume` sid is already in the JSON)
   for serve.py to offer "resume stalled loop" on startup — the biggest reliability gap now
   that loops launch themselves.
3. **Roster routing in intake.** mission.py knows `.claude/agents/*` exist but doesn't pick one.
   Let it choose `role` and have the brief open with "/spawn <role>" — real division of labor.
4. **Brain-first intake.** Have `api_mission` run `hermes.py query` on the goal and inject hits
   into the intake prompt — "you solved this on 07-08, reuse it" beats re-solving (recall.py
   already does this for interactive sessions; the bar bypasses it).
5. **Skill auto-credit.** When an accepted mission matches a skill's trigger/tags, run
   `engine.py use <skill>` automatically — right now earning is manual and lags reality.
6. **Session labels**: map wire hex ids → mission names in the feed (instances already do).
7. **Pulse hardening**: exponential backoff on the account probes; a "refresh token" deep link
   on stale cards (`$env:CLAUDE_CONFIG_DIR` copy exists; could auto-open the terminal).
8. **desktop.py polish**: tray icon + auto-start server + single-instance lock — make the
   "local app" story real.

## Design constraints — READ BEFORE TOUCHING THE UI

Daniel notices slop instantly and has redirected the design **three times**. Current spec in
his memory (`design-taste.md`); the load-bearing rules:

- **Royal plum theme** (primary `#5c1346`, mid `#7c1e60`, tint `#dca9cd`, canvas `#f0eaee`).
  If re-theming again, hue-rotate the whole palette programmatically — don't hand-swap hexes.
- **LIGHT surfaces for content.** White cards, ink text. He rejected a dark skill tree as
  "can't see anything, AI slop". Only sanctioned dark accents: the vinyl player, the System
  box, one dark feature card per view.
- **"Max 3 colors" = the main theme only.** Per-service brand colors are wanted (Claude
  orange, Gmail red, GitHub ink, Spotify green). Semantic green ▲ deltas OK on stat tiles.
- **Punched-out text** — big/bold numbers and names; no scattered sparse rows; no lonely
  carets; tight bordered mini-cards over full-width scatter.
- **Real icons only** (Lucide paths). Sentence case. No fake status dots. Never wash the page
  in a gradient.
- **ALWAYS verify at ~1280px** with Playwright MCP (cache-bust `?v=N`, screenshot, READ the
  screenshot, check console). `min-width:0` on every grid/flex child that holds text.
- **Animation rules learned the hard way:** no `filter:` on animated elements (lag);
  `scrollTo({behavior:smooth})` is a no-op under scroll-snap (use an rAF tween with snap
  temporarily off); guard innerHTML rebuilds behind content signatures or the 2.5s poll resets
  scroll/animation/clicks; unique class names (`.deck` and `.cchip` collisions each broke a
  page once).

## Conventions

- Shell is Windows. Don't hand-quote JSON with backslashes through bash — build payloads in
  Python temp files (a heredoc with `\U` paths inside `python -c` will SyntaxError).
- Git args starting with `/` get MSYS-mangled in git bash.
- **Paths with spaces**: Win32-OpenSSH runs SSH_ASKPASS unquoted → use `short_path()`
  (8.3 names) for anything handed to a program that execs it raw.
- Playwright MCP screenshots save to `~/.playwright-mcp/` (or cwd for element shots) — and the
  browser occasionally closes between calls; just re-navigate.
- Mobbin MCP is paywalled ("requires a paid plan") — don't burn turns on it; design from
  reference images Daniel supplies.
- End commits with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- After hard problems: `python hermes/hermes.py note "<problem>" "<solution>" --tags ... --source ...`
  (16 notes and counting — query before solving anything).

## Secrets map (all gitignored — verify with `git check-ignore` before any commit)

| File | Holds |
|---|---|
| `.env` | ANTHROPIC_API_KEY (value copied in; NEVER reference the consumer's path) |
| `state/pulse.json` | Spotify client id/secret/refresh token, GitHub token, Gmail app password, weather lat/lon, claude_accounts dirs |
| `state/claude-seen.json` | per-account Claude OAuth access tokens (seen-cache) |
| `state/ssh-creds.json` | DPAPI-encrypted ssh passwords |

A pre-commit content scan for token patterns was clean at `83a6d28`; keep it that way.

## Context outside the repo

Daniel's memory (`C:\Users\user\.claude\projects\C--Users-user\memory\`): `agentic-os.md`
(dense per-session changelog of this project — read it), `design-taste.md`,
`conscious-agent-spend.md`, index `MEMORY.md`. Obsidian vault:
`C:\Obsidian_Brain\Daniel_Obsidian_Vault` — Rune reads all, writes only under `Rune/`.

## Suggested first move

`python bootstrap.py` (expect 12/12) → kill stragglers on :8817 → `python desktop.py` → click
through all tabs → type something into the command bar and watch the loop run. If the task is
UI, re-read `design-taste.md` and verify at 1280px before showing anything.
