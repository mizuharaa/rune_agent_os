---
description: Show or set the current goal; skills not serving it decay at prune
---

No arguments → show: `python skills/engine.py goal`

With arguments → set the new goal:
1. `python skills/engine.py goal $ARGUMENTS`
2. Link every skill that genuinely serves the new goal:
   `python skills/engine.py link <name>` (be honest — linking everything defeats pruning).
3. Preview the fallout: `python skills/engine.py review`
4. Emit: `python .claude/hooks/mirror.py --event goal --detail "goal: $ARGUMENTS"`
