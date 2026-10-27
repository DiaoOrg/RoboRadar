"""生成 Markdown / HTML 报告，保存在 data/reports/。"""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta

from . import db
from .config import REPORT_DIR


def digest_papers(date: str, include_all: bool = False) -> list[dict]:
    cond = "" if include_all else "AND (matched=1 OR semantic=1)"
    rows = db.query(f"SELECT * FROM papers WHERE digest_date=? {cond} ORDER BY relevance DESC, score DESC", (date,))
    return [db.decode_paper(r) for r in rows]


def authors_line(p: dict) -> str:
    a = p["authors"]
    s = ", ".join(a[:3]) + (f" 等 {len(a)} 人" if len(a) > 3 else "")
    corr = p.get("corresponding") or []
    return s + f"｜通讯：{', '.join(corr) if corr else '未标注'}"


def tags_line(p: dict) -> str:
    tags = "、".join(p.get("tags") or p.get("matched_topics") or [])
    return tags + (f"｜{p['platform']}" if p.get("platform") else "")


def paper_markdown(i: int, p: dict) -> str:
    aid = p["arxiv_id"]
    lines = [f"### {i}. [{p['title']}](https://arxiv.org/abs/{aid})"]
    if p.get("title_zh"):
        lines.append(f"**{p['title_zh']}**")
    lines.append(f"- 作者：{authors_line(p)}")
    if p.get("affiliations"):
        lines.append(f"- 单位：{'；'.join(p['affiliations'])}")
    lines.append(f"- 标签：{tags_line(p)}")
    if p.get("method"):
        lines.append(f"- 方法：{p['method']}")
    if p.get("result"):
        lines.append(f"- 结果：{p['result']}")
    links = [f"[PDF](https://arxiv.org/pdf/{aid})"] + [f"[{l['label']}]({l['url']})" for l in p.get("links") or []]
    lines.append(f"- 链接：{' · '.join(links)}")
    return "\n".join(lines)


def arxiv_markdown(date: str) -> str:
    papers = digest_papers(date)
    total = db.query("SELECT COUNT(*) AS n FROM papers WHERE digest_date=?", (date,))[0]["n"]
    out = [f"# arXiv 机器人论文日报 · {date}", "",
           f"共 {total} 篇新论文，其中与关注主题相关 **{len(papers)}** 篇。", ""]
    out += [paper_markdown(i, p) + "\n" for i, p in enumerate(papers, 1)]
    return "\n".join(out)


CSS = """body{font-family:-apple-system,"PingFang SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif;max-width:920px;
margin:32px auto;padding:0 20px;color:#1f2937;line-height:1.65}h1{font-size:24px}.card{border:1px solid #e5e7eb;
border-radius:12px;padding:16px 20px;margin:14px 0}.card h3{margin:0 0 4px;font-size:17px}a{color:#2563eb;
text-decoration:none}.zh{color:#374151;font-weight:600;margin-bottom:6px}.meta{color:#4b5563;font-size:14px}
.tag{display:inline-block;background:#eef2ff;color:#3730a3;border-radius:999px;padding:1px 10px;margin:2px 4px 2px 0;
font-size:13px}.plat{background:#ecfdf5;color:#047857}.sum{margin-top:6px}.muted{color:#6b7280;font-size:13px}"""


def arxiv_html(date: str) -> str:
    papers = digest_papers(date)
    cards = []
    for i, p in enumerate(papers, 1):
        aid = p["arxiv_id"]
        esc = html.escape
        tags = "".join(f'<span class="tag">{esc(t)}</span>' for t in (p.get("tags") or p.get("matched_topics") or []))
        if p.get("platform"):
            tags += f'<span class="tag plat">{esc(p["platform"])}</span>'
        links = [f'<a href="https://arxiv.org/pdf/{aid}">PDF</a>'] + [
            f'<a href="{esc(l["url"])}">{esc(l["label"])}</a>' for l in p.get("links") or []]
        cards.append(f"""<div class="card"><h3>{i}. <a href="https://arxiv.org/abs/{aid}">{esc(p['title'])}</a></h3>
<div class="zh">{esc(p.get('title_zh') or '')}</div>
<div class="meta">👥 {esc(authors_line(p))}</div>
<div class="meta">🏛 {esc('；'.join(p.get('affiliations') or []) or '—')}</div>
<div>{tags}</div>
<div class="sum">🔧 {esc(p.get('method') or '')}<br>📊 {esc(p.get('result') or '')}</div>
<div class="muted">{' · '.join(links)}</div></div>""")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>arXiv 日报 {date}</title><style>{CSS}</style>"
            f"</head><body><h1>arXiv 机器人论文日报 · {date}</h1><p class='muted'>相关论文 {len(papers)} 篇</p>"
            + "".join(cards) + "</body></html>")


def write_arxiv_report(date: str):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    md, ht = REPORT_DIR / f"arxiv-{date}.md", REPORT_DIR / f"arxiv-{date}.html"
    md.write_text(arxiv_markdown(date), "utf-8")
    ht.write_text(arxiv_html(date), "utf-8")
    return md, ht


TYPE_ICON = {"video": "🎬", "repo": "📦", "release": "🚀", "commit": "🛠", "post": "📝", "link": "🔗", "paper": "📄"}


def watch_items(days: int = 1) -> list[dict]:
    since = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
    return db.query("""SELECT items.*, entities.name AS entity FROM items JOIN entities ON entities.id=items.entity_id
                       WHERE items.fetched_at >= ? AND items.baseline=0 ORDER BY items.published DESC""", (since,))


def watch_markdown(days: int = 1, items: list[dict] | None = None) -> str:
    items = items if items is not None else watch_items(days)
    out = [f"# 机器人圈动态 · {datetime.now():%Y-%m-%d}", "", f"新增 {len(items)} 条", ""]
    by_ent: dict[str, list[dict]] = {}
    for it in items:
        by_ent.setdefault(it["entity"], []).append(it)
    for ent, its in by_ent.items():
        out.append(f"## {ent}")
        for it in its:
            s = f"- {TYPE_ICON.get(it['type'], '•')} [{it['title']}]({it['url']})"
            if it.get("summary_zh"):
                s += f" — {it['summary_zh']}"
            out.append(s)
        out.append("")
    return "\n".join(out)


def write_watch_report():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"watch-{datetime.now():%Y-%m-%d}.md"
    path.write_text(watch_markdown(1), "utf-8")
    return path


def to_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
