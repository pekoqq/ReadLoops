#!/usr/bin/env python3
"""把 phrases 表并入 words 表，让短语成为**一等学习单位**。

## 为什么

罗肖尼视频里明确讲：常用 3000 词其实不止 3000 个词条 —— 很多是
**多词组合语（固定搭配）**，每个都有自己的意义。短语和单词同等重要。

但项目里短语一直是二等公民：只在生成 prompt 里当装饰（每次从高频前 50 条里随机挑 2-3 条），
在图里当背景色块，**没有 FSRS、没有掌握度、不被选词算法选中、不计入任何统计**。

用户的结论是对的，做法是错的。统一之后短语自动获得：
  遇见跟踪（word_encounters）· 掌握度模型（mastery）· FSRS 调度 ·
  选词分层（selection）· 图谱节点（graph）—— 全部复用现成机制。

## 做法

`words.type` 这一列从建表起就存在、却从未使用过（18.6 万行全是 'word'）——
它就是为这种场景留的。把 phrases 的 1037 条搬进 words，`type='phrase'`。

- `frequency` 沿用 phrases 表里的**真题语料频次**（words.frequency 此前恒为 0，正好可用）
- `source` 标成 `phrase_library`，与 ecdict / legacy / user / ai 并列
- **不写 tags**：不给短语打考试标签，避免把「四级大纲词表」的口径撑大、
  让统计页的缺口数字突然变化。短语是独立的一池。
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from app.config import DB_PATH

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT text, meaning, frequency, level, created_at FROM phrases"
    ).fetchall()
    if not rows:
        print("phrases 表为空，无需迁移")
        return 0

    # 已经迁过的跳过（幂等）
    have = {
        r[0] for r in conn.execute("SELECT lower(text) FROM words WHERE type='phrase'")
    }
    now = int(time.time())
    todo = []
    for r in rows:
        t = (r["text"] or "").strip().lower()
        if not t or t in have:
            continue
        todo.append((
            t, t, "phrase", r["meaning"] or "", None,
            # level 沿用短语库的档位；短语没有 COCA 词频，frq/bnc 留 0
            r["level"] or "CET4",
            r["frequency"] or 0,
            "phrase_library",
            r["created_at"] or now, now,
        ))

    if todo:
        conn.executemany(
            """INSERT INTO words
               (lemma, text, type, meaning, phonetic, level, frequency,
                source, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            todo,
        )
        conn.commit()

    # 迁移完成后废弃旧表 —— 留着就是第二份事实源，迟早不一致。
    conn.execute("DROP TABLE IF EXISTS phrases")
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM words WHERE type='phrase'").fetchone()[0]
    words = conn.execute("SELECT COUNT(*) FROM words WHERE type='word'").fetchone()[0]
    print(f"迁入 {len(todo)} 条短语（跳过已存在的 {len(have)} 条）")
    print(f"  words  表: {words:,} 个词 + {total:,} 条短语")
    print("  短语样例:", [r[0] for r in conn.execute(
        "SELECT text FROM words WHERE type='phrase' ORDER BY frequency DESC LIMIT 6")])
    return 0


if __name__ == "__main__":
    sys.exit(main())
