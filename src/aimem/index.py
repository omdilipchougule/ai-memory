"""Derived, rebuildable search index (SQLite FTS5 + vectors). Never committed to git.

Source of truth is always the Markdown/JSONL under memory/. Deleting .index/ is safe.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import datetime as dt

import numpy as np

from . import store
from .common import home

SCHEMA = """
create table if not exists items(
  id text primary key, layer text, project text, kind text, text text,
  ts text, agent text, status text, src text, emb blob);
create index if not exists items_src on items(src);
create index if not exists items_pk on items(project, kind);
create virtual table if not exists fts using fts5(text, tokenize='porter unicode61');
create table if not exists files(src text primary key, sig text, off integer default 0);
create table if not exists meta(k text primary key, v text);
"""
_WORD = re.compile(r"[a-z0-9_]+")


class Index:
    def __init__(self, emb) -> None:
        self.emb = emb
        p = home() / ".index"
        p.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(p / "index.db", timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cur = self.db.execute("select v from meta where k='embedder'").fetchone()
        if not cur or cur[0] != emb.name:  # model changed -> re-embed everything
            self.db.executescript("delete from items; delete from fts; delete from files;")
            self.db.execute("insert or replace into meta values('embedder', ?)", (emb.name,))
            self.db.commit()

    # ---- writes -----------------------------------------------------------
    def _put(self, r: dict, vec: np.ndarray) -> None:
        row = self.db.execute("select rowid from items where id=?", (r["id"],)).fetchone()
        vals = (r["layer"], r.get("project"), r["kind"], r["text"], r["ts"], r.get("agent", ""),
                r.get("status", ""), r["src"], vec.tobytes())
        if row:
            rid = row[0]
            self.db.execute("update items set layer=?,project=?,kind=?,text=?,ts=?,agent=?,status=?,src=?,emb=? "
                            "where rowid=?", (*vals, rid))
            self.db.execute("delete from fts where rowid=?", (rid,))
        else:
            rid = self.db.execute("insert into items(layer,project,kind,text,ts,agent,status,src,emb,id) "
                                  "values(?,?,?,?,?,?,?,?,?,?)", (*vals, r["id"])).lastrowid
        self.db.execute("insert into fts(rowid,text) values(?,?)", (rid, r["text"]))

    def _delete(self, ids: list[str]) -> None:
        for i in ids:
            row = self.db.execute("select rowid from items where id=?", (i,)).fetchone()
            if row:
                self.db.execute("delete from fts where rowid=?", (row[0],))
                self.db.execute("delete from items where rowid=?", (row[0],))

    def _drop_src(self, src: str) -> None:
        ids = [r[0] for r in self.db.execute("select id from items where src=?", (src,))]
        self._delete(ids)
        self.db.execute("delete from files where src=?", (src,))

    def _embed_put(self, recs: list[dict]) -> None:
        if recs:
            vecs = self.emb.embed([r["text"] for r in recs])
            for r, v in zip(recs, vecs):
                self._put(r, v)

    # ---- incremental sync -------------------------------------------------
    def sync(self) -> None:
        flag = home() / ".index" / "rescan"  # set by `aimem sync` after a git merge touched episode files
        if flag.exists():
            self.db.execute("update files set off=0 where src like 'memory/episodes/%'")
            flag.unlink()
        seen = set()
        for path, layer, proj in store.sources():
            src = path.relative_to(home()).as_posix()
            seen.add(src)
            st = path.stat()
            row = self.db.execute("select sig, off from files where src=?", (src,)).fetchone()
            if layer == "episode":
                self._sync_episode(path, src, row)
            else:
                sig = f"{st.st_size}:{st.st_mtime_ns}"
                if not (row and row["sig"] == sig):
                    self._sync_md(path, src, layer, proj, sig)
        for (src,) in self.db.execute("select src from files").fetchall():
            if src not in seen and not src.startswith("graph:"):
                self._drop_src(src)
        self.db.commit()

    def _sync_md(self, path, src, layer, proj, sig) -> None:
        recs = store.parse_file(path)
        have = {r["id"]: r for r in self.db.execute("select id,text,status from items where src=?", (src,))}
        self._delete([i for i in have if i not in recs])
        todo = []
        for i, r in recs.items():
            r.update(layer=layer, project=proj, src=src)
            old = have.get(i)
            if old is None or old["text"] != r["text"]:
                todo.append(r)
            elif old["status"] != r["status"]:
                self.db.execute("update items set status=?, ts=? where id=?", (r["status"], r["ts"], i))
        self._embed_put(todo)
        self.db.execute("insert or replace into files(src,sig,off) values(?,?,0)", (src, sig))

    def _sync_episode(self, path, src, row) -> None:
        size = path.stat().st_size
        off = row["off"] if row else 0
        if row and size < off:  # file was rewritten: rebuild this source
            self._drop_src(src)
            off, row = 0, None
        if row and size == off:
            return
        with open(path, "rb") as f:
            f.seek(off)
            chunk = f.read()
        end = chunk.rfind(b"\n")
        if end < 0:
            return
        recs = []
        for ln in chunk[: end + 1].decode("utf-8", "replace").splitlines():
            try:
                j = json.loads(ln)
                recs.append({"id": j["id"], "layer": "episode", "project": j.get("project") or None,
                             "kind": j.get("kind", "session"), "text": j["text"], "ts": j["ts"],
                             "agent": j.get("agent", ""), "status": "", "src": src})
            except (ValueError, KeyError):
                continue
        todo = []
        for r in recs:  # a rescan re-reads old lines; only embed what is new or changed
            row0 = self.db.execute("select text from items where id=?", (r["id"],)).fetchone()
            if not row0 or row0[0] != r["text"]:
                todo.append(r)
        self._embed_put(todo)
        self.db.execute("insert or replace into files(src,sig,off) values(?,?,?)", (src, "", off + end + 1))

    def replace_graph(self, project: str, recs: list[dict]) -> int:
        src = f"graph:{project}"
        self._drop_src(src)
        for r in recs:
            r.update(layer="graph", project=project, src=src)
        self._embed_put(recs)
        self.db.execute("insert or replace into files(src,sig,off) values(?,?,0)", (src, "graph"))
        self.db.commit()
        return len(recs)

    # ---- reads ------------------------------------------------------------
    def _where(self, project, layers, kinds):
        w, a = ["1=1"], []
        if project:
            w.append("(project=? or project is null)")
            a.append(project)
        if layers:
            w.append(f"layer in ({','.join('?' * len(layers))})")
            a += list(layers)
        if kinds:
            w.append(f"kind in ({','.join('?' * len(kinds))})")
            a += list(kinds)
        return " and ".join(w), a

    def search(self, query, project=None, layers=None, kinds=None, k=8) -> list[dict]:
        cond, args = self._where(project, layers, kinds)
        lex, vec = [], []
        words = list(dict.fromkeys(_WORD.findall(query.lower())))
        if words:
            q = " OR ".join(f'"{w}"' for w in words)
            lex = [r[0] for r in self.db.execute(
                f"select items.rowid from fts join items on items.rowid=fts.rowid "
                f"where fts match ? and {cond} order by bm25(fts) limit 40", [q, *args])]
        rows = self.db.execute(f"select rowid, emb from items where {cond}", args).fetchall()
        if rows:
            m = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), -1)
            sims = m @ self.emb.embed([query])[0]
            vec = [rows[i][0] for i in np.argsort(-sims)[:40] if sims[i] >= self.emb.min_sim]
        score: dict[int, float] = {}
        for lst in (lex, vec):
            for rank, rid in enumerate(lst):
                score[rid] = score.get(rid, 0.0) + 1.0 / (60 + rank)
        if not score:
            return []
        marks = ",".join("?" * len(score))
        out = [dict(r) for r in self.db.execute(
            f"select rowid as rid,id,layer,project,kind,text,ts,agent,status from items where rowid in ({marks})",
            list(score))]
        today = dt.date.today()
        for r in out:
            s = score[r["rid"]]
            if project and r["project"] == project:
                s *= 1.2
            try:
                age = (today - dt.date.fromisoformat(r["ts"][:10])).days
                s *= 1 + 0.1 * math.exp(-max(age, 0) / 90)
            except ValueError:
                pass
            if r["status"] == "done":
                s *= 0.6
            r["score"] = s
        return sorted(out, key=lambda r: -r["score"])[:k]

    def recent(self, project, kind, limit=5, status=None) -> list[dict]:
        sql = "select id,layer,project,kind,text,ts,agent,status from items where project=? and kind=?"
        a = [project, kind]
        if status:
            sql += " and status=?"
            a.append(status)
        sql += " order by ts desc, rowid desc limit ?"
        return [dict(r) for r in self.db.execute(sql, [*a, limit])]

    def recent_global(self, kind, limit=6) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "select id,layer,project,kind,text,ts,agent,status from items where layer='global' and kind=? "
            "order by ts desc, rowid desc limit ?", (kind, limit))]

    def counts(self, project) -> dict:
        r = self.db.execute("select sum(status='open'), sum(status='done') from items where project=? and kind='task'",
                            (project,)).fetchone()
        return {"open": r[0] or 0, "done": r[1] or 0}

    def get(self, id_: str):
        r = self.db.execute("select id,layer,project,kind,text,ts,agent,status from items where id=?", (id_,)).fetchone()
        return dict(r) if r else None

    def near_duplicate(self, text, layer, project, kind):
        rows = self.db.execute("select id,text,emb from items where layer=? and ifnull(project,'')=? and kind=?",
                               (layer, project or "", kind)).fetchall()
        if not rows:
            return None
        m = np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32).reshape(len(rows), -1)
        sims = m @ self.emb.embed([text])[0]
        i = int(np.argmax(sims))
        return (rows[i][0], rows[i][1], float(sims[i])) if sims[i] >= self.emb.dup else None

    def stats(self) -> dict:
        return {r[0]: r[1] for r in self.db.execute("select layer, count(*) from items group by layer")}
