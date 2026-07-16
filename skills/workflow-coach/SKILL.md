---
name: workflow-coach
description: Produce ranked, evidence-backed, opt-in automation suggestions from Rune event JSONL, including repeated actions, bounded sequences, and recovery patterns, without executing them. Use for /workflow-coach or a forward-looking automation opportunity scan; use workflow-audit only for its legacy summary.
---

# Workflow Coach

1. Run `python skills/workflow-coach/scripts/analyze.py` from the repository root to inspect the default event wire, or pass another readable JSONL path for an isolated fixture.
2. Add `--json` when a caller needs stable structured suggestions and supporting evidence.
3. Review only candidates observed at least three times. The CLI rejects lower thresholds, redacts credential-shaped evidence, and reports malformed input counts. Ignore directory navigation and listing noise.
4. Check the normalized family, affected sessions, and sample evidence before recommending a script, hook, skill, or deliberate manual checkpoint.
5. Treat failure/recovery correlations as leads, not proof; confirm the cause before changing retry behavior.
6. Present the smallest reversible improvement and ask before implementing it.

Never execute, schedule, or install a suggestion automatically.
