---
description: Eng-manager locks architecture, state machines, and the test matrix
---

First, emit the stage:
`python .claude/hooks/mirror.py --stage plan --detail "eng-review: $ARGUMENTS"`

Spawn the **eng-manager** agent (Task tool, subagent_type: eng-manager) with the
CEO-approved mission. Collect: ASCII architecture / state machine(s) / test
matrix / the one integration point most likely to break.

The test matrix is the contract — every row must name a runnable command. Build
does not start until you can run every verification the matrix names.

Close the loop:
`python .claude/hooks/mirror.py --event review --detail "eng plan locked"`
Then proceed to build (emit `--stage build` when you start).
