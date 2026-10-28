#!/usr/bin/env python3
"""End-to-end tests for mcp-server/rtl_i18n_check_mcp_server.py.

Standard library only (unittest). Spawns the server as a subprocess
and speaks newline-delimited JSON-RPC 2.0 over stdio, exactly as an
MCP client would.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp-server" / "rtl_i18n_check_mcp_server.py"
FIX = ROOT / "tests" / "fixtures"


def rpc(proc, payload):
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("server closed stdout")
    return json.loads(line)


class TestMcpServer(unittest.TestCase):
    def setUp(self):
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        self.assertEqual(rpc(self.proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05",
                       "capabilities": {}, "clientInfo": {"name": "test",
                                                          "version": "0"}}}
        )["result"]["serverInfo"]["name"], "rtl-i18n-check")
        # Notifications get no response — write without reading.
        self.proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0",
             "method": "notifications/initialized"}) + "\n")
        self.proc.stdin.flush()

    def tearDown(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        self.proc.stdout.close()
        self.proc.stderr.close()

    def call(self, tool, arguments):
        return rpc(self.proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments}})

    def test_tools_list(self):
        resp = rpc(self.proc, {"jsonrpc": "2.0", "id": 3,
                               "method": "tools/list"})
        tools = resp["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["check_rtl_locales"])
        self.assertIn("files", tools[0]["inputSchema"]["properties"])

    def test_clean_file(self):
        resp = self.call("check_rtl_locales",
                         {"files": [str(FIX / "fa-clean.json")],
                          "source": str(FIX / "en.json")})
        text = resp["result"]["content"][0]["text"]
        self.assertIn("exit code: 0", text)
        self.assertIn("0 errors, 0 warnings", text)

    def test_broken_file(self):
        resp = self.call("check_rtl_locales",
                         {"files": [str(FIX / "fa-broken.json")],
                          "source": str(FIX / "en.json")})
        text = resp["result"]["content"][0]["text"]
        self.assertIn("exit code: 1", text)
        self.assertIn("15 errors, 1 warning", text)
        self.assertIn("RTL-01", text)
        self.assertIn("RTL-03", text)
        self.assertIn("RTL-05", text)

    def test_unknown_tool(self):
        resp = self.call("no_such_tool", {})
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()
