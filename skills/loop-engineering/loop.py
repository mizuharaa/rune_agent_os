#!/usr/bin/env python3
"""Critic->doer loop: run DOER, then the GOAL predicate (exit 0 = goal met),
until it passes or the iteration budget is spent. Every iteration hits the wire.

Usage:
  python skills/loop-engineering/loop.py --doer "CMD" --goal "CMD" [--max N] [--label TEXT]

Exit 0 = goal met; exit 1 = budget exhausted (the loop tells you the last critic output).
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIRROR = os.path.join(ROOT, ".claude", "hooks", "mirror.py")


def emit(detail):
    subprocess.run([sys.executable, MIRROR, "--event", "loop", "--detail", detail],
                   capture_output=True)


def main(argv):
    args = {"--max": "5", "--label": "loop"}
    i = 0
    while i < len(argv):
        args[argv[i]] = argv[i + 1]
        i += 2
    doer, goal = args.get("--doer"), args.get("--goal")
    if not doer or not goal:
        print(__doc__)
        return 1
    budget = int(args["--max"])
    label = args["--label"]
    for it in range(1, budget + 1):
        d = subprocess.run(doer, shell=True, capture_output=True, text=True)
        c = subprocess.run(goal, shell=True, capture_output=True, text=True)
        met = c.returncode == 0
        line = "%s iter %d/%d: doer exit=%d, goal %s" % (
            label, it, budget, d.returncode, "MET" if met else "not met")
        print(line)
        emit(line)
        if met:
            emit("%s PASS in %d iteration(s)" % (label, it))
            print("%s: goal predicate satisfied." % label)
            return 0
    emit("%s FAIL: budget %d exhausted" % (label, budget))
    print("%s: budget exhausted. Last critic output:\n%s" % (label, (c.stdout + c.stderr).strip()[:500]))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
