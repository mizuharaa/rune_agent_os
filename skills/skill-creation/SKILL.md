# skill-creation

Name it, drop it, ship it, reuse it. A capability becomes a skill the moment it
has a name, a trigger verb, and a card.

- **Trigger:** /newskill
- **Command:** `python skills/engine.py add <name> "<one-line desc>" --branch <branch> --trigger /<verb>`
- **Earn:** skills start as *candidate*, become *learning* on first use, and are
  **EARNED (active) after 3 uses** — `python skills/engine.py use <name>` each time.

## Process

1. **Name it** — a verb-shaped name (`vault-gardening`, not `misc-helper-2`).
2. **Drop it** — run the add command; it scaffolds `skills/<name>/SKILL.md`.
3. **Ship it** — fill the card's Process section with the exact commands that
   worked, while the solution is fresh. Link it: `python skills/engine.py link <name>`.
4. **Reuse it** — record every real use. Three uses = earned. Unused vs the
   current goal = decays at prune (2 strikes → archived).
