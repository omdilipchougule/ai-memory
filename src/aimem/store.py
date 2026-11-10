"""Git-tracked storage: Markdown bullet lines + append-only JSONL.

Line format (one memory per line, so git diffs/merges are line-level):
    - (2026-11-11 claude-code) text goes here ^a1b2c3d4
    - [ ] (2026-11-11 gemini) an open task ^a1b2c3d4        (tasks.md only; [x] = done)
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml

from .common import data, home, host, lock, mid, norm, slug, today

# kind -> (layer, filename)
KIND_FILES = {
    "profile": ("global", "profile.md"),
    "preference": ("global", "preferences.md"),
    "workflow": ("global", "workflows.md"),
    "fact": ("global", "knowledge.md"),
    "knowledge": ("global", "knowledge.md"),
    "decision": ("project", "decisions.md"),
    "progress": ("project", "status.md"),
    "task": ("project", "tasks.md"),
    "note": ("project", "notes.md"),
    "architecture": ("project", "notes.md"),
    "research": ("project", "notes.md"),
    "resource": ("project", "notes.md"),
}
FILE_KIND = {
    "profile.md": "profile", "preferences.md": "preference", "workflows.md": "workflow",
    "knowledge.md": "fact", "decisions.md": "decision", "status.md": "progress",
    "tasks.md": "task", "notes.md": "note",
}
EPISODE_KINDS = {"episode", "session", "discovery"}
ALL_KINDS = sorted(set(KIND_FILES) | EPISODE_KINDS)

LINE = re.compile(
    r"^- (?:\[(?P<chk>[ x])\] )?\((?P<date>\d{4}-\d{2}-\d{2})(?: (?P<agent>[^)\s]+))?\) "
    r"(?P<text>.*?) \^(?P<id>[0-9a-f]{8})\s*$"
)
NOTE_TAG = re.compile(r"^\[(architecture|research|resource)\] ")


def fmt(text: str, id_: str, date: str, agent: str, chk: str | None = None) -> str:
    box = f"[{chk}] " if chk is not None else ""
    return f"- {box}({date} {agent}) {norm(text)} ^{id_}"


def parse_file(path: Path) -> dict[str, dict]:
    """Return {id: record}. Duplicate ids (from union merges) collapse: done beats open, then newest."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    kind = FILE_KIND.get(path.name, "note")
    for n, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        m = LINE.match(raw)
        if not m:
            continue
        text, k = m["text"], kind
        if kind == "note" and (t := NOTE_TAG.match(text)):
            k = t.group(1)
        rec = {
            "id": m["id"], "kind": k, "text": text, "ts": m["date"], "agent": m["agent"] or "",
            "status": ("done" if m["chk"] == "x" else "open") if m["chk"] is not None else "",
            "line": n,
        }
        old = out.get(rec["id"])
        if old and (old["status"] == "done" and rec["status"] != "done"):
            continue
        out[rec["id"]] = rec
    return out


def _header(path: Path) -> str:
    title = {"decisions.md": "Decisions", "status.md": "Progress log", "tasks.md": "Tasks",
             "notes.md": "Notes", "preferences.md": "Preferences", "workflows.md": "Workflows",
             "knowledge.md": "Knowledge", "profile.md": "Profile"}.get(path.name, path.stem)
    return f"# {title}\n\n"


def append_line(path: Path, line: str) -> None:
    with lock():
        path.parent.mkdir(parents=True, exist_ok=True)
        lead = ""
        if path.exists() and path.stat().st_size:
            with open(path, "rb") as b:
                b.seek(-1, os.SEEK_END)
                lead = "" if b.read(1) == b"\n" else "\n"
        elif not path.exists():
            lead = _header(path)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(lead + line + "\n")


