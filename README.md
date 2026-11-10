# ai-memory

One memory for every AI tool you use. Claude Code, Claude Desktop, Gemini CLI and Copilot all talk to the same
local MCP server; what they learn lives in plain Markdown/JSONL in a Git repo, so it follows you across models,
providers (OmniRoute included) and machines.

## Architecture

```
 Claude Code ─┐                       ┌─ memory/global/*.md          Layer 1  Global     (Markdown, git)
 Claude Desktop ─┤   stdio MCP         │  memory/projects/<p>/…      Layer 2  Project    (Markdown + project.yml, git)
 Gemini CLI ──┼──► aimem.server ──► files ─ memory/episodes/…jsonl  Layer 3  Episodic  (append-only JSONL, git)
 Copilot ─────┘        │                    ▲
                       ▼                    │ rebuilt from files, never committed
              .index/index.db  (SQLite FTS5 + vectors, hybrid RRF ranking)
                       │
   Graphify (hosted MCP connector) ◄── pointer in project.yml; agents call graphify_* directly
```

| Layer | Holds | Stored as | Retrieval |
|---|---|---|---|
| Global | preferences, workflows, durable facts | `memory/global/*.md` | top prefs ride along in the briefing; the rest by search |
| Project | summary, decisions, tasks, progress, notes, Graphify link | `memory/projects/<name>/` | `get_project_context` (auto-detected), `retrieve_decisions`, `search_memory` |
| Episodic | session narratives, discoveries (and auto-captured candidates) | `memory/episodes/YYYY-MM/<agent>@<host>.jsonl` | `search_memory(kind="episode")` |

Why this shape:
- **No duplication:** each fact has exactly one home (kind decides the file). `log_session` stores the narrative once and
  links decisions/tasks by id instead of copying them.
- **Incremental:** writes append one line; edits (e.g. completing a task) rewrite one line. The index re-parses only
  changed files, reads JSONL from a saved byte offset, and embeds only new or changed text.
- **Merge-friendly:** line-per-memory + `merge=union` means two machines rarely conflict. Episode files are per agent and
  host. Real conflicts (`project.yml`, `context.md`) keep your local copy and save the other as `*.conflict-<host>`.
- **Cheap to use:** the 10 tool schemas cost about 1,270 tokens per turn; a project briefing is under 700 tokens;
  searches return one line per hit within a token budget.

## MCP tools

| Tool | Purpose |
|---|---|
| `get_project_context` | compact briefing: summary, open tasks, recent decisions/progress, top prefs, Graphify pointer |
| `search_memory` | hybrid keyword + vector search; `kind` = a layer or a kind (`decision`, `task`, `fact`…) |
| `save_memory` | one durable line; dedups, refuses secrets, `replaces=<id>` to update |
| `get_project_status` | task counts, open tasks, progress log |
| `update_project_progress` | log progress, mark tasks done, add tasks |
| `manage_project_tasks` | `list` / `all` / `add` / `done` / `reopen` / `remove` |
| `retrieve_decisions` | past decisions by meaning, or most recent |
| `search_knowledge_graph` | locally ingested graph nodes + architecture notes, plus the Graphify repo id for live graph tools |
| `log_session` | end-of-session episode; decisions → `decisions.md`, pending → tasks |
| `init_project` | create a project (`repo` = GitHub `owner/name` enables auto-detection) |

The project is detected from the server's working directory (git remote → `project.yml:repo`, then folder name/alias),
so CLI agents need no arguments. Claude Desktop has no project folder: pass `project=` or say which project.

## Setup

