#!/usr/bin/env python3
"""Read Rune's event wire and suggest repeated workflows without executing them."""

import argparse
import collections
import datetime
import json
import os
import re
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DEFAULT_EVENTS = os.path.join(ROOT, "state", "events.jsonl")
DEFAULT_THRESHOLD = 3
SEQUENCE_WINDOW_SECONDS = 15 * 60
SHELL_TOOLS = {"bash", "shell", "powershell", "command", "terminal"}
TRIVIAL_COMMANDS = {
    "cd", "chdir", "pwd", "get-location", "ls", "dir", "get-childitem",
    "get-child-item",
}
FAILURE_RE = re.compile(
    r"\b(fail(?:ed|ure)?|error|crash(?:ed)?|exception|timed?\s*out|timeout|"
    r"stalled|exhausted|rate.?limit|permission\s+denied|refus(?:ed|al))\b", re.I)
RECOVERY_RE = re.compile(
    r"\b(done|recover(?:ed|y)?|resolved|fixed|passed|succeeded|accepted|approved|"
    r"resum(?:e|ed)|retry(?:ing|ied)?)\b", re.I)
ID_RE = re.compile(
    r"(?<![0-9a-f])(?:[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9a-f]{7,40})(?![0-9a-f])",
    re.I)
QUOTED_PATH_RE = re.compile(
    r"([\"'])(?:(?:[A-Za-z]:[\\/])|(?:\.{0,2}[\\/])|/)[^\"'\r\n]*[\\/][^\"'\r\n]*\1")
PATH_TOKEN_RE = re.compile(
    r"(?<![\w<])(?:(?:[A-Za-z]:[\\/])|(?:\.{1,2}[\\/])|/)[^\s;&|,\)\]]+")
RELATIVE_PATH_RE = re.compile(r"\b(?:[A-Za-z0-9_.-]+[\\/])+(?:[A-Za-z0-9_.-]+)\b")
NUMBER_RE = re.compile(r"\b\d+\b")
AUTH_HEADER_RE = re.compile(
    r"(?i)(\bauthorization\s*:\s*(?:(?:bearer|basic)\s+)?)[^\s\"']+")
CREDENTIAL_ASSIGN_RE = re.compile(
    r"(?i)(\b(?:api[_ -]?key|password|passwd|secret|access[_ -]?token|"
    r"refresh[_ -]?token)\b[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)")
CREDENTIAL_FLAG_RE = re.compile(
    r"(?i)(--(?:api[-_]?key|password|secret|token)\s+)"
    r"(?:\"[^\"]*\"|'[^']*'|\S+)")
TOKEN_SHAPE_RE = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]{8,}|gh[pousr]_[a-z0-9]{8,}|"
    r"xox[baprs]-[a-z0-9-]{8,})\b")


def _read_events_health(path):
    """Return valid rows plus explicit input-health counts; opening errors raise."""
    rows = []
    health = {"lines": 0, "malformed_lines": 0, "non_object_lines": 0}
    handle = open(path, encoding="utf-8")
    with handle:
        for line in handle:
            health["lines"] += 1
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                health["malformed_lines"] += 1
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                health["non_object_lines"] += 1
    return rows, health


def read_events(path=DEFAULT_EVENTS):
    """Compatibility helper returning valid rows; use analyze_path for health."""
    try:
        return _read_events_health(path)[0]
    except OSError:
        return []


def normalize_text(value):
    """Remove secrets plus volatile paths, identifiers, and numbers."""
    text = str(value or "").strip()
    text = AUTH_HEADER_RE.sub(lambda match: match.group(1) + "<redacted>", text)
    text = CREDENTIAL_ASSIGN_RE.sub(
        lambda match: match.group(1) + "<redacted>", text)
    text = CREDENTIAL_FLAG_RE.sub(
        lambda match: match.group(1) + "<redacted>", text)
    text = TOKEN_SHAPE_RE.sub("<redacted>", text)
    text = QUOTED_PATH_RE.sub("<path>", text)
    text = PATH_TOKEN_RE.sub("<path>", text)
    text = RELATIVE_PATH_RE.sub("<path>", text)
    text = ID_RE.sub("<id>", text)
    text = NUMBER_RE.sub("<n>", text)
    return re.sub(r"\s+", " ", text).strip()[:240]