def edit_line(path: Path, id_: str, new_line: str | None) -> bool:
    """Replace (or delete when new_line is None) the line(s) ending in ^id. Touches only that line."""
    with lock():
        if not path.exists():
            return False
        lines = path.read_text(encoding="utf-8").splitlines()
        out, hit = [], False
        for ln in lines:
            if ln.rstrip().endswith(f"^{id_}") and LINE.match(ln):
                hit = True
                if new_line is not None and not any(o == new_line for o in out):
                    out.append(new_line)
            else:
                out.append(ln)
        if hit:
            tmp = path.with_suffix(".tmp")
            tmp.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
            os.replace(tmp, path)
        return hit


def append_episode(rec: dict) -> Path:
    """One file per (month, agent, host): two machines never write the same file."""
    agent = slug(rec.get("agent") or "agent")
    p = data() / "episodes" / rec["ts"][:7] / f"{agent}@{host()}.jsonl"
    with lock():
        p.parent.mkdir(parents=True, exist_ok=True)
        lead = ""
        if p.exists() and p.stat().st_size:
            with open(p, "rb") as b:
                b.seek(-1, os.SEEK_END)
                lead = "" if b.read(1) == b"\n" else "\n"
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(lead + json.dumps(rec, ensure_ascii=False) + "\n")
    return p


# ---- projects -------------------------------------------------------------

def projects_dir() -> Path:
    return data() / "projects"


def list_projects() -> list[str]:
    d = projects_dir()
    return sorted(p.name for p in d.iterdir() if (p / "project.yml").exists()) if d.exists() else []


def load_project(name: str) -> dict:
    return yaml.safe_load((projects_dir() / name / "project.yml").read_text(encoding="utf-8")) or {}


def create_project(name: str, description: str = "", repo: str = "", graphify_repo: str = "",
                   graphify_id: str = "", aliases: list[str] | None = None) -> str:
    s = slug(name)
    d = projects_dir() / s
    with lock():
        d.mkdir(parents=True, exist_ok=True)
        meta = d / "project.yml"
        if not meta.exists():
            cfg = {"name": s, "description": description, "aliases": aliases or [], "repo": repo,
                   "graphify": {"repo": graphify_repo or repo, "repository_id": graphify_id}}
            meta.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8", newline="\n")
        ctx = d / "context.md"
        if not ctx.exists():
            ctx.write_text(
                f"# {s}\n\n<!-- Curated summary: goal, stack, architecture in a few lines. "
                "Always loaded by get_project_context, so keep it under ~1500 chars. -->\n\n"
                f"{description}\n", encoding="utf-8", newline="\n")
    return s


def resolve_project(name: str | None = None, cwd: str | None = None) -> str | None:
    """Explicit name/alias first; otherwise match cwd's git remote, then its folder name."""
    known = list_projects()
    metas = {p: load_project(p) for p in known}
    if name:
        n = slug(name)
        for p, m in metas.items():
            if n == p or n in [slug(a) for a in (m.get("aliases") or [])] or n == slug(str(m.get("repo", "")).split("/")[-1]):
                return p
        return None
    if cwd:
        import subprocess

        try:
            url = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"], capture_output=True,
                                 text=True, timeout=5).stdout.strip()
        except Exception:
            url = ""
        m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
        if m:
            for p, meta in metas.items():
                if str(meta.get("repo", "")).lower() == m.group(1).lower():
                    return p
        base = slug(Path(cwd).name)
        for p, meta in metas.items():
            if base == p or base in [slug(a) for a in (meta.get("aliases") or [])]:
                return p
    return None


def sources():
    """Yield (path, layer, project) for everything the index should cover."""
    d = data()
    for p in sorted(d.glob("global/*.md")):
        yield p, "global", None
    pd = projects_dir()
    if pd.exists():
        for proj in sorted(pd.iterdir()):
            if proj.is_dir():
                for f in ("decisions", "status", "tasks", "notes"):
                    p = proj / f"{f}.md"
                    if p.exists():
                        yield p, "project", proj.name
    for p in sorted(d.glob("episodes/*/*.jsonl")):
        yield p, "episode", None


def new_id(layer: str, project: str | None, text: str) -> str:
    return mid(layer, project or "", text)


def stamp() -> str:
    return today()
