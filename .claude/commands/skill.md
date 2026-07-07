---
description: Skill registry operations (usage - /skill review | /skill use <name> | /skill new <name> <desc>)
---

Arguments: $ARGUMENTS

- **review** (weekly cadence) → `python skills/engine.py review`, then walk the
  output with Daniel: which learning skills earned their third use? which decayed
  skills should be linked instead of archived? Apply with
  `python skills/engine.py prune` only after he agrees.
- **use <name>** → `python skills/engine.py use <name>` (record a REAL use only).
- **new <name> <desc>** → follow skills/skill-creation/SKILL.md
  (`python skills/engine.py add ...`), then fill the scaffolded card.
- bare → `python skills/engine.py list`

Emit after any change:
`python .claude/hooks/mirror.py --event skill --detail "skill: $ARGUMENTS"`
