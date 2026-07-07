---
description: Query or feed the knowledge flywheel (usage - /hermes query <text> | /hermes note <problem> :: <solution>)
---

Arguments: $ARGUMENTS

- Starts with **query** → `python hermes/hermes.py query "<rest>"`. Try 2-3
  phrasings before declaring a miss. On a hit, USE the prior solution.
- Starts with **note** → split the rest on `::` into problem/solution, then
  `python hermes/hermes.py note "<problem>" "<solution>" --tags <infer> --source "conductor"`.
  Confirm both the jsonl line and the Obsidian card path in your reply.
- Neither → treat the whole argument as a query (the common case: "have we
  solved this before?").

Every hard problem gets queried BEFORE and noted AFTER. No exceptions — the
flywheel only spins if fed.
