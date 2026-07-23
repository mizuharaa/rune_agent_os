"""Self-check: the Wisp MCP server speaks the protocol and its tools hit a
real engine. Boots an engine on a test port, runs the stdio handshake, lists
tools, and calls desktop_windows + agent_activity for real."""
import json
import os
import subprocess
import sys
import time
import urllib.request

PORT = "8898"


def rpc(proc, mid, method, params=None):
    msg = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    out = json.loads(line)
    assert out.get("id") == mid, out
    return out


def main():
    engine = subprocess.Popen([sys.executable, "dashboard/serve.py", PORT],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    server = None
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen("http://127.0.0.1:%s/api/activity" % PORT,
                                       timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise SystemExit("engine never came up")

        env = dict(os.environ, WISP_ENGINE="http://127.0.0.1:" + PORT)
        server = subprocess.Popen([sys.executable, "mcp/server.py"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True, env=env)
        init = rpc(server, 1, "initialize",
                   {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "self-check", "version": "0"}})
        assert init["result"]["serverInfo"]["name"] == "wisp", init

        tools = rpc(server, 2, "tools/list")["result"]["tools"]
        names = {t["name"] for t in tools}
        assert {"desktop_windows", "window_tree", "ui_act", "ui_read",
                "agent_activity", "stop_all"} <= names, names

        act = rpc(server, 3, "tools/call",
                  {"name": "agent_activity", "arguments": {}})["result"]
        assert not act["isError"], act
        feed = json.loads(act["content"][0]["text"])
        assert {"running", "recent", "approvals"} <= set(feed), feed

        win = rpc(server, 4, "tools/call",
                  {"name": "desktop_windows", "arguments": {}})["result"]
        assert not win["isError"], win
        wins = json.loads(win["content"][0]["text"])["windows"]
        assert isinstance(wins, list) and wins, "no desktop windows found"

        bad = rpc(server, 5, "tools/call", {"name": "nope", "arguments": {}})
        assert "error" in bad, bad

        print("tools:", len(tools), "| windows seen:", len(wins))
        print("MCP_SELF_CHECK_OK")
    finally:
        if server:
            server.kill()
        engine.kill()


if __name__ == "__main__":
    main()
