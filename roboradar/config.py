"""设置的读写。所有用户可调参数都保存在 data/settings.json，GUI 修改后即时生效。"""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ROBORADAR_DATA", ROOT / "data"))
REPORT_DIR = DATA_DIR / "reports"
SETTINGS_PATH = DATA_DIR / "settings.json"
DB_PATH = DATA_DIR / "roboradar.db"

DEFAULT_TOPICS = [
    {
        "name": "全身控制",
        "role": "filter",
        "enabled": True,
        "keywords": [
            "whole-body control", "whole body control", "whole-body controller",
            "loco-manipulation", "whole-body teleoperation", "whole-body motion",
            "humanoid whole-body",
        ],
    },
    {
        "name": "动作跟踪",
        "role": "filter",
        "enabled": True,
        "keywords": [
            "motion tracking", "motion imitation", "motion retargeting", "retargeting",
            "tracking controller", "tracking policy", "motion mimic", "human motion imitation",
            "human-to-humanoid", "motion prior", "general motion tracking",
        ],
    },
    {
        "name": "感知驱动运动控制",
        "role": "filter",
        "enabled": True,
        "keywords": [
            "perceptive locomotion", "perceptive motion", "perceptive control",
            "vision-based locomotion", "visual locomotion", "elevation map", "height map",
            "terrain perception", "depth-based locomotion", "egocentric depth", "parkour",
        ],
    },
    {
        "name": "视频遥操作",
        "role": "filter",
        "enabled": True,
        "keywords": [
            "teleoperation", "tele-operation", "teleoperate", "video-based teleoperation",
            "monocular video", "human video", "from video", "video imitation",
            "motion capture",
        ],
    },
    {
        "name": "人形机器人",
        "role": "tag",
        "enabled": True,
        "keywords": ["humanoid"],
    },
    {
        "name": "Sim-to-Real",
        "role": "tag",
        "enabled": True,
        "keywords": ["sim-to-real", "sim2real", "zero-shot transfer", "real-world deployment"],
    },
]

DEFAULT_SETTINGS = {
    "timezone": "Asia/Shanghai",
    "arxiv": {
        "enabled": True,
        "categories": ["cs.RO"],
        # arXiv 北京时间约 08:00（夏令时）/ 09:00（冬令时）发布新公告
        "times": ["09:30"],
        "include_cross_lists": True,
        "semantic_screen": False,
        "fetch_fulltext": True,
        "max_llm_papers": 40,
    },
    "topics": DEFAULT_TOPICS,
    "watch": {
        "enabled": True,
        "times": ["08:00", "12:30", "20:00"],
        "lookback_days": 14,
        "llm_summarize": True,
    },
    "llm": {
        # claude_cli | anthropic | openai | none
        "backend": "claude_cli",
        "claude_cli_model": "sonnet",
        "anthropic_model": "claude-opus-5-5",
        "anthropic_api_key": "",
        "openai_base_url": "https://api.deepseek.com",
        "openai_api_key": "",
        "openai_model": "deepseek-chat",
        "concurrency": 4,
        "timeout": 180,
    },
    "notify": {
        "desktop": True,
        "webhook_type": "none",  # none | feishu | dingtalk | wecom | slack | generic
        "webhook_url": "",
        "email_enabled": False,
        "smtp_host": "",
        "smtp_port": 465,
        "smtp_user": "",
        "smtp_password": "",
        "email_to": "",
    },
    "network": {
        "proxy": "",
        "github_token": "",
    },
}

_lock = threading.Lock()


def _merge(default, user):
    """用户设置覆盖默认值；新版本新增的键自动补上。"""
    if isinstance(default, dict) and isinstance(user, dict):
        out = copy.deepcopy(default)
        for k, v in user.items():
            out[k] = _merge(default[k], v) if k in default else v
        return out
    return copy.deepcopy(user)


def load_settings() -> dict:
    with _lock:
        if SETTINGS_PATH.exists():
            try:
                user = json.loads(SETTINGS_PATH.read_text("utf-8"))
            except json.JSONDecodeError:
                user = {}
        else:
            user = {}
        return _merge(DEFAULT_SETTINGS, user)


def save_settings(settings: dict) -> dict:
    merged = _merge(DEFAULT_SETTINGS, settings)
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, SETTINGS_PATH)
    return merged


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_PATH.exists():
        save_settings(DEFAULT_SETTINGS)
