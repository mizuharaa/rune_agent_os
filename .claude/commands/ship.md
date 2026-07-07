---
description: Release engineer ships the reviewed change; ends with the mandatory reflect step
---

First, emit the stage:
`python .claude/hooks/mirror.py --stage ship --detail "ship: $ARGUMENTS"`

1. Preconditions: /review and /qa both completed for this change (check the wire:
   recent `review` and `test` events). Missing → refuse and say which.
2. Spawn the **release-engineer** agent (Task tool, subagent_type: release-engineer).
   Gated actions (force-push, deploy, release) will hit the guard — if blocked,
   ask Daniel, then `python .claude/hooks/approve.py <action> --minutes 15`.
3. Collect: WHAT SHIPPED (hash/URL) / GATED ACTIONS.

**Mandatory reflect step — /ship is NOT done without it:**
Distill the hardest problem this mission solved and note it:
`python hermes/hermes.py note "<problem>" "<solution>" --tags <tags> --source "ship:<mission>"`

Close the loop:
`python .claude/hooks/mirror.py --event ship --detail "shipped: <hash/URL> + reflected"`
