# ai-memory repository

This repo is a personal memory store plus the `aimem` MCP server that reads and writes it.

- `memory/` is **data**: one memory per line (`- (date agent) text ^id`). Prefer the MCP tools or `aimem save`
  over hand-editing. Hand edits are fine; the index picks them up. Never put secrets here (`aimem scan`).
- `src/aimem/` is the server. Tests: `python -m unittest discover -s tests`.
- `.index/` is derived and git-ignored. `aimem reindex` rebuilds it from `memory/`.
- Sync with `aimem sync` (commit + merge + push). After a manual `git pull`, run `aimem reindex`.

<!-- the shared-memory block below is managed by `aimem install project-files` -->

<!-- aimem:start -->
## Shared memory (ai-memory MCP server)
- Session start: call `get_project_context` (the project is detected from the working directory).
- Before asking me for background, try `search_memory` / `retrieve_decisions`.
- Save durable facts, preferences and decisions immediately with `save_memory` (one short line; never secrets or keys).
- Track work with `manage_project_tasks` and `update_project_progress`.
- Code structure: use Graphify tools (`graphify_find`, `query_graph`, `graphify_impact`) with the repository id from the project context; `search_knowledge_graph` covers locally ingested graph and architecture notes.
- Before finishing: call `log_session` once (summary, discoveries, decisions, pending).
- Retrieval is selective. Never request or dump the whole memory.
<!-- aimem:end -->
