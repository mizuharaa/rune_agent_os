---
description: CEO challenges the mission's scope and framing before any code
---

First, emit the stage:
`python .claude/hooks/mirror.py --stage plan --detail "ceo-review: $ARGUMENTS"`

Spawn the **ceo** agent (Task tool, subagent_type: ceo) with the current mission
and plan. Collect its report: MISSION / CUT / RISK / VERDICT.

- VERDICT = reframe → adjust the mission per its report, tell Daniel what changed and why, then re-run this command once. Do not silently ignore a reframe.
- VERDICT = go → proceed to /plan-eng-review.

Close the loop: summarize the verdict in one line on the wire:
`python .claude/hooks/mirror.py --event review --detail "ceo verdict: <go|reframe>"`