def _tokens(command):
    return re.findall(r'"[^"\r\n]*"|\'[^\'\r\n]*\'|\S+', command)


def _split_shell(command):
    """Split command chains without breaking quoted multiline Python/scripts."""
    parts, current, quote, escaped = [], [], None, False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            current.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\" and quote:
            current.append(char)
            escaped = True
            index += 1
            continue
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in ("\"", "'"):
            quote = char
            current.append(char)
            index += 1
            continue
        separator = command[index:index + 2] in ("&&", "||")
        if separator or char in (";", "\n", "\r"):
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            index += 2 if separator else 1
            continue
        current.append(char)
        index += 1
    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts


def _command_family(command):
    """Map one shell step to a stable actionable family, or None for noise."""
    command = re.sub(r"^\s*(?:(?:sudo|call)\s+|timeout\s+\d+\s+)+", "", command,
                     flags=re.I).strip()
    if not command:
        return None
    tokens = _tokens(command)
    if not tokens:
        return None
    executable = tokens[0].strip("\"'").replace("\\", "/").rsplit("/", 1)[-1].lower()
    if executable in TRIVIAL_COMMANDS:
        return None

    lowered = command.lower()
    if re.search(r"\bpytest(?:\.exe)?\b", lowered):
        return "run pytest"
    if re.search(r"\bunittest\b", lowered):
        return "run unittest"
    git = re.search(r"(?:^|\s)git(?:\.exe)?\s+([a-z][a-z0-9-]*)", lowered)
    if git:
        return "git " + git.group(1)
    package = re.search(r"(?:^|\s)(npm|pnpm|yarn)(?:\.cmd)?\s+([a-z][a-z0-9:-]*)", lowered)
    if package:
        return package.group(1) + " " + package.group(2)
    if executable in {"rg", "grep", "findstr", "select-string"}:
        return "search text"
    if executable in {"curl", "wget", "invoke-webrequest", "iwr"}:
        return "http request"
    if executable in {"taskkill", "kill", "pkill", "stop-process"}:
        return "stop process"
    if executable in {"pip", "pip.exe", "pip3", "pip3.exe"} and len(tokens) > 1:
        return "pip " + re.sub(r"[^a-z-]", "", tokens[1].lower())
    if "python" in executable or executable in {"py", "py.exe"}:
        if len(tokens) > 2 and tokens[1] == "-m":
            module = re.sub(r"[^a-z0-9_.-]", "", tokens[2].lower()) or "module"
            return "run python module " + module
        if len(tokens) > 1 and tokens[1] in {"-c", "-"}:
            return "run inline python"
        if len(tokens) > 1 and not tokens[1].startswith("-"):
            script = tokens[1].strip("\"'").replace("\\", "/").rsplit("/", 1)[-1].lower()
            script = ID_RE.sub("<id>", script)
            script = NUMBER_RE.sub("<n>", script)
            return "run python " + (script or "script")
        return "run python script"
    if executable in {"echo", "write-output"}:
        return None

    family = normalize_text(" ".join(tokens[:2])).lower()
    return family if family and family not in TRIVIAL_COMMANDS else None


def _file_family(tool, detail):
    path = str(detail or "").replace("\\", "/").rstrip("/")
    name = path.rsplit("/", 1)[-1].lower()
    name = ID_RE.sub("<id>", name)
    name = NUMBER_RE.sub("<n>", name)
    return "%s %s" % (tool.lower(), name) if name else None


def _evidence(row, family):
    return {
        "session": str(row.get("session") or "?"),
        "ts": str(row.get("ts") or ""),
        "event": str(row.get("event") or "?"),
        "tool": str(row.get("tool") or ""),
        "family": family,
        "detail": normalize_text(row.get("detail")),
    }


