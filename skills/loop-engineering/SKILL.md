# loop-engineering

Critic→doer loop: iterate work toward a **checkable** goal predicate under a
max-iteration budget. The critic is a command that exits 0 when the goal is met
— never a vibe.

- **Trigger:** /loop-engineer
- **Command:** `python skills/loop-engineering/loop.py --doer "CMD" --goal "CMD" [--max N] [--label TEXT]`
- **Earn:** `python skills/engine.py use loop-engineering` after each real loop

## Process

1. **Write the critic first.** A shell command that exits 0 iff done
   (a test, a grep, an HTTP check). If you can't write the critic, the goal
   isn't defined yet — stop and define it.
2. **Set the budget** (`--max`, default 5). The budget is a feature: hitting it
   means the approach is wrong, not that you need more iterations.
3. **Run the loop.** Each iteration runs the doer, then the critic, and emits
   an event to the wire — watch it live on the dashboard.
4. Exit 0 = goal met. Exit 1 = budget spent → change the doer (or the framing),
   don't just raise the budget.
5. To iterate an *agent* rather than a command: make the doer
   `claude -p "<task>" ...` and keep the same critic.
