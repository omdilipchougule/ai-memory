"""Paths, ids, locking and small text helpers shared by every module."""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import os
import re
import socket
import time
from pathlib import Path


def home() -> Path:
    """Repo root. Override with AIMEM_HOME; default ~/ai-memory."""
    return Path(os.environ.get("AIMEM_HOME") or (Path.home() / "ai-memory")).expanduser()


def data() -> Path:
    return home() / "memory"


def today() -> str:
    return dt.date.today().isoformat()


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "untitled"


def host() -> str:
    return slug(socket.gethostname())[:24]


_TAIL_ID = re.compile(r"\s*\^[0-9a-f]{8}\s*$")


def norm(text: str) -> str:
    """Collapse whitespace and strip a trailing ^id so text stays one clean line."""
    return _TAIL_ID.sub("", re.sub(r"\s+", " ", text or "")).strip()


def mid(*parts: str) -> str:
    """Stable 8-hex id from normalised parts (same text -> same id -> free dedup)."""
    h = hashlib.sha1("\x1f".join(norm(p).lower() for p in parts).encode("utf-8"))
    return h.hexdigest()[:8]


@contextlib.contextmanager
def lock(name: str = "write", timeout: float = 10.0):
    """Tiny cross-platform lock (O_EXCL lockfile, stale after 30s)."""
    p = home() / ".lock" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    while True:
        try:
            os.close(os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - p.stat().st_mtime > 30:
                    p.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.time() - start > timeout:
                raise TimeoutError("memory write lock busy")
            time.sleep(0.05)
    try:
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            p.unlink()


_SECRET = re.compile(
    r"(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AIza[0-9A-Za-z_-]{30,}"
    r"|AKIA[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?i:(?:api[_-]?key|secret|token|passwd|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-./+]{16,}))"
)


def looks_secret(text: str) -> bool:
    return bool(_SECRET.search(text or ""))