def actionable_events(rows):
    """Expand event rows into stable actionable families in input order."""
    actions = []
    for row in rows:
        if str(row.get("event") or "").lower() != "tool":
            continue
        tool = str(row.get("tool") or "").lower()
        detail = str(row.get("detail") or "").strip()
        if not detail:
            continue
        if tool in SHELL_TOOLS:
            parts = _split_shell(detail)
            families = [_command_family(part) for part in parts]
        elif tool in {"read", "edit", "write"}:
            families = [_file_family(tool, detail)]
        elif tool in {"grep", "glob", "search"}:
            families = ["search text"]
        else:
            families = [_command_family(detail)]
        for family in families:
            if family:
                actions.append(_evidence(row, family))
    return actions


def _suggest_family(family):
    if family in {"run pytest", "run unittest"}:
        return "Create one verification command for this test family that captures failures and reports the next action."
    if family.startswith("git "):
        return "Bundle this repeated repository check into a read-only health command with stable output."
    if family == "search text":
        return "Capture the repeated search as a named, scoped audit command with stable output."
    if family.startswith(("read ", "edit ", "write ")):
        return "Create a focused helper or checklist for this repeated file operation; keep every edit operator-triggered."
    return "Package this repeated action as an opt-in script or skill with a dry-run and an explicit change boundary."


def _target(row):
    session = str(row.get("session") or "?")
    if session not in {"?", "operator", "conductor"}:
        return session
    match = ID_RE.search(str(row.get("detail") or ""))
    return match.group(0).lower() if match else session


def _is_failure(row):
    status = str(row.get("status") or row.get("event") or "").lower()
    return status in {"failed", "failure", "error", "stalled", "exhausted"} or bool(
        FAILURE_RE.search(str(row.get("detail") or "")))


def _is_recovery(row):
    detail = str(row.get("detail") or "")
    event = str(row.get("event") or "").lower()
    if event in {"ceo-action", "orch-action"} and re.search(
            r"(?:->|\b)(?:resume|revise|approve|accept|retry)\b", detail, re.I):
        return True
    return bool(RECOVERY_RE.search(detail)) and not _is_failure(row)


def _failure_reason(row):
    detail = str(row.get("detail") or "").lower()
    for reason, pattern in (
        ("permission", r"permission|approval|denied"),
        ("rate-limit", r"rate.?limit|usage limit|quota|\b429\b"),
        ("timeout", r"timed?\s*out|timeout"),
        ("budget", r"exhausted|out of turns|max.?turns"),
        ("test", r"test(?:s|ing)?\s+(?:failed|failure)|pytest.*fail"),
        ("crash", r"crash|exception"),
    ):
        if re.search(pattern, detail):
            return reason
    signature = normalize_text(detail).lower()
    signature = " ".join(signature.split()[:8]) or "unknown"
    return "generic/" + signature


def _failure_suggestion(reason):
    if reason == "permission":
        return "Add an explicit permission-wait state, preserve context, and retry only after operator approval."
    if reason == "rate-limit":
        return "Add bounded exponential backoff for this limit and surface the next retry time."
    if reason == "timeout":
        return "Capture a heartbeat and checkpoint, then retry this timeout at most twice from the last safe state."
    if reason == "budget":
        return "Resume the saved session with a fresh bounded turn budget instead of restarting completed work."
    if reason == "test":
        return "Feed the failing check and its output back to the worker, then rerun that check before declaring recovery."
    return "Capture the root error, feed it back once, retry at most twice, and surface unresolved work."


def _confidence(count, sessions, threshold, recovered_count=None):
    if count >= threshold * 2 and len(sessions) >= 2:
        return "high"
    if len(sessions) >= 2 or count > threshold or (recovered_count or 0) >= threshold:
        return "medium"
    return "low"


def _review_fields(next_action):
    return {
        "advisory_only": True,
        "review_required": True,
        "next_action": next_action,
    }


def _sequence_gap(first, second):
    """Seconds between two actions, or None when they are not one workflow."""
    try:
        start = datetime.datetime.fromisoformat(
            str(first.get("ts") or "").replace("Z", "+00:00"))
        end = datetime.datetime.fromisoformat(
            str(second.get("ts") or "").replace("Z", "+00:00"))
        gap = (end - start).total_seconds()
    except (TypeError, ValueError):
        return None
    return gap if 0 <= gap <= SEQUENCE_WINDOW_SECONDS else None


