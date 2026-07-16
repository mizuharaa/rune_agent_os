# Rune handoff

Current as of 2026-07-14. Repository:
`C:\Users\user\OneDrive\Desktop\Python Env\agentic_os`.

## Current morning flow

The dashboard now leads with a working **Microsoft Calendar** card and a rebuilt
**Daily briefing**. Calendar data comes from Microsoft Graph through
`dashboard/pulse.py`, survives cold starts through an atomic last-good cache,
and shows honest synced/cached/error states.

The briefing is no longer a list of commit messages, sessions, stale CEO runs,
or management prose. `daily_briefing.py` treats yesterday's repository activity
as hidden evidence and persists exactly three concrete priorities from three
different repositories. Each collapsed card shows why, outcome, and first
move. Expanding it reveals the CEO steps, definition of done, and responsive
role cards with icons, missions, deliverables, model, and effort.

Generation is strictly **plan-only**. No priority or agent card starts work.
The generator validates structured output, retries once, uses a lock, and
atomically retains the previous good briefing on failure. A normal primary run
is idempotent for the source date; **Generate 3 more** appends another batch.

Brainstorm choices are Fable 5 (`fable`) and GPT-5.6 Sol
(`gpt-5.6-sol`). Effort is `low|medium|high|xhigh|max`. Individual planned
agents can also use Haiku, Sonnet, or Opus and persist their own model/effort.

## Run and verify

```text
python bootstrap.py
python daily_briefing.py --selfcheck
python dashboard/pulse.py --selfcheck
python daily_briefing.py --summary
python desktop.py
# or: python dashboard/serve.py
```

Open `http://127.0.0.1:8817/dashboard/`. Check the calendar card first, then the
three briefing cards at desktop and narrow widths. Expand each card, change one
agent setting, and confirm the choice survives the next poll.

On-demand generation:

```text
briefing.cmd
briefing.cmd --model gpt-5.6-sol --effort max
briefing.cmd --more
```

The Windows Task Scheduler target is `briefing.cmd` at **09:30 local time every
day**. Both `briefing.cmd` and `loop.sh` use `daily_briefing.py scheduled`, which
freezes the latest source date due at the 09:30 boundary. The dashboard server
performs boot/minute catch-up, persists failures, and retries after 15 minutes;
the retired review/grading loop has no scheduling side effects.

## Calendar connection

`state/pulse.json` is gitignored. Its minimum Outlook section is:

```json
{
  "outlook": {
    "client_id": "<Azure public-client application id>",
    "tenant": "common",
    "timezone": "SE Asia Standard Time"
  }
}
```

Run `python dashboard/pulse.py --outlook-login` once. The app requests
`offline_access Calendars.Read`, stores the rotating refresh token, requests a
timezone-explicit `calendarView`, and safely follows Graph pagination. A
transient refresh error keeps the prior events visible as cached data. An
expired/revoked sign-in requires running the login command again.

## Runtime contracts

| Surface | Contract |
|---|---|
| `GET /api/pulse` | Current in-memory service snapshot. |
| `GET /api/calendar` | Normalized Outlook/ICS events with freshness. |
| `GET /api/briefing` | Last briefing, job state, defaults, and calendar. |
| `POST /api/briefing/generate` | Queue a plan-only primary or additional batch. |
| `POST /api/briefing/agent` | Persist a planned role's model/effort. |
| `POST /api/briefing/settings` | Persist default model, effort, and repo roots. |

Model work never happens on dashboard polling. It happens only through the
explicit POST/CLI path. The async dashboard job lives in the server process;
the validated briefing itself lives in `state/briefing.json`.

## Existing surfaces preserved

The command bar, account strip, Spotify player, agent console, skills, Brain,
integrations, audit, guard, Hermes, and event wire remain separate from the
daily briefing. In particular, a briefing role card is not a command-bar CEO
run and must not inherit historical run state.

The app remains Python-stdlib plus vanilla HTML/CSS/JS. `desktop.py` is the
intended chromeless local entry point. The server must bind only
`127.0.0.1:8817`; never expose it on `0.0.0.0` because local POST routes can
launch agents.

Before restarting, ensure only one process owns port 8817. A stale server can
serve old Python routes even when the HTML has already changed.

## UI constraints worth preserving

- Royal plum theme with light, readable content surfaces.
- Dense, specific cards; no status decoration that has no source.
- Real inline SVG role icons and sentence-case labels.
- `min-width: 0` for text-bearing grid/flex children.
- Guard DOM rebuilds during the 2.5-second poll so controls keep focus.
- Respect `prefers-reduced-motion`; avoid expensive animation filters.
- Verify around 1280 px and a narrow mobile width, including the console log.

## Secrets

Keep these uncommitted:

| File | Contents |
|---|---|
| `.env` | API keys. |
| `state/pulse.json` | Outlook, Spotify, GitHub, Gmail, and account credentials. |
| `state/pulse-cache.json` | Cached mail/calendar subjects and events. |
| `state/claude-seen.json` | Per-account Claude OAuth data. |
| `state/ssh-creds.json` | DPAPI-encrypted SSH passwords. |

The repository path contains spaces. Quote it in Task Scheduler actions and
when passing it to external programs.
