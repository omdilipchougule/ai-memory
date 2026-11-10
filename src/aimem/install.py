"""Wire the memory server into agents. Safe to re-run: merges config, backs up the original once."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .common import home

NAME = "ai-memory"
START, END = "<!-- aimem:start -->", "<!-- aimem:end -->"

BLOCK = f"""{START}
## Shared memory (ai-memory MCP server)
- Session start: call `get_project_context` (the project is detected from the working directory).
- Before asking me for background, try `search_memory` / `retrieve_decisions`.
- Save durable facts, preferences and decisions immediately with `save_memory` (one short line; never secrets or keys).
- Track work with `manage_project_tasks` and `update_project_progress`.
- Code structure: use Graphify tools (`graphify_find`, `query_graph`, `graphify_impact`) with the repository id from the project context; `search_knowledge_graph` covers locally ingested graph and architecture notes.
- Before finishing: call `log_session` once (summary, discoveries, decisions, pending).
- Retrieval is selective. Never request or dump the whole memory.
{END}
"""


def server_entry(agent: str) -> dict:
    return {"command": sys.executable, "args": ["-m", "aimem.server"],
            "env": {"AIMEM_HOME": str(home()), "AIMEM_AGENT": agent}}


def _backup(p: Path) -> None:
    b = p.with_name(p.name + ".aimem.bak")
    if p.exists() and not b.exists():
        shutil.copy2(p, b)


def _load(p: Path) -> dict:
    if p.exists() and p.read_text(encoding="utf-8").strip():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _save(p: Path, cfg: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    _backup(p)
    p.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def put_block(path: Path, block: str = BLOCK) -> str:
    """Insert/replace the marked block, leaving the rest of the file untouched."""
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if START in old and END in old:
        a, b = old.index(START), old.index(END) + len(END)
        new = old[:a] + block.strip() + old[b:]
    else:
        new = (old.rstrip() + "\n\n" if old.strip() else "") + block
    if new != old:
        _backup(path)
        path.write_text(new if new.endswith("\n") else new + "\n", encoding="utf-8")
        return f"updated {path}"
    return f"unchanged {path}"


def _hook_cmd(event: str) -> str:
    return f'"{sys.executable}" -m aimem.cli --home "{home()}" hook {event}'


def claude_code(write: bool) -> list[str]:
    out = []
    env = [f"AIMEM_HOME={home()}", "AIMEM_AGENT=claude-code"]
    cmd = ["claude", "mcp", "add", "--scope", "user", NAME, *[x for e in env for x in ("-e", e)], "--",
           sys.executable, "-m", "aimem.server"]
    settings = Path.home() / ".claude" / "settings.json"
    if write:
        if shutil.which("claude"):
            r = subprocess.run(cmd, capture_output=True, text=True)
            out.append("claude mcp add: " + ((r.stdout or r.stderr).strip().splitlines() or ["done"])[-1])
        else:
            out.append("`claude` not on PATH; run manually: " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
        cfg = _load(settings)
        hooks = cfg.setdefault("hooks", {})
        for event, timeout in (("SessionStart", 30), ("SessionEnd", 60)):
            entries = hooks.setdefault(event, [])
            if not any("aimem.cli" in h.get("command", "") for e in entries for h in e.get("hooks", [])):
                entries.append({"matcher": "", "hooks": [{"type": "command",
                                "command": _hook_cmd("session-start" if event == "SessionStart" else "session-end"),
                                "timeout": timeout}]})
        _save(settings, cfg)
        out.append(f"hooks merged into {settings}")
        out.append(put_block(Path.home() / ".claude" / "CLAUDE.md"))
    else:
        out.append("would run: " + " ".join(cmd))
        out.append(f"would add SessionStart/SessionEnd hooks to {settings} and the instruction block to ~/.claude/CLAUDE.md")
    return out


def _json_mcp(path: Path, agent: str, write: bool, key="mcpServers", typed=False) -> list[str]:
    entry = server_entry(agent)
    if typed:
        entry = {"type": "stdio", **entry}
    if not write:
        return [f"would add '{NAME}' to {path}:", json.dumps({key: {NAME: entry}}, indent=2)]
    cfg = _load(path)
    cfg.setdefault(key, {})[NAME] = entry
    _save(path, cfg)
    return [f"'{NAME}' written to {path}"]


def claude_desktop(write: bool) -> list[str]:
    s = platform.system()
    if s == "Windows":
        p = Path(os.environ.get("APPDATA", Path.home())) / "Claude" / "claude_desktop_config.json"
    elif s == "Darwin":
        p = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    else:
        p = Path.home() / ".config/Claude/claude_desktop_config.json"
    return _json_mcp(p, "claude-desktop", write) + ["restart Claude Desktop to load it"]


def gemini(write: bool) -> list[str]:
    out = _json_mcp(Path.home() / ".gemini" / "settings.json", "gemini-cli", write)
    if write:
        out.append(put_block(Path.home() / ".gemini" / "GEMINI.md"))
    return out


def vscode(write: bool) -> list[str]:
    return _json_mcp(Path.cwd() / ".vscode" / "mcp.json", "copilot", write, key="servers", typed=True)


def project_files(target: Path) -> list[str]:
    """Drop the shared instruction block into a project repo for every agent that reads one of these files."""
    return [put_block(target / f) for f in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", ".github/copilot-instructions.md")]


TARGETS = {"claude-code": claude_code, "claude-desktop": claude_desktop, "gemini": gemini, "vscode": vscode}


def install(target: str, write: bool, project_dir: str | None = None) -> str:
    if target == "project-files":
        return "\n".join(project_files(Path(project_dir or ".").resolve()))
    names = list(TARGETS) if target == "all" else [target]
    out = []
    for n in names:
        out.append(f"== {n} ==")
        out += TARGETS[n](write)
    return "\n".join(out)
