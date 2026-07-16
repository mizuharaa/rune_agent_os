#!/usr/bin/env python3
"""Proof: missions cannot fail silently through the shared model client.

Drives model_client.complete() end-to-end (real code path, urlopen mocked) for
each non-success mode the task names — refusal, http_500, max_tokens, and a raw
exception — and asserts each one is BOTH:

  * surfaced   -> complete() returns status 200 with a non-null stopped_reason
                  (the caller loop stays alive; it branches, it never raises)
  * logged     -> the model_client logger emitted the stopped_reason, so the
                  failure is observable even if a caller ignores the return

Run: python test_stopped_reason.py   (no network, no API key needed)
"""
import io
import json
import logging
import urllib.error
import urllib.request

import model_client as mc

# --- capture what model_client logs -----------------------------------------
buf = io.StringIO()
handler = logging.StreamHandler(buf)
mc.log.addHandler(handler)
mc.log.setLevel(logging.WARNING)

# force complete() past the key check onto the HTTP path
mc._api_key = lambda: "test-key"


class _Resp:
    """Minimal stand-in for the urlopen context manager json.load() reads."""
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        return self._b


def _patch(payload=None, raise_exc=None):
    def _open(req, timeout=None):
        if raise_exc:
            raise raise_exc
        return _Resp(payload)
    return _open


def check(name, patch, expect_reason, reason_in_log=None):
    buf.truncate(0)
    buf.seek(0)
    mc.urllib.request.urlopen = patch
    out = mc.complete("do a thing")   # <- must NEVER raise
    logged = buf.getvalue()
    assert out["status"] == 200, "%s: caller got non-200 (%r)" % (name, out["status"])
    assert out["stopped_reason"] == expect_reason, \
        "%s: stopped_reason %r != %r" % (name, out["stopped_reason"], expect_reason)
    needle = reason_in_log or expect_reason
    assert needle in logged, "%s: %r not logged (log=%r)" % (name, needle, logged)
    print("  %-11s -> stopped_reason=%-10s logged=yes  detail=%s"
          % (name, out["stopped_reason"], (out["detail"] or "")[:40]))


print("proof: every non-success is surfaced AND logged, caller never raises\n")

# 1. refusal — model declines on safety grounds
check("refusal", _patch({
    "stop_reason": "refusal", "content": [],
    "stop_details": {"type": "refusal", "explanation": "declined"}}),
    "refusal")

# 2. http_500 — API returns a server error (HTTPError on the wire)
check("http_500", _patch(raise_exc=urllib.error.HTTPError(
    "https://api", 500, "Server Error", {},
    io.BytesIO(json.dumps({"type": "error",
                           "error": {"message": "boom"}}).encode()))),
    "http_500")

# 3. max_tokens — output truncated mid-answer
check("max_tokens", _patch({
    "stop_reason": "max_tokens",
    "content": [{"type": "text", "text": "half an ans"}]}),
    "max_tokens")

# 4. exception — anything else on the call (e.g. connection dropped). Collapses
#    to api_error; the raised type is preserved in `detail`, so it is observable.
check("exception", _patch(raise_exc=ConnectionError("network down")),
    "api_error", reason_in_log="api_error")
out = mc.complete("x")   # confirm the exception detail names the cause
assert "ConnectionError" in (out["detail"] or ""), out["detail"]

# 5. control — a clean answer stays SILENT: no stopped_reason, nothing logged.
buf.truncate(0); buf.seek(0)
mc.urllib.request.urlopen = _patch({
    "stop_reason": "end_turn", "content": [{"type": "text", "text": "done"}]})
ok = mc.complete("x")
assert ok["stopped_reason"] is None and ok["text"] == "done"
assert buf.getvalue() == "", "clean call must not log (would be noise): %r" % buf.getvalue()
print("  %-11s -> stopped_reason=None       logged=no   (correctly silent)" % "clean")

print("\nOK — refusal/http_500/max_tokens/exception each logged AND surfaced; "
      "complete() never raised; clean path stays quiet.")
