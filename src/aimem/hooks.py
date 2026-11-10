"""Automatic capture + session-start briefing for agents that support hooks (Claude Code).

Auto-extraction is rule-based on purpose: zero API calls, zero tokens. It records *candidate* decisions,
discoveries and open items from the end of a transcript as an episode tagged `auto`. Auto items go to the
episodic layer only; the curated layers (decisions.md, tasks.md) are written by explicit calls.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .common import looks_secret, norm

PATTERNS = {
    "decisions": re.compile(r"\b(decided to|we(?:'ll| will) (?:use|go with)|going with|chose|let'?s (?:use|go with)|"
                            r"switch(?:ed)? to|decision:)\b", re.I),
    "discoveries": re.compile(r"\b(root cause|turns out|the (?:bug|issue|problem) (?:was|is)|gotcha|workaround|"
                              r"discovered|fixed by)\b", re.I),
    "open": re.compile(r"\b(todo|next step|still need to|remaining:|follow[- ]up)\b", re.I),
}
LABEL = {"decisions": "Decisions", "discoveries": "Discoveries", "open": "Open"}


def _texts(transcript: Path, last: int = 80) -> tuple[list[str], bool]:
    """Assistant text blocks from a Claude Code-style JSONL transcript, plus whether log_session was already used."""
    out, used = [], False
    try:
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], False
    used = any("log_session" in ln for ln in lines)
    for ln in lines[-last:]:
        try:
            j = json.loads(ln)
        except ValueError:
            continue
        msg = j.get("message") or {}
        if (j.get("type") or msg.get("role")) != "assistant":
            continue
        c = msg.get("content")
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            out += [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
    return out, used


def extract(transcript: Path, per_type: int = 3) -> tuple[str, bool]:
    texts, used = _texts(transcript)
    found: dict[str, list[str]] = {k: [] for k in PATTERNS}
    for t in texts:
        for sent in re.split(r"(?<=[.!?])\s+|\n+", t):
            s = norm(re.sub(r"^[\s>*#\-\d.)]+", "", sent))
            if not 25 <= len(s) <= 280 or looks_secret(s):
                continue
            for k, rx in PATTERNS.items():
                if rx.search(s) and s not in found[k] and len(found[k]) < per_type:
                    found[k].append(s)
    body = " ".join(f"{LABEL[k]}: " + " | ".join(v) + "." for k, v in found.items() if v)
    return (f"Auto-captured, unreviewed. {body}" if body else ""), used


def session_start(cwd: str) -> str:
    from .core import Memory

    m = Memory()
    try:
        return m.get_project_context(None, cwd)
    finally:
        m.close()


def session_end(payload: dict) -> str:
    from .core import Memory
    from . import gitsync

    cwd = payload.get("cwd") or "."
    msg = ""
    tp = payload.get("transcript_path")
    if tp:
        text, used = extract(Path(tp))
        if text and not used:
            m = Memory()
            try:
                msg = m.save(text, "episode", cwd=cwd, agent="auto-" + (payload.get("agent") or "claude-code"),
                             tags=["auto"])
            finally:
                m.close()
    return (msg + "; " if msg else "") + gitsync.sync()
