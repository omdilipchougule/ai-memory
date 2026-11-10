import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["AIMEM_HOME"] = self.tmp
        os.environ["AIMEM_EMBEDDER"] = "hash"
        from aimem.core import Memory

        self.m = Memory()
        self.m.init_project("demo", "Demo ECG app", repo="acme/demo", graphify_repo="acme/demo",
                            graphify_id="uuid-1", aliases=["ecg-demo"])

    def tearDown(self):
        self.m.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def md(self, rel):
        return (Path(self.tmp) / "memory" / rel).read_text(encoding="utf-8")


class TestSave(Base):
    def test_routing_and_dedup(self):
        self.assertIn("saved", self.m.save("Prefers concise answers with code first", "preference", agent="claude-code"))
        self.assertIn("duplicate", self.m.save("Prefers concise answers with code first", "preference"))
        self.assertIn("Prefers concise", self.md("global/preferences.md"))
        r = self.m.save("Use SQLite FTS5 for retrieval because it ships with Python", "decision", project="demo")
        self.assertIn("saved", r)
        self.assertIn("FTS5", self.md("projects/demo/decisions.md"))

    def test_secret_refused(self):
        r = self.m.save("my key is sk-abcdefghijklmnopqrstuvwxyz123456", "fact")
        self.assertIn("REFUSED", r)
        self.assertFalse((Path(self.tmp) / "memory/global/knowledge.md").exists())

    def test_near_duplicate_and_replace(self):
        self.m.save("The ECG model runs on Python 3.11 with onnxruntime", "fact")
        r = self.m.save("The ECG model runs on Python 3.11 with onnxruntime today", "fact")
        self.assertIn("similar memory exists", r)
        old = r.split("[")[1].split("]")[0]
        r2 = self.m.save("The ECG model runs on Python 3.12 with onnxruntime", "fact", replaces=old)
        self.assertIn("saved", r2)
        txt = self.md("global/knowledge.md")
        self.assertIn("3.12", txt)
        self.assertNotIn("3.11", txt)

    def test_unknown_project_and_too_long(self):
        with self.assertRaises(ValueError):
            self.m.save("x", "decision", project="nope")
        with self.assertRaises(ValueError):
            self.m.save("y" * 700, "fact")

    def test_alias_resolves(self):
        self.assertIn("saved", self.m.save("Uses bandpass filter 0.5-40Hz", "note", project="ecg-demo"))


class TestTasksProgress(Base):
    def test_task_lifecycle_edits_one_line(self):
        self.m.manage_tasks("add", "demo", text="Write QRS detector")
        self.m.manage_tasks("add", "demo", text="Add unit tests for filters")
        before = self.md("projects/demo/tasks.md").splitlines()
        self.m.manage_tasks("done", "demo", task="QRS detector")
        after = self.md("projects/demo/tasks.md").splitlines()
        self.assertEqual(len(before), len(after))
        self.assertEqual(sum(a != b for a, b in zip(before, after)), 1)
        self.assertIn("[x]", "\n".join(after))
        self.assertNotIn("QRS", self.m.manage_tasks("list", "demo"))
        self.assertIn("QRS", self.m.manage_tasks("list", "demo", include_done=True))

    def test_progress_completes_and_adds(self):
        self.m.manage_tasks("add", "demo", text="Write QRS detector")
        r = self.m.update_progress("QRS detector done, 98% on test set", "demo", completed=["QRS detector"],
                                   new_tasks=["Tune thresholds"])
        self.assertIn("logged", r)
        st = self.m.get_project_status("demo")
        self.assertIn("1 open / 1 done", st)
        self.assertIn("98%", st)


