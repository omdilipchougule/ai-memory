"""aimem command line: the same core as the MCP server, for terminals, hooks and web-chat copy/paste."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys


def _out(s: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aimem", description=__doc__)
    ap.add_argument("--home", help="memory repo root (default: $AIMEM_HOME or ~/ai-memory)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="run the MCP server on stdio")
    c = sub.add_parser("context", help="print the compact project briefing (paste into web chats like gemini.google.com)")
    c.add_argument("--project")
    c.add_argument("--cwd", default=os.getcwd())
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--project")
    s.add_argument("--kind")
    s.add_argument("-k", type=int, default=8)
    sv = sub.add_parser("save")
    sv.add_argument("text")
    sv.add_argument("--kind", default="fact")
    sv.add_argument("--project")
    sv.add_argument("--agent", default="cli")
    ip = sub.add_parser("init-project")
    ip.add_argument("name")
    ip.add_argument("--description", default="")
    ip.add_argument("--repo", default="", help="GitHub owner/name")
    ip.add_argument("--graphify-id", default="", help="Graphify repository id")
    ig = sub.add_parser("ingest-graph", help="index a nodes/edges JSON export of a code graph")
    ig.add_argument("file")
    ig.add_argument("--project", required=True)
    sy = sub.add_parser("sync", help="commit, pull/merge and push the memory repo")
    sy.add_argument("--no-push", action="store_true")
    sub.add_parser("reindex", help="rebuild the derived search index from the Markdown/JSONL files")
    sub.add_parser("warmup", help="download the optional semantic model (AIMEM_EMBEDDER=fastembed)")
    sub.add_parser("doctor", help="health check")
    sub.add_parser("scan", help="look for secrets in memory files")
    sub.add_parser("dashboard", help="write a static HTML overview")
    ins = sub.add_parser("install", help="configure an agent")
    ins.add_argument("target", choices=["claude-code", "claude-desktop", "gemini", "vscode", "all", "project-files"])
    ins.add_argument("--write", action="store_true", help="apply (default is a dry run)")
    ins.add_argument("--project-dir", help="for project-files: repo to receive AGENTS.md/CLAUDE.md/GEMINI.md/copilot file")
    hk = sub.add_parser("hook", help="agent hook entry point (reads JSON on stdin)")
    hk.add_argument("event", choices=["session-start", "session-end"])

    a = ap.parse_args(argv)
    if a.home:
        os.environ["AIMEM_HOME"] = a.home

    from . import common

    if a.cmd == "serve":
        from .server import main as serve

        serve()
        return 0
    if a.cmd == "install":
        from .install import install

        _out(install(a.target, a.write, a.project_dir) + ("" if a.write or a.target == "project-files" else
                                                          "\n\n(dry run; add --write to apply)"))
        return 0
    if a.cmd == "sync":
        from .gitsync import sync

        _out(sync(push=not a.no_push))
        return 0
    if a.cmd == "scan":
        from .gitsync import scan_secrets

        hits = scan_secrets()
        _out("possible secrets at: " + ", ".join(hits) if hits else "clean")
        return 1 if hits else 0
    if a.cmd == "reindex":
        shutil.rmtree(common.home() / ".index", ignore_errors=True)
    if a.cmd == "warmup":
        os.environ["AIMEM_EMBEDDER"] = "fastembed"
        from .embed import FastEmbedder

        FastEmbedder().embed(["warmup"])
        _out("semantic model ready. Set AIMEM_EMBEDDER=fastembed in each MCP config's env (then `aimem reindex`).")
        return 0
    if a.cmd == "hook":
        from . import hooks

        payload = {}
        if not sys.stdin.isatty():
            try:
                payload = json.loads(sys.stdin.read() or "{}")
            except ValueError:
                payload = {}
        if a.event == "session-start":
            _out(hooks.session_start(payload.get("cwd") or os.getcwd()))
        else:
            _out(hooks.session_end(payload))
        return 0

    from .core import Memory

    m = Memory()
    try:
        if a.cmd == "context":
            _out(m.get_project_context(a.project, a.cwd))
        elif a.cmd == "search":
            lay = {"global", "project", "episode", "graph"}
            _out(m.search(a.query, a.project, os.getcwd(), [a.kind] if a.kind in lay else None,
                          [a.kind] if a.kind and a.kind not in lay else None, a.k))
        elif a.cmd == "save":
            _out(m.save(a.text, a.kind, a.project, os.getcwd(), a.agent))
        elif a.cmd == "init-project":
            _out(m.init_project(a.name, a.description, a.repo, a.repo, a.graphify_id))
        elif a.cmd == "ingest-graph":
            _out(m.ingest_graph(a.file, a.project))
        elif a.cmd == "reindex":
            _out("reindexed: " + json.dumps(m.ix.stats()))
        elif a.cmd == "dashboard":
            from .dashboard import write

            _out(write(m))
        elif a.cmd == "doctor":
            from .gitsync import git, scan_secrets
            from . import store

            h = common.home()
            _out(f"home:      {h}")
            _out(f"embedder:  {m.emb.name}" + ("" if m.emb.name != "hash-384" else "  (lexical; AIMEM_EMBEDDER=fastembed for semantic)"))
            _out(f"projects:  {', '.join(store.list_projects()) or 'none'}")
            _out(f"indexed:   {json.dumps(m.ix.stats())}")
            if (h / ".git").exists():
                _out(f"git:       {git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()} | remote: "
                     f"{git('remote', 'get-url', 'origin').stdout.strip() or 'none'}")
            else:
                _out("git:       not a repo")
            sec = scan_secrets()
            _out("secrets:   " + (f"POSSIBLE at {sec[:5]}" if sec else "none found"))
    finally:
        m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
