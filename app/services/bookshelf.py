"""书架：公共领域书籍的检索与下载。

数据源只使用 **Project Gutenberg 官方站点**。
第三方镜像 gutendex.com 实测时好时坏（偶发超时 / 空响应 / 需尾斜杠），
不能作为唯一依赖，因此这里直接解析官方搜索页，下载也走官方路径。

为什么只收公共领域书籍：这类书籍的版权已过期，可以合法下载、分发与再加工，
是唯一没有版权风险的语料来源。用户的私人材料请走「材料导入」。
"""
from __future__ import annotations

import html
import json
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from app.config import GUTENBERG_BASE, LIBRARY_DIR
from app.database import get_db

# Gutenberg 对脚本访问比较敏感，带上 UA 更稳
_HEADERS = {
    "User-Agent": "ReadLoops/2.3.1 (personal reading trainer; contact: local user)",
    "Accept": "text/html,application/json,text/plain,*/*",
}
_TIMEOUT = 20.0


def _client() -> httpx.Client:
    # trust_env=False：避免本机 HTTP_PROXY 劫持本地/直连请求
    return httpx.Client(timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True, trust_env=False)


# ---------------------------------------------------------------- 搜索

_BOOKLINK_RE = re.compile(r'<li class="booklink">(.*?)</li>', re.S)
_HREF_RE = re.compile(r'href="/ebooks/(\d+)"')
_TITLE_RE = re.compile(r'<span class="title">(.*?)</span>', re.S)
_AUTHOR_RE = re.compile(r'<span class="subtitle">(.*?)</span>', re.S)
_EXTRA_RE = re.compile(r'<span class="extra">(.*?)</span>', re.S)


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def search_books(query: str, page: int = 1, language: str = "en") -> dict:
    """搜索 Gutenberg 书籍。

    返回 {count_hint, page, has_next, results: [{source_id, title, author, downloads}]}
    """
    query = (query or "").strip()
    if not query:
        return {"results": [], "page": page, "has_next": False}

    params = {"query": query}
    if language:
        # Gutenberg 的语言过滤参数形如 ?query=xxx&lang=en（部分页面用 languages）
        params["lang"] = language
    if page > 1:
        params["start_index"] = (page - 1) * 25 + 1

    url = f"{GUTENBERG_BASE}/ebooks/search/"
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        body = r.text

    results = []
    for block in _BOOKLINK_RE.findall(body):
        m_id = _HREF_RE.search(block)
        if not m_id:
            continue
        m_title = _TITLE_RE.search(block)
        m_author = _AUTHOR_RE.search(block)
        m_extra = _EXTRA_RE.search(block)
        downloads = 0
        if m_extra:
            num = re.search(r"([\d,]+)", _clean(m_extra.group(1)))
            if num:
                downloads = int(num.group(1).replace(",", ""))
        results.append({
            "source": "gutenberg",
            "source_id": m_id.group(1),
            "title": _clean(m_title.group(1)) if m_title else "(无标题)",
            "author": _clean(m_author.group(1)) if m_author else "",
            "downloads": downloads,
            "cover_url": f"{GUTENBERG_BASE}/cache/epub/{m_id.group(1)}/pg{m_id.group(1)}.cover.small.jpg",
            "detail_url": f"{GUTENBERG_BASE}/ebooks/{m_id.group(1)}",
        })

    return {
        "results": results,
        "page": page,
        "has_next": len(results) >= 25,
    }


# ---------------------------------------------------------------- 下载

def _download_urls(source_id: str) -> list[tuple[str, str]]:
    """候选下载地址，按优先级排列：(格式, URL)。"""
    sid = str(source_id)
    return [
        ("txt", f"{GUTENBERG_BASE}/cache/epub/{sid}/pg{sid}.txt"),
        ("txt", f"{GUTENBERG_BASE}/files/{sid}/{sid}-0.txt"),
        ("epub", f"{GUTENBERG_BASE}/ebooks/{sid}.epub3.images"),
        ("epub", f"{GUTENBERG_BASE}/ebooks/{sid}.epub.images"),
    ]


