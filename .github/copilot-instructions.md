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
