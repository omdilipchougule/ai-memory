import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def sh(*a, cwd=None, inp=None, env=None):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, input=inp, env=env or os.environ)


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old = dict(os.environ)
        os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, AIMEM_EMBEDDER="hash",
                          HOME=str(self.tmp / "home"), USERPROFILE=str(self.tmp / "home"),
                          PYTHONPATH=str(ROOT / "src"))
        (self.tmp / "home").mkdir()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def machine(self, name, clone_from=None):
        h = self.tmp / name
        if clone_from:
            self.assertEqual(sh("git", "clone", "-q", str(clone_from), str(h)).returncode, 0)
        else:
            h.mkdir()
            sh("git", "init", "-q", "-b", "main", cwd=h)
            for f in (".gitignore", ".gitattributes"):
                shutil.copy(ROOT / f, h / f)
        return h

    def mem(self, home):
        os.environ["AIMEM_HOME"] = str(home)
        from aimem.core import Memory

        return Memory()


class TestTwoMachineSync(Env):
    def test_converges_without_conflict_markers(self):
        remote = self.tmp / "remote.git"
        sh("git", "init", "-q", "--bare", "-b", "main", str(remote))
        from aimem import gitsync

        a = self.machine("A")
        sh("git", "remote", "add", "origin", str(remote), cwd=a)
        ma = self.mem(a)
        ma.init_project("demo", "Demo", repo="acme/demo")
        ma.manage_tasks("add", "demo", text="Write parser")
        ma.save("Prefers tabs", "preference", agent="claude-code")
        self.assertIn("pushed", gitsync.sync())
        ma.close()

        b = self.machine("B", clone_from=remote)
        mb = self.mem(b)
        self.assertIn("Write parser", mb.manage_tasks("list", "demo"))
        mb.close()

        # concurrent work, no sync in between
        ma = self.mem(a)
        ma.manage_tasks("add", "demo", text="Add CI")
        ma.manage_tasks("done", "demo", task="Write parser")
        ma.save("Chose pytest", "decision", "demo", agent="claude-code")
        ma.log_session("A session: wired CI", "demo", agent="claude-code")
        (a / "memory/projects/demo/context.md").write_text("# demo\n\nSummary written on machine A.\n")
        ma.close()
        mb = self.mem(b)
        mb.manage_tasks("add", "demo", text="Write docs")
        mb.save("Chose ruff", "decision", "demo", agent="gemini")
        mb.log_session("B session: drafted docs", "demo", agent="claude-code")  # same agent+host name as A!
        (b / "memory/projects/demo/context.md").write_text("# demo\n\nSummary written on machine B.\n")
        mb.close()

        os.environ["AIMEM_HOME"] = str(a)
        self.assertIn("pushed", gitsync.sync())
        os.environ["AIMEM_HOME"] = str(b)
        r = gitsync.sync()
        self.assertIn("conflict", r)
        os.environ["AIMEM_HOME"] = str(a)
        gitsync.sync()

        for home in (a, b):
            for p in (home / "memory").rglob("*"):
                if p.is_file():
                    self.assertNotIn("<<<<<<<", p.read_text(), p)
            m = self.mem(home)
            tasks = m.manage_tasks("list", "demo", include_done=True)
            for t in ("Write parser", "Add CI", "Write docs"):
                self.assertIn(t, tasks)
            self.assertRegex(tasks, r"\] x Write parser")
            self.assertEqual(m.manage_tasks("list", "demo").count("Write parser"), 0)
            d = m.retrieve_decisions(None, "demo")
            self.assertIn("pytest", d)
            self.assertIn("ruff", d)
            e = m.search("session", "demo", layers=["episode"])
            self.assertIn("A session", e)
            self.assertIn("B session", e)
            m.close()
        # curated file conflict: local kept, remote kept as sidecar
        sidecars = list((b / "memory/projects/demo").glob("context.md.conflict-*"))
        self.assertEqual(len(sidecars), 1)
        self.assertEqual(json.loads(json.dumps(sh("git", "status", "--porcelain", cwd=b).stdout.strip() == "")), True)

    def test_refuses_to_commit_secrets(self):
        from aimem import gitsync

        a = self.machine("S")
        os.environ["AIMEM_HOME"] = str(a)
        (a / "memory/global").mkdir(parents=True)
        (a / "memory/global/knowledge.md").write_text(
            "- (2026-11-11 x) token ghp_abcdefghijklmnopqrstuvwxyz0123456789 ^aaaaaaaa\n")
        self.assertIn("REFUSED", gitsync.sync())
        self.assertEqual(sh("git", "log", cwd=a).returncode, 128)  # no commit made