def download_book(source_id: str, title: str = "", author: str = "",
                  language: str = "en", subjects: Optional[list] = None) -> dict:
    """下载一本书到本地书库，并写入 books 表。

    逐个尝试候选地址，成功即止；全部失败时记录 failed 状态与原因。
    """
    sid = str(source_id)
    subjects = subjects or []
    now = int(time.time())
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)

    last_error = ""
    with _client() as c:
        for fmt, url in _download_urls(sid):
            try:
                r = c.get(url)
                if r.status_code != 200 or not r.content:
                    last_error = f"{url} -> HTTP {r.status_code}"
                    continue

                ext = "txt" if fmt == "txt" else "epub"
                path = LIBRARY_DIR / f"pg{sid}.{ext}"
                path.write_bytes(r.content)

                text = ""
                if fmt == "txt":
                    text = _decode_text(r.content)
                word_count = len(re.findall(r"[A-Za-z][A-Za-z'-]*", text)) if text else 0

                with get_db() as db:
                    db.execute(
                        """INSERT INTO books
                           (source, source_id, title, author, language, subjects, format,
                            download_url, local_path, word_count, status, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT DO NOTHING""",
                        ("gutenberg", sid, title or f"Gutenberg #{sid}", author, language,
                         json.dumps(subjects, ensure_ascii=False), ext, url, str(path),
                         word_count, "downloaded", now, now),
                    )
                    row = db.execute(
                        "SELECT id FROM books WHERE source='gutenberg' AND source_id=?",
                        (sid,),
                    ).fetchone()
                    if row:
                        db.execute(
                            """UPDATE books SET local_path=?, format=?, word_count=?,
                               status='downloaded', error=NULL, updated_at=? WHERE id=?""",
                            (str(path), ext, word_count, now, row["id"]),
                        )
                    book_id = row["id"] if row else None

                return {"ok": True, "book_id": book_id, "path": str(path),
                        "format": ext, "word_count": word_count}

            except Exception as e:  # 网络异常换下一个候选
                last_error = f"{url} -> {e}"
                continue

    with get_db() as db:
        db.execute(
            """INSERT INTO books
               (source, source_id, title, author, language, subjects, format,
                status, error, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("gutenberg", sid, title or f"Gutenberg #{sid}", author, language,
             json.dumps(subjects, ensure_ascii=False), "", "failed", last_error, now, now),
        )
    return {"ok": False, "error": last_error or "所有候选地址均不可用"}


def _decode_text(raw: bytes) -> str:
    """Gutenberg 的 txt 多为 UTF-8，少数是 latin-1，逐个尝试。"""
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def strip_gutenberg_boilerplate(text: str) -> str:
    """去掉 Gutenberg 的页眉页脚声明，只保留正文。

    正文被 *** START OF ... *** / *** END OF ... *** 包裹，取中间部分即可。
    """
    m = re.search(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", text, re.S | re.I)
    if m:
        text = text[m.end():]
    m = re.search(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", text, re.S | re.I)
    if m:
        text = text[:m.start()]
    return text.strip()


# ---------------------------------------------------------------- 书架查询

def list_books(status: Optional[str] = None) -> list[dict]:
    sql = "SELECT * FROM books"
    args: tuple = ()
    if status:
        sql += " WHERE status=?"
        args = (status,)
    sql += " ORDER BY created_at DESC"
    with get_db() as db:
        return [dict(r) for r in db.execute(sql, args)]


def get_book(book_id: int) -> Optional[dict]:
    with get_db() as db:
        row = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        return dict(row) if row else None


def delete_book(book_id: int) -> bool:
    """删除书架条目，同时删掉下载到本地书库的文件。

    ⚠️ 只删**书库目录内**的文件。local_path 一直由本模块生成（LIBRARY_DIR/pg{id}.ext），
    但删除是不可逆的 —— 加一道目录包含校验，万一将来数据被写坏或有人手工改库，
    也不会越界删掉用户自己的文件。
    """
    book = get_book(book_id)
    if not book:
        return False
    p = book.get("local_path")
    if p:
        try:
            target = Path(p).resolve()
            library = LIBRARY_DIR.resolve()
            if target == library or library in target.parents:
                target.unlink(missing_ok=True)
            else:
                # 不在书库目录内：只解除引用，绝不删文件
                print(f"拒绝删除书库目录外的文件：{target}")
        except OSError:
            pass
    with get_db() as db:
        db.execute("DELETE FROM books WHERE id=?", (book_id,))
    return True
