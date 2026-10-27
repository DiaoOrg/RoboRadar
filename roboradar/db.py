"""SQLite 存储：论文、关注对象、动态条目、运行记录。"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DB_PATH, DATA_DIR

_write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    arxiv_id TEXT PRIMARY KEY,
    version TEXT,
    digest_date TEXT,
    category TEXT,
    announce_type TEXT,
    title TEXT,
    authors TEXT,
    abstract TEXT,
    comments TEXT,
    subjects TEXT,
    published TEXT,
    matched INTEGER DEFAULT 0,
    semantic INTEGER DEFAULT 0,
    score REAL DEFAULT 0,
    matched_keywords TEXT,
    matched_topics TEXT,
    header_text TEXT,
    title_zh TEXT,
    tags TEXT,
    platform TEXT,
    corresponding TEXT,
    affiliations TEXT,
    method TEXT,
    result TEXT,
    relevance INTEGER,
    links TEXT,
    llm_status TEXT DEFAULT 'pending',
    llm_error TEXT,
    starred INTEGER DEFAULT 0,
    read INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_papers_date ON papers(digest_date);

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT DEFAULT 'company',
    note TEXT DEFAULT '',
    filter_keywords TEXT DEFAULT '[]',
    enabled INTEGER DEFAULT 1,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    type TEXT NOT NULL,
    meta TEXT DEFAULT '{}',
    enabled INTEGER DEFAULT 1,
    last_checked TEXT,
    last_status TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    entity_id INTEGER,
    source_id INTEGER,
    type TEXT,
    title TEXT,
    url TEXT,
    published TEXT,
    description TEXT,
    thumbnail TEXT,
    summary_zh TEXT,
    baseline INTEGER DEFAULT 0,
    fetched_at TEXT,
    read INTEGER DEFAULT 0,
    starred INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_pub ON items(published);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT,
    trigger TEXT,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    message TEXT,
    stats TEXT
);
"""

JSON_FIELDS_PAPER = ("authors", "matched_keywords", "matched_topics", "tags",
                     "corresponding", "affiliations", "links")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx():
    """写事务：串行化，避免多个后台任务同时写入。"""
    with _write_lock:
        conn = connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def query(sql: str, params=()) -> list[dict]:
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def init_db():
    with tx() as conn:
        conn.executescript(SCHEMA)
        n = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        if n == 0:
            _seed_entities(conn)


# 默认关注列表：都已验证可访问。用户可在 GUI 中增删改。
DEFAULT_ENTITIES = [
    ("Figure AI", "company", "Figure 人形机器人", [], [
        "https://www.youtube.com/@figureai", "https://www.figure.ai/news"]),
    ("OpenAI", "company", "只保留与机器人相关的动态", ["robot", "robotics", "humanoid", "embodied", "机器人"], [
        "https://www.youtube.com/@OpenAI", "https://openai.com/news/rss.xml", "https://github.com/openai"]),
    ("Tesla Optimus", "company", "Tesla 频道中只保留 Optimus / 机器人", ["optimus", "robot", "humanoid", "机器人"], [
        "https://www.youtube.com/@tesla"]),
    ("廖恰源 Qiayuan Liao", "researcher", "UC Berkeley Hybrid Robotics", [], [
        "https://qiayuanl.github.io", "https://github.com/qiayuanl", "arxiv-author:Qiayuan Liao"]),
    ("张翀 Chong Zhang", "researcher", "同名作者多，arXiv 限定 cs.RO", [], [
        "https://zita-ch.github.io/publications/", "https://github.com/zita-ch", "arxiv-author:Chong Zhang|cs.RO"]),
    ("Unitree 宇树", "company", "G1 / H1 / Go2", [], [
        "https://www.youtube.com/@unitreerobotics", "https://github.com/unitreerobotics"]),
    ("Boston Dynamics", "company", "", [], [
        "https://www.youtube.com/@BostonDynamics"]),
    ("Agility Robotics", "company", "Digit", [], ["https://www.youtube.com/@AgilityRobotics"]),
    ("1X", "company", "NEO", [], ["https://www.youtube.com/@1X-tech", "https://www.1x.tech/discover"]),
    ("Physical Intelligence", "company", "π0 系列", [], ["https://www.physicalintelligence.company/blog"]),
    ("彭学斌 Xue Bin Peng", "researcher", "SFU / NVIDIA，角色动画与动作模仿", [], [
        "https://xbpeng.github.io", "arxiv-author:Xue Bin Peng"]),
    ("石冠亚 Guanya Shi", "researcher", "CMU LeCAR Lab", [], [
        "https://www.gshi.me", "arxiv-author:Guanya Shi"]),
]


def _seed_entities(conn: sqlite3.Connection):
    from .sources import detect_type  # 延迟导入避免循环

    ts = now_iso()
    for name, kind, note, kws, urls in DEFAULT_ENTITIES:
        cur = conn.execute(
            "INSERT INTO entities(name, kind, note, filter_keywords, enabled, created_at) VALUES (?,?,?,?,1,?)",
            (name, kind, note, json.dumps(kws, ensure_ascii=False), ts))
        for url in urls:
            conn.execute("INSERT INTO sources(entity_id, url, type) VALUES (?,?,?)",
                         (cur.lastrowid, url, detect_type(url)))


def decode_paper(row: dict) -> dict:
    for f in JSON_FIELDS_PAPER:
        v = row.get(f)
        row[f] = json.loads(v) if v else []
    return row


def start_run(kind: str, trigger: str) -> int:
    with tx() as conn:
        cur = conn.execute("INSERT INTO runs(kind, trigger, started_at, status) VALUES (?,?,?,'running')",
                           (kind, trigger, now_iso()))
        return cur.lastrowid


def finish_run(run_id: int, status: str, message: str = "", stats: dict | None = None):
    with tx() as conn:
        conn.execute("UPDATE runs SET finished_at=?, status=?, message=?, stats=? WHERE id=?",
                     (now_iso(), status, message, json.dumps(stats or {}, ensure_ascii=False), run_id))
