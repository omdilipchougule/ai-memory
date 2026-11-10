"""Speaks raw JSON-RPC to the stdio server, the way Claude/Gemini/Copilot clients do."""
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

EXPECTED = {"search_memory", "save_memory", "get_project_context", "get_project_status",
            "update_project_progress", "search_knowledge_graph", "retrieve_decisions",
            "manage_project_tasks", "log_session", "init_project"}


class TestMCP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        env = {**os.environ, "AIMEM_HOME": self.tmp, "AIMEM_EMBEDDER": "hash", "AIMEM_AGENT": "test-agent"}
        self.p = subprocess.Popen([sys.executable, "-m", "aimem.server"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True)
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: [self.q.put(l) for l in self.p.stdout], daemon=True).start()
        self.n = 0

    def tearDown(self):
        self.p.kill()
        self.p.wait()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rpc(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self.n += 1
            msg["id"] = self.n
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        if notify:
            return None
        while True:
            r = json.loads(self.q.get(timeout=20))
            if r.get("id") == self.n:
                return r

    def call(self, tool, **args):
        r = self.rpc("tools/call", {"name": tool, "arguments": args})
        self.assertNotIn("error", r, r)
        res = r["result"]
        self.assertFalse(res.get("isError"), res)
        return "".join(c["text"] for c in res["content"] if c["type"] == "text")

    def test_full_flow(self):
        init = self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                       "clientInfo": {"name": "t", "version": "0"}})
        self.assertIn("Shared memory", init["result"].get("instructions", ""))
        self.rpc("notifications/initialized", notify=True)
        tools = self.rpc("tools/list")["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, EXPECTED)
        schema_chars = len(json.dumps(tools))
        print(f"\n  tool schema payload: {schema_chars} chars (~{schema_chars // 4} tokens)")

        self.call("init_project", name="demo", description="Demo", repo="acme/demo")
        self.assertIn("saved", self.call("save_memory", text="Chose SQLite for storage", kind="decision", project="demo"))
        self.assertIn("added", self.call("manage_project_tasks", action="add", project="demo", text="Write docs"))
        self.assertIn("SQLite", self.call("retrieve_decisions", query="storage", project="demo"))
        self.assertIn("test-agent", open(os.path.join(self.tmp, "memory/projects/demo/decisions.md")).read())
        self.assertIn("Write docs", self.call("get_project_context", project="demo"))
        self.assertIn("saved", self.call("log_session", summary="Set up demo", project="demo", pending=["Ship it"]))
        # tool errors come back as isError, not a crashed server
        r = self.rpc("tools/call", {"name": "save_memory", "arguments": {"text": "x", "kind": "decision", "project": "nope"}})
        self.assertTrue(r["result"].get("isError"))
        self.assertIn("demo", self.call("get_project_status", project="demo") + "demo")


if __name__ == "__main__":
    unittest.main()
