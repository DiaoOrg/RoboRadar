"""arXiv 抓取：公告列表、元数据、PDF 首页（单位/通讯作者）、关键词匹配。"""
from __future__ import annotations

import re
import time
from datetime import datetime

import feedparser
from bs4 import BeautifulSoup

from . import net

RECENT_URL = "https://arxiv.org/list/{cat}/recent?skip=0&show=2000"
RSS_URL = "https://rss.arxiv.org/rss/{cat}"
API_URL = "https://export.arxiv.org/api/query"


# ---------------------------------------------------------------- 列表

def fetch_recent_days(cat: str) -> dict[str, list[dict]]:
    """解析 /list/{cat}/recent，返回 {公告日期: [{id, title, announce_type}]}，按日期倒序。"""
    html = net.get(RECENT_URL.format(cat=cat), timeout=60).text
    soup = BeautifulSoup(html, "lxml")
    days: dict[str, list[dict]] = {}
    for h3 in soup.find_all("h3"):
        m = re.match(r"\w+,\s+(\d+ \w+ \d{4})", h3.get_text(strip=True))
        if not m:
            continue
        date = datetime.strptime(m.group(1), "%d %b %Y").strftime("%Y-%m-%d")
        dl = h3.parent if h3.parent and h3.parent.name == "dl" else h3.find_next_sibling("dl")
        entries = []
        if dl:
            for dt in dl.find_all("dt"):
                a = dt.find("a", title="Abstract")
                if not a:
                    continue
                aid = a.get("id") or a.get_text(strip=True).replace("arXiv:", "")
                dd = dt.find_next_sibling("dd")
                primary = dd.select_one(".primary-subject") if dd else None
                is_cross = bool(primary) and f"({cat})" not in primary.get_text()
                entries.append({"id": aid.strip(), "announce_type": "cross" if is_cross else "new"})
        days.setdefault(date, []).extend(entries)
    return days


def fetch_rss(cat: str) -> tuple[str | None, list[dict]]:
    """RSS 兜底：只含最新一天，返回 (日期, 条目)。"""
    feed = feedparser.parse(net.get(RSS_URL.format(cat=cat)).content)
    out, date = [], None
    for e in feed.entries:
        atype = e.get("arxiv_announce_type", "new")
        if atype.startswith("replace"):
            continue
        aid = e.link.rsplit("/", 1)[-1]
        out.append({"id": aid, "announce_type": atype})
        if not date and e.get("published_parsed"):
            date = time.strftime("%Y-%m-%d", e.published_parsed)
    return date, out


def fetch_metadata(ids: list[str]) -> list[dict]:
    """通过 arXiv API 批量取元数据（标题、摘要、作者、comments、分类）。"""
    out = []
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        r = net.get(API_URL, params={"id_list": ",".join(chunk), "max_results": len(chunk)}, timeout=90)
        out.extend(parse_api_feed(r.content))
        if i + 100 < len(ids):
            time.sleep(3.1)  # arXiv API 要求请求间隔 ≥3s
    return out


def parse_api_feed(content: bytes) -> list[dict]:
    feed = feedparser.parse(content)
    out = []
    for e in feed.entries:
        m = re.search(r"abs/([^v\s]+)(v\d+)?$", e.id)
        if not m:
            continue
        out.append({
            "id": m.group(1),
            "version": m.group(2) or "v1",
            "title": _clean(e.title),
            "abstract": _clean(e.get("summary", "")),
            "authors": [a.get("name", "") for a in e.get("authors", [])],
            "comments": _clean(e.get("arxiv_comment", "")),
            "subjects": [t.get("term") for t in e.get("tags", [])],
            "published": e.get("published", ""),
        })
    return out


def search_author(name: str, category: str | None = None, max_results: int = 10) -> list[dict]:
    q = f'au:"{name}"'
    if category:
        q += f" AND cat:{category}"
    r = net.get(API_URL, params={"search_query": q, "sortBy": "submittedDate", "sortOrder": "descending",
                                 "max_results": max_results}, timeout=60)
    return parse_api_feed(r.content)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# ---------------------------------------------------------------- 全文首页

ENVELOPE = re.compile(r"\\faIcon\[regular\]\{envelope\}|\\faEnvelope|\\Letter|\\envelope")


def fetch_header_text(arxiv_id: str, max_chars: int = 5000) -> str:
    """取论文首页文本（作者、单位、通讯标记都在这里）。优先 PDF，过大或失败时用 HTML 版。"""
    try:
        data = net.download_capped(f"https://arxiv.org/pdf/{arxiv_id}", max_bytes=60 * 1024 * 1024)
        if data:
            import pymupdf

            doc = pymupdf.open(stream=data, filetype="pdf")
            text = doc[0].get_text()
            # 通讯作者信息有时在第一页脚注之外，补抓前两页中带 corresponding/email 的行
            extra = []
            for pno in range(1, min(2, doc.page_count)):
                for line in doc[pno].get_text().splitlines():
                    if re.search(r"correspond|e-?mail|@", line, re.I):
                        extra.append(line)
            text = text + ("\n" + "\n".join(extra) if extra else "")
            return text[:max_chars]
    except Exception:
        pass
    try:
        html = net.get(f"https://arxiv.org/html/{arxiv_id}", retries=2).text
        soup = BeautifulSoup(html, "lxml")
        for ann in soup.select("annotation"):
            if ENVELOPE.search(ann.get_text()):
                ann.replace_with(" ✉(corresponding) ")
        parts = [n.get_text(" ", strip=True) for n in soup.select(".ltx_authors, .ltx_author_notes, .ltx_note_content")]
        return "\n".join(parts)[:max_chars]
    except Exception:
        return ""


