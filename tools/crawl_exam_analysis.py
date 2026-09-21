#!/usr/bin/env python3
"""抓取真题文章页的**结构化精读数据**。

## 为什么

现有 `crawl_exam_corpus.py` 只抽了 `text` 字段 —— 而源站每篇文章页上还有
一整套教学化分析，我们全扔了：

| 小节 | 内容 | 对本项目的用途 |
|---|---|---|
| `#passage` | 原文分段 | 已有 |
| `#paragraphs` | 逐段精读（英文标题 + 中文解读） | 段落级结构 |
| `#structure` | **篇章结构**（意群划分 + 每段功能 + gist） | **生成时仿的「形式层」骨架** |
| `#quick-genre` | **体裁速判** | 体裁分布目标 |
| `#attitude` | **作者态度 + 态度信号词（含段号）** | 态度分布目标 |
| `#sentences` | **句子精讲**（9 句/篇，附独立解析页链接） | **阶段 8 长难句标注** |
| `#words` | 生词推断（推断释义 + 推理链） | 词汇习得方法 |
| `#rhetoric` | 修辞手法 | 分析维度 |
| `#implied` | 推断题分析 | 题型维度 |
| `#self-check` | 段落自检题（1 正确 + 5 干扰 + 解析） | 题型模式（**非试卷原题**） |

## 合规

源站 `robots.txt` 为**所有** user-agent（含 GPTBot/ClaudeBot）声明
`Disallow: /downloads/` + `Allow: /` —— 除下载目录外全部允许抓取。本工具只访问
文章页，不碰 `/downloads/`。

⚠️ 页脚注明「真题版权归全国大学英语四六级考试委员会所有」，
**产出仅限本地自用，不得随开源包分发**。

## 用法

    python3 tools/crawl_exam_analysis.py --from-corpus      # 抓我们已有的 547 篇
    python3 tools/crawl_exam_analysis.py --from-sitemap     # 抓站点全部（含听力）
    python3 tools/crawl_exam_analysis.py --limit 5          # 试跑
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "语料库" / "真题精读" / "exam_analysis.json"
SITEMAP = "https://english-exam.lazynote.cn/sitemap-articles.xml"
UA = {"User-Agent": "Mozilla/5.0 (compatible; ReadLoops/2.8; personal study tool)"}


def fetch(url: str, tries: int = 3) -> str | None:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if i < tries - 1:
                time.sleep(2 * (i + 1))
        except Exception:
            if i < tries - 1:
                time.sleep(2 * (i + 1))
    return None


_LEAD_LABEL = re.compile(r"^(?:P\d+\b[\s:：]*)+")


def strip_label(x: str) -> str:
    """剥掉段首的定位标签（P1 / P12）—— 它来自锚点标记，不是正文。"""
    return _LEAD_LABEL.sub("", x).strip()


def clean(x: str) -> str:
    x = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", x, flags=re.S | re.I)
    x = re.sub(r"<[^>]+>", " ", x)
    return re.sub(r"\s+", " ", html.unescape(x)).strip()


def slice_section(s: str, name: str, ends: list[str]) -> str:
    """截取 id="<name>" 到下一个已知小节之间的片段。"""
    m = re.search(rf'id="{re.escape(name)}(?:-title)?"', s)
    if not m:
        return ""
    # ⚠️ 从**开标签结束处**开始，否则会把 `id="x" aria-labelledby=...>` 当文本带出来
    start = s.find(">", m.end())
    start = start + 1 if start > 0 else m.start()
    stop = len(s)
    for e in ends:
        m2 = re.search(rf'id="{re.escape(e)}(?:-title)?"', s[start:])
        if m2:
            stop = min(stop, start + m2.start())
    return s[start:stop]


def items(seg: str, prefix: str) -> list[dict]:
    """把片段按 id="<prefix>-<key>" 切成条目。

    ⚠️ key 不一定是数字：句子/段落/意群用编号（sdg-1、pp-3），
    但生词用词本身（wi-riot、wi-self-sufficiency）。
    """
    marks = list(re.finditer(rf'id="{re.escape(prefix)}-([^"]+)"', seg))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(seg)
        start = seg.find(">", m.end())
        start = start + 1 if start > 0 else m.start()
        out.append({"key": m.group(1), "text": clean(seg[start:end])})
    return out


# 小节的先后顺序，用于界定切片边界
ORDER = ["passage", "paragraphs", "self-check", "structure", "quick-main", "qm-signals",
         "main-idea", "words", "rhetoric", "implied", "attitude", "quick-genre",
         "sentences", "resources", "faq"]


def parse(url: str, page: str) -> dict:
    def sec(name: str) -> str:
        idx = ORDER.index(name) if name in ORDER else -1
        ends = ORDER[idx + 1:] if idx >= 0 else []
        return slice_section(page, name, ends)

    rec: dict = {"url": url, "source_url": url}

    # 标题：<title> 或 h1
    m = re.search(r"<title>([^<]+)</title>", page)
    rec["title"] = html.unescape(m.group(1)).strip() if m else ""

    # 原文分段
    paras = items(sec("passage"), "para")
    rec["paragraphs"] = [strip_label(p["text"]) for p in paras]

    # 题型：从 URL 推
    pm = re.search(r"/(cet[46])/articles/([^/]+)/([^/]+)/?", url)
    if pm:
        rec["level"] = pm.group(1).upper()
        rec["paper"] = pm.group(2)
        rec["part"] = pm.group(3)

    # 逐段精读
    rec["deep_reading"] = [x["text"][:600] for x in items(sec("paragraphs"), "pp")]

    # 篇章结构：小节标题里带意群数
    st = sec("structure")
    rec["structure_raw"] = clean(st)[:1500]
    rec["structure"] = [x["text"][:400] for x in items(st, "sg")]

    # 句子精讲
    sd = sec("sentences")
    sents = [x["text"][:500] for x in items(sd, "sdg")]
    rec["sentences"] = sents
    rec["sentence_links"] = re.findall(r'href="(/cet[46]/paper/[^"]+/p\d+-s\d+/)"', sd)

    # 生词推断
    rec["word_inference"] = [x["text"][:400] for x in items(sec("words"), "wi")]

    # 修辞 / 推断题
    rec["rhetoric"] = [x["text"][:300] for x in items(sec("rhetoric"), "rh")]
    rec["implied"] = [x["text"][:300] for x in items(sec("implied"), "im")]

    # 作者态度
    rec["attitude"] = clean(sec("attitude"))[:800]

    # 体裁
    rec["genre"] = clean(sec("quick-genre"))[:500]

    # 主旨
    rec["main_idea"] = clean(sec("main-idea"))[:500]

    # 自检题（非试卷原题）
    rec["self_check"] = [x["text"][:600] for x in items(sec("self-check"), "check")]

    # CEFR / 体裁 / 结构：从 JSON-LD 的 FAQPage 里取（站方给出的结论）
    rec["faq"] = []
    for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            d = json.loads(b)
        except (ValueError, TypeError):
            continue
        if isinstance(d, dict) and d.get("@type") == "FAQPage":
            for it in d.get("mainEntity", []):
                ans = (it.get("acceptedAnswer") or {}).get("text", "")
                rec["faq"].append({"q": it.get("name", ""), "a": ans})
    rec["word_count"] = sum(len(p.split()) for p in rec["paragraphs"])
    return rec


def load_urls(args) -> list[str]:
    if args.from_sitemap:
        raw = fetch(SITEMAP) or ""
        urls = [u for u in re.findall(r"<loc>([^<]+)</loc>", raw)
                if re.search(r"/part\d-section", u)]
        return urls
    corpus = ROOT / "语料库" / "真题阅读纯文本" / "exam_corpus.json"
    data = json.loads(corpus.read_text(encoding="utf-8"))
    return [d["url"] for d in data]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-sitemap", action="store_true", help="抓站点全部文章页（含听力）")
    ap.add_argument("--from-corpus", action="store_true", help="抓我们已有语料对应的页")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.6)
    args = ap.parse_args()

    urls = load_urls(args)
    if args.limit:
        urls = urls[:args.limit]
    print(f"待抓 {len(urls)} 个页面")

    done: dict[str, dict] = {}
    if OUT.exists():
        done = {r["url"]: r for r in json.loads(OUT.read_text(encoding="utf-8"))}
        print(f"已完成 {len(done)} 个，断点续抓")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    for i, url in enumerate(urls, 1):
        if url in done:
            continue
        page = fetch(url)
        if not page:
            fail += 1
            print(f"  [{i}/{len(urls)}] ✗ {url}")
            continue
        try:
            done[url] = parse(url, page)
            ok += 1
        except Exception as e:
            fail += 1
            print(f"  [{i}/{len(urls)}] ✗ 解析失败 {e}")
        if i % 10 == 0 or i == len(urls):
            OUT.write_text(json.dumps(list(done.values()), ensure_ascii=False),
                           encoding="utf-8")
            print(f"  [{i}/{len(urls)}] 成功 {ok} 失败 {fail}")
        time.sleep(args.delay)

    OUT.write_text(json.dumps(list(done.values()), ensure_ascii=False), encoding="utf-8")
    print(f"\n已写入 {OUT.relative_to(ROOT)}（{len(done)} 篇）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
