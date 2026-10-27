"""后台任务的实时进度（供 GUI 轮询）与互斥锁。"""
from __future__ import annotations

import threading
import time

_state: dict[str, dict] = {}
_locks = {"arxiv": threading.Lock(), "watch": threading.Lock()}
_mu = threading.Lock()


def lock(kind: str) -> threading.Lock:
    return _locks[kind]


def update(kind: str, **kw):
    with _mu:
        st = _state.setdefault(kind, {})
        st.update(kw, ts=time.time())


def snapshot() -> dict:
    with _mu:
        return {k: dict(v) for k, v in _state.items()}