class TestInstall(Env):
    def test_gemini_desktop_vscode_idempotent(self):
        from aimem import install

        os.environ["AIMEM_HOME"] = str(self.tmp / "m")
        install.install("gemini", True)
        install.install("gemini", True)
        cfg = json.loads((self.tmp / "home/.gemini/settings.json").read_text())
        self.assertEqual(cfg["mcpServers"]["ai-memory"]["env"]["AIMEM_AGENT"], "gemini-cli")
        g = (self.tmp / "home/.gemini/GEMINI.md").read_text()
        self.assertEqual(g.count("aimem:start"), 1)
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            install.install("vscode", True)
        finally:
            os.chdir(cwd)
        v = json.loads((self.tmp / ".vscode/mcp.json").read_text())
        self.assertEqual(v["servers"]["ai-memory"]["type"], "stdio")
        install.install("claude-desktop", True)

    def test_existing_config_preserved_and_backed_up(self):
        from aimem import install

        os.environ["AIMEM_HOME"] = str(self.tmp / "m")
        p = self.tmp / "home/.gemini/settings.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}}}))
        install.install("gemini", True)
        cfg = json.loads(p.read_text())
        self.assertEqual(cfg["theme"], "dark")
        self.assertIn("other", cfg["mcpServers"])
        self.assertTrue(p.with_name("settings.json.aimem.bak").exists())

    def test_claude_code_hooks_merge_and_dry_run(self):
        from aimem import install

        os.environ["AIMEM_HOME"] = str(self.tmp / "m")
        s = self.tmp / "home/.claude/settings.json"
        s.parent.mkdir(parents=True)
        s.write_text(json.dumps({"hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "keep"}]}]}}))
        self.assertIn("dry run" if False else "would run", install.install("claude-code", False))
        self.assertNotIn("SessionStart", s.read_text())
        with mock.patch("aimem.install.shutil.which", return_value=None):
            install.install("claude-code", True)
            install.install("claude-code", True)
        cfg = json.loads(s.read_text())
        self.assertEqual(cfg["hooks"]["Stop"][0]["hooks"][0]["command"], "keep")
        self.assertEqual(len(cfg["hooks"]["SessionStart"]), 1)
        self.assertEqual(len(cfg["hooks"]["SessionEnd"]), 1)
        self.assertIn("aimem.cli", cfg["hooks"]["SessionStart"][0]["hooks"][0]["command"])

    def test_project_files(self):
        from aimem import install

        d = self.tmp / "proj"
        d.mkdir()
        (d / "CLAUDE.md").write_text("# My rules\nBe nice.\n")
        install.install("project-files", True, str(d))
        for f in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", ".github/copilot-instructions.md"):
            self.assertIn("get_project_context", (d / f).read_text())
        self.assertIn("Be nice.", (d / "CLAUDE.md").read_text())


class TestHooksAndCLI(Env):
    def setUp(self):
        super().setUp()
        self.home = self.machine("H")
        os.environ["AIMEM_HOME"] = str(self.home)
        m = self.mem(self.home)
        m.init_project("demo", "Demo app", repo="acme/demo")
        m.manage_tasks("add", "demo", text="Ship v1")
        m.save("Keep answers short", "preference")
        m.close()
        self.work = self.tmp / "demo"
        self.work.mkdir()

    def transcript(self, extra=""):
        t = self.tmp / "t.jsonl"
        msgs = [
            {"type": "user", "message": {"role": "user", "content": "why does it flicker?"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "The root cause was a missing debounce on the ADC read. We decided to use a 20ms moving average."},
                {"type": "text", "text": "Still need to benchmark on the ESP32 board.\n" + extra}]}},
        ]
        t.write_text("\n".join(json.dumps(x) for x in msgs))
        return t

    def test_session_start_brief(self):
        r = sh(sys.executable, "-m", "aimem.cli", "hook", "session-start", inp=json.dumps({"cwd": str(self.work)}))
        self.assertIn("Project: demo", r.stdout, r.stderr)
        self.assertIn("Ship v1", r.stdout)
        self.assertIn("Keep answers short", r.stdout)

    def test_session_end_auto_capture(self):
        r = sh(sys.executable, "-m", "aimem.cli", "hook", "session-end",
               inp=json.dumps({"cwd": str(self.work), "transcript_path": str(self.transcript())}))
        self.assertEqual(r.returncode, 0, r.stderr)
        s = sh(sys.executable, "-m", "aimem.cli", "search", "debounce ADC", "--project", "demo")
        self.assertIn("episode", s.stdout)
        self.assertIn("Auto-captured", s.stdout)
        # curated layers untouched by auto capture
        self.assertNotIn("moving average", (self.home / "memory/projects/demo/decisions.md").read_text()
                         if (self.home / "memory/projects/demo/decisions.md").exists() else "")

    def test_session_end_skips_when_agent_logged_explicitly(self):
        t = self.transcript(extra="tool call: log_session")
        sh(sys.executable, "-m", "aimem.cli", "hook", "session-end",
           inp=json.dumps({"cwd": str(self.work), "transcript_path": str(t)}))
        s = sh(sys.executable, "-m", "aimem.cli", "search", "debounce ADC", "--project", "demo")
        self.assertNotIn("Auto-captured", s.stdout)

    def test_cli_misc(self):
        self.assertIn("embedder", sh(sys.executable, "-m", "aimem.cli", "doctor").stdout)
        self.assertEqual(sh(sys.executable, "-m", "aimem.cli", "scan").stdout.strip(), "clean")
        out = sh(sys.executable, "-m", "aimem.cli", "dashboard").stdout.strip()
        html = Path(out).read_text()
        self.assertIn("Ship v1", html)
        self.assertIn("<h2>demo</h2>", html)
        self.assertIn("reindexed", sh(sys.executable, "-m", "aimem.cli", "reindex").stdout)


if __name__ == "__main__":
    unittest.main()
