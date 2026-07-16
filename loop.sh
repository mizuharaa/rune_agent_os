#!/usr/bin/env bash
# Rune daily briefing wrapper.
#
# Schedule at 09:30 local time. The Python generator owns idempotency, locking,
# validation, and last-good retention, so this wrapper deliberately does nothing
# else (no legacy review/grading/orchestration side effects):
#
#   30 9 * * * /absolute/path/to/agentic_os/loop.sh
#
# Windows Task Scheduler should run briefing.cmd daily at 09:30 instead.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
export PYTHONUNBUFFERED=1
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python

exec "$PYTHON" "$ROOT/daily_briefing.py" scheduled "$@"
