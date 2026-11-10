"""Memory facade: routing, dedup, retrieval budgets. Every interface (MCP, CLI, hooks) calls this."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from . import store
from .common import looks_secret, mid, norm, now, today
from .embed import get_embedder
from .index import Index
from .store import ALL_KINDS, EPISODE_KINDS, KIND_FILES

MAX_CURATED = 600
_HEX8 = re.compile(r"^[0-9a-f]{8}$")


def fmt_hit(r: dict, n: int = 300) -> str:
    scope = r["layer"] + (f"/{r['project']}" if r.get("project") else "")
    t = r["text"] if len(r["text"]) <= n else r["text"][: n - 1] + "…"
    st = f" ({r['status']})" if r.get("status") else ""
    return f"[{r['id']}] {scope} {r['kind']}{st} {r['ts'][:10]}: {t}"


def render(hits: list[dict], max_tokens: int = 1200, n: int = 300) -> str:
    out, used = [], 0
    for r in hits:
        line = fmt_hit(r, n)
        used += len(line) // 4 + 1
        if out and used > max_tokens:
            out.append(f"… {len(hits) - len(out)} more (raise max_tokens or narrow the query)")
            break
        out.append(line)
    return "\n".join(out)


class Memory:
    def __init__(self) -> None:
        self.emb = get_embedder()
        self.ix = Index(self.emb)
        self.ix.sync()

    def refresh(self) -> None:
        self.ix.sync()

    def close(self) -> None:
        self.ix.db.close()

    # ---- helpers ----------------------------------------------------------
    def _proj(self, project=None, cwd=None, required=False) -> str | None:
        if project:
            p = store.resolve_project(project)
            if not p:
                raise ValueError(f"unknown project '{project}'. Known: {store.list_projects() or 'none'}. "
                                 "Create one with init_project.")
            return p
        p = store.resolve_project(None, cwd) if cwd else None
        if required and not p:
            raise ValueError("no project given and none matched the working directory. "
                             f"Pass project=. Known: {store.list_projects() or 'none'}.")
        return p

    def _ppath(self, proj: str, fname: str) -> Path:
        return store.projects_dir() / proj / fname

    # ---- projects ---------------------------------------------------------
    def init_project(self, name, description="", repo="", graphify_repo="", graphify_id="", aliases=None) -> str:
        s = store.create_project(name, description, repo, graphify_repo, graphify_id, aliases)
        return f"project '{s}' ready at memory/projects/{s}/"

    # ---- save -------------------------------------------------------------
    def save(self, text, kind="fact", project=None, cwd=None, agent="agent", tags=None,
             replaces=None, force=False) -> str:
        raw = (text or "").strip()
        text = norm(raw)
        if not text:
            raise ValueError("empty text")
        if looks_secret(raw):
            return "REFUSED: text looks like a credential or secret; nothing was saved."
        kind = (kind or "fact").lower()
        if kind not in ALL_KINDS:
            raise ValueError(f"kind must be one of {ALL_KINDS}")
        self.refresh()
        if kind in EPISODE_KINDS:
            return self._episode(raw, kind, project, cwd, agent, tags)
        layer, fname = KIND_FILES[kind]
        proj = self._proj(project, cwd, required=True) if layer == "project" else None
        path = self._ppath(proj, fname) if proj else store.data() / "global" / fname
        stored = f"[{kind}] {text}" if fname == "notes.md" and kind != "note" else text
        if len(stored) > MAX_CURATED:
            raise ValueError(f"{len(stored)} chars; curated memories max {MAX_CURATED}. "
                             "Condense it, or use kind='episode' for narrative.")
        id_ = store.new_id(layer, proj, stored)
        if id_ in store.parse_file(path) and not replaces:
            return f"duplicate: [{id_}] already saved"
        if not replaces and not force:
            nd = self.ix.near_duplicate(stored, layer, proj, kind)
            if nd:
                return (f"similar memory exists [{nd[0]}] (sim {nd[2]:.2f}): {nd[1][:160]}\n"
                        f"To update it, call again with replaces='{nd[0]}'. To keep both, pass force=true.")
        line = store.fmt(stored, id_, today(), agent, " " if kind == "task" else None)
        if replaces:
            if not store.edit_line(path, replaces, line):
                return f"replaces id [{replaces}] not found in {fname}; nothing saved"
        else:
            store.append_line(path, line)
        self.refresh()
        return f"saved [{id_}] {layer}{'/' + proj if proj else ''} {kind}"

    def _episode(self, raw, kind, project, cwd, agent, tags, refs=None) -> str:
        proj = self._proj(project, cwd)
        raw = raw[:4000]
        id_ = mid("episode", proj or "", raw)
        if self.ix.get(id_):
            return f"duplicate: [{id_}] already saved"
        rec = {"id": id_, "ts": now(), "agent": agent, "project": proj, "kind": kind, "text": raw,
               "tags": tags or []}
        if refs:
            rec["refs"] = refs
        store.append_episode(rec)
        self.refresh()
        return f"saved [{id_}] episode{'/' + proj if proj else ''} {kind}"

    # ---- search -----------------------------------------------------------
    def search(self, query, project=None, cwd=None, layers=None, kinds=None, k=8, max_tokens=1200) -> str:
        self.refresh()
        proj = self._proj(project, cwd)
        hits = self.ix.search(query, proj, layers, kinds, max(1, min(k, 30)))
        return render(hits, max_tokens) or "no matches"

    def retrieve_decisions(self, query=None, project=None, cwd=None, limit=5) -> str:
        self.refresh()
        proj = self._proj(project, cwd)
        if query:
            hits = self.ix.search(query, proj, None, ["decision"], limit)
        elif proj:
            hits = self.ix.recent(proj, "decision", limit)
        else:
            hits = []
            for p in store.list_projects():
                hits += self.ix.recent(p, "decision", limit)
            hits = sorted(hits, key=lambda r: r["ts"], reverse=True)[:limit]
        return render(hits, 1500, 400) or "no decisions recorded"

    # ---- project context / status ----------------------------------------
    def _graph_pointer(self, proj: str) -> str:
        g = (store.load_project(proj).get("graphify") or {})
        ident = g.get("repository_id") or g.get("repo")
        if not ident:
            return ""
        return (f"code graph: Graphify repo {g.get('repo') or ident} → call graphify_find / query_graph / "
                f"graphify_impact with repository_id='{ident}'; shared notes: recall(repository_id='{ident}')")

    def _prefs(self, n=5) -> list[str]:
        return [f"- {r['text'][:160]}" for r in self.ix.recent_global("preference", n)]

    def get_project_context(self, project=None, cwd=None) -> str:
        self.refresh()
        proj = self._proj(project, cwd)
        prefs = self._prefs()
        if not proj:
            out = ["# No project matched. Known projects: " + (", ".join(store.list_projects()) or "none")]
            if prefs:
                out += ["## Global preferences", *prefs]
            return "\n".join(out)
        meta = store.load_project(proj)
        head = f"# Project: {proj}" + (f" — {meta['description']}" if meta.get("description") else "")
        out = [head]
        if meta.get("repo"):
            out.append(f"repo: {meta['repo']}")
        if (gp := self._graph_pointer(proj)):
            out.append(gp)
        ctx_path = self._ppath(proj, "context.md")
        if ctx_path.exists():
            body = re.sub(r"<!--.*?-->", "", ctx_path.read_text(encoding="utf-8"), flags=re.S)
            body = re.sub(r"^# .*\n", "", body.strip()).strip()
            if body and body != (meta.get("description") or "").strip():
                out += ["## Summary", body[:1800]]
        tasks = self.ix.recent(proj, "task", 5, "open")
        if tasks:
            out += ["## Open tasks", *[f"- [{t['id']}] {t['text'][:140]}" for t in tasks]]
        decs = self.ix.recent(proj, "decision", 3)
        if decs:
            out += ["## Recent decisions", *[f"- [{d['id']}] {d['text'].split(' — ')[0][:140]}" for d in decs]]
        prog = self.ix.recent(proj, "progress", 3)
        if prog:
            out += ["## Recent progress", *[f"- {p['ts']}: {p['text'][:160]}" for p in prog]]
        if prefs:
            out += ["## Global preferences", *prefs]
        out.append(f"(more: search_memory(query, project='{proj}'), retrieve_decisions, get_project_status)")
        return "\n".join(out)

    def get_project_status(self, project=None, cwd=None) -> str:
        self.refresh()
        proj = self._proj(project, cwd, required=True)
        c = self.ix.counts(proj)
        out = [f"# {proj}: {c['open']} open / {c['done']} done tasks"]
        tasks = self.ix.recent(proj, "task", 8, "open")
        out += [f"- [{t['id']}] {t['text'][:140]}" for t in tasks] or ["- no open tasks"]
        out.append("## Progress log")
        prog = self.ix.recent(proj, "progress", 5)
        out += [f"- {p['ts']} ({p['agent']}): {p['text'][:200]}" for p in prog] or ["- nothing logged yet"]
        return "\n".join(out)

    # ---- progress & tasks -------------------------------------------------
    def _find_task(self, proj: str, ref: str):
        ref = ref.strip()
        if _HEX8.match(ref):
            r = self.ix.get(ref)
            if r and r["kind"] == "task" and r["project"] == proj:
                return r
        hits = self.ix.search(ref, proj, ["project"], ["task"], 3)
        return hits[0] if hits else None

    def _set_task(self, proj: str, rec: dict, action: str, agent: str) -> str:
        path = self._ppath(proj, "tasks.md")
        cur = store.parse_file(path).get(rec["id"])
        if not cur:
            return f"task [{rec['id']}] not found in tasks.md"
        if action == "remove":
            store.edit_line(path, rec["id"], None)
        else:
            store.edit_line(path, rec["id"], store.fmt(cur["text"], rec["id"], today(), agent,
                                                       "x" if action == "done" else " "))
        self.refresh()
        return f"{action}: [{rec['id']}] {cur['text'][:80]}"

    def _add_task(self, proj: str, text: str, agent: str) -> str:
        text = norm(text)
        id_ = store.new_id("project", proj, text)
        path = self._ppath(proj, "tasks.md")
        if id_ in store.parse_file(path):
            return f"duplicate task [{id_}]"
        store.append_line(path, store.fmt(text, id_, today(), agent, " "))
        self.refresh()
        return f"added [{id_}] {text[:80]}"

    def manage_tasks(self, action, project=None, cwd=None, text=None, task=None, agent="agent",
                     include_done=False) -> str:
        proj = self._proj(project, cwd, required=True)
        self.refresh()
        if action == "list":
            rows = self.ix.recent(proj, "task", 50, None if include_done else "open")
            return "\n".join(f"[{t['id']}] {'x' if t['status'] == 'done' else ' '} {t['text']}" for t in rows) \
                or "no tasks"
        if action == "add":
            if not text:
                raise ValueError("text required for add")
            if looks_secret(text):
                return "REFUSED: looks like a secret"
            return self._add_task(proj, text, agent)
        if action in ("done", "reopen", "remove"):
            if not task:
                raise ValueError("task (id or text) required")
            rec = self._find_task(proj, task)
            return self._set_task(proj, rec, action, agent) if rec else f"no task matches '{task}'"
        raise ValueError("action must be list|add|done|reopen|remove")

    def update_progress(self, summary, project=None, cwd=None, completed=None, new_tasks=None,
                        agent="agent") -> str:
        proj = self._proj(project, cwd, required=True)
        if looks_secret(summary):
            return "REFUSED: looks like a secret"
        self.refresh()
        out = []
        text = norm(summary)[:400]
        id_ = store.new_id("project", proj, text)
        path = self._ppath(proj, "status.md")
        if id_ in store.parse_file(path):
            out.append(f"duplicate progress [{id_}]")
        else:
            store.append_line(path, store.fmt(text, id_, today(), agent))
            out.append(f"logged [{id_}]")
        self.refresh()
        for ref in completed or []:
            rec = self._find_task(proj, ref)
            out.append(self._set_task(proj, rec, "done", agent) if rec else f"no task matches '{ref}'")
        for t in new_tasks or []:
            out.append(self._add_task(proj, t, agent))
        return "\n".join(out)

    # ---- sessions ---------------------------------------------------------
    def log_session(self, summary, project=None, cwd=None, agent="agent", discoveries=None, completed=None,
                    decisions=None, pending=None, tags=None) -> str:
        """One episode with the narrative; decisions and pending items go to their own layers (by id, not copied)."""
        proj = self._proj(project, cwd)
        refs: dict[str, list[str]] = {}
        msgs = []
        if proj:
            for d in decisions or []:
                r = self.save(d, "decision", proj, agent=agent)
                if m := re.search(r"\[([0-9a-f]{8})\]", r):
                    refs.setdefault("decisions", []).append(m.group(1))
            for t in pending or []:
                r = self._add_task(proj, t, agent)
                if m := re.search(r"\[([0-9a-f]{8})\]", r):
                    refs.setdefault("tasks", []).append(m.group(1))
        parts = [summary.strip()]
        if discoveries:
            parts.append("Discoveries: " + "; ".join(norm(x) for x in discoveries))
        if completed:
            parts.append("Completed: " + "; ".join(norm(x) for x in completed))
        if not proj and (decisions or pending):
            parts.append("Decisions/pending (no project): " + "; ".join(norm(x) for x in [*(decisions or []), *(pending or [])]))
        msgs.append(self._episode("\n".join(parts), "session", proj, None, agent, tags, refs))
        if refs:
            msgs.append("linked " + ", ".join(f"{k}={','.join(v)}" for k, v in refs.items()))
        return "\n".join(msgs)

    # ---- knowledge graph --------------------------------------------------
    def search_graph(self, query, project=None, cwd=None, k=6) -> str:
        self.refresh()
        proj = self._proj(project, cwd)
        hits = self.ix.search(query, proj, ["graph"], None, k)
        hits += self.ix.search(query, proj, None, ["architecture"], 3)
        out = [render(hits, 1200, 400)] if hits else ["no local graph/architecture matches"]
        if proj and (gp := self._graph_pointer(proj)):
            out.append(gp)
        return "\n".join(out)

    def ingest_graph(self, path, project) -> str:
        proj = self._proj(project, None, required=True)
        j = json.loads(Path(path).read_text(encoding="utf-8"))
        recs = graph_records(j, proj)
        n = self.ix.replace_graph(proj, recs)
        return f"indexed {n} graph nodes for {proj} (derived; re-run after the graph changes)"


def graph_records(j: dict, project: str) -> list[dict]:
    """Tolerant reader for node/edge JSON exports (nodes|vertices, edges|links|relationships)."""
    nodes = j.get("nodes") or j.get("vertices") or []
    edges = j.get("edges") or j.get("links") or j.get("relationships") or []

    def ident(x):
        return str(x.get("id") if isinstance(x, dict) else x)

    names, meta = {}, {}
    for n in nodes:
        if isinstance(n, dict):
            i = str(n.get("id") or n.get("name") or n.get("label"))
            names[i], meta[i] = str(n.get("label") or n.get("name") or i), n
    outs, ins = defaultdict(list), defaultdict(list)
    for e in edges:
        s, t = ident(e.get("source") or e.get("from")), ident(e.get("target") or e.get("to"))
        rel = str(e.get("relation") or e.get("type") or e.get("label") or "links")
        outs[s].append((rel, t))
        ins[t].append((rel, s))
    recs = []
    for i, n in list(meta.items())[:8000]:
        file = n.get("file") or n.get("path") or n.get("source_file") or ""
        typ = n.get("type") or n.get("kind") or "node"
        o = "; ".join(f"{rel} {names.get(t, t)}" for rel, t in outs[i][:8])
        u = "; ".join(f"{names.get(s, s)} {rel}" for rel, s in ins[i][:6])
        text = (f"{names[i]} ({typ})" + (f" in {file}" if file else "")
                + (f". Out: {o}" if o else "") + (f". In: {u}" if u else ""))
        recs.append({"id": mid("graph", project, i), "kind": "graph", "text": text[:600], "ts": today(),
                     "agent": "graphify", "status": ""})
    return recs