class TestSearchContext(Base):
    def setUp(self):
        super().setUp()
        self.m.save("Always give code examples before long explanations", "preference")
        self.m.save("Chose pan-tompkins for QRS detection since it is cheap on microcontrollers", "decision", project="demo")
        self.m.save("Chose React for the dashboard front end", "decision", project="demo")
        self.m.init_project("other", "Unrelated")
        self.m.save("Chose Postgres for storage", "decision", project="other")
        self.m.log_session("Debugged baseline wander in ECG signal; root cause was missing highpass filter",
                           project="demo", agent="claude-code", discoveries=["MIT-BIH sample rate is 360Hz"],
                           decisions=["Apply 0.5Hz highpass before QRS detection"], pending=["Benchmark on ESP32"])

    def test_search_is_scoped_and_relevant(self):
        r = self.m.search("QRS detection algorithm", project="demo")
        self.assertIn("pan-tompkins", r)
        self.assertNotIn("Postgres", self.m.search("storage database", project="demo"))
        self.assertIn("Postgres", self.m.search("storage database"))

    def test_retrieve_decisions(self):
        self.assertIn("React", self.m.retrieve_decisions("front end", "demo"))
        self.assertIn("highpass", self.m.retrieve_decisions(None, "demo"))

    def test_no_duplication_across_layers(self):
        ep = "".join(p.read_text() for p in (Path(self.tmp) / "memory/episodes").rglob("*.jsonl"))
        row = json.loads(ep.splitlines()[0])
        self.assertNotIn("Benchmark on ESP32", row["text"])       # pending lives in tasks.md only
        self.assertEqual(len(row["refs"]["tasks"]), 1)
        self.assertIn("Benchmark on ESP32", self.md("projects/demo/tasks.md"))

    def test_context_is_small_and_has_graph_pointer(self):
        ctx = self.m.get_project_context("demo")
        self.assertIn("graphify_find", ctx)
        self.assertIn("uuid-1", ctx)
        self.assertIn("Always give code examples", ctx)
        self.assertLess(len(ctx) // 4, 700)

    def test_context_from_cwd_name(self):
        d = Path(self.tmp) / "work" / "ecg-demo"
        d.mkdir(parents=True)
        self.assertIn("Project: demo", self.m.get_project_context(cwd=str(d)))

    def test_budget_truncates(self):
        for i in range(30):
            self.m.save(f"ECG note number {i} about filtering and sampling rates", "note", project="demo", force=True)
        out = self.m.search("ECG filtering sampling", project="demo", k=30, max_tokens=120)
        self.assertIn("more", out.splitlines()[-1])


class TestIncremental(Base):
    def test_external_edit_picked_up_without_reembedding_unchanged(self):
        self.m.save("Fact one about oscilloscopes", "fact")
        calls = []
        orig = self.m.emb.embed
        self.m.emb.embed = lambda t: (calls.append(list(t)), orig(t))[1]
        p = Path(self.tmp) / "memory/global/knowledge.md"
        p.write_text(p.read_text() + "- (2026-11-11 gemini) Fact two about multimeters ^deadbeef\n", encoding="utf-8")
        self.assertIn("multimeters", self.m.search("multimeters"))
        embedded = [t for c in calls for t in c]
        self.assertTrue(any("multimeters" in t for t in embedded))
        self.assertFalse(any("oscilloscopes" in t for t in embedded if "multimeters" not in t and t.startswith("Fact one")))

    def test_deleted_line_leaves_index(self):
        self.m.save("Temporary fact about soldering irons", "fact")
        p = Path(self.tmp) / "memory/global/knowledge.md"
        p.write_text("# Knowledge\n\n", encoding="utf-8")
        self.assertEqual(self.m.search("soldering"), "no matches")

    def test_duplicate_union_lines_collapse_done_wins(self):
        self.m.manage_tasks("add", "demo", text="Ship v1")
        p = Path(self.tmp) / "memory/projects/demo/tasks.md"
        line = [l for l in p.read_text().splitlines() if "Ship v1" in l][0]
        p.write_text(p.read_text() + line.replace("[ ]", "[x]") + "\n", encoding="utf-8")
        self.assertEqual(self.m.manage_tasks("list", "demo"), "no tasks")


class TestGraph(Base):
    def test_ingest_and_search(self):
        g = {"nodes": [{"id": "a", "label": "detect_qrs", "type": "function", "file": "ecg/qrs.py"},
                       {"id": "b", "label": "bandpass", "type": "function", "file": "ecg/filt.py"}],
             "links": [{"source": "a", "target": "b", "relation": "calls"}]}
        f = Path(self.tmp) / "g.json"
        f.write_text(json.dumps(g))
        self.assertIn("2 graph nodes", self.m.ingest_graph(str(f), "demo"))
        out = self.m.search_graph("qrs", "demo")
        self.assertIn("calls bandpass", out)
        self.assertIn("graphify_find", out)


if __name__ == "__main__":
    unittest.main()
