# workflow-audit

Read the event wire, find what I keep doing by hand, flag it for automation.

Choose this legacy audit for a compact event-type and repeat summary. Use
`workflow-coach` when you need ranked suggestions, bounded sequences,
failure/recovery evidence, confidence, or structured JSON.

- **Trigger:** /audit ("audit my workflow", "what am I repeating")
- **Command:** `python skills/workflow-audit/audit.py`
- **Earn:** `python skills/engine.py use workflow-audit` after each real use

## Process

1. Run the audit command — it summarizes `state/events.jsonl` by event type,
   session, and repeated command verbs (≥3 repeats = automation candidate).
2. For each candidate, decide: automate (see `skills/automation`), skill-ify
   (see `skills/skill-creation`), or consciously keep manual.
3. Log the decision on the wire:
   `python .claude/hooks/mirror.py --event audit --detail "<decision>"`