def _failure_groups(rows):
    timelines = collections.defaultdict(list)
    for row in rows:
        timelines[_target(row)].append(row)
    groups = collections.defaultdict(list)
    for target, timeline in timelines.items():
        for index, row in enumerate(timeline):
            if not _is_failure(row):
                continue
            recovery = next((candidate for candidate in timeline[index + 1:index + 31]
                             if _is_recovery(candidate)), None)
            groups[_failure_reason(row)].append({
                "session": target,
                "ts": str(row.get("ts") or ""),
                "failure": normalize_text(row.get("detail") or row.get("event")),
                "recovered": recovery is not None,
                "recovery": normalize_text(recovery.get("detail")) if recovery else "",
            })
    return groups


def analyze_rows(rows, threshold=DEFAULT_THRESHOLD):
    """Build a deterministic, evidence-backed report from already-loaded rows."""
    threshold = max(DEFAULT_THRESHOLD, int(threshold))
    actions = actionable_events(rows)
    family_evidence = collections.defaultdict(list)
    by_session = collections.defaultdict(list)
    for action in actions:
        family_evidence[action["family"]].append(action)
        by_session[action["session"]].append(action)

    suggestions = []
    for family, evidence in family_evidence.items():
        if len(evidence) < threshold:
            continue
        sessions = sorted({item["session"] for item in evidence})
        suggestion = {
            "kind": "repeated-family",
            "family": family,
            "count": len(evidence),
            "sessions": sessions,
            "confidence": _confidence(len(evidence), sessions, threshold),
            "suggestion": _suggest_family(family),
            "evidence": evidence[:5],
        }
        suggestion.update(_review_fields(
            "Review the samples and confirm they represent the same intent before creating automation."))
        suggestions.append(suggestion)

    sequence_evidence = collections.defaultdict(list)
    for session, evidence in by_session.items():
        for first, second in zip(evidence, evidence[1:]):
            sequence = (first["family"], second["family"])
            gap = _sequence_gap(first, second)
            if sequence[0] == sequence[1] or gap is None:
                continue
            sequence_evidence[sequence].append({
                "session": session,
                "ts": first["ts"],
                "gap_seconds": round(gap),
                "sequence": list(sequence),
                "detail": "%s -> %s" % sequence,
            })
    for sequence, evidence in sequence_evidence.items():
        if len(evidence) < threshold:
            continue
        label = " -> ".join(sequence)
        sessions = sorted({item["session"] for item in evidence})
        suggestion = {
            "kind": "repeated-sequence",
            "family": label,
            "count": len(evidence),
            "sessions": sessions,
            "confidence": _confidence(len(evidence), sessions, threshold),
            "suggestion": "Bundle this repeated sequence into an opt-in workflow with a checkpoint between steps.",
            "evidence": evidence[:5],
        }
        suggestion.update(_review_fields(
            "Review the sequence boundaries and choose the checkpoint that must remain manual."))
        suggestions.append(suggestion)

    for reason, evidence in _failure_groups(rows).items():
        if len(evidence) < threshold:
            continue
        sessions = sorted({item["session"] for item in evidence})
        recovered_count = sum(1 for item in evidence if item["recovered"])
        suggestion = {
            "kind": "failure-recovery",
            "family": "failure: " + reason,
            "count": len(evidence),
            "recovered_count": recovered_count,
            "sessions": sessions,
            "confidence": _confidence(len(evidence), sessions, threshold, recovered_count),
            "suggestion": _failure_suggestion(reason),
            "evidence": evidence[:5],
        }
        suggestion.update(_review_fields(
            "Review the paired failure and recovery evidence, then confirm the retry policy and limit."))
        suggestions.append(suggestion)

    kind_order = {"failure-recovery": 0, "repeated-sequence": 1, "repeated-family": 2}
    suggestions.sort(key=lambda item: (
        kind_order.get(item["kind"], 9), -item["count"], item["family"]))
    families = [{
        "family": family,
        "count": len(evidence),
        "sessions": sorted({item["session"] for item in evidence}),
    } for family, evidence in family_evidence.items() if len(evidence) >= threshold]
    families.sort(key=lambda item: (-item["count"], item["family"]))
    events_by_type = collections.Counter(str(row.get("event") or "?") for row in rows)
    return {
        "version": 1,
        "threshold": threshold,
        "stats": {
            "events": len(rows),
            "sessions": len({str(row.get("session") or "?") for row in rows}),
            "actionable_events": len(actions),
        },
        "events_by_type": dict(sorted(events_by_type.items())),
        "families": families,
        "suggestions": suggestions,
        "advisory_only": True,
        "review_required": True,
        "executed": False,
    }


