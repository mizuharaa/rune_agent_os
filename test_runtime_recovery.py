#!/usr/bin/env python3
"""Focused, offline regression tests for shared task recovery.

No API key, Claude process, browser, or repository mutation is required. Run:

    python test_runtime_recovery.py
"""
import json
import os
import signal
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))
os.environ["RUNE_DISABLE_BOOT_RECOVERY"] = "1"

import runtime as agent_runtime
import ceo
import orchestrator


class InlineThread:
    """Thread stand-in that makes resume tests deterministic."""
    def __init__(self, target=None, args=(), daemon=None, name=None):
        self.target, self.args = target, args
        self.started = False

    def start(self):
        self.started = True
        self.target(*self.args)

    def is_alive(self):
        return False


def role(**patch):
    value = {
        "id": "eng", "title": "Engineer", "mission": "Fix the local unit-test bug.",
        "model": "haiku", "turns": 10, "depends_on": [], "review": False,
        "status": "pending", "result": "", "secs": 0, "cost": 0,
    }
    value.update(patch)
    return value


def mission(cid="m1", roles=None, **patch):
    value = {
        "cid": cid, "name": "recovery test", "summary": "", "goal": "fix local test",
        "refined": "fix local test", "keywords": "local test", "recall": False,
        "roles": [role()] if roles is None else roles, "route": "delegate",
        "opts": {}, "account_pref": "auto", "status": "running", "cost": 0,
        "auto_recover": True, "planning_attempt": 0, "planning_history": [],
        "started": "2026-07-15T10:00:00",
    }
    value.update(patch)
    return value


class CeoRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rune-recovery-test-")
        self.old_cdir, self.old_adir = ceo.CDIR, ceo.ADIR
        ceo.CDIR = self.temp.name
        ceo.ADIR = os.path.join(self.temp.name, "archive")
        ceo.LIVE.clear()
        self.patches = [
            mock.patch.object(ceo, "emit", lambda *a, **kw: None),
            mock.patch.object(ceo, "_wait_retry", lambda *a, **kw: True),
            mock.patch.object(ceo.pulse, "least_used", lambda: ""),
            mock.patch.object(ceo.pulse, "dir_for", lambda _name: ""),
            mock.patch.object(ceo.subprocess, "run", lambda *a, **kw: types.SimpleNamespace(
                returncode=0, stdout="", stderr="")),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        ceo.LIVE.clear()
        ceo.CDIR, ceo.ADIR = self.old_cdir, self.old_adir
        self.temp.cleanup()

    def save(self, value):
        ceo._save(value)
        return value

    def load(self, cid="m1"):
        path = ceo._path(cid)
        if not os.path.isfile(path):
            path = os.path.join(ceo.ADIR, cid + ".json")
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def make_live(self, cid="m1"):
        ceo.LIVE[cid] = {"thread": threading.current_thread(), "proc": None,
                         "stop": False, "gate": {}}

    def test_planner_retries_then_starts_roles(self):
        self.save(mission(roles=[], status="planning"))
        plan = {
            "name": "fixed plan", "summary": "one safe role",
            "roles": [{"id": "eng", "title": "Engineer", "mission": "local fix",
                       "model": "haiku", "turns": 10, "depends_on": [],
                       "review": False}],
        }
        calls, ran = [], []

        def fake_api(*_args, **_kwargs):
            calls.append(1)
            return {"error": "API 503 service unavailable"} if len(calls) == 1 else plan

        with mock.patch.object(ceo, "_api", fake_api), \
                mock.patch.object(ceo, "_recall", lambda _text: ""), \
                mock.patch.object(ceo, "_run", lambda cid: ran.append(cid)):
            ceo._plan_then_run("m1")

        got = self.load()
        self.assertEqual(len(calls), 2)
        self.assertEqual([x["status"] for x in got["planning_history"]],
                         ["failed", "done"])
        self.assertEqual(got["status"], "running")
        self.assertEqual(got["roles"][0]["status"], "pending")
        self.assertEqual(ran, ["m1"])

    def test_planner_permission_failure_pauses_without_retry(self):
        self.save(mission(roles=[], status="planning"))
        calls = []

        def denied(*_args, **_kwargs):
            calls.append(1)
            return {"error": "credentials required: missing API key"}

        with mock.patch.object(ceo, "_api", denied), \
                mock.patch.object(ceo, "_recall", lambda _text: ""):
            ceo._plan_then_run("m1")

        got = self.load()
        self.assertEqual(len(calls), 1)
        self.assertEqual(got["status"], "waiting_permission")
        self.assertEqual(got["planning_history"][0]["classification"], "permission")
        self.assertIn("credential", got["next_action"].lower())

    def test_malformed_role_list_is_retried_safely(self):
        self.save(mission(roles=[], status="planning"))
        plan = {
            "name": "fixed plan", "summary": "one safe role",
            "roles": [{"id": "eng", "title": "Engineer", "mission": "local fix",
                       "model": "haiku", "turns": 10, "depends_on": [],
                       "review": False}],
        }
        replies = iter([{"name": "bad", "summary": "bad", "roles": "eng"}, plan])
        ran = []
        with mock.patch.object(ceo, "_api", lambda *_a, **_kw: next(replies)), \
                mock.patch.object(ceo, "_recall", lambda _text: ""), \
                mock.patch.object(ceo, "_run", lambda cid: ran.append(cid)):
            ceo._plan_then_run("m1")

        got = self.load()
        self.assertEqual(len(got["planning_history"]), 2)
        self.assertIn("malformed roles", got["planning_history"][0]["detail"])
        self.assertEqual(got["status"], "running")
        self.assertEqual(ran, ["m1"])

    def test_empty_role_planning_failure_can_resume(self):
        self.save(mission(roles=[], status="error", detail="planner unavailable"))
        calls = []
        with mock.patch.object(ceo.threading, "Thread", InlineThread), \
                mock.patch.object(ceo, "_plan_then_run", lambda cid: calls.append(cid)):
            self.assertIsNone(ceo.resume("m1"))
        got = self.load()
        self.assertEqual(calls, ["m1"])
        self.assertEqual(got["status"], "planning")
        self.assertEqual(got["resumes"], 1)

    def test_task_failure_runs_fixer_then_original_verifies(self):
        self.save(mission())
        self.make_live()
        replies = iter([
            {"is_error": True, "result": "unit test assertion failed"},
            {"is_error": False, "result": "RECOVERY REPORT: fixed local fixture; focused test passed"},
            {"is_error": False, "result": "original role complete; focused test passed"},
        ])
        with mock.patch.object(ceo, "_worker", lambda *a, **kw: next(replies)):
            ceo._run("m1")
        got = self.load()
        item = got["roles"][0]
        self.assertEqual(got["status"], "done")
        self.assertEqual(item["status"], "done")
        self.assertEqual(len(item["attempts"]), 2)
        self.assertEqual(len(item["recovery_history"]), 1)
        self.assertEqual(item["recovery_history"][0]["verification"],
                         "passed-original-rerun")
        self.assertEqual(item["recovery_history"][0]["failure_class"], "task")
        self.assertEqual(item["recovery_history"][0]["repair_class"], "success")
        self.assertTrue(item["recovery_history"][0]["learnable"])
        self.assertIn("verification=passed-original-rerun", item["recovery_summary"])
        self.assertFalse(os.path.exists(ceo._path("m1")))
        self.assertTrue(os.path.isfile(os.path.join(ceo.ADIR, "m1.json")))
        self.assertTrue(got.get("finished_at"))
        history = ceo.list_history()
        self.assertEqual([entry["cid"] for entry in history], ["m1"])
        self.assertTrue(history[0]["archived"])

    def test_task_fixer_is_capped_at_two_cycles(self):
        self.save(mission())
        self.make_live()
        replies = iter([
            {"is_error": True, "result": "unit test assertion failed one"},
            {"is_error": False, "result": "RECOVERY REPORT: adjusted fixture one"},
            {"is_error": True, "result": "unit test assertion failed two"},
            {"is_error": False, "result": "RECOVERY REPORT: adjusted fixture two"},
            {"is_error": True, "result": "unit test assertion failed three"},
        ])
        with mock.patch.object(ceo, "_worker", lambda *a, **kw: next(replies)):
            ceo._run("m1")
        got = self.load()
        item = got["roles"][0]
        self.assertEqual(got["status"], "failed")
        self.assertEqual(item["status"], "failed")
        self.assertEqual(len(item["recovery_history"]), 2)
        self.assertEqual(len(item["attempts"]), 3)
        self.assertIn("recovery budget exhausted", item["detail"])

    def test_transient_retry_budget_is_capped(self):
        self.save(mission())
        self.make_live()
        calls = []

        def failed(*_args, **_kwargs):
            calls.append(1)
            return {"is_error": True, "result": "connection reset by peer"}

        with mock.patch.object(ceo, "_worker", failed):
            ceo._run("m1")
        got = self.load()
        item = got["roles"][0]
        self.assertEqual(len(calls), 1 + ceo.MAX_TRANSIENT_RETRIES)
        self.assertEqual(len(item["attempts"]), 1 + ceo.MAX_TRANSIENT_RETRIES)
        self.assertFalse(item.get("recovery_history"))
        self.assertEqual(item["status"], "failed")
        self.assertIn("transient retry budget exhausted", item["detail"])

    def test_permission_failure_never_launches_fixer(self):
        self.save(mission())
        self.make_live()
        calls = []

        def denied(*_args, **_kwargs):
            calls.append(1)
            return {"is_error": True,
                    "result": "MAESTRO GUARD: blocked gated action 'deploy' — requires approval"}

        with mock.patch.object(ceo, "_worker", denied):
            ceo._run("m1")
        got = self.load()
        self.assertEqual(len(calls), 1)
        self.assertEqual(got["status"], "waiting_permission")
        self.assertEqual(got["roles"][0]["status"], "waiting_permission")
        self.assertFalse(got["roles"][0].get("recovery_history"))

    def test_recoverable_states_never_auto_archive(self):
        states = ("failed", "stopped", "exhausted", "waiting_permission")
        for index, status in enumerate(states):
            cid = "keep%d" % index
            self.save(mission(cid=cid, status=status,
                              roles=[role(status=status)]))

        with mock.patch.object(ceo, "_age_days", lambda _run: 999):
            listed = ceo.list_all()

        self.assertEqual({run["cid"] for run in listed},
                         {"keep0", "keep1", "keep2", "keep3"})
        self.assertFalse(os.path.isdir(ceo.ADIR))

    def test_manual_archive_is_idempotent_and_history_is_bounded(self):
        first = self.save(mission(cid="old", status="failed"))
        first["finished_at"] = "2026-07-15T10:00:00"
        ceo._save(first)
        recent = self.save(mission(cid="recent", status="done",
                                   roles=[role(status="done")]))
        recent["finished_at"] = "2026-07-16T10:00:00"
        ceo._save(recent)

        self.assertIsNone(ceo.archive("old"))
        self.assertIsNone(ceo.archive("old"))
        history = ceo.list_history(limit=1)
        self.assertEqual([item["cid"] for item in history], ["recent"])
        self.assertFalse(history[0]["archived"])
        self.assertEqual(len(ceo.list_history(limit=ceo.HISTORY_MAX + 500)), 2)

    def test_stop_wins_when_worker_returns_after_cancellation(self):
        self.save(mission())
        self.make_live()

        def late_success(*_args, **_kwargs):
            self.assertIsNone(ceo.action("m1", "eng", "stop"))
            return {"is_error": False, "result": "finished just after cancellation"}

        with mock.patch.object(ceo, "_worker", late_success):
            ceo._run("m1")
        got = self.load()
        self.assertEqual(got["status"], "stopped")
        self.assertEqual(got["roles"][0]["status"], "stopped")

    def test_only_verified_nontrivial_recovery_is_learnable(self):
        candidate = mission(status="done")
        candidate["roles"][0].update(status="done", recovery_history=[{
            "cycle": 1, "repair_class": "success",
            "repair_summary": "fixed it", "verification": "passed-original-rerun",
            "learnable": False,
        }])
        self.assertFalse(ceo._worth_remembering(candidate))
        candidate["roles"][0]["recovery_history"][0].update(
            repair_summary="Corrected stale fixture ordering and verified the focused restart test.",
            learnable=True)
        self.assertTrue(ceo._worth_remembering(candidate))


class OrchestratorRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rune-orch-test-")
        self.old_odir = orchestrator.ODIR
        orchestrator.ODIR = self.temp.name
        orchestrator.LIVE.clear()
        self.patches = [
            mock.patch.object(orchestrator, "emit", lambda *a, **kw: None),
            mock.patch.object(orchestrator.pulse, "least_used", lambda: ""),
            mock.patch.object(orchestrator.pulse, "dir_for", lambda _name: ""),
            mock.patch.object(orchestrator.agent_runtime, "wait_backoff",
                              lambda *a, **kw: True),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        orchestrator.LIVE.clear()
        orchestrator.ODIR = self.old_odir
        self.temp.cleanup()

    @staticmethod
    def loop(oid="o1"):
        return {
            "oid": oid, "name": "loop", "mission": "fix the local test",
            "dir": "", "model": "default", "critic": "opus", "account": "",
            "turns": 10, "rounds": 1, "auto": True, "skip": False,
            "status": "running", "round": 0, "cost": 0, "turns_log": [],
            "detail": "", "next_action": "", "session_id": None,
            "started": "2026-07-15T10:00:00",
        }

    def test_worker_transient_retries_are_bounded(self):
        value = self.loop()
        orchestrator._save(value)
        orchestrator.LIVE["o1"] = {
            "thread": threading.current_thread(), "proc": None,
            "stop": False, "human": None}
        calls = []

        def failed():
            calls.append(1)
            return {"is_error": True, "result": "503 service unavailable"}

        got = orchestrator._call_with_transient_retries("o1", value, "worker", failed)
        self.assertEqual(len(calls), 1 + orchestrator.MAX_TRANSIENT_RETRIES)
        self.assertEqual(got["classification"], "transient")
        self.assertEqual(got["retry_count"], orchestrator.MAX_TRANSIENT_RETRIES)

    def test_late_worker_completion_cannot_overwrite_stop(self):
        value = self.loop()
        orchestrator._save(value)
        orchestrator.LIVE["o1"] = {
            "thread": threading.current_thread(), "proc": None,
            "stop": False, "human": None}

        def late_success(*_args, **_kwargs):
            self.assertIsNone(orchestrator.action("o1", "stop"))
            return {"is_error": False, "result": "late success"}

        with mock.patch.object(orchestrator, "_claude", late_success):
            orchestrator._run("o1")
        with open(orchestrator._path("o1"), encoding="utf-8") as handle:
            got = json.load(handle)
        self.assertEqual(got["status"], "stopped")


class RuntimeHelperTests(unittest.TestCase):
    def test_process_tree_kill_selects_windows_and_posix_helpers(self):
        class Proc:
            pid = 321
            killed = False

            def kill(self):
                self.killed = True

        commands = []

        def runner(argv, **_kwargs):
            commands.append(argv)
            return types.SimpleNamespace(returncode=0)

        win = Proc()
        method = agent_runtime.terminate_process_tree(win, platform="win32", runner=runner)
        self.assertEqual(method, "windows-taskkill-tree")
        self.assertEqual(commands, [["taskkill", "/PID", "321", "/T", "/F"]])
        self.assertFalse(win.killed)

        groups = []
        posix = Proc()
        method = agent_runtime.terminate_process_tree(
            posix, platform="linux", getpgid=lambda pid: pid + 10,
            killpg=lambda pgid, sig: groups.append((pgid, sig)))
        self.assertEqual(method, "posix-process-group")
        self.assertEqual(groups, [(331, signal.SIGTERM)])
        self.assertFalse(posix.killed)

    def test_recovery_prompt_refuses_permission_and_outward_decisions(self):
        prompt, reason = agent_runtime.build_recovery_prompt(
            "deploy to production", "permission denied; enter API key", 1)
        self.assertIsNone(prompt)
        self.assertIn("permission", reason)

        prompt, reason = agent_runtime.build_recovery_prompt(
            "fix the local test with api_key=super-secret-token", "assertion failed", 1)
        self.assertIsNone(prompt)
        self.assertIn("credential", reason)

    def test_recovery_preflight_blocks_common_outward_and_destructive_actions(self):
        for mission_text in (
                "post a message to Slack",
                "upload the artifact to S3",
                "run aws s3 sync build s3://release-bucket",
                "run git reset --hard"):
            with self.subTest(mission=mission_text):
                prompt, reason = agent_runtime.build_recovery_prompt(
                    mission_text, "the local step failed", 1)
                self.assertIsNone(prompt)
                self.assertIn("consequential", reason)

    def test_learned_evidence_redacts_bearer_credentials(self):
        item = role(recovery_history=[{
            "cycle": 1, "failure_class": "task", "learnable": True,
            "repair_summary": "Authorization: Bearer super-secret-token; fixed fixture ordering",
            "verification": "passed-original-rerun",
        }])
        evidence = agent_runtime.compact_recovery_evidence(item, learnable_only=True)
        self.assertNotIn("super-secret-token", evidence)
        self.assertIn("<redacted>", evidence)


if __name__ == "__main__":
    unittest.main(verbosity=2)