# ---------------------------------------------------------------- 匹配与规则提取

def _norm(s: str) -> str:
    s = s.lower().replace("-", " ").replace("_", " ").replace("/", " ")
    return re.sub(r"\s+", " ", s)


def _kw_regex(kw: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + re.escape(_norm(kw)) + r"(?![a-z0-9])")


def match_topics(title: str, abstract: str, topics: list[dict]) -> dict:
    """关键词匹配。关键词里写 `a + b` 表示 a 和 b 都要出现。标题命中权重 3，摘要命中权重 1。"""
    t, a = _norm(title), _norm(abstract)
    score, kws, filter_topics, tag_topics = 0.0, [], [], []
    for topic in topics:
        if not topic.get("enabled", True):
            continue
        hit = False
        for kw in topic.get("keywords", []):
            parts = [p.strip() for p in kw.split("+") if p.strip()]
            if not parts:
                continue
            regs = [_kw_regex(p) for p in parts]
            in_title = all(r.search(t) for r in regs)
            in_any = all(r.search(t) or r.search(a) for r in regs)
            if in_any:
                hit = True
                kws.append(kw)
                score += 3 if in_title else 1
        if hit:
            (filter_topics if topic.get("role", "filter") == "filter" else tag_topics).append(topic["name"])
    return {"matched": bool(filter_topics), "score": score, "keywords": sorted(set(kws)),
            "topics": filter_topics + tag_topics}


ROBOTS = [
    (r"unitree\s*g1|\bg1\b(?= humanoid| robot)|\bg1\b", "G1"), (r"\bh1[- ]?2\b", "H1-2"), (r"\bh1\b", "H1"),
    (r"\bgo2\b", "Go2"), (r"\bgo1\b", "Go1"), (r"\bb2\b(?= robot| quadruped)", "B2"), (r"\ba1\b(?= robot| quadruped)", "A1"),
    (r"\bdigit\b", "Digit"), (r"\bcassie\b", "Cassie"), (r"\batlas\b", "Atlas"), (r"\bspot\b(?= robot)|boston dynamics spot", "Spot"),
    (r"\banymal\b", "ANYmal"), (r"booster t1|\bt1 humanoid", "Booster T1"), (r"\bgr[- ]?1\b|fourier", "Fourier GR-1"),
    (r"\bpm01\b", "PM01"), (r"\boptimus\b", "Optimus"), (r"\bfranka\b|\bpanda arm\b", "Franka"), (r"\baloha\b", "ALOHA"),
    (r"\bur5e?\b", "UR5"), (r"berkeley humanoid", "Berkeley Humanoid"), (r"\btoddlerbot\b", "ToddlerBot"),
]
REAL_HINT = re.compile(r"real[- ]world|real robot|physical robot|hardware|sim[- ]?to[- ]?real|on a real|deploy", re.I)
URL_RE = re.compile(r"https?://[^\s\)\]\}<>,;\"']+")


def guess_platform(text: str) -> str:
    low = text.lower()
    names = []
    for pat, name in ROBOTS:
        if re.search(pat, low) and name not in names:
            names.append(name)
    real = bool(REAL_HINT.search(text))
    if not names:
        return "实机验证" if real else ""
    return " / ".join(names[:3]) + (" 实机" if real else " 仿真")


def extract_links(comments: str, abstract: str, header: str) -> list[dict]:
    links, seen = [], set()
    for src in (comments, abstract, header):
        for u in URL_RE.findall(src or ""):
            u = u.rstrip(".")
            if u in seen or "arxiv.org" in u or "creativecommons" in u or "doi.org" in u:
                continue
            seen.add(u)
            label = ("代码" if "github.com" in u or "gitlab" in u else
                     "数据/模型" if "huggingface.co" in u else "视频" if "youtu" in u else "项目主页")
            links.append({"label": label, "url": u})
    return links[:4]


AFFIL_RE = re.compile(r"universit|institut|laborator|\blab\b|college|school|academy|inc\.|ltd|corporation|"
                      r"research|nvidia|google|deepmind|meta|eth|mit\b|tsinghua|peking|大学|研究院|公司", re.I)


def guess_affiliations(header: str) -> list[str]:
    """无 LLM 时的兜底：从首页摘要之前的行里挑像单位的行。"""
    head = re.split(r"\bAbstract\b", header, maxsplit=1, flags=re.I)[0]
    out = []
    for line in head.splitlines():
        line = re.sub(r"^[\d\s,∗*†‡§¶#]+", "", line).strip()
        if 6 < len(line) < 160 and AFFIL_RE.search(line) and line not in out:
            out.append(line)
    return out[:5]


def guess_corresponding(header: str, authors: list[str]) -> list[str]:
    """无 LLM 时的兜底：读出“X Corresponding author”图例里的符号，再找带该符号的作者。"""
    head = header[:3000]
    syms = {"✉", "🖂"}
    for m in re.finditer(r"([∗*†‡§¶#✉])\s*\(?\s*corresponding", head, re.I):
        syms.add(m.group(1))
    found = []
    for name in authors:
        idx = head.find(name)
        if idx < 0:
            continue
        tail = head[idx + len(name): idx + len(name) + 14]
        marks = re.match(r"^[\s\d,∗*†‡§¶#✉🖂]*", tail).group(0)
        if any(s in marks for s in syms):
            found.append(name)
    return found