def analyze_path(path=DEFAULT_EVENTS, threshold=DEFAULT_THRESHOLD):
    rows, health = _read_events_health(path)
    report = analyze_rows(rows, threshold)
    report["source"] = os.path.normpath(path)
    report["input_health"] = health
    return report


def print_human(report):
    stats = report["stats"]
    print("workflow coach: %d events | %d sessions | %d actionable" % (
        stats["events"], stats["sessions"], stats["actionable_events"]))
    health = report.get("input_health") or {}
    if health:
        print("input health: %d lines | %d malformed | %d non-object" % (
            health.get("lines", 0), health.get("malformed_lines", 0),
            health.get("non_object_lines", 0)))
    print("threshold: %d matching observations" % report["threshold"])
    print("\nsuggestions (evidence only; nothing executed):")
    if not report["suggestions"]:
        print("  none yet -- collect at least %d matching actions" % report["threshold"])
        return
    visible = report["suggestions"][:12]
    for index, item in enumerate(visible, 1):
        print("  %d. [%s/%s] %s x%d across %d session(s)" % (
            index, item["kind"], item["confidence"], item["family"], item["count"],
            len(item["sessions"])))
        print("     %s" % item["suggestion"])
        print("     next: %s" % item["next_action"])
        sample = item["evidence"][0]
        detail = sample.get("detail") or sample.get("failure") or "(no detail)"
        print("     evidence: %s %s -- %s" % (
            sample.get("session", "?"), sample.get("ts", ""), detail))
    hidden = len(report["suggestions"]) - len(visible)
    if hidden:
        print("\n  %d more candidate(s) available with --json" % hidden)


def print_audit_report(report):
    """Keep workflow-audit's original summary shape while using coach analysis."""
    stats = report["stats"]
    print("events: %d  |  sessions: %d" % (stats["events"], stats["sessions"]))
    print("\nby event type:")
    for event, count in sorted(report["events_by_type"].items(),
                               key=lambda item: (-item[1], item[0])):
        print("  %-14s %d" % (event, count))
    repeats = [item for item in report["suggestions"]
               if item["kind"] == "repeated-family"]
    print("\nautomation candidates (same verb >=%dx):" % report["threshold"])
    if not repeats:
        print("  none yet -- wire needs more history")
    for item in repeats:
        print("  %-40s x%d  -> %s" % (
            item["family"], item["count"], item["suggestion"]))
    failures = [item for item in report["suggestions"]
                if item["kind"] == "failure-recovery"]
    if failures:
        print("\nrecovery candidates:")
        for item in failures:
            print("  %-40s x%d  -> %s" % (
                item["family"], item["count"], item["suggestion"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", nargs="?", default=DEFAULT_EVENTS,
                        help="event JSONL path (default: state/events.jsonl)")
    def threshold_value(value):
        try:
            parsed = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("threshold must be an integer")
        if parsed < DEFAULT_THRESHOLD:
            raise argparse.ArgumentTypeError(
                "threshold must be at least %d" % DEFAULT_THRESHOLD)
        return parsed

    parser.add_argument("--threshold", type=threshold_value, default=DEFAULT_THRESHOLD)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        report = analyze_path(args.events, args.threshold)
    except OSError as exc:
        print("workflow coach: cannot read %s: %s" % (args.events, exc),
              file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
