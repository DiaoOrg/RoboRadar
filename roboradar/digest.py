"""每日 arXiv 论文汇总流程。"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import arxiv, db, notify, progress, report
from .config import load_settings
from .llm import get_llm

log = logging.getLogger("roboradar.digest")

SYSTEM = ("你是机器人学（人形机器人、足式运动控制、全身控制、模仿学习、遥操作）方向的资深研究员，"
          "为同行撰写每日 arXiv 论文速览。只依据给出的原文信息作答，不要编造；输出严格的 JSON。")

PAPER_SCHEMA = {
    "type": "object",
    "properties": {
        "title_zh": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "platform": {"type": "string"},
        "corresponding_authors": {"type": "array", "items": {"type": "string"}},
        "affiliations": {"type": "array", "items": {"type": "string"}},
        "method": {"type": "string"},
        "result": {"type": "string"},
        "relevance": {"type": "integer"},
    },
    "required": ["title_zh", "tags", "platform", "corresponding_authors", "affiliations",
                 "method", "result", "relevance"],
    "additionalProperties": False,
}

SCREEN_SCHEMA = {
    "type": "object",
    "properties": {"relevant": {"type": "array", "items": {"type": "integer"}}},
    "required": ["relevant"],
    "additionalProperties": False,
}


def _topic_names(settings) -> list[str]:
    return [t["name"] for t in settings["topics"] if t.get("enabled", True)]


def build_paper_prompt(p: dict, settings: dict) -> str:
    topics = "、".join(_topic_names(settings))
    return f"""用户关注的主题标签：{topics}
规则匹配命中的主题：{"、".join(p.get("matched_topics") or []) or "无"}

【arXiv ID】{p["arxiv_id"]}
【标题】{p["title"]}
【作者（按顺序）】{", ".join(p["authors"])}
【摘要】{p["abstract"]}
【Comments】{p.get("comments") or "无"}
【论文首页文本（含单位、通讯作者标记）】
{(p.get("header_text") or "（未获取到）")[:4000]}

