"""关注源抓取：YouTube 频道、GitHub 用户/组织/仓库、RSS/博客、普通网页（链接变化检测）、arXiv 作者。

每个 fetch_* 返回统一的条目字典：
  {id, type, title, url, published(ISO 或 ""), description, thumbnail}
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import feedparser
from bs4 import BeautifulSoup

from . import arxiv, net
from .config import load_settings

TYPE_LABELS = {
    "youtube": "YouTube 频道",
    "github_user": "GitHub 用户/组织",
    "github_repo": "GitHub 仓库",
    "rss": "RSS/Atom 订阅",
    "webpage": "网页（自动发现 RSS / 检测新链接）",
    "arxiv_author": "arXiv 作者",
}


def detect_type(url: str) -> str:
    u = url.strip()
    if u.lower().startswith("arxiv-author:") or re.match(r"https?://arxiv\.org/a/", u):
        return "arxiv_author"
    host = urlparse(u).netloc.lower()
    path = urlparse(u).path.strip("/")
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if host in ("github.com", "www.github.com"):
        parts = [p for p in path.split("/") if p]
        if len(parts) == 1:
            return "github_user"
        if len(parts) >= 2:
            return "github_repo"
    if re.search(r"(\.xml|\.rss|\.atom|/feed/?|/rss/?|atom2)$", u, re.I):
        return "rss"
    return "webpage"


def _item_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]


def _iso(struct) -> str:
    if not struct:
        return ""
    return datetime.fromtimestamp(time.mktime(struct), tz=timezone.utc).isoformat(timespec="seconds")


def _text(html: str, n: int = 600) -> str:
    if not html:
        return ""
    return re.sub(r"\s+", " ", BeautifulSoup(html, "lxml").get_text(" ")).strip()[:n]


# ---------------------------------------------------------------- YouTube

def _youtube_channel_id(url: str, meta: dict) -> str:
    if meta.get("channel_id"):
        return meta["channel_id"]
    m = re.search(r"/channel/(UC[\w-]{22})", url)
    if m:
        return m.group(1)
    html = net.get(url).text
    m = (re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})', html)
         or re.search(r'"channelId":"(UC[\w-]{22})"', html)
         or re.search(r'"externalId":"(UC[\w-]{22})"', html))
    if not m:
        raise RuntimeError("无法解析 YouTube 频道 ID")
    meta["channel_id"] = m.group(1)
    return meta["channel_id"]


def fetch_youtube(url: str, meta: dict) -> list[dict]:
    cid = _youtube_channel_id(url, meta)
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
    feed = None
    try:
        # 官方 RSS 经常 404/500，先快速试两次，不行就解析频道视频页
        for _ in range(2):
            feed = feedparser.parse(net.get(feed_url, retries=1, timeout=15).content)
            if feed.entries:
                break
    except Exception:  # noqa: BLE001
        feed = None
    if feed and feed.entries:
        out = []
        for e in feed.entries:
            vid = e.get("yt_videoid") or e.link.rsplit("=", 1)[-1]
            out.append({
                "id": _item_id("yt", vid), "type": "video", "title": e.title, "url": e.link,
                "published": _iso(e.get("published_parsed")),
                "description": (e.get("media_description") or e.get("summary") or "")[:600],
                "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            })
        return out
    return _youtube_scrape(cid)


_REL = re.compile(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", re.I)
_REL_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 7 * 86400,
                "month": 30 * 86400, "year": 365 * 86400}


def _relative_to_iso(text: str) -> str:
    m = _REL.search(text or "")
    if not m:
        return ""
    secs = int(m.group(1)) * _REL_SECONDS[m.group(2).lower()]
    return datetime.fromtimestamp(time.time() - secs, tz=timezone.utc).isoformat(timespec="seconds")


def _walk(o, keys):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in keys:
                yield k, v
            else:
                yield from _walk(v, keys)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v, keys)


def _youtube_scrape(cid: str) -> list[dict]:
    """解析频道“视频”页内嵌的 ytInitialData（发布时间是“2 days ago”这类相对时间，换算为近似日期）。"""
    html = net.get(f"https://www.youtube.com/channel/{cid}/videos").text
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.S)
    if not m:
        raise RuntimeError("YouTube RSS 不可用，且无法解析频道页")
    data = json.loads(m.group(1))
    out, seen = [], set()
    for kind, v in _walk(data, ("lockupViewModel", "videoRenderer")):
        try:
            if kind == "lockupViewModel":
                if v.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO":
                    continue
                vid = v["contentId"]
                md = v["metadata"]["lockupMetadataViewModel"]
                title = md["title"]["content"]
                labels = [p.get("accessibilityLabel") or p.get("text", {}).get("content", "")
                          for row in md.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
                          for p in row.get("metadataParts", [])]
                published = next((_relative_to_iso(x) for x in labels if _REL.search(x or "")), "")
            else:
                vid = v["videoId"]
                title = v["title"]["runs"][0]["text"]
                published = _relative_to_iso(v.get("publishedTimeText", {}).get("simpleText", ""))
        except (KeyError, IndexError, TypeError):
            continue
        if vid in seen:
            continue
        seen.add(vid)
        out.append({"id": _item_id("yt", vid), "type": "video", "title": title,
                    "url": f"https://www.youtube.com/watch?v={vid}", "published": published, "description": "",
                    "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"})
        if len(out) >= 20:
            break
    if not out:
        raise RuntimeError("频道页里没有找到视频")
    return out


# ---------------------------------------------------------------- GitHub

def _gh_headers() -> dict:
    h = {"Accept": "application/vnd.github+json"}
    token = load_settings()["network"].get("github_token")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch_github_user(url: str, meta: dict) -> list[dict]:
    owner = urlparse(url).path.strip("/").split("/")[0]
    # /users/{name}/repos 对组织同样有效，一次请求即可（未登录限额 60 次/小时）
    try:
        repos = net.get(f"https://api.github.com/users/{owner}/repos", headers=_gh_headers(),
                        params={"sort": "created", "direction": "desc", "per_page": 15}).json()
    except Exception as e:  # noqa: BLE001
        items = _github_atom(owner)
        if items is None:
            raise RuntimeError(f"GitHub API 不可用（{e.__class__.__name__}，多为限额用尽），"
                               "可在设置里填写 GitHub Token") from e
        return items
    out = []
    for r in repos:
        if r.get("fork"):
            continue
        out.append({
            "id": _item_id("ghrepo", r["full_name"]), "type": "repo",
            "title": f"新仓库 {r['full_name']}" + (f" ⭐{r['stargazers_count']}" if r.get("stargazers_count") else ""),
            "url": r["html_url"], "published": r.get("created_at", ""),
            "description": r.get("description") or "", "thumbnail": "",
        })
    return out


def _github_atom(owner: str) -> list[dict] | None:
    """API 限额用尽时的兜底：用户公开动态 Atom 中的“新建/公开仓库”事件（组织没有该订阅，返回 None）。"""
    feed = feedparser.parse(net.get(f"https://github.com/{owner}.atom").content)
    if not feed.entries:
        return None
    out = []
    for e in feed.entries:
        title = e.get("title", "")
        if "created a repository" in title or "made" in title and "public" in title:
            out.append({"id": _item_id("ghrepo", e.link.replace("https://github.com/", "")), "type": "repo",
                        "title": f"新仓库 {e.link.replace('https://github.com/', '')}", "url": e.link,
                        "published": _iso(e.get("published_parsed")), "description": "", "thumbnail": ""})
    return out


def fetch_github_repo(url: str, meta: dict) -> list[dict]:
    parts = urlparse(url).path.strip("/").split("/")
    full = f"{parts[0]}/{parts[1]}"
    out = []
    feed = feedparser.parse(net.get(f"https://github.com/{full}/releases.atom").content)
    for e in feed.entries[:10]:
        out.append({"id": _item_id("ghrel", e.link), "type": "release", "title": f"{full} 发布 {e.title}",
                    "url": e.link, "published": _iso(e.get("updated_parsed")),
                    "description": _text(e.get("summary", "")), "thumbnail": ""})
    if not out:  # 没有 release 的仓库：看最近提交
        feed = feedparser.parse(net.get(f"https://github.com/{full}/commits.atom").content)
        for e in feed.entries[:5]:
            out.append({"id": _item_id("ghcommit", e.link), "type": "commit", "title": f"{full}：{e.title}",
                        "url": e.link, "published": _iso(e.get("updated_parsed")), "description": "",
                        "thumbnail": ""})
    return out


# ---------------------------------------------------------------- RSS / 网页

def fetch_rss(url: str, meta: dict) -> list[dict]:
    feed = feedparser.parse(net.get(url).content)
    if not feed.entries and feed.bozo:
        raise RuntimeError("不是有效的 RSS/Atom 订阅")
    out = []
    for e in feed.entries[:30]:
        link = e.get("link", "")
        thumb = ""
        if e.get("media_thumbnail"):
            thumb = e.media_thumbnail[0].get("url", "")
        out.append({"id": _item_id("rss", link or e.get("id", e.get("title", ""))), "type": "post",
                    "title": e.get("title", "(无标题)"), "url": link,
                    "published": _iso(e.get("published_parsed") or e.get("updated_parsed")),
                    "description": _text(e.get("summary", "")), "thumbnail": thumb})
    return out


FEED_PATHS = ("feed.xml", "atom.xml", "rss.xml", "index.xml", "feed", "rss")
SKIP_LINK = re.compile(r"^(mailto:|javascript:|#)|\.(png|jpe?g|gif|svg|css|js|ico)$|"
                       r"(twitter|x)\.com|linkedin\.com|facebook\.com|instagram\.com|scholar\.google|"
                       r"/(privacy|terms|cookie|careers|login|signin|contact)", re.I)


CONTENT_HINT = re.compile(r"arxiv\.org/(abs|pdf)|\.pdf$|github\.com/[^/]+/[^/]+|youtube\.com/watch|youtu\.be/|"
                          r"/(news|blog|posts?|research|publications?|papers?|projects?|discover|articles?|stories)/[^/]+", re.I)


def _discover_feed(url: str, soup: BeautifulSoup) -> str | None:
    for link in soup.find_all("link", rel="alternate"):
        if "rss" in (link.get("type") or "") or "atom" in (link.get("type") or ""):
            return urljoin(url, link.get("href"))
    return None


def fetch_webpage(url: str, meta: dict) -> list[dict]:
    """有 RSS 就用 RSS；否则对页面链接做快照，报告新增链接（首次只取最前面几条作为基线展示）。"""
    if meta.get("feed_url"):
        return fetch_rss(meta["feed_url"], meta)
    html = net.get(url).text
    soup = BeautifulSoup(html, "lxml")
    feed_url = _discover_feed(url, soup)
    if feed_url:
        try:
            items = fetch_rss(feed_url, meta)
            if items:
                meta["feed_url"] = feed_url
                return items
        except Exception:  # noqa: BLE001
            pass
    links, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"]).split("#")[0]
        text = re.sub(r"\s+", " ", a.get_text(" ")).strip()
        if not text or SKIP_LINK.search(href) or href.rstrip("/") == url.rstrip("/") or href in seen:
            continue
        if a.find_parent(["footer", "nav", "header"]):
            continue
        # 只保留“内容”链接：较长的标题文字，或指向论文/项目/文章/视频的地址；导航类短链接忽略
        if len(text) < 20 and not CONTENT_HINT.search(href):
            continue
        if len(text) < 20:
            # “Paper / Code / PDF” 这类短链接：用所在条目（li/p/div）的文字作为标题
            block = a.find_parent(["li", "p", "tr", "div", "article"])
            ctx = re.sub(r"\s+", " ", block.get_text(" ")).strip() if block else ""
            if 20 <= len(ctx) <= 400:
                text = ctx
            elif len(text) < 4:
                continue
        seen.add(href)
        links.append({"id": _item_id("web", href), "type": "link", "title": text[:200], "url": href,
                      "published": "", "description": "", "thumbnail": ""})
    if not links:
        raise RuntimeError("页面中没有找到内容链接（可能需要 JS 渲染或有反爬验证），建议改用该站的 RSS 或 YouTube 频道")
    known = set(meta.get("known", []))
    first = not known
    meta["known"] = [it["id"] for it in links][:1000]
    if first:
        # 首次只展示几条最像“内容”的链接作为基线，之后只报告新增链接
        strong = [it for it in links if CONTENT_HINT.search(it["url"]) and len(it["title"]) >= 20][:5]
        for it in strong:
            it["baseline"] = True
        return strong
    return [it for it in links if it["id"] not in known]


def fetch_arxiv_author(url: str, meta: dict) -> list[dict]:
    if url.lower().startswith("arxiv-author:"):
        spec = url.split(":", 1)[1]
        name, _, cat = spec.partition("|")
        papers = arxiv.search_author(name.strip(), cat.strip() or None)
    else:  # https://arxiv.org/a/xxx 作者页
        feed_url = url.rstrip("/")
        if not feed_url.endswith(".atom2"):
            feed_url += ".atom2"
        papers = arxiv.parse_api_feed(net.get(feed_url).content)
    out = []
    for p in papers:
        authors = p["authors"]
        out.append({"id": _item_id("arxiv", p["id"]), "type": "paper", "title": p["title"],
                    "url": f"https://arxiv.org/abs/{p['id']}", "published": p.get("published", ""),
                    "description": ", ".join(authors[:6]) + (" 等" if len(authors) > 6 else "") + " — " + p["abstract"][:400],
                    "thumbnail": ""})
    return out


FETCHERS = {
    "youtube": fetch_youtube,
    "github_user": fetch_github_user,
    "github_repo": fetch_github_repo,
    "rss": fetch_rss,
    "webpage": fetch_webpage,
    "arxiv_author": fetch_arxiv_author,
}


def fetch_source(src: dict) -> tuple[list[dict], dict]:
    meta = json.loads(src.get("meta") or "{}")
    items = FETCHERS[src["type"]](src["url"], meta)
    return items, meta
