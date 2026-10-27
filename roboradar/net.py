"""HTTP 工具：统一 UA、代理、重试。"""
from __future__ import annotations

import time

import httpx

from .config import load_settings

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36 RoboRadar/1.0")


def client(timeout: float = 30) -> httpx.Client:
    proxy = load_settings()["network"].get("proxy") or None
    return httpx.Client(
        headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8"},
        timeout=timeout, follow_redirects=True, proxy=proxy)


def get(url: str, *, retries: int = 3, timeout: float = 30, headers: dict | None = None,
        params: dict | None = None) -> httpx.Response:
    """GET，5xx / 网络错误时指数退避重试；4xx 直接抛出。"""
    last_exc: Exception | None = None
    with client(timeout) as c:
        for attempt in range(retries):
            try:
                r = c.get(url, headers=headers, params=params)
                if r.status_code >= 500 or r.status_code == 429:
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                return r
            except httpx.HTTPStatusError as e:
                last_exc = e
                if e.response is not None and 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                    raise
            except httpx.TransportError as e:
                last_exc = e
            time.sleep(1.5 * (2 ** attempt))
    assert last_exc is not None
    raise last_exc


def download_capped(url: str, max_bytes: int, timeout: float = 90) -> bytes | None:
    """流式下载，超过 max_bytes 就放弃（返回 None），避免下载超大 PDF。"""
    with client(timeout) as c:
        with c.stream("GET", url) as r:
            r.raise_for_status()
            size = int(r.headers.get("content-length") or 0)
            if size and size > max_bytes:
                return None
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    return None
            return bytes(buf)
