---
description: Reviewer hunts production bugs in the current change
---

First, emit the stage:
`python .claude/hooks/mirror.py --stage review --detail "review: $ARGUMENTS"`

Spawn the **reviewer** agent (Task tool, subagent_type: reviewer) on the current
change. Collect its tagged findings:

- **[AUTO-FIXED]** — verify each fix's pasted output actually proves the fix.
- **[ASK]** — surface these to Daniel verbatim; do not decide for him.

If anything user-facing changed, also spawn **designer** for a slop pass (one
spawn, only when there's a surface to critique).

Close the loop:
`python .claude/hooks/mirror.py --event review --detail "review done: N fixed, M ask"`
