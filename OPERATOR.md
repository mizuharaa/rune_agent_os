# Operating Rune's daily briefing

The production loop has one job: create a validated plan from the previous
local calendar day. It does not run review, grading, old CEO operations, or any
planned agent.

## Connect Microsoft Calendar

Create a public Microsoft application that allows device-code/public-client
flows and delegated `Calendars.Read`. Put its id in the gitignored
`state/pulse.json`:

```json
{
  "outlook": {
    "client_id": "<Azure application id>",
    "tenant": "common",
    "timezone": "SE Asia Standard Time"
  }
}
```

Sign in once:

```text
python dashboard/pulse.py --outlook-login
```

Start or restart `python dashboard/serve.py`. The Microsoft Calendar overview
card should show `synced` and the next event. When Graph is temporarily
unavailable, Rune keeps the last good events and labels them `cached` instead
of clearing the card.

Deterministic offline verification:

```text
python dashboard/pulse.py --selfcheck
```

## Generate a briefing manually

Windows:

```text
briefing.cmd
briefing.cmd --model fable --effort high
briefing.cmd --model gpt-5.6-sol --effort max
briefing.cmd --more
```

Direct CLI:

```text
python daily_briefing.py scheduled
python daily_briefing.py generate --date yesterday
python daily_briefing.py generate --date 2026-07-13 --json
python daily_briefing.py --summary
python daily_briefing.py --selfcheck
```

Useful generation flags:

| Flag | Meaning |
|---|---|
| `--model fable|gpt-5.6-sol` | Select the brainstorming provider. |
| `--effort low|medium|high|xhigh|max` | Select planning depth. |
| `--more` | Append another validated three-priority batch. |
| `--force` | Replace the primary result even when that source day already exists. |
| `--root <path>` | Override configured discovery roots; repeat to supply several. |
| `--json` | Print the persisted result as JSON. |

Without `--more` or `--force`, a successful primary result for that source date
is returned unchanged. A run needs at least three discoverable Git repositories.
The configured model CLI must be installed and authenticated.

## Install the 09:30 Windows schedule

Run the following PowerShell from the repository root. It creates an
interactive-user task so the model CLIs can read the same user profile and auth
as manual runs:

```powershell
$root = (Resolve-Path .).Path
$action = New-ScheduledTaskAction `
  -Execute "$env:SystemRoot\System32\cmd.exe" `
  -Argument ('/d /c ""{0}\briefing.cmd""' -f $root) `
  -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At 9:30AM
$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "Rune Daily Briefing" `
  -Action $action -Trigger $trigger -Settings $settings `
  -Principal $principal -Description "Plan yesterday's three highest-leverage repository changes at 09:30 local time." -Force
```

Verify the trigger and next run:

```powershell
Get-ScheduledTask -TaskName "Rune Daily Briefing" |
  Select-Object TaskName, State, Actions, Triggers
Get-ScheduledTaskInfo -TaskName "Rune Daily Briefing" |
  Select-Object LastRunTime, LastTaskResult, NextRunTime
```

Windows Task Scheduler interprets `9:30AM` in the machine's local timezone.
The `StartWhenAvailable` setting runs a missed briefing after sleep or shutdown.
The dashboard server is a second recovery path: on boot and every minute it
checks the authoritative 09:30 cycle, launches one external catch-up process if
needed, and honors the durable retry timestamp after failure.

For cron-based hosts, `loop.sh` is the equivalent wrapper:

```cron
30 9 * * * /absolute/path/to/agentic_os/loop.sh
```

## Dashboard controls

The first generation button posts yesterday, selected model, and selected
effort to `/api/briefing/generate`. Once a primary batch exists it becomes
**Generate 3 more**. The request queues an in-process job; polling
`GET /api/briefing` reads status and the last good snapshot without rescanning
repositories or calling a model.

The freshness strip shows the saved plan day, last attempt, next scheduled
refresh or retry, and whether automatic catch-up is active. **Check now** is a
GET-only status refresh and never starts a model run.

**View more** reveals each priority's CEO plan and planned agents. Changing an
agent's model or effort posts to `/api/briefing/agent` and changes plan metadata
only. Supported agent models are Haiku, Sonnet, Opus, Fable 5, and GPT-5.6 Sol.

The solid **Run this plan** action is the protected default. It starts the CEO
harness with native permission handling. The outlined **Run · skip permissions**
action confirms every launch, bypasses permission prompts for that run only,
and executes the saved role cards directly. Claude cards use
`--dangerously-skip-permissions`; GPT-5.6 Sol cards use Codex `--yolo`. Provider,
model, repository, and argv are resolved from the stored briefing on the server,
not trusted from browser input. These workers have no console window; monitor or
stop them in Mission Activity. Automatic recovery is always contained and does
not inherit permission bypass.

## Failure behavior

- A file lock rejects overlapping generations.
- Scheduled attempts are recorded in `state/briefing-status.json`. A failed
  attempt retains the last good plan and becomes eligible for retry 15 minutes
  after that attempt finishes.
- Invalid structured output is retried once, then reported without overwriting
  the prior briefing.
- A failed Microsoft refresh retains the last good per-service cache and marks
  it stale.
- Repository evidence is untrusted prompt data; sensitive-looking paths are
  omitted and raw evidence is not persisted.
- The dashboard server is loopback-only, rejects cross-origin POSTs, caps JSON
  request bodies, and blocks private state from static serving.
