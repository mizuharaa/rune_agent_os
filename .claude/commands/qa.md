---
description: QA lead drives the real product, breaks it, fixes it, leaves a regression check
---

First, emit the stage:
`python .claude/hooks/mirror.py --stage test --detail "qa: $ARGUMENTS"`

Spawn the **qa-lead** agent (Task tool, subagent_type: qa-lead) against the real
running surface (start the server/app first if needed). Collect: PATHS DRIVEN /
BUGS (fixed vs [ASK]) / REGRESSION CHECK path.

- No regression check in the report → the QA pass is incomplete; send it back.
- If the change touches input handling, secrets, network, or the guard, also
  spawn **security-officer**; a BLOCKED verdict stops /ship, full stop.

Close the loop:
`python .claude/hooks/mirror.py --event test --detail "qa done: <summary>"`