```bash
# first machine: unzip to ~/ai-memory, then make it a PRIVATE GitHub repo
cd ~/ai-memory && git init -b main && git add -A && git commit -m "init memory"
git remote add origin git@github.com:<you>/ai-memory.git && git push -u origin main
# every other machine: git clone git@github.com:<you>/ai-memory.git ~/ai-memory && cd ~/ai-memory
python -m venv .venv && . .venv/bin/activate                     # Windows: .venv\Scripts\activate
pip install -e .                                                  # add ".[semantic]" for real embeddings
aimem doctor
aimem install all                # dry run: shows exactly what would change
aimem install all --write        # Claude Code, Claude Desktop, Gemini CLI (VS Code: run inside a repo)
aimem install project-files --project-dir /path/to/any/repo --write   # AGENTS.md, CLAUDE.md, GEMINI.md, copilot-instructions
aimem init-project my-app --repo <you>/my-app --graphify-id <Graphify repository id>   # one per project (project-ecg is pre-seeded)
```

The installer writes the absolute path of the Python that ran it, so configs keep working without PATH or venv activation.
Existing config files are merged (never overwritten) and backed up once as `*.aimem.bak`; re-running is safe.

### Per-agent behaviour

| Agent | Connection | Automatic parts |
|---|---|---|
| Claude Code | `claude mcp add` (user scope) | SessionStart hook injects the briefing; SessionEnd hook captures candidates and syncs; `~/.claude/CLAUDE.md` block |
| Claude Desktop | `claude_desktop_config.json` | none; relies on the server instructions, say "check memory" if needed |
| Gemini CLI | `~/.gemini/settings.json` | `~/.gemini/GEMINI.md` block tells it to call `get_project_context` |
| Copilot (VS Code) | `.vscode/mcp.json` | `AGENTS.md` / `.github/copilot-instructions.md` block |
| Gemini website | no MCP support | `aimem context \| clip` (macOS `pbcopy`, Linux `xclip`) and paste |

OmniRoute sits between Claude Code and model providers; the memory server is attached to the *tool* layer, so switching
the model behind OmniRoute doesn't change what the agent remembers.

## Add another AI platform

1. If it supports MCP over stdio, register this command (set `AIMEM_AGENT` so entries show who wrote them):
   `{"command": "<python>", "args": ["-m", "aimem.server"], "env": {"AIMEM_HOME": "<repo>", "AIMEM_AGENT": "<name>"}}`
2. Put the instruction block in whatever file that agent reads: `aimem install project-files` already covers
   `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` and Copilot's file. For another file, copy the block between the `aimem:start/end` markers.
3. No MCP? Use `aimem context` / `aimem search` / `aimem save` from a shell, or paste the briefing into the chat.
4. To make it a first-class installer target, add a function to `src/aimem/install.py` (about 5 lines for JSON-based clients).

## Graphify

Graphify stays the source for live code structure (`graphify_find`, `query_graph`, `graphify_impact`, `graphify_callers`,
`graphify_tests_for`). Each project's `project.yml` holds its Graphify repository id; `get_project_context` hands the agent
that id and the tool names, so it never needs to be told. For offline or cross-project search of graph nodes,
`aimem ingest-graph graph.json --project <p>` indexes a nodes/edges JSON export (derived data, not committed).
Graphify's own `remember`/`recall` is not used: this repo is the single source of truth, which avoids two memories drifting apart.

## Sync

`aimem sync` = scan for secrets → commit `memory/` → fetch → merge → push (one retry if another machine pushed first).
The SessionEnd hook runs it; run it by hand elsewhere. After a plain `git pull`, run `aimem reindex`.
Nothing that looks like a key or token is written by `save_memory` or committed by `sync`; `.gitignore` excludes
`.env`, keys, the index and lock files.

## Optional: real semantic embeddings

The default embedder is a dependency-free hashed bag-of-words: good for shared vocabulary, blind to synonyms. For true
semantic search: `pip install -e ".[semantic]" && aimem warmup`, then add `"AIMEM_EMBEDDER": "fastembed"` to each client's
`env` and run `aimem reindex`. (Opt-in because the first model download can exceed an MCP client's start-up timeout.)

## Tools for you

`aimem dashboard` writes a static HTML overview to `.index/dashboard.html`. `aimem doctor` checks setup and scans for secrets.
`python -m unittest discover -s tests` runs the 28 tests.
