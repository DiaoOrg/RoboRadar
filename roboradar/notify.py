"""推送：桌面通知、群机器人 Webhook（飞书/钉钉/企业微信/Slack/通用 JSON）、邮件。"""
from __future__ import annotations

import logging
import shutil
import smtplib
import subprocess
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from . import db, report
from .config import load_settings

log = logging.getLogger("roboradar.notify")


def desktop(title: str, body: str):
    exe = shutil.which("notify-send")
    if exe:
        subprocess.run([exe, "-a", "RoboRadar", title, body[:300]], check=False, timeout=10)


def webhook(cfg: dict, title: str, markdown: str):
    kind, url = cfg.get("webhook_type", "none"), cfg.get("webhook_url", "")
    if kind == "none" or not url:
        return
    md = markdown[:18000]
    if kind == "feishu":
        payload = {"msg_type": "interactive", "card": {
            "header": {"title": {"tag": "plain_text", "content": title}},
            "elements": [{"tag": "markdown", "content": md}]}}
    elif kind == "dingtalk":
        payload = {"msgtype": "markdown", "markdown": {"title": title, "text": md}}
    elif kind == "wecom":
        payload = {"msgtype": "markdown", "markdown": {"content": md[:4000]}}
    elif kind == "slack":
        payload = {"text": f"*{title}*\n{md}"}
    else:
        payload = {"title": title, "markdown": md}
    r = httpx.post(url, json=payload, timeout=20)
    r.raise_for_status()


def email(cfg: dict, title: str, html_body: str):
    if not cfg.get("email_enabled") or not cfg.get("smtp_host") or not cfg.get("email_to"):
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = title, cfg["smtp_user"], cfg["email_to"]
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    port = int(cfg.get("smtp_port") or 465)
    cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    with cls(cfg["smtp_host"], port, timeout=30) as s:
        if port != 465:
            s.starttls()
        if cfg.get("smtp_user"):
            s.login(cfg["smtp_user"], cfg.get("smtp_password", ""))
        s.sendmail(cfg["smtp_user"], [x.strip() for x in cfg["email_to"].split(",")], msg.as_string())


def _send(title: str, short: str, markdown: str, html_body: str) -> list[str]:
    cfg = load_settings()["notify"]
    errors = []
    for name, fn in (("桌面", lambda: cfg.get("desktop") and desktop(title, short)),
                     ("Webhook", lambda: webhook(cfg, title, markdown)),
                     ("邮件", lambda: email(cfg, title, html_body))):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            log.warning("%s 推送失败：%s", name, e)
            errors.append(f"{name}: {e}")
    return errors


def send_digest(date: str) -> list[str]:
    papers = report.digest_papers(date)
    title = f"arXiv 机器人日报 {date}：{len(papers)} 篇相关论文"
    short = "\n".join(f"• {p.get('title_zh') or p['title']}" for p in papers[:5])
    return _send(title, short, report.arxiv_markdown(date), report.arxiv_html(date))


def send_watch(items: list[dict]) -> list[str]:
    # 从库里重新读，带上刚生成的中文摘要
    ids = [it["id"] for it in items]
    items = db.query(f"""SELECT items.*, entities.name AS entity FROM items JOIN entities ON entities.id=items.entity_id
                         WHERE items.id IN ({','.join('?' * len(ids))}) ORDER BY items.published DESC""", tuple(ids))
    title = f"机器人圈动态：新增 {len(items)} 条"
    short = "\n".join(f"• {it['entity']}：{it['title']}" for it in items[:5])
    md = report.watch_markdown(items=items)
    html_body = "<pre style='font-family:inherit;white-space:pre-wrap'>" + md + "</pre>"
    return _send(title, short, md, html_body)


def send_test() -> list[str]:
    return _send("RoboRadar 测试通知", "通知配置正常 ✅", "**RoboRadar** 测试通知：配置正常 ✅", "<b>RoboRadar</b> 测试通知 ✅")
