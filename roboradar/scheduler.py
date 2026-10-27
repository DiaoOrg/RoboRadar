"""定时任务：按设置里的时间每天运行 arXiv 汇总和关注列表抓取；启动时补跑错过的任务。"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db
from .config import load_settings
from .digest import run_digest
from .watch import run_watch

log = logging.getLogger("roboradar.scheduler")
_sched: BackgroundScheduler | None = None


def _parse(t: str) -> tuple[int, int]:
    h, m = t.strip().split(":")
    return int(h), int(m)


def reschedule():
    """根据当前设置重建全部定时任务（GUI 保存设置后调用）。"""
    global _sched
    settings = load_settings()
    tz = settings.get("timezone", "Asia/Shanghai")
    if _sched is None:
        _sched = BackgroundScheduler(timezone=tz, job_defaults={"misfire_grace_time": 3600, "coalesce": True})
        _sched.start()
    _sched.remove_all_jobs()
    if settings["arxiv"].get("enabled"):
        for t in settings["arxiv"].get("times", []):
            h, m = _parse(t)
            _sched.add_job(run_digest, CronTrigger(hour=h, minute=m, timezone=tz), kwargs={"trigger": "schedule"},
                           id=f"arxiv-{t}", max_instances=1)
    if settings["watch"].get("enabled"):
        for t in settings["watch"].get("times", []):
            h, m = _parse(t)
            _sched.add_job(run_watch, CronTrigger(hour=h, minute=m, timezone=tz), kwargs={"trigger": "schedule"},
                           id=f"watch-{t}", max_instances=1)


def next_runs() -> list[dict]:
    if _sched is None:
        return []
    jobs = sorted(_sched.get_jobs(), key=lambda j: j.next_run_time or datetime.max.astimezone())
    return [{"id": j.id, "kind": j.id.split("-")[0],
             "next": j.next_run_time.isoformat(timespec="minutes") if j.next_run_time else None} for j in jobs]


def catch_up():
    """开机/重启后：今天的 arXiv 定时点已过但还没成功跑过，就补跑一次；关注列表 6 小时内没跑也补跑。"""
    settings = load_settings()
    now = datetime.now().astimezone()
    today = now.strftime("%Y-%m-%d")

    def _last(kind):
        r = db.query("SELECT started_at FROM runs WHERE kind=? AND status IN ('ok','partial') "
                     "ORDER BY id DESC LIMIT 1", (kind,))
        return datetime.fromisoformat(r[0]["started_at"]) if r else None

    if settings["arxiv"].get("enabled") and settings["arxiv"].get("times"):
        first = min(_parse(t) for t in settings["arxiv"]["times"])
        last = _last("arxiv")
        if (now.hour, now.minute) >= first and (last is None or last.strftime("%Y-%m-%d") != today):
            log.info("补跑今天的 arXiv 汇总")
            threading.Thread(target=run_digest, kwargs={"trigger": "schedule"}, daemon=True).start()
    if settings["watch"].get("enabled"):
        last = _last("watch")
        if last is None or (now - last).total_seconds() > 6 * 3600:
            log.info("补跑关注列表抓取")
            threading.Thread(target=run_watch, kwargs={"trigger": "catchup"}, daemon=True).start()


def shutdown():
    if _sched is not None:
        _sched.shutdown(wait=False)
