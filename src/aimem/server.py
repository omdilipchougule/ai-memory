"""Central MCP memory server (stdio). Run: python -m aimem.server

The tool schemas are re-sent to the model every turn, so they are kept minimal:
no cwd parameter (the server detects the project from its own working directory, which CLI agents set
to the project folder; pass project= explicitly from Claude Desktop), terse docstrings, few optional args.
"""
from __future__ import annotations

import os

try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from .core import Memory

AGENT = os.environ.get("AIMEM_AGENT", "mcp-agent")  # set per client in its MCP config so entries say who wrote them
LAYERS = {"global", "project", "episode", "graph"}

INSTRUCTIONS = (
    "Shared memory across AI tools. At session start call get_project_context. "
    "Use search_memory/retrieve_decisions before asking the user for background. "
    "Save durable facts and decisions with save_memory as they happen (one short line, never secrets). "
    "Call log_session once before finishing."
)

mcp = _Server("ai-memory", instructions=INSTRUCTIONS)
_mem: Memory | None = None


def tool():
    """Plain-text results: skip the per-tool outputSchema the SDK would otherwise add (saves tokens every turn)."""
    try:
        return mcp.tool(structured_output=False)
    except TypeError:  # older SDK without the kwarg
        return mcp.tool()


def mem() -> Memory:
    global _mem
    if _mem is None:
        _mem = Memory()
    return _mem


def _cwd() -> str:
    return os.getcwd()


@tool()
def search_memory(query: str, project: str = "", kind: str = "", k: int = 8) -> str:
    """Hybrid keyword+semantic search. kind: a layer (global|project|episode|graph) or a kind (decision, task, fact...). Project auto-detected."""
    layers = [kind] if kind in LAYERS else None
    kinds = [kind] if kind and kind not in LAYERS else None
    return mem().search(query, project, _cwd(), layers, kinds, k)


@tool()
def save_memory(text: str, kind: str = "fact", project: str = "", replaces: str = "",
                force: bool = False) -> str:
    """Save one durable line (<=600 chars). kind: preference|workflow|fact|profile (global); decision|note|architecture|research|resource|task (project); episode (history). replaces=<id> updates an entry."""
    return mem().save(text, kind, project, _cwd(), AGENT, None, replaces, force)


@tool()
def get_project_context(project: str = "") -> str:
    """Compact briefing: summary, open tasks, recent decisions/progress, prefs, Graphify pointer. Call first each session."""
    return mem().get_project_context(project, _cwd())


@tool()
def get_project_status(project: str = "") -> str:
    """Task counts, open tasks, latest progress log."""
    return mem().get_project_status(project, _cwd())


@tool()
def update_project_progress(summary: str, project: str = "", completed: list[str] | None = None,
                            new_tasks: list[str] | None = None) -> str:
    """Log one progress line; optionally mark tasks done (ids or words) and add tasks."""
    return mem().update_progress(summary, project, _cwd(), completed, new_tasks, AGENT)


@tool()
def search_knowledge_graph(query: str, project: str = "") -> str:
    """Search ingested code-graph nodes + architecture notes; returns the Graphify repo id for live graph tools."""
    return mem().search_graph(query, project, _cwd())


@tool()
def retrieve_decisions(query: str = "", project: str = "", limit: int = 5) -> str:
    """Past decisions and rationale: semantic if query, else most recent."""
    return mem().retrieve_decisions(query, project, _cwd(), limit)


@tool()
def manage_project_tasks(action: str, project: str = "", text: str = "",
                         task: str = "") -> str:
    """action: list|all|add|done|reopen|remove. add needs text; done/reopen/remove need task (id or words)."""
    return mem().manage_tasks("list" if action == "all" else action, project, _cwd(), text, task, AGENT,
                              include_done=(action == "all"))


@tool()
def log_session(summary: str, project: str = "", discoveries: list[str] | None = None,
                completed: list[str] | None = None, decisions: list[str] | None = None,
                pending: list[str] | None = None) -> str:
    """End-of-session record. Narrative -> episode; decisions -> decisions.md; pending -> tasks."""
    return mem().log_session(summary, project, _cwd(), AGENT, discoveries, completed, decisions, pending)


@tool()
def init_project(name: str, description: str = "", repo: str = "", graphify_repository_id: str = "") -> str:
    """Create a project. repo = GitHub owner/name (lets the server detect it from a checkout)."""
    return mem().init_project(name, description, repo, repo, graphify_repository_id)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
