#!/usr/bin/env python3
"""把 ECDICT 的考试标签与词频导入 words 表。

为什么需要：**词汇差距统计的前提是知道「哪些词属于哪场考试」**
（离四级还差多少词、离六级还差多少词），以及「这个词有多常用」
（定级测试按词频分档抽题、选词按频率加权）。这些信息 ECDICT 有，但此前
从未落库 —— `words.level` 只有 CET4(4,529) 和 common(171,247) 两档。

做法：**不重新导入词典**。释义/音标/词形早已入库且完好，这里只补 5 个字段：
  tags     空格分隔的考试标签（cet4 cet6 ky ielts toefl gre gk zk）
  frq      COCA 词频排名（0 = 不在表；越小越高频）
  bnc      BNC 词频排名
  collins  柯林斯星级 1–5
  oxford   是否牛津核心 3000 词

⚠️ 词频挂在**词元**上：`is`/`are`/`has`/`students` 这类屈折形式在 ECDICT 里
`frq` 是 0，真正的频次在 `be`/`have`/`student` 上。直接用表面形式查词频会把
英语最高频的一批词当成生僻词（我踩过这个坑，连续算错三轮）。所以这里**额外
生成一张词元映射表**，把屈折形式指回有词频的原形，供上层做词频查询。

用法：
    python3 tools/import_exam_tags.py            # 自动找/下载 ECDICT
    python3 tools/import_exam_tags.py --stats    # 只看导入结果，不重新导入
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ECDICT_URL = "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv"
DEFAULT_CSV = ROOT / "data" / "ecdict" / "ecdict.csv"

# 感兴趣的 ECDICT tag（其余如 "zk/gk" 也一起存，成本为零）
TAG_KEYS = ("zk", "gk", "cet4", "cet6", "ky", "ielts", "toefl", "gre")

csv.field_size_limit(10 ** 7)


def ecdict_path() -> Path:
    """定位 ECDICT csv；没有就从官方仓库下载（MIT 协议）。"""
    env = os.getenv("READLOOPS_ECDICT_CSV")
    if env:
        return Path(env)
    if DEFAULT_CSV.exists():
        return DEFAULT_CSV
    DEFAULT_CSV.parent.mkdir(parents=True, exist_ok=True)
    print(f"下载 ECDICT（约 63MB，MIT 协议）→ {DEFAULT_CSV}")
    req = urllib.request.Request(ECDICT_URL, headers={"User-Agent": "readloops/2.3.2"})
    with urllib.request.urlopen(req, timeout=180) as r, open(DEFAULT_CSV, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    print("  下载完成")
    return DEFAULT_CSV


def _int(value, default=0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def read_ecdict(path: Path) -> dict[str, tuple]:
    """流式读取 ECDICT，返回 {小写词: (tags, frq, bnc, collins, oxford, exchange)}。"""
    out: dict[str, tuple] = {}
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.DictReader(f):
            word = (row.get("word") or "").strip().lower()
            if not word:
                continue
            tag = (row.get("tag") or "").strip().lower()
            parts = [p for p in tag.split() if p in TAG_KEYS]
            out[word] = (
                " ".join(parts),
                _int(row.get("frq")),
                _int(row.get("bnc")),
                _int(row.get("collins"), None),
                1 if (row.get("oxford") or "").strip() else 0,
                (row.get("exchange") or "").strip(),
            )
    return out


def build_lemma_rank(ecdict: dict[str, tuple]) -> dict[str, int]:
    """屈折形式 → 原形词频排名。

    两个方向都要接：
      1. `exchange` 里的 `0:lemma`（students → student）
      2. 反向索引（was 没有 0:lemma，但 be 的 exchange 里写着 p:was）
    """
    rank: dict[str, int] = {}
    for word, (_t, frq, bnc, _c, _o, _ex) in ecdict.items():
        best = frq or bnc
        if best:
            rank[word] = best

    alias: dict[str, int] = {}
    for word, (_t, frq, bnc, _c, _o, ex) in ecdict.items():
        best = frq or bnc
        if not best or not ex:
            continue
        for part in ex.split("/"):
            if ":" not in part:
                continue
            code, form = part.split(":", 1)
            form = form.strip().lower()
            if form and code != "0":
                alias.setdefault(form, best)
    # 显式 0:lemma 优先于反向索引
    for word, (_t, frq, bnc, _c, _o, ex) in ecdict.items():
        for part in ex.split("/"):
            if part.startswith("0:"):
                lem = part[2:].strip().lower()
                if lem in rank:
                    alias[word] = rank[lem]
    return alias


def import_all(conn: sqlite3.Connection, path: Path) -> dict:
    # 先确保 schema 是最新的：新增列由 COLUMN_MIGRATIONS 幂等补齐，
    # 否则直接跑本脚本会遇到 "no such column: tags"。
    from app.database import _ensure_columns
    _ensure_columns(conn)
    conn.commit()

    print(f"读取 ECDICT: {path}")
    t0 = time.time()
    ecdict = read_ecdict(path)
    print(f"  {len(ecdict):,} 条，用时 {time.time()-t0:.1f}s")

    have = {r[0].lower() for r in conn.execute("SELECT text FROM words")}
    matched = [w for w in ecdict if w in have]
    print(f"  库内 {len(have):,} 词，命中 {len(matched):,}")

    conn.execute("DROP TABLE IF EXISTS _ecdict_stage")
    conn.execute(
        """CREATE TEMP TABLE _ecdict_stage (
               word TEXT PRIMARY KEY, tags TEXT, frq INTEGER, bnc INTEGER,
               collins INTEGER, oxford INTEGER, lemma_frq INTEGER)"""
    )
    alias = build_lemma_rank(ecdict)
    print(f"  词形→原形词频映射 {len(alias):,} 条")

    stage = []
    for w in matched:
        tags, frq, bnc, collins, oxford, _ex = ecdict[w]
        lemma_frq = frq or bnc or alias.get(w, 0)
        stage.append((w, tags, frq, bnc, collins, oxford, lemma_frq))
    conn.executemany("INSERT INTO _ecdict_stage VALUES (?,?,?,?,?,?,?)", stage)

    conn.execute(
        """UPDATE words SET
               tags    = (SELECT s.tags    FROM _ecdict_stage s WHERE s.word = lower(words.text)),
               frq     = (SELECT s.frq     FROM _ecdict_stage s WHERE s.word = lower(words.text)),
               bnc     = (SELECT s.bnc     FROM _ecdict_stage s WHERE s.word = lower(words.text)),
               collins = (SELECT s.collins FROM _ecdict_stage s WHERE s.word = lower(words.text)),
               oxford  = (SELECT s.oxford  FROM _ecdict_stage s WHERE s.word = lower(words.text))
           WHERE lower(text) IN (SELECT word FROM _ecdict_stage)"""
    )
    conn.execute("DROP TABLE IF EXISTS _ecdict_stage")
    conn.commit()
    print(f"  更新完成，用时 {time.time()-t0:.1f}s")
    return {"matched": len(matched)}


def add_missing(conn: sqlite3.Connection, path: Path, max_frq: int = 30000) -> dict:
    """把 ECDICT 里**词典缺失**的高词频词补进来。

    为什么必须做：实测 ECDICT 中 COCA 前 20,000 的词里有 **2,119 个（11.9%）**
    不在库内 —— 包括 `i`、`n't`、`others`、`including`、`recent`、`internet`、
    `percent` 这种高频词。而覆盖率计算会把「无法还原的 token」算作不认识，
    于是覆盖率被系统性低估（四级语料有 11% 的 token 落在这里）。

    只补两头的交集：**高词频 + 有中文释义**。生僻且无释义的词补进来毫无用处
    （库里已有 7 万个这样的条目）。
    """
    from app.services.dict_import import level_from_tag, parse_exchange

    have = {r[0] for r in conn.execute("SELECT lower(text) FROM words")}
    now = int(time.time())
    rows = []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.DictReader(f):
            word = (row.get("word") or "").strip()
            if not word:
                continue
            low = word.lower()
            if low in have:
                continue
            frq = _int(row.get("frq"))
            bnc = _int(row.get("bnc"))
            # ⚠️ 必须 frq 或 bnc 一起看：ECDICT 里有一批常见词的 COCA 排名是 0
            # 但 BNC 有排名（例如 percent：frq=0 / bnc=2802）。只看 frq 会把它们
            # 当成生僻词漏掉，而覆盖率会把「查不到」当作「不认识」。
            rank = frq or bnc
            if not (0 < rank <= max_frq):
                continue
            meaning = (row.get("translation") or "").strip()
            if not meaning:
                continue
            tag = (row.get("tag") or "").strip().lower()
            tags = " ".join(p for p in tag.split() if p in TAG_KEYS)
            ex = parse_exchange(row.get("exchange") or "")
            rows.append((
                low, word, "word", meaning, (row.get("phonetic") or "").strip(),
                "CET4" if "cet4" in tags else level_from_tag(tag) or "common",
                tags, frq, bnc, _int(row.get("collins"), None),
                1 if (row.get("oxford") or "").strip() else 0,
                json.dumps(ex, ensure_ascii=False) if ex else None,
                now, now,
            ))

    if rows:
        conn.executemany(
            """INSERT INTO words (lemma, text, type, meaning, phonetic, level,
                                  tags, frq, bnc, collins, oxford, exchange,
                                  created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
    print(f"  补入 {len(rows):,} 个缺失的高频词（COCA ≤ {max_frq:,}，且带释义）")
    return {"added": len(rows)}


def report(conn: sqlite3.Connection) -> None:
    print("\n=== 导入结果 ===")
    total = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
    tagged = conn.execute("SELECT COUNT(*) FROM words WHERE tags IS NOT NULL AND tags != ''").fetchone()[0]
    ranked = conn.execute("SELECT COUNT(*) FROM words WHERE frq > 0").fetchone()[0]
    print(f"  总词数 {total:,}｜有考试标签 {tagged:,}｜有 COCA 词频 {ranked:,}")

    print("\n  各考试词表规模：")
    for key, name in (("zk", "中考"), ("gk", "高考"), ("cet4", "四级"), ("cet6", "六级"),
                      ("ky", "考研"), ("ielts", "雅思"), ("toefl", "托福"), ("gre", "GRE")):
        n = conn.execute(
            "SELECT COUNT(*) FROM words WHERE ' ' || tags || ' ' LIKE ?",
            (f"% {key} %",),
        ).fetchone()[0]
        print(f"    {name:<5} {n:>6}")

    print("\n  词频分档（COCA 排名区间 → 词数）：")
    for lo, hi in ((1, 1000), (1001, 2000), (2001, 3000), (3001, 5000),
                   (5001, 8000), (8001, 12000), (12001, 20000)):
        n = conn.execute("SELECT COUNT(*) FROM words WHERE frq BETWEEN ? AND ?", (lo, hi)).fetchone()[0]
        print(f"    {lo:>6}–{hi:<6} {n:>6}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true", help="只打印统计，不导入")
    ap.add_argument("--add-missing", action="store_true",
                    help="补入 ECDICT 中库内缺失的高词频词（默认只更新已有行）")
    ap.add_argument("--max-frq", type=int, default=30000,
                    help="补词时的 COCA 词频上限（默认 30000）")
    ap.add_argument("--db", default=None, help="数据库路径（默认取 app.config）")
    args = ap.parse_args()

    from app.config import DB_PATH
    db_path = Path(args.db) if args.db else DB_PATH
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    if not args.stats:
        path = ecdict_path()
        if not path.exists():
            print(f"找不到 ECDICT: {path}")
            return 1
        import_all(conn, path)
        if args.add_missing:
            add_missing(conn, path, max_frq=args.max_frq)
            # 补词后要重新更新一遍标签（新词还没打上）
            import_all(conn, path)

    report(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