请输出 JSON，字段要求：
1. title_zh：标题的中文翻译，简洁准确，专有名词/方法名保留英文。
2. tags：1-4 个中文标签，优先从用户主题标签中选；方法有鲜明特点时可再补 1 个简短标签（如“扩散策略”“世界模型”“强化学习”）。
3. platform：实验平台与验证方式（≤12 字，不加括号说明），例如“G1 实机”“H1 仿真”“Go2 实机+仿真”“Franka 实机”；无机器人实验写“无实机实验”；未说明写“未说明”。
4. corresponding_authors：依据首页通讯标记（✉、Corresponding author 图例对应的符号、邮箱）判断，用作者列表中的原名；无法判断返回 []。
5. affiliations：作者单位，按出现顺序去重，最多 5 个，用常用简称（如“UC Berkeley”“清华大学”“NVIDIA”“ETH Zurich”）。
6. method：一句中文（≤80 字），说明提出了什么方法/系统及关键思路。
7. result：一句中文（≤80 字），说明主要结果，尽量包含关键数字、对比基线或实机平台。
8. relevance：0-10 的整数，表示与用户关注主题的相关程度。"""


def summarize_rule_based(p: dict) -> dict:
    sents = re.split(r"(?<=[.!?])\s+", p["abstract"])
    method = next((s for s in sents if re.search(r"\bwe (propose|present|introduce|develop)", s, re.I)), sents[0] if sents else "")
    result = next((s for s in reversed(sents) if re.search(r"\d|outperform|demonstrat|achiev|real[- ]world", s, re.I)), "")
    header = p.get("header_text") or ""
    return {
        "title_zh": "",
        "tags": p.get("matched_topics") or [],
        "platform": arxiv.guess_platform(p["abstract"] + " " + (p.get("comments") or "")),
        "corresponding_authors": arxiv.guess_corresponding(header, p["authors"]),
        "affiliations": arxiv.guess_affiliations(header),
        "method": method,
        "result": result,
        "relevance": min(10, int(p.get("score") or 0) + 3),
    }


def enrich_paper(p: dict, settings: dict, llm) -> dict:
    """取首页 → LLM 总结（失败时用规则兜底）→ 写库。"""
    if settings["arxiv"].get("fetch_fulltext", True) and not p.get("header_text"):
        p["header_text"] = arxiv.fetch_header_text(p["arxiv_id"])
    status, err = "rule", ""
    out = None
    if llm is not None:
        try:
            out = llm.complete_json(SYSTEM, build_paper_prompt(p, settings), PAPER_SCHEMA)
            status = "ok"
        except Exception as e:  # noqa: BLE001
            err = str(e)[:500]
            status = "error"
            log.warning("LLM 总结失败 %s: %s", p["arxiv_id"], err)
    if out is None:
        out = summarize_rule_based(p)
    links = arxiv.extract_links(p.get("comments", ""), p["abstract"], p.get("header_text", ""))
    fields = {
        "header_text": p.get("header_text", ""),
        "title_zh": out.get("title_zh", ""),
        "tags": json.dumps(out.get("tags") or [], ensure_ascii=False),
        "platform": out.get("platform", ""),
        "corresponding": json.dumps(out.get("corresponding_authors") or [], ensure_ascii=False),
        "affiliations": json.dumps(out.get("affiliations") or [], ensure_ascii=False),
        "method": out.get("method", ""),
        "result": out.get("result", ""),
        "relevance": int(out.get("relevance") or 0),
        "links": json.dumps(links, ensure_ascii=False),
        "llm_status": status,
        "llm_error": err,
        "updated_at": db.now_iso(),
    }
    with db.tx() as conn:
        conn.execute(f"UPDATE papers SET {', '.join(k + '=?' for k in fields)} WHERE arxiv_id=?",
                     (*fields.values(), p["arxiv_id"]))
    return fields


def semantic_screen(papers: list[dict], settings: dict, llm) -> set[str]:
    """让 LLM 从未命中关键词的论文里补选相关论文（按批）。"""
    topics = settings["topics"]
    desc = "\n".join(f"- {t['name']}：{', '.join(t['keywords'][:6])}" for t in topics
                     if t.get("enabled", True) and t.get("role", "filter") == "filter")
    picked: set[str] = set()
    for i in range(0, len(papers), 40):
        batch = papers[i:i + 40]
        listing = "\n".join(f"[{j}] {p['title']} — {p['abstract'][:300]}" for j, p in enumerate(batch))
        user = (f"用户关注以下主题：\n{desc}\n\n下面是今天的论文（编号、标题、摘要片段）。"
                f"只挑出与上述主题**直接相关**的论文编号，宁缺毋滥。\n\n{listing}\n\n输出 JSON：{{\"relevant\": [编号...]}}")
        try:
            res = llm.complete_json(SYSTEM, user, SCREEN_SCHEMA)
            for j in res.get("relevant", []):
                if isinstance(j, int) and 0 <= j < len(batch):
                    picked.add(batch[j]["arxiv_id"])
        except Exception as e:  # noqa: BLE001
            log.warning("语义补筛失败：%s", e)
    return picked


def run_digest(date: str | None = None, trigger: str = "manual", force: bool = False) -> dict:
    """抓取指定公告日（默认最新一天）的论文并汇总。返回统计信息。"""
    lk = progress.lock("arxiv")
    if not lk.acquire(blocking=False):
        return {"status": "busy", "message": "arXiv 任务正在运行"}
    run_id = db.start_run("arxiv", trigger)
    stats: dict = {}
    try:
        settings = load_settings()
        cfg = settings["arxiv"]
        progress.update("arxiv", running=True, stage="获取 arXiv 公告列表", done=0, total=0, message="")

        # 1) 列表：recent 页（可回溯最近 5 个公告日），失败时用 RSS
        wanted: dict[str, dict] = {}
        digest_date = date
        for cat in cfg["categories"]:
            try:
                days = arxiv.fetch_recent_days(cat)
                day = date or (max(days) if days else None)
                entries = days.get(day, []) if day else []
            except Exception as e:  # noqa: BLE001
                log.warning("recent 页失败，改用 RSS：%s", e)
                day, entries = arxiv.fetch_rss(cat)
                if date and day != date:
                    entries = []
            digest_date = digest_date or day
            for e in entries:
                if e["announce_type"] == "cross" and not cfg.get("include_cross_lists", True):
                    continue
                wanted.setdefault(e["id"], {**e, "category": cat})
        if not digest_date or not wanted:
            raise RuntimeError(f"未找到公告日 {date or '(最新)'} 的论文（arXiv 周末/节假日不发布）")
        stats["date"] = digest_date
        stats["total"] = len(wanted)

        # 2) 元数据（已入库的跳过）：列表页已有标题/作者/comments；摘要优先取 RSS（最新一天），缺的才调 arXiv API
        existing = {r["arxiv_id"] for r in db.query(
            f"SELECT arxiv_id FROM papers WHERE abstract != '' AND arxiv_id IN ({','.join('?' * len(wanted))})",
            tuple(wanted))}  # 之前缺摘要的论文会重新补取
        new_ids = [i for i in wanted if i not in existing]
        stats["new"] = len(new_ids)
        metas = {i: dict(wanted[i]) for i in new_ids}
        if any(not m.get("abstract") for m in metas.values()):
            progress.update("arxiv", stage=f"获取 {len(new_ids)} 篇论文摘要")
            for cat in cfg["categories"]:
                try:
                    rss_day, rss_entries = arxiv.fetch_rss(cat)
                except Exception as e:  # noqa: BLE001
                    log.warning("RSS 获取失败：%s", e)
                    continue
                if rss_day != digest_date:
                    continue
                for e in rss_entries:
                    m = metas.get(e["id"])
                    if m is not None and not m.get("abstract"):
                        m.update({k: v for k, v in e.items() if v and (k == "abstract" or not m.get(k))})
            missing = [i for i, m in metas.items() if not m.get("abstract")]
            if missing:
                progress.update("arxiv", stage=f"从 arXiv API 获取 {len(missing)} 篇摘要")
                try:
                    for a in arxiv.fetch_metadata(missing):
                        if a["id"] in metas:
                            metas[a["id"]].update({k: v for k, v in a.items() if v})
                except Exception as e:  # noqa: BLE001
                    stats["warning"] = f"{len(missing)} 篇摘要获取失败（{e}），这些论文只按标题匹配"
                    log.warning(stats["warning"])
        topics = settings["topics"]
        with db.tx() as conn:
            for m in metas.values():
                mt = arxiv.match_topics(m.get("title", ""), m.get("abstract", ""), topics)
                conn.execute(
                    """INSERT INTO papers(arxiv_id, version, digest_date, category, announce_type, title,
                       authors, abstract, comments, subjects, published, matched, score, matched_keywords,
                       matched_topics, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(arxiv_id) DO UPDATE SET abstract=excluded.abstract, matched=excluded.matched,
                       score=excluded.score, matched_keywords=excluded.matched_keywords,
                       matched_topics=excluded.matched_topics, updated_at=excluded.updated_at
                       WHERE papers.abstract = '' AND excluded.abstract != ''""",
                    (m["id"], m.get("version", ""), digest_date, m.get("category"), m.get("announce_type"),
                     m.get("title", ""), json.dumps(m.get("authors", []), ensure_ascii=False), m.get("abstract", ""),
                     m.get("comments", ""), ", ".join(m.get("subjects", [])), m.get("published", ""),
                     int(mt["matched"]), mt["score"], json.dumps(mt["keywords"], ensure_ascii=False),
                     json.dumps(mt["topics"], ensure_ascii=False), db.now_iso(), db.now_iso()))
        if force:  # 关键词改过之后重新匹配当天全部论文
            rematch_date(digest_date)

        rows = [db.decode_paper(r) for r in db.query(
            f"SELECT * FROM papers WHERE arxiv_id IN ({','.join('?' * len(wanted))})", tuple(wanted))]
        llm = None
        try:
            llm = get_llm(settings["llm"])
        except Exception as e:  # noqa: BLE001
            stats["llm_warning"] = str(e)

        # 3) 语义补筛
        if cfg.get("semantic_screen") and llm is not None:
            unmatched = [r for r in rows if not r["matched"] and not r["semantic"]]
            if unmatched:
                progress.update("arxiv", stage=f"LLM 语义补筛 {len(unmatched)} 篇")
                picked = semantic_screen(unmatched, settings, llm)
                if picked:
                    with db.tx() as conn:
                        conn.executemany("UPDATE papers SET semantic=1 WHERE arxiv_id=?", [(i,) for i in picked])
                    for r in rows:
                        if r["arxiv_id"] in picked:
                            r["semantic"] = 1

        # 4) 对相关论文做首页解析 + 总结
        selected = sorted([r for r in rows if r["matched"] or r["semantic"]], key=lambda r: -r["score"])
        selected = selected[: cfg.get("max_llm_papers", 40)]
        todo = [r for r in selected if force or r["llm_status"] in ("pending", "error")]
        stats["matched"] = len(selected)
        stats["summarized"] = len(todo)
        progress.update("arxiv", stage="解析论文并生成总结", done=0, total=len(todo))
        workers = max(1, int(settings["llm"].get("concurrency", 4)))
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(enrich_paper, r, settings, llm): r for r in todo}
            for f in as_completed(futs):
                done += 1
                try:
                    f.result()
                except Exception as e:  # noqa: BLE001
                    log.exception("处理论文失败 %s: %s", futs[f]["arxiv_id"], e)
                progress.update("arxiv", done=done, message=futs[f]["title"][:80])

        # 5) 报告 + 通知
        progress.update("arxiv", stage="生成报告")
        md_path, _ = report.write_arxiv_report(digest_date)
        stats["report"] = str(md_path)
        # 定时任务只在有新公告时推送，避免周末重复推送旧日报
        if (trigger == "schedule" and new_ids) or trigger == "manual-notify":
            notify.send_digest(digest_date)
        msg = f"{digest_date}：共 {stats['total']} 篇，相关 {stats['matched']} 篇"
        if stats.get("warning"):
            msg += f"；注意：{stats['warning']}"
        db.finish_run(run_id, "ok", msg, stats)
        progress.update("arxiv", running=False, stage="完成", message=msg)
        return {"status": "ok", "message": msg, **stats}
    except Exception as e:  # noqa: BLE001
        log.exception("arXiv 任务失败")
        db.finish_run(run_id, "error", str(e), stats)
        progress.update("arxiv", running=False, stage="失败", message=str(e))
        return {"status": "error", "message": str(e)}
    finally:
        lk.release()


def rematch_date(date: str):
    """按当前关键词重新匹配某天的论文（修改关键词后使用）。"""
    settings = load_settings()
    rows = db.query("SELECT arxiv_id, title, abstract FROM papers WHERE digest_date=?", (date,))
    with db.tx() as conn:
        for r in rows:
            mt = arxiv.match_topics(r["title"], r["abstract"], settings["topics"])
            conn.execute("UPDATE papers SET matched=?, score=?, matched_keywords=?, matched_topics=? WHERE arxiv_id=?",
                         (int(mt["matched"]), mt["score"], json.dumps(mt["keywords"], ensure_ascii=False),
                          json.dumps(mt["topics"], ensure_ascii=False), r["arxiv_id"]))


def summarize_one(arxiv_id: str, include: bool = False) -> dict:
    """GUI 中“重新总结”或“手动收录”单篇论文。"""
    rows = db.query("SELECT * FROM papers WHERE arxiv_id=?", (arxiv_id,))
    if not rows:
        raise KeyError(arxiv_id)
    p = db.decode_paper(rows[0])
    if include:
        with db.tx() as conn:
            conn.execute("UPDATE papers SET semantic=1 WHERE arxiv_id=?", (arxiv_id,))
    settings = load_settings()
    enrich_paper(p, settings, get_llm(settings["llm"]))
    report.write_arxiv_report(p["digest_date"])
    return db.decode_paper(db.query("SELECT * FROM papers WHERE arxiv_id=?", (arxiv_id,))[0])
