"""关注列表的定时抓取：遍历启用的关注对象和源，入库新条目，可选 LLM 一句话中文摘要。"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from . import db, notify, progress, report
from .config import load_settings
from .llm import get_llm
from .sources import fetch_source

log = logging.getLogger("roboradar.watch")

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summaries": {"type": "array", "items": {
        "type": "object",
        "properties": {"i": {"type": "integer"}, "zh": {"type": "string"}},
        "required": ["i", "zh"], "additionalProperties": False}}},
    "required": ["summaries"],
    "additionalProperties": False,
}


def _keep(item: dict, keywords: list[str]) -> bool:
    if not keywords:
        return True
    text = f"{item['title']} {item.get('description', '')}".lower()
    return any(re.search(re.escape(k.lower()), text) for k in keywords)


def _recent(item: dict, cutoff: datetime) -> bool:
    if not item.get("published"):
        return True
    try:
        dt = datetime.fromisoformat(item["published"].replace("Z", "+00:00"))
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= cutoff


def check_source(src: dict, entity: dict, cutoff: datetime) -> list[dict]:
    """抓取一个源并把新条目写库，返回新条目。"""
    try:
        items, meta = fetch_source(src)
        status, err = "ok", ""
    except Exception as e:  # noqa: BLE001
        items, meta, status, err = [], json.loads(src.get("meta") or "{}"), "error", str(e)[:300]
    kws = json.loads(entity.get("filter_keywords") or "[]")
    new = []
    with db.tx() as conn:
        conn.execute("UPDATE sources SET meta=?, last_checked=?, last_status=?, last_error=? WHERE id=?",
                     (json.dumps(meta, ensure_ascii=False), db.now_iso(), status, err, src["id"]))
        for it in items:
            if not _keep(it, kws) or not _recent(it, cutoff):
                continue
            cur = conn.execute(
                """INSERT OR IGNORE INTO items(id, entity_id, source_id, type, title, url, published, description,
                   thumbnail, baseline, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (it["id"], entity["id"], src["id"], it["type"], it["title"], it["url"],
                 it.get("published") or db.now_iso(), it.get("description", ""), it.get("thumbnail", ""),
                 int(bool(it.get("baseline"))), db.now_iso()))
            if cur.rowcount:
                new.append({**it, "entity": entity["name"]})
    if status == "error":
        raise RuntimeError(err)
    return new


def summarize_items(items: list[dict], llm) -> None:
    """一次请求为多条动态生成一句话中文摘要。"""
    for i in range(0, len(items), 25):
        batch = items[i:i + 25]
        listing = "\n".join(f"[{j}] ({it['entity']} / {it['type']}) {it['title']} — {it.get('description', '')[:300]}"
                            for j, it in enumerate(batch))
        user = ("下面是机器人领域公司/研究者的最新动态（视频、仓库、博客、论文）。"
                "为每条写一句中文摘要（≤50 字），说清楚是什么工作、亮点是什么；信息不足就翻译标题。\n\n"
                f"{listing}\n\n输出 JSON：{{\"summaries\": [{{\"i\": 编号, \"zh\": \"摘要\"}}]}}")
        try:
            res = llm.complete_json("你是机器人领域的资讯编辑，只依据给出的信息写作，不编造。", user, SUMMARY_SCHEMA)
        except Exception as e:  # noqa: BLE001
            log.warning("动态摘要失败：%s", e)
            continue
        with db.tx() as conn:
            for s in res.get("summaries", []):
                j = s.get("i")
                if isinstance(j, int) and 0 <= j < len(batch):
                    conn.execute("UPDATE items SET summary_zh=? WHERE id=?", (s.get("zh", ""), batch[j]["id"]))


def run_watch(entity_id: int | None = None, trigger: str = "manual") -> dict:
    lk = progress.lock("watch")
    if not lk.acquire(blocking=False):
        return {"status": "busy", "message": "关注列表任务正在运行"}
    run_id = db.start_run("watch", trigger)
    try:
        settings = load_settings()
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(settings["watch"].get("lookback_days", 14)))
        if entity_id:
            entities = db.query("SELECT * FROM entities WHERE id=?", (entity_id,))
        else:
            entities = db.query("SELECT * FROM entities WHERE enabled=1")
        ent_by_id = {e["id"]: e for e in entities}
        srcs = [s for s in db.query("SELECT * FROM sources WHERE enabled=1") if s["entity_id"] in ent_by_id]
        progress.update("watch", running=True, stage="抓取关注源", done=0, total=len(srcs), message="")
        new_items, errors, done = [], [], 0
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(check_source, s, ent_by_id[s["entity_id"]], cutoff): s for s in srcs}
            for f in as_completed(futs):
                s = futs[f]
                done += 1
                try:
                    new_items.extend(f.result())
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{ent_by_id[s['entity_id']]['name']} {s['url']}: {e}")
                progress.update("watch", done=done, message=s["url"])
        if new_items and settings["watch"].get("llm_summarize", True):
            try:
                llm = get_llm(settings["llm"])
            except Exception:  # noqa: BLE001
                llm = None
            if llm is not None:
                progress.update("watch", stage=f"生成 {len(new_items)} 条动态摘要")
                summarize_items(new_items, llm)
        report.write_watch_report()
        fresh = [it for it in new_items if not it.get("baseline")]
        if fresh and trigger == "schedule":
            notify.send_watch(fresh)
        msg = f"检查 {len(srcs)} 个源，新增 {len(new_items)} 条" + (f"，{len(errors)} 个源失败" if errors else "")
        stats = {"sources": len(srcs), "new": len(new_items), "errors": errors}
        db.finish_run(run_id, "ok" if not errors else "partial", msg, stats)
        progress.update("watch", running=False, stage="完成", message=msg)
        return {"status": "ok", "message": msg, **stats}
    except Exception as e:  # noqa: BLE001
        log.exception("关注任务失败")
        db.finish_run(run_id, "error", str(e))
        progress.update("watch", running=False, stage="失败", message=str(e))
        return {"status": "error", "message": str(e)}
    finally:
        lk.release()
