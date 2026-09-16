#!/usr/bin/env python3
"""为短语补释义 —— **只从真实词典来源取，绝不凭空生成**。

## 为什么不能用 AI 生成

短语释义如果由模型编，会出现两类错误：把非固定搭配讲成固定搭配、
把搭配的意思讲错。而学习者没有能力分辨 —— 错误的释义比没有释义更糟。

## 三个真实来源，按优先级

| 来源 | 语言 | 依据 | 覆盖 |
|---|---|---|---|
| **ECDICT** | 中文 | MIT，与本项目词库同源；含变体匹配（连字符/复数） | ~264/1037 |
| **维基百科跨语言链接** | 中文 | 英文条目 → 中文条目名（`traffic jam` → 交通堵塞），CC BY-SA | 待测 |
| **英文维基词典** | 英文 | `page/definition` 端点，CC BY-SA | 待测 |

繁简转换用 **OpenCC 的 TSCharacters 映射表**（Apache-2.0，公开可审计），
落在 `data/opencc/TSCharacters.txt`，**不引入 pip 依赖**。

## 「词典里有没有条目」本身就是最好的筛选

实测：`high school` / `social media` / `traffic jam` / `work-life balance` 都有词条，
而 `young people` / `new study` **404** —— 因为后者**根本不是词汇单位**，
只是碰巧连在一起的高频词。所以**查不到来源的短语不该硬造释义，
而应当从短语库里剔除**（`meaning_source IS NULL` 即标记为待剔除）。

对比过 PMI 方案：PMI 最高的一批全是专有名词（`loma linda` / `hal gregersen`），
不如「词典是否有条目」干净。

用法：
    python3 tools/enrich_phrases.py              # 跑全部三个阶段
    python3 tools/enrich_phrases.py --limit 50   # 试跑
    python3 tools/enrich_phrases.py --stats      # 只看进度
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UA = {"User-Agent": "readloops/2.6 (personal study tool; offline vocabulary trainer)"}
ECDICT_CSV = ROOT / "data" / "ecdict" / "ecdict.csv"
TS_TABLE = ROOT / "data" / "opencc" / "TSCharacters.txt"
WIKI_BATCH = 40          # 维基 API 单次最多 50 个标题，留点余量

# ---------------------------------------------------------------- 人工复核的修正
# 每条都注明依据。放在这里而不是手改数据库 —— 工具重跑时会自动应用，不会被覆盖，
# 也让「哪些是我判断过的、依据是什么」可审计。
#
# 格式：短语 -> (正确释义 or None, 来源标记)
MANUAL_FIXES: dict[str, tuple[str | None, str]] = {
    # Wikidata 的 zh-hans 标签是「社交媒体」；而英文维基的跨语言链接给的是
    # 中文维基条目名「社群媒体」，那是台湾用词。
    "social media": ("社交媒体", "wikidata"),
    # 真题语料里 work life 有 14/17 次是 work-life balance 的碎片，不是独立单位；
    # ECDICT 给的是机械义项「[机] 有效期间」，与语料用法不符。
    # 正确的单位是 work-life balance（它自己有词条），所以这里标为「非词汇单位」。
    "work life": (None, "not_a_unit"),
}


def apply_manual_fixes(conn) -> int:
    now = int(time.time())
    n = 0
    for text, (meaning, source) in MANUAL_FIXES.items():
        cur = conn.execute(
            "UPDATE words SET meaning=?, meaning_source=?, updated_at=? "
            "WHERE type='phrase' AND text=?",
            (meaning, source, now, text))
        n += cur.rowcount
    conn.commit()
    return n


# ---------------------------------------------------------------- 繁简

_TS: dict[str, str] | None = None


def ts_map() -> dict[str, str]:
    """OpenCC 的繁→简字符表（Apache-2.0）。取第一个候选字。"""
    global _TS
    if _TS is None:
        m = {}
        if TS_TABLE.exists():
            for line in TS_TABLE.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    m[parts[0]] = parts[1]
        _TS = m
    return _TS


def to_simplified(s: str) -> str:
    m = ts_map()
    return "".join(m.get(c, c) for c in (s or ""))


# ---------------------------------------------------------------- HTTP

def get_json(url: str, tries: int = 4) -> dict | None:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 503) and i < tries - 1:
                time.sleep(2.5 * (i + 1))     # 限流：退避重试
                continue
            return None
        except Exception:
            if i < tries - 1:
                time.sleep(1.5)
                continue
            return None
    return None


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- 来源 1：ECDICT

def variants(p: str) -> list[str]:
    """ECDICT 里的变体形式：连字符、复数、去空格。"""
    out = [p]
    out.append(p.replace(" ", "-"))
    out.append(p.replace("-", " "))
    out.append(re.sub(r"\b(\w+)s\b", r"\1", p))
    out.append(re.sub(r"\b(\w+)s\b", r"\1", p).replace(" ", "-"))
    out.append(p.replace(" ", ""))
    out.append(re.sub(r"\b(\w+)es\b", r"\1", p))
    seen, uniq = set(), []
    for v in out:
        if v and v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def load_ecdict_multi() -> dict[str, str]:
    """ECDICT 里的多词条目 → 中文释义。"""
    out: dict[str, str] = {}
    if not ECDICT_CSV.exists():
        return out
    csv.field_size_limit(10 ** 7)
    with open(ECDICT_CSV, encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.DictReader(f):
            w = (row.get("word") or "").strip().lower()
            tr = (row.get("translation") or "").strip()
            if w and " " in w and tr:
                out.setdefault(w, tr.replace("\\n", " ").strip())
    return out


def phase_ecdict(conn, rows: list[tuple[int, str]]) -> int:
    ec = load_ecdict_multi()
    now = int(time.time())
    n = 0
    for wid, text in rows:
        for v in variants(text):
            if v in ec:
                conn.execute(
                    "UPDATE words SET meaning=?, meaning_source='ecdict', updated_at=? WHERE id=?",
                    (ec[v][:300], now, wid))
                n += 1
                break
    conn.commit()
    return n


# ---------------------------------------------------------------- 来源 2：维基百科跨语言链接

def phase_wikipedia(conn, rows: list[tuple[int, str]], delay: float) -> int:
    """英文维基条目 → 中文条目名，作为中文释义。"""
    now = int(time.time())
    n = 0
    for i in range(0, len(rows), WIKI_BATCH):
        chunk = rows[i:i + WIKI_BATCH]
        titles = "|".join(t for _, t in chunk)
        u = ("https://en.wikipedia.org/w/api.php?action=query&format=json&prop=langlinks"
             "&lllang=zh&lllimit=50&redirects=1&titles=" + urllib.parse.quote(titles))
        d = get_json(u)
        if not d:
            print(f"    [wikipedia] 第 {i//WIKI_BATCH+1} 批失败，跳过")
            time.sleep(delay)
            continue
        # 标题规范化后可能变了，用 normalized/redirects 映射回原短语
        norm = {}
        for key in ("normalized", "redirects"):
            for m in d.get("query", {}).get(key, []):
                norm[(m.get("from") or "").lower()] = m.get("to") or ""
        by_title = {}
        for pid, pg in d.get("query", {}).get("pages", {}).items():
            if pid != "-1":
                by_title[(pg.get("title") or "").lower()] = pg

        for wid, text in chunk:
            key = text.lower()
            pg = by_title.get(key) or by_title.get(norm.get(key, "").lower())
            if not pg:
                continue
            ll = pg.get("langlinks")
            if not ll:
                continue
            zh = to_simplified(ll[0]["*"])
            if not zh or len(zh) > 40:
                continue
            conn.execute(
                "UPDATE words SET meaning=?, meaning_source='wikipedia', updated_at=? WHERE id=?",
                (zh, now, wid))
            n += 1
        conn.commit()
        print(f"    [wikipedia] {min(i+WIKI_BATCH, len(rows))}/{len(rows)}，已补 {n}")
        time.sleep(delay)
    return n


# ---------------------------------------------------------------- 来源 2b：Wikidata 标签

def phase_wikidata(conn, rows: list[tuple[int, str]], delay: float) -> int:
    """用 Wikidata 的 **zh-hans** 标签取中文 —— 显式简体、且是大陆用词。

    为什么比「英文维基百科的跨语言链接」更好：
      - langlinks 给的是中文维基的**条目名**，可能是台湾/香港用词
        （实测 `social media` → 社群媒体，而大陆是**社交媒体**）
      - 且条目名可能是繁体（`climate change` → 氣候變化）
    Wikidata 的 label 直接带 `zh-hans` / `zh-hant` / `zh-cn` 变体，
    实测 `Social media` → zh-hans=**社交媒体**、`Climate change` → **气候变化**。
    """
    now = int(time.time())
    n = 0
    for i in range(0, len(rows), WIKI_BATCH):
        chunk = rows[i:i + WIKI_BATCH]
        u = ("https://www.wikidata.org/w/api.php?action=wbgetentities&format=json"
             "&sites=enwiki&props=labels|sitelinks&languages=zh-hans|zh-cn|zh"
             "&titles=" + urllib.parse.quote("|".join(t for _, t in chunk)))
        d = get_json(u)
        if not d:
            print(f"    [wikidata] 第 {i//WIKI_BATCH+1} 批失败，跳过")
            time.sleep(delay * 3)
            continue
        got = {}
        for eid, ent in (d.get("entities") or {}).items():
            if eid == "-1" or not isinstance(ent, dict):
                continue
            labels = ent.get("labels") or {}
            title = ((ent.get("sitelinks") or {}).get("enwiki") or {}).get("title")
            if not title:
                continue
            # 优先 zh-hans，其次 zh-cn，最后 zh
            for lang in ("zh-hans", "zh-cn", "zh"):
                if lang in labels:
                    got[title.lower()] = labels[lang]["value"]
                    break
        for wid, text in chunk:
            zh = got.get(text.lower())
            if zh and len(zh) <= 40:
                conn.execute(
                    "UPDATE words SET meaning=?, meaning_source='wikidata', updated_at=? WHERE id=?",
                    (zh, now, wid))
                n += 1
        conn.commit()
        print(f"    [wikidata] {min(i+WIKI_BATCH, len(rows))}/{len(rows)}，已补 {n}")
        time.sleep(delay)
    return n


# ---------------------------------------------------------------- 来源 3：英文维基词典

def _wikitext_defs(wikitext: str) -> list[str]:
    """从词条 wikitext 里抽英文定义行。

    维基词典的结构是 `===Noun===` 之类的词性小节，下面 `# 释义`。
    这里取顶层 `#` 行（排除 `##` 子义项与 `#:` 例句），去掉模板与链接标记。
    """
    out = []
    for line in wikitext.split("\n"):
        if not re.match(r"^#[^#*:]", line):
            continue
        t = re.sub(r"\{\{[^{}]*\}\}", "", line)      # 去模板
        t = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", t)   # 去 wiki 链接
        t = re.sub(r"<[^>]+>", "", t)
        t = t.lstrip("# ").strip()
        if len(t) > 3:
            out.append(t)
    return out


def phase_wiktionary(conn, rows: list[tuple[int, str]], delay: float) -> int:
    """英文维基词典的定义（英文），**批量取 wikitext**。

    ⚠️ 不要用逐条的 REST `page/definition` 端点：1000 条短语就是 1000 次请求，
    维基媒体会按 IP 限流（实测全部 429，跑半小时只补了 13 条）。
    改用 `prop=revisions` 批量取 wikitext —— 一次 50 条，1000 条只需 20 次请求。
    """
    now = int(time.time())
    n = 0
    for i in range(0, len(rows), WIKI_BATCH):
        chunk = rows[i:i + WIKI_BATCH]
        u = ("https://en.wiktionary.org/w/api.php?action=query&format=json"
             "&prop=revisions&rvprop=content&rvslots=main&redirects=1&titles="
             + urllib.parse.quote("|".join(t for _, t in chunk)))
        d = get_json(u)
        if not d:
            print(f"    [wiktionary] 第 {i//WIKI_BATCH+1} 批失败（限流？），跳过")
            time.sleep(delay * 3)
            continue
        norm = {}
        for key in ("normalized", "redirects"):
            for m in d.get("query", {}).get(key, []):
                norm[(m.get("from") or "").lower()] = m.get("to") or ""
        by_title = {}
        for pid, pg in d.get("query", {}).get("pages", {}).items():
            if pid != "-1":
                by_title[(pg.get("title") or "").lower()] = pg

        for wid, text in chunk:
            key = text.lower()
            pg = by_title.get(key) or by_title.get(norm.get(key, "").lower())
            if not pg:
                continue
            try:
                wt = pg["revisions"][0]["slots"]["main"]["*"]
            except (KeyError, IndexError, TypeError):
                continue
            defs = _wikitext_defs(wt)
            if defs:
                conn.execute(
                    "UPDATE words SET meaning=?, meaning_source='wiktionary', updated_at=? WHERE id=?",
                    ("；".join(defs[:2])[:300], now, wid))
                n += 1
        conn.commit()
        print(f"    [wiktionary] {min(i+WIKI_BATCH, len(rows))}/{len(rows)}，已补 {n}")
        time.sleep(delay)
    return n


# ---------------------------------------------------------------- 主流程

def report(conn) -> None:
    total = conn.execute("SELECT COUNT(*) FROM words WHERE type='phrase'").fetchone()[0]
    print(f"\n=== 短语释义进度（共 {total} 条）===")
    rows = conn.execute(
        """SELECT COALESCE(meaning_source,'(无来源)') s, COUNT(*) c
           FROM words WHERE type='phrase' GROUP BY s ORDER BY c DESC"""
    ).fetchall()
    have = 0
    for s, c in rows:
        print(f"  {s:<12} {c}")
        if s != "(无来源)":
            have += c
    print(f"  有来源 {have}/{total} = {have/total*100:.1f}%")
    print("  样例:")
    for m, s, t in conn.execute(
        """SELECT meaning, meaning_source, text FROM words
           WHERE type='phrase' AND meaning IS NOT NULL AND meaning != ''
           ORDER BY frequency DESC LIMIT 8"""):
        print(f"    {t:<20} [{s}] {m[:44]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条（试跑）")
    ap.add_argument("--delay", type=float, default=0.8, help="维基请求间隔秒数")
    ap.add_argument("--stats", action="store_true", help="只看进度")
    ap.add_argument("--skip", default="", help="跳过的阶段，逗号分隔：ecdict,wikidata,wikipedia,wiktionary")
    ap.add_argument("--redo", default="", help="清掉某个来源的释义后重取（如 --redo wikipedia）")
    args = ap.parse_args()

    from app.config import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    if args.stats:
        report(conn)
        return 0

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    fixed = apply_manual_fixes(conn)
    if fixed:
        print(f"已应用 {fixed} 条人工复核修正")

    if args.redo:
        n = conn.execute(
            "UPDATE words SET meaning=NULL, meaning_source=NULL "
            "WHERE type='phrase' AND meaning_source=?", (args.redo,)).rowcount
        conn.commit()
        print(f"已清除 {n} 条来源为 {args.redo} 的释义，准备重取")
    # 只处理还没有释义的短语
    todo = [(r["id"], r["text"]) for r in conn.execute(
        """SELECT id, text FROM words
           WHERE type='phrase' AND (meaning IS NULL OR meaning = '')
           ORDER BY frequency DESC""")]
    if args.limit:
        todo = todo[:args.limit]
    print(f"待补释义的短语: {len(todo)} 条")

    if "ecdict" not in skip:
        print("\n[1/4] ECDICT（离线，含变体匹配）…")
        n = phase_ecdict(conn, todo)
        print(f"      补上 {n} 条")
        done = {r[0] for r in conn.execute(
            "SELECT id FROM words WHERE type='phrase' AND meaning IS NOT NULL AND meaning != ''")}
        todo = [t for t in todo if t[0] not in done]

    if "wikidata" not in skip and todo:
        print(f"\n[2/4] Wikidata zh-hans 标签（还剩 {len(todo)} 条）…")
        n = phase_wikidata(conn, todo, args.delay)
        print(f"      补上 {n} 条")
        done = {r[0] for r in conn.execute(
            "SELECT id FROM words WHERE type='phrase' AND meaning IS NOT NULL AND meaning != ''")}
        todo = [t for t in todo if t[0] not in done]

    if "wikipedia" not in skip and todo:
        print(f"\n[3/4] 维基百科跨语言链接（还剩 {len(todo)} 条）…")
        n = phase_wikipedia(conn, todo, args.delay)
        print(f"      补上 {n} 条")
        done = {r[0] for r in conn.execute(
            "SELECT id FROM words WHERE type='phrase' AND meaning IS NOT NULL AND meaning != ''")}
        todo = [t for t in todo if t[0] not in done]

    if "wiktionary" not in skip and todo:
        print(f"\n[4/4] 英文维基词典（还剩 {len(todo)} 条）…")
        n = phase_wiktionary(conn, todo, args.delay)
        print(f"      补上 {n} 条")

    report(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
