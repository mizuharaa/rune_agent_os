---
description: Mission kickoff — frame the problem, check Hermes, decide the minimum roster
---

First, emit the stage:
`python .claude/hooks/mirror.py --stage think --detail "office-hours: $ARGUMENTS"`

Then, as the conductor:
1. Restate the mission ($ARGUMENTS) in one sentence. If the framing looks wrong, push back NOW — before any work.
2. `python hermes/hermes.py query "$ARGUMENTS"` — has this (or part of it) been solved before? Reuse beats rebuild.
3. `python skills/engine.py list` — which earned skills apply?
4. Decide the MINIMUM roster for this mission (often: none — you alone). Name who you'd hire and why each is necessary. Do not spawn yet.
5. Output: MISSION / PRIOR ART (hermes hits) / SKILLS IN PLAY / ROSTER (with justification) / NEXT (usually /plan-ceo-review for non-trivial missions, straight to build for small ones).
