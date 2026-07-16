#!/usr/bin/env python3
"""Compatibility entrypoint for the richer, read-only workflow coach."""

import importlib.util
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVENTS = os.path.join(ROOT, "state", "events.jsonl")
COACH = os.path.join(ROOT, "skills", "workflow-coach", "scripts", "analyze.py")


def _coach():
    spec = importlib.util.spec_from_file_location("rune_workflow_coach", COACH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    coach = _coach()
    coach.print_audit_report(coach.analyze_path(EVENTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
