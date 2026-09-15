#!/usr/bin/env python3
"""爬取四六级真题全文语料（CET4 + CET6，三个阅读板块）。

为什么需要它：词汇差距统计需要一个「目标语料」做分母 —— 覆盖率曲线、风格蒸馏
基准、以及「你在四级真题上能读懂多少」都建立在这份语料上。

来源：english-exam.lazynote.cn（robots.txt 允许抓取，仅禁 /downloads/）。
产出（默认落在 语料库/真题阅读纯文本/，已被 .gitignore 排除，**永不进公开库**）：
  - exam_corpus.json   逐篇结构化（级别/板块/来源/标题/URL/词数/正文）
  - exam_corpus.txt    纯文本拼接，供蒸馏与覆盖率计算

设计要点：
  - **可断点续爬**：每篇抓完立即落盘，重跑自动跳过已抓到的 URL
  - **限速 + 重试**：默认每篇间隔 0.6s，失败退避重试 3 次
  - `source` 字段沿用「YYYY-MM-N Section C1」格式，保持与
    `app/services/similarity.py` 里 `'Section C' in source` 的过滤兼容
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://english-exam.lazynote.cn"
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = Path(os.getenv("READLOOPS_CORPUS_DIR") or ROOT / "语料库" / "真题阅读纯文本")
JSON_OUT = OUT_DIR / "exam_corpus.json"
TXT_OUT = OUT_DIR / "exam_corpus.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# (级别 slug, 级别名, 板块 slug, URL 片段, source 里的小节字母)
SECTIONS = [
    ("cet4", "CET4", "reading", "part3-section-c", "C"),
    ("cet4", "CET4", "long-reading", "part3-section-b", "B"),
    ("cet4", "CET4", "banked-cloze", "part3-section-a", "A"),
    ("cet6", "CET6", "reading", "part3-section-c", "C"),
    ("cet6", "CET6", "long-reading", "part3-section-b", "B"),
    ("cet6", "CET6", "banked-cloze", "part3-section-a", "A"),
]

DELAY = float(os.getenv("CRAWL_DELAY", "0.6"))
RETRIES = 3
TIMEOUT = 25


def fetch(url: str) -> str:
    """带退避重试地抓取一个页面。"""
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"抓取失败 {url}: {last}")


def strip_html(html: str) -> str:
    """把 HTML 片段转成纯文本，保留段落。"""
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL)
    # 试卷正文里的填空位（下划线 / 输入框）统一成 ____
    html = re.sub(r'<input[^>]*>', '____', html)
    html = re.sub(r"<br\s*/?>", "\n", html)
    html = re.sub(r"</p>", "\n\n", html)
    html = re.sub(r"<[^>]+>", "", html)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&quot;", '"'),
                 ("&#39;", "'"), ("&lt;", "<"), ("&gt;", ">")):
        html = html.replace(a, b)
    html = re.sub(r"[ \t]+", " ", html)
    return re.sub(r"\n{3,}", "\n\n", html).strip()


def list_section(level: str, section: str) -> list[str]:
    """列出某级别某板块下的全部文章链接。"""
    html = fetch(f"{BASE}/{level}/sections/{section}/")
    links = sorted(set(re.findall(
        rf'href=["\'](/{level}/articles/[^"\']+/)["\']', html)))
    return links


def parse_article(url: str, level_name: str, letter: str) -> dict | None:
    """抓取并解析一篇文章。"""
    page = fetch(url)
    m = re.search(r"<article[^>]*>(.*?)</article>", page, re.DOTALL)
    if not m:
        return None
    text = strip_html(m.group(1))
    # 去掉正文里残留的 "P1  " 段号前缀
    text = re.sub(r"(?m)^P\d+\s+", "", text)
    if len(text.split()) < 60:      # 太短的不当语料
        return None

    title_m = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.DOTALL)
    title = strip_html(title_m.group(1)) if title_m else ""

    # URL 形如 /cet6/articles/2015-06-1/part3-section-c-1/
    parts = [p for p in url.rstrip("/").split("/") if p]
    date = parts[parts.index("articles") + 1] if "articles" in parts else ""
    tail = parts[-1]
    num = tail.rsplit("-", 1)[-1] if tail.rsplit("-", 1)[-1].isdigit() else ""

    return {
        "source": f"{date} Section {letter}{num}",
        "title": title or f"{date} Section {letter}{num}",
        "url": url,
        "level": level_name,
        "section": letter,
        "word_count": len(text.split()),
        "text": text,
    }


def load_existing() -> list[dict]:
    if JSON_OUT.exists():
        try:
            return json.loads(JSON_OUT.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("!! 已有 JSON 损坏，重新开始")
    return []


def save(rows: list[dict]) -> None:
    """立即落盘（断点续爬的关键），JSON 与 txt 同时更新。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    TXT_OUT.write_text("\n\n".join(r["text"] for r in rows), encoding="utf-8")


def main() -> int:
    rows = load_existing()
    done = {r["url"] for r in rows}
    if rows:
        print(f"续爬：已有 {len(rows)} 篇")

    targets: list[tuple[str, str, str, str]] = []
    for level, level_name, section, frag, letter in SECTIONS:
        try:
            links = list_section(level, section)
        except RuntimeError as exc:
            print(f"!! 列表抓取失败 {level}/{section}: {exc}")
            continue
        for link in links:
            if frag not in link:
                continue
            targets.append((BASE + link, level_name, letter, f"{level}/{section}"))
        print(f"  {level}/{section}: {len(links)} 篇")

    todo = [t for t in targets if t[0] not in done]
    print(f"\n待抓 {len(todo)} / 共 {len(targets)} 篇（已抓 {len(done)}）\n")

    ok = fail = 0
    for i, (url, level_name, letter, tag) in enumerate(todo, 1):
        try:
            row = parse_article(url, level_name, letter)
        except RuntimeError as exc:
            print(f"  [{i}/{len(todo)}] 失败 {exc}")
            fail += 1
            continue
        if not row:
            fail += 1
            continue
        rows.append(row)
        done.add(url)
        ok += 1
        if ok % 25 == 0 or i == len(todo):
            save(rows)
            print(f"  [{i}/{len(todo)}] 已抓 {ok} 篇（累计 {len(rows)}）")
        time.sleep(DELAY)

    save(rows)
    print(f"\n完成：新增 {ok} 篇，失败/跳过 {fail} 篇，总计 {len(rows)} 篇")
    print(f"  {JSON_OUT}")
    print(f"  {TXT_OUT}")

    from collections import Counter
    print("  分布:", dict(Counter((r["level"], r["section"]) for r in rows)))
    print("  总词数:", sum(r["word_count"] for r in rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
