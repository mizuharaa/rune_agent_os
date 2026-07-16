#!/usr/bin/env python3
"""Offline regression tests for the read-only workflow coach."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(ROOT, "skills", "workflow-coach", "scripts", "analyze.py")
SPEC = importlib.util.spec_from_file_location("workflow_coach", SCRIPT)
COACH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COACH)


def tool(session, detail, ts="2026-07-15T10:00:00"):
    return {"session": session, "event": "tool", "tool": "Bash",
            "detail": detail, "ts": ts}


class WorkflowCoachTests(unittest.TestCase):
    def test_filters_navigation_noise_and_normalizes_paths_and_ids(self):
        rows = [
            tool("a1", 'cd "/tmp/repo-a" && ls -la'),
            tool("a1", "pwd"),
            tool("a1", 'cd "/tmp/repo-a" && python -m pytest tests/test_a.py::test_deadbeef'),
            tool("b2", 'cd "C:\\work\\repo-b" && python -m pytest tests/test_b.py::test_01234567'),
            tool("c3", "python -m pytest /srv/repo-c/tests/test_c.py::test_89abcdef"),
            tool("d4", '"../../.venv/Scripts/python.exe" -m pytest tests/test_d.py::test_feedface'),
        ]
        report = COACH.analyze_rows(rows)
        families = {item["family"]: item["count"] for item in report["families"]}
        self.assertEqual(families, {"run pytest": 4})
        self.assertEqual(report["stats"]["actionable_events"], 4)
        evidence = report["suggestions"][0]["evidence"]
        self.assertTrue(all("deadbeef" not in item["detail"] for item in evidence))

    def test_repeat_threshold_requires_three_observations(self):
        rows = [tool("a", "git status --short"), tool("b", "git status --short")]
        self.assertFalse(COACH.analyze_rows(rows)["suggestions"])
        rows.append(tool("c", "git status --short"))
        suggestions = COACH.analyze_rows(rows)["suggestions"]
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["family"], "git status")
        self.assertEqual(suggestions[0]["count"], 3)

    def test_json_cli_is_structured_and_never_changes_input(self):
        rows = []
        for session in ("one", "two", "three"):
            rows.extend([tool(session, "git status --short"),
                         tool(session, "python -m pytest -q")])
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(json.dumps(row) for row in rows) + "\n")
            with open(path, "rb") as handle:
                before = handle.read()
            run = subprocess.run([sys.executable, SCRIPT, path, "--json"],
                                 capture_output=True, text=True, check=True)
            report = json.loads(run.stdout)
            with open(path, "rb") as handle:
                after = handle.read()
        self.assertEqual(before, after)
        self.assertFalse(report["executed"])
        self.assertTrue(report["advisory_only"])
        self.assertTrue(report["review_required"])
        self.assertEqual(report["threshold"], 3)
        self.assertTrue(any(item["kind"] == "repeated-sequence"
                            for item in report["suggestions"]))
        self.assertTrue(all(item["evidence"] for item in report["suggestions"]))
        self.assertTrue(all(item["confidence"] in {"low", "medium", "high"}
                            for item in report["suggestions"]))
        self.assertTrue(all(item["review_required"] and item["next_action"]
                            for item in report["suggestions"]))

    def test_repeated_failure_emits_permission_recovery_suggestion(self):
        rows = []
        for index, session in enumerate(("aaaaaaa1", "bbbbbbb2", "ccccccc3")):
            rows.extend([
                {"session": session, "event": "ceo",
                 "detail": "role worker FAILED: permission denied for tool",
                 "ts": "2026-07-15T10:0%d:00" % index},
                {"session": session, "event": "ceo-action",
                 "detail": "resume approved", "ts": "2026-07-15T10:0%d:10" % index},
                {"session": session, "event": "ceo",
                 "detail": "role worker done", "ts": "2026-07-15T10:0%d:20" % index},
            ])
        suggestions = COACH.analyze_rows(rows)["suggestions"]
        recovery = next(item for item in suggestions if item["kind"] == "failure-recovery")
        self.assertEqual(recovery["family"], "failure: permission")
        self.assertEqual(recovery["count"], 3)
        self.assertEqual(recovery["recovered_count"], 3)
        self.assertIn("operator approval", recovery["suggestion"])

    def test_redacts_credentials_from_evidence(self):
        secret = "sk-live-SECRET123"
        rows = [tool(session, 'curl -H "Authorization: Bearer %s" https://example.test' %
                     secret) for session in ("one", "two", "three")]
        report = COACH.analyze_rows(rows)
        evidence = json.dumps(report["suggestions"], sort_keys=True)
        self.assertNotIn(secret, evidence)
        self.assertIn("<redacted>", evidence)

    def test_threshold_floor_and_input_health(self):
        rows = [tool("a", "git status --short")]
        self.assertEqual(COACH.analyze_rows(rows, threshold=1)["threshold"], 3)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(rows[0]) + "\n")
                handle.write("{malformed}\n")
                handle.write("[]\n")
            report = COACH.analyze_path(path)
        self.assertEqual(report["input_health"],
                         {"lines": 3, "malformed_lines": 1, "non_object_lines": 1})

    def test_missing_input_and_low_cli_threshold_fail(self):
        missing = os.path.join(ROOT, "state", "definitely-missing-events.jsonl")
        run = subprocess.run([sys.executable, SCRIPT, missing, "--json"],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertIn("cannot read", run.stderr)
        low = subprocess.run([sys.executable, SCRIPT, "--threshold", "1"],
                             capture_output=True, text=True)
        self.assertNotEqual(low.returncode, 0)
        self.assertIn("at least 3", low.stderr)


if __name__ == "__main__":
    unittest.main()
