---
name: recovery-supervisor
description: Diagnose failed, stalled, exhausted, or permission-blocked agent tasks and recover them with preserved context, bounded retries, explicit approval gates, and verified feedback. Use for /recover or whenever an agent run stops before completing.
---

# Recovery Supervisor

Runtime implementation: `dashboard/runtime.py` owns classification, backoff,
process-tree termination, and the protected recovery prompt.
`dashboard/ceo.py` persists mission and role state in `state/ceo/<cid>.json`,
including `attempts`, `recovery_history`, `next_action`, and resumable sessions.

1. Inspect the persisted mission, role status, latest event evidence, and any saved session before changing state.
2. Map failure classes explicitly: `success` needs no recovery; `transient` and `transient_limit` receive bounded backoff; `exhausted` resumes a saved session; `permission` becomes `waiting_permission`; `task` is then judged repairable or permanent. `stopped` is a separate operator-requested orchestration state, not a failure class.
3. Preserve completed roles and resumable session context. Never restart an entire mission when a smaller continuation can finish it.
4. Ask for approval when recovery needs new permission, destructive work, external communication, or broader scope.
5. Give the worker one concrete diagnosis and one checkable next action. Retry repairable work at most twice; use backoff for transient limits.
6. Stop retrying when the same condition repeats, the safety boundary is unclear, or verification cannot distinguish success from failure.
7. Verify the original definition of done, persist the final status and reason, and surface unresolved work plainly.
8. Record a compact, secret-safe Hermes learning only when a successful, verified recovery reveals a reusable root cause or recipe. A memory-write failure must not change the mission result.

Never hide a failed attempt, silently bypass a gate, or execute an unrelated cleanup during recovery.
