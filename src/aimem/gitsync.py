"""Git synchronisation with conflict handling.

Design: the append-only layout makes conflicts rare (one memory per line; episodes are one file per
agent+host). `merge=union` (see .gitattributes) keeps both sides of any line-log. The only real conflicts
are curated files (project.yml, context.md): we keep the local copy and save the remote copy beside it
as <file>.conflict-<host> for a human to reconcile. Nothing that looks like a secret is ever committed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .common import data, home, host, looks_secret, now


def git(*a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(home()), *a], capture_output=True, text=True)


def _ident() -> list[str]:
    if git("config", "user.email").stdout.strip():
        return []
    return ["-c", "user.name=ai-memory", "-c", "user.email=ai-memory@localhost"]


def scan_secrets() -> list[str]:
    hits = []
    for p in data().rglob("*"):
        if p.is_file() and p.suffix in {".md", ".jsonl", ".yml", ".yaml", ".json", ".txt"}:
            for n, ln in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if looks_secret(ln):
                    hits.append(f"{p.relative_to(home()).as_posix()}:{n}")
    return hits


def _resolve_conflicts() -> str:
    files = git("diff", "--name-only", "--diff-filter=U").stdout.split()
    for f in files:
        theirs = git("show", f":3:{f}")
        if theirs.returncode == 0:
            side = home() / f"{f}.conflict-{host()}"
            side.write_text(theirs.stdout, encoding="utf-8", newline="\n")
            git("add", "--", side.relative_to(home()).as_posix())
        git("checkout", "--ours", "--", f)
        git("add", "--", f)
    c = git(*_ident(), "commit", "-q", "--no-edit")
    if c.returncode != 0:
        return "merge conflict could not be auto-resolved: " + c.stderr.strip()[:200]
    return f"conflict in {files}: kept local, remote copy saved as *.conflict-{host()}"


def sync(push: bool = True) -> str:
    if not (home() / ".git").exists():
        return "not a git repo yet (git init; git remote add origin <url>)"
    log: list[str] = []
    bad = scan_secrets()
    if bad:
        return f"REFUSED to commit: possible secrets at {bad[:5]}. Remove them, then re-run sync."
    git("add", "-A", "--", "memory")
    for extra in (".gitattributes", ".gitignore", "AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        if (home() / extra).exists():
            git("add", "--", extra)
    if git("diff", "--cached", "--quiet").returncode != 0:
        r = git(*_ident(), "commit", "-q", "-m", f"memory: {host()} {now()}")
        log.append("committed" if r.returncode == 0 else "commit failed: " + r.stderr.strip()[:200])
    if not git("remote").stdout.split():
        return "; ".join(log + ["no remote configured (local only)"])
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    up = f"origin/{branch}"

    def pull() -> str | None:
        f = git("fetch", "-q", "origin")
        if f.returncode != 0:
            return "fetch failed (offline?): " + f.stderr.strip()[:150]
        if git("rev-parse", "--verify", "-q", up).returncode == 0:
            m = git(*_ident(), "merge", "--no-edit", "-q", "--allow-unrelated-histories", up)
            if m.returncode != 0:
                log.append(_resolve_conflicts())
        return None

    head0 = git("rev-parse", "HEAD").stdout.strip()
    if (err := pull()):
        return "; ".join(log + [err])
    if git("rev-parse", "HEAD").stdout.strip() != head0:  # merge brought in changes: make the index rescan episodes
        flag = home() / ".index" / "rescan"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("1")
        log.append("merged remote changes")
    if push:
        p = git("push", "-q", "-u", "origin", branch)
        if p.returncode != 0:  # raced with another machine: merge once more and retry
            pull()
            p = git("push", "-q", "-u", "origin", branch)
        log.append("pushed" if p.returncode == 0 else "push failed: " + p.stderr.strip()[:200])
    return "; ".join(log) or "up to date"
