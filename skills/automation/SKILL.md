# automation

Turn a repeated workflow into a hook, script, or schedule so it never needs
willpower again. Systems > willpower.

- **Trigger:** /automate
- **Earn:** `python skills/engine.py use automation` after each shipped automation

## Process

1. **Prove the repeat** — `python skills/workflow-audit/audit.py` must show the
   pattern ≥3 times. Don't automate a guess.
2. **Pick the mechanism** (smallest that works):
   - reacts to a tool event → hook in `.claude/settings.json` (see `.claude/hooks/`)
   - runnable on demand → script + a skill card + trigger verb
   - time-based → OS scheduler calling the script
3. **Gate it** — if the automation deletes, deploys, sends, or spends, it goes
   through the guard like everything else. No automation bypasses `guard.py`.
4. **Wire it** — the automation must emit to `state/events.jsonl` via
   `python .claude/hooks/mirror.py --event automation --detail "..."` or it's invisible.
5. Record the use; after 3, it's earned.
