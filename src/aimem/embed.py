"""Embedders. Default is a dependency-free hashing embedder; fastembed (ONNX) is opt-in.

Why opt-in: fastembed downloads a model on first use, which can exceed an MCP
client's start-up timeout. Run `aimem warmup` once, then set AIMEM_EMBEDDER=fastembed.
"""
from __future__ import annotations

import os
import re
import zlib

import numpy as np

_TOK = re.compile(r"[a-z0-9_]+")


def tokens(text: str) -> list[str]:
    out = []
    for t in _TOK.findall(text.lower()):
        if len(t) > 4:
            for suf in ("ing", "ed", "es", "s"):
                if t.endswith(suf) and len(t) - len(suf) >= 3:
                    t = t[: -len(suf)]
                    break
        out.append(t)
    return out


def _unit(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1
    return (m / n).astype(np.float32)


class HashEmbedder:
    """Feature-hashed unigrams + bigrams. Lexical-semantic only (no synonyms)."""

    name = "hash-384"
    dim = 384
    dup = 0.88        # cosine above which two memories count as near-duplicates
    min_sim = 0.10    # ignore vector hits below this

    def embed(self, texts: list[str]) -> np.ndarray:
        m = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            tk = tokens(t[:1500])
            for a in tk:
                m[i, zlib.crc32(a.encode()) % self.dim] += 1.0
            for a, b in zip(tk, tk[1:]):
                m[i, zlib.crc32(f"{a} {b}".encode()) % self.dim] += 0.5
        np.log1p(m, out=m)
        return _unit(m)


class FastEmbedder:
    """Real semantic embeddings via fastembed (BAAI/bge-small-en-v1.5, 384-d, local ONNX)."""

    name = "fastembed-bge-small-en-v1.5"
    dim = 384
    dup = 0.93
    min_sim = 0.35

    def __init__(self) -> None:
        from fastembed import TextEmbedding  # optional dependency

        self._m = TextEmbedding("BAAI/bge-small-en-v1.5")

    def embed(self, texts: list[str]) -> np.ndarray:
        arr = np.array(list(self._m.embed([t[:1500] for t in texts])), dtype=np.float32)
        return _unit(arr)


def get_embedder():
    want = os.environ.get("AIMEM_EMBEDDER", "hash").lower()
    if want == "fastembed":
        try:
            return FastEmbedder()
        except Exception as e:  # missing package / no cached model
            import sys

            print(f"[aimem] fastembed unavailable ({type(e).__name__}); using hash embedder", file=sys.stderr)
    return HashEmbedder()
