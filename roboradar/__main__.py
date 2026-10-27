"""命令行入口。

  python -m roboradar serve [--host 127.0.0.1] [--port 8770] [--open]
  python -m roboradar arxiv [--date YYYY-MM-DD] [--force]
  python -m roboradar watch
"""
from __future__ import annotations

import argparse
import logging


def main():
    ap = argparse.ArgumentParser(prog="roboradar", description="机器人论文与动态雷达")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="启动 GUI 与定时任务")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8770)
    s.add_argument("--open", action="store_true", help="启动后打开浏览器")
    a = sub.add_parser("arxiv", help="立即运行一次 arXiv 汇总")
    a.add_argument("--date")
    a.add_argument("--force", action="store_true", help="重新匹配并重新总结")
    sub.add_parser("watch", help="立即抓取一次关注列表")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "serve":
        import threading
        import webbrowser

        import uvicorn

        if args.open:
            threading.Timer(1.5, lambda: webbrowser.open(f"http://{args.host}:{args.port}")).start()
        uvicorn.run("roboradar.app:app", host=args.host, port=args.port, log_level="info")
        return

    from . import db
    from .config import ensure_dirs

    ensure_dirs()
    db.init_db()
    if args.cmd == "arxiv":
        from .digest import run_digest

        print(run_digest(date=args.date, trigger="cli", force=args.force))
    elif args.cmd == "watch":
        from .watch import run_watch

        print(run_watch(trigger="cli"))


if __name__ == "__main__":
    main()
