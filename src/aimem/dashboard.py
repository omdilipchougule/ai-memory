"""Static, read-only HTML overview of projects, tasks, decisions and progress (the Ponytail-style UI piece)."""
from __future__ import annotations

import html

from . import store
from .common import home, today

CSS = """
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1c1a;--mut:#6b6a66;--line:#e4e2dc;--acc:#2f6f5e;--warn:#b5651d}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--ink:#ecebe7;--mut:#9a9890;--line:#33322f;--acc:#6fc2a9;--warn:#e0a060}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1000px;margin:0 auto;padding:32px 16px 64px}h1{font-size:22px;margin:0 0 4px}.sub{color:var(--mut);margin:0 0 24px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(440px,1fr));gap:16px}
@media(max-width:520px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px}
.card h2{font-size:17px;margin:0 0 2px}.desc{color:var(--mut);font-size:13px;margin:0 0 12px}
.stats{display:flex;gap:8px;margin-bottom:12px}.pill{font-size:12px;border:1px solid var(--line);border-radius:99px;padding:2px 10px}
.pill b{color:var(--acc)}h3{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);margin:14px 0 6px}
ul{margin:0;padding:0;list-style:none}li{padding:5px 0;border-top:1px solid var(--line);font-size:14px}li:first-child{border-top:0}
li small{color:var(--mut)}.empty{color:var(--mut);font-size:13px}
"""


def _ul(items: list[str]) -> str:
    return "<ul>" + "".join(items) + "</ul>" if items else '<p class="empty">nothing yet</p>'


def build(mem) -> str:
    mem.refresh()
    cards = []
    for p in store.list_projects():
        meta = store.load_project(p)
        c = mem.ix.counts(p)
        tasks = [f"<li>{html.escape(t['text'])} <small>{t['id']}</small></li>" for t in mem.ix.recent(p, "task", 6, "open")]
        decs = [f"<li>{html.escape(d['text'].split(' — ')[0][:120])} <small>{d['ts']}</small></li>"
                for d in mem.ix.recent(p, "decision", 4)]
        prog = [f"<li>{html.escape(r['text'][:140])} <small>{r['ts']} · {html.escape(r['agent'])}</small></li>"
                for r in mem.ix.recent(p, "progress", 4)]
        cards.append(
            f'<section class="card"><h2>{html.escape(p)}</h2><p class="desc">{html.escape(meta.get("description") or "")}</p>'
            f'<div class="stats"><span class="pill"><b>{c["open"]}</b> open</span><span class="pill"><b>{c["done"]}</b> done</span></div>'
            f"<h3>Open tasks</h3>{_ul(tasks)}<h3>Decisions</h3>{_ul(decs)}<h3>Progress</h3>{_ul(prog)}</section>")
    st = mem.ix.stats()
    sub = " · ".join(f"{k}: {v}" for k, v in sorted(st.items())) or "empty"
    return (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>AI memory</title><style>{CSS}</style><main><h1>AI memory</h1>"
            f'<p class="sub">{html.escape(sub)} · generated {today()}</p><div class="grid">{"".join(cards)}</div></main>')


def write(mem) -> str:
    p = home() / ".index" / "dashboard.html"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build(mem), encoding="utf-8")
    return str(p)
