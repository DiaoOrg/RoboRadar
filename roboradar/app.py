"""Web GUI 后端（FastAPI）。启动：python -m roboradar serve"""
from __future__ import annotations

import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import db, digest, notify, progress, report, scheduler, sources, watch
from .config import ensure_dirs, load_settings, save_settings
from .llm import get_llm

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("roboradar")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_dirs()
    db.init_db()
    scheduler.reschedule()
    scheduler.catch_up()
    yield
    scheduler.shutdown()


app = FastAPI(title="RoboRadar", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _bg(fn, **kw):
    threading.Thread(target=fn, kwargs=kw, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


# ------------------------------------------------------------ 设置

def _mask(s: dict) -> dict:
    s = json.loads(json.dumps(s))
    for sec, key in (("llm", "anthropic_api_key"), ("llm", "openai_api_key"), ("network", "github_token"),
                     ("notify", "smtp_password")):
        if s[sec].get(key):
            s[sec][key] = "••••••" + s[sec][key][-4:]
    return s


@app.get("/api/settings")
def get_settings():
    return _mask(load_settings())


@app.put("/api/settings")
def put_settings(new: dict = Body(...)):
    old = load_settings()
    # 掩码值表示“未修改”
    for sec, key in (("llm", "anthropic_api_key"), ("llm", "openai_api_key"), ("network", "github_token"),
                     ("notify", "smtp_password")):
        v = new.get(sec, {}).get(key)
        if isinstance(v, str) and v.startswith("••••••"):
            new[sec][key] = old[sec][key]
    for sec in ("arxiv", "watch"):
        for t in new.get(sec, {}).get("times", []):
            try:
                h, m = scheduler._parse(t)
                assert 0 <= h < 24 and 0 <= m < 60
            except Exception:
                raise HTTPException(400, f"时间格式错误：{t}（应为 HH:MM）")
    saved = save_settings(new)
    scheduler.reschedule()
    return _mask(saved)


# ------------------------------------------------------------ 状态与运行

@app.get("/api/status")
def status():
    runs = db.query("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
    return {"progress": progress.snapshot(), "next_runs": scheduler.next_runs(), "last_run": runs[0] if runs else None}


@app.get("/api/runs")
def runs(limit: int = 50):
    rows = db.query("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["stats"] = json.loads(r["stats"] or "{}")
    return rows


@app.post("/api/run/arxiv")
def run_arxiv(body: dict = Body(default={})):
    if progress.lock("arxiv").locked():
        raise HTTPException(409, "arXiv 任务正在运行")
    trigger = "manual-notify" if body.get("notify") else "manual"
    _bg(digest.run_digest, date=body.get("date") or None, trigger=trigger, force=bool(body.get("force")))
    return {"ok": True}


@app.post("/api/run/watch")
def run_watch(body: dict = Body(default={})):
    if progress.lock("watch").locked():
        raise HTTPException(409, "关注列表任务正在运行")
    _bg(watch.run_watch, entity_id=body.get("entity_id"), trigger="manual")
    return {"ok": True}


# ------------------------------------------------------------ 论文

@app.get("/api/digest/dates")
def digest_dates():
    return db.query("""SELECT digest_date AS date, COUNT(*) AS total, SUM(matched OR semantic) AS related
                       FROM papers GROUP BY digest_date ORDER BY digest_date DESC LIMIT 60""")


@app.get("/api/arxiv/available-dates")
def available_dates():
    """recent 页上可回溯的公告日。"""
    from . import arxiv

    cats = load_settings()["arxiv"]["categories"]
    days = arxiv.fetch_recent_days(cats[0])
    return [{"date": d, "count": len(v)} for d, v in sorted(days.items(), reverse=True)]


@app.get("/api/digest")
def get_digest(date: str | None = None, all: int = 0):
    if not date:
        r = db.query("SELECT MAX(digest_date) AS d FROM papers")
        date = r[0]["d"] if r and r[0]["d"] else None
    if not date:
        return {"date": None, "papers": [], "total": 0}
    papers = report.digest_papers(date, include_all=bool(all))
    for p in papers:
        p.pop("header_text", None)
    total = db.query("SELECT COUNT(*) AS n FROM papers WHERE digest_date=?", (date,))[0]["n"]
    return {"date": date, "papers": papers, "total": total}


@app.get("/api/papers/starred")
def starred_papers():
    rows = [db.decode_paper(r) for r in db.query("SELECT * FROM papers WHERE starred=1 ORDER BY digest_date DESC")]
    for p in rows:
        p.pop("header_text", None)
    return rows


@app.get("/api/papers/search")
def search_papers(q: str, limit: int = 100):
    like = f"%{q}%"
    rows = [db.decode_paper(r) for r in db.query(
        """SELECT * FROM papers WHERE title LIKE ? OR abstract LIKE ? OR authors LIKE ? OR title_zh LIKE ?
           ORDER BY digest_date DESC LIMIT ?""", (like, like, like, like, limit))]
    for p in rows:
        p.pop("header_text", None)
    return rows


@app.post("/api/papers/{aid}/flag")
def flag_paper(aid: str, body: dict = Body(...)):
    field = body.get("field")
    if field not in ("starred", "read"):
        raise HTTPException(400, "field 只能是 starred / read")
    with db.tx() as conn:
        conn.execute(f"UPDATE papers SET {field}=? WHERE arxiv_id=?", (int(bool(body.get("value"))), aid))
    return {"ok": True}


@app.post("/api/papers/{aid}/summarize")
def summarize_paper(aid: str, body: dict = Body(default={})):
    try:
        p = digest.summarize_one(aid, include=bool(body.get("include")))
    except KeyError:
        raise HTTPException(404, "论文不存在")
    p.pop("header_text", None)
    return p


@app.post("/api/digest/rematch")
def rematch(body: dict = Body(...)):
    digest.rematch_date(body["date"])
    return {"ok": True}


# ------------------------------------------------------------ 动态

@app.get("/api/feed")
def feed(days: int = 14, entity_id: int | None = None, type: str | None = None, unread: int = 0,
         starred: int = 0, baseline: int = 0, limit: int = 300):
    cond, params = ["1=1"], []
    if not baseline and not entity_id and not starred:
        cond.append("items.baseline=0")  # 首次抓取的主页已有内容不算“新动态”
    if days:
        cond.append("items.published >= datetime('now', ?)")
        params.append(f"-{days} days")
    if entity_id:
        cond.append("items.entity_id=?")
        params.append(entity_id)
    if type:
        cond.append("items.type=?")
        params.append(type)
    if unread:
        cond.append("items.read=0")
    if starred:
        cond.append("items.starred=1")
    rows = db.query(f"""SELECT items.*, entities.name AS entity, entities.kind AS entity_kind FROM items
                        JOIN entities ON entities.id=items.entity_id WHERE {' AND '.join(cond)}
                        ORDER BY items.published DESC LIMIT ?""", (*params, limit))
    return rows


@app.post("/api/items/{iid}/flag")
def flag_item(iid: str, body: dict = Body(...)):
    field = body.get("field")
    if field not in ("starred", "read"):
        raise HTTPException(400, "field 只能是 starred / read")
    with db.tx() as conn:
        conn.execute(f"UPDATE items SET {field}=? WHERE id=?", (int(bool(body.get("value"))), iid))
    return {"ok": True}


@app.post("/api/items/read-all")
def read_all():
    with db.tx() as conn:
        conn.execute("UPDATE items SET read=1")
    return {"ok": True}


# ------------------------------------------------------------ 关注对象

def _entity_full(eid: int) -> dict:
    e = db.query("SELECT * FROM entities WHERE id=?", (eid,))
    if not e:
        raise HTTPException(404, "关注对象不存在")
    e = e[0]
    e["filter_keywords"] = json.loads(e["filter_keywords"] or "[]")
    e["sources"] = db.query("SELECT id, url, type, enabled, last_checked, last_status, last_error FROM sources "
                            "WHERE entity_id=? ORDER BY id", (eid,))
    e["item_count"] = db.query("SELECT COUNT(*) AS n FROM items WHERE entity_id=?", (eid,))[0]["n"]
    return e


@app.get("/api/entities")
def list_entities():
    return [_entity_full(e["id"]) for e in db.query("SELECT id FROM entities ORDER BY id")]


@app.get("/api/source-types")
def source_types():
    return sources.TYPE_LABELS


@app.post("/api/sources/detect")
def detect(body: dict = Body(...)):
    t = sources.detect_type(body.get("url", ""))
    return {"type": t, "label": sources.TYPE_LABELS[t]}


def _save_entity(conn, eid: int | None, body: dict) -> int:
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    vals = (name, body.get("kind", "company"), body.get("note", ""),
            json.dumps([k.strip() for k in body.get("filter_keywords", []) if k.strip()], ensure_ascii=False),
            int(body.get("enabled", True)))
    if eid is None:
        eid = conn.execute("INSERT INTO entities(name, kind, note, filter_keywords, enabled, created_at) "
                           "VALUES (?,?,?,?,?,?)", (*vals, db.now_iso())).lastrowid
    else:
        conn.execute("UPDATE entities SET name=?, kind=?, note=?, filter_keywords=?, enabled=? WHERE id=?", (*vals, eid))
    keep = []
    for s in body.get("sources", []):
        url = (s.get("url") or "").strip()
        if not url:
            continue
        stype = s.get("type") or sources.detect_type(url)
        if stype not in sources.FETCHERS:
            raise HTTPException(400, f"未知的源类型：{stype}")
        if s.get("id"):
            old = conn.execute("SELECT url FROM sources WHERE id=? AND entity_id=?", (s["id"], eid)).fetchone()
            if old:
                # URL 改了就清空缓存的元信息（频道 ID、网页快照等）
                meta_reset = ", meta='{}'" if old["url"] != url else ""
                conn.execute(f"UPDATE sources SET url=?, type=?, enabled=?{meta_reset} WHERE id=?",
                             (url, stype, int(s.get("enabled", True)), s["id"]))
                keep.append(s["id"])
                continue
        keep.append(conn.execute("INSERT INTO sources(entity_id, url, type, enabled) VALUES (?,?,?,?)",
                                 (eid, url, stype, int(s.get("enabled", True)))).lastrowid)
    conn.execute(f"DELETE FROM sources WHERE entity_id=? AND id NOT IN ({','.join('?' * len(keep)) or 'NULL'})",
                 (eid, *keep))
    return eid


@app.post("/api/entities")
def create_entity(body: dict = Body(...)):
    with db.tx() as conn:
        eid = _save_entity(conn, None, body)
    return _entity_full(eid)


@app.put("/api/entities/{eid}")
def update_entity(eid: int, body: dict = Body(...)):
    _entity_full(eid)
    with db.tx() as conn:
        _save_entity(conn, eid, body)
    return _entity_full(eid)


@app.delete("/api/entities/{eid}")
def delete_entity(eid: int):
    with db.tx() as conn:
        conn.execute("DELETE FROM items WHERE entity_id=?", (eid,))
        conn.execute("DELETE FROM sources WHERE entity_id=?", (eid,))
        conn.execute("DELETE FROM entities WHERE id=?", (eid,))
    return {"ok": True}


@app.post("/api/sources/test")
def test_source(body: dict = Body(...)):
    """试抓一个 URL（不入库），用于添加前预览。"""
    url = (body.get("url") or "").strip()
    stype = body.get("type") or sources.detect_type(url)
    try:
        items, _ = sources.fetch_source({"url": url, "type": stype, "meta": "{}"})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "type": stype, "error": str(e)[:300]}
    return {"ok": True, "type": stype, "count": len(items), "items": items[:8]}


# ------------------------------------------------------------ 工具

@app.post("/api/llm/test")
def llm_test():
    try:
        llm = get_llm()
        if llm is None:
            return {"ok": True, "message": "已关闭 LLM，将使用规则提取（无中文总结）"}
        r = llm.complete_json("你是测试助手。", '请输出 JSON：{"ok": true, "hello": "你好"}',
                              {"type": "object", "properties": {"ok": {"type": "boolean"}, "hello": {"type": "string"}},
                               "required": ["ok", "hello"], "additionalProperties": False})
        return {"ok": True, "message": f"连接成功：{r}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)[:400]}


@app.post("/api/notify/test")
def notify_test():
    errs = notify.send_test()
    return {"ok": not errs, "errors": errs}


@app.post("/api/notify/digest")
def notify_digest(body: dict = Body(...)):
    errs = notify.send_digest(body["date"])
    return {"ok": not errs, "errors": errs}


@app.get("/api/export/arxiv")
def export_arxiv(date: str, fmt: str = "md"):
    if fmt == "html":
        return HTMLResponse(report.arxiv_html(date))
    return PlainTextResponse(report.arxiv_markdown(date), media_type="text/markdown; charset=utf-8")


@app.get("/api/export/watch")
def export_watch(days: int = 1):
    return PlainTextResponse(report.watch_markdown(days), media_type="text/markdown; charset=utf-8")
