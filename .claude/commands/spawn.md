---
description: Assess the situation, hire the MINIMUM roster, collect reports, close the loop
---

Mission: $ARGUMENTS

First, emit: `python .claude/hooks/mirror.py --stage plan --detail "spawn assessment: $ARGUMENTS"`

You are the conductor. Least-privilege, minimum roster, closed loops:

1. **Assess** — can you do this solo? If yes, do it solo; a roster of zero is the
   best roster. Balanced spend: hire only for genuinely parallel or specialist work.
2. **Hire the minimum.** Roster (spawn via Task tool, one per line of need):
   ceo (framing) / eng-manager (architecture) / designer (slop) / reviewer (bugs)
   / qa-lead (real-browser QA) / security-officer (gate) / release-engineer (ship)
   / doc-engineer (docs) / hermes (prior art). Justify each hire in one line.
   One agent is the common case. Never "stand up the whole office."
   **Spend consciously — least privilege covers model + tokens, not just tools.**
   For each hire, pick the SMALLEST model that can do the job and pass it on the
   Task call (do not just inherit by reflex):
   - `haiku` — mechanical/lookup work: hermes queries, doc formatting, log audits.
   - `sonnet` — most build, review, and QA work (the default workhorse).
   - `opus` — only genuinely hard reasoning: CEO reframes, architecture, security.
   Set a turn/output budget to match (a tight budget is a feature, not a limit) and
   keep tools read-only unless writing IS the job. State the choice: "hire reviewer
   on sonnet, ~15 turns, read+edit — bug hunt only."
3. **Collect** every report before proceeding. An agent with no report is an
   open loop — chase it or declare it failed.
4. **Close** — act on the reports, then emit:
   `python .claude/hooks/mirror.py --event agent-exit --detail "<agent>: <one-line outcome>"`
5. Agents exit when done. Never keep one alive "in case".
