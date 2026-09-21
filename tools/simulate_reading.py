#!/usr/bin/env python3
"""模拟**不同类型的读者**，验证算法对所有人都成立。

## 为什么需要它

此前的每一条设计都隐含一个假设：**读者会查词、会加生词本、会做测试**。
但只要用户不这么做，整套机制就空转 —— 而这恰恰是大多数人的真实状态。

所以算法必须对**最省力的行为**也成立：读完就走，什么都不做。

本工具模拟三种典型读者，在**独立的临时数据库**里跑若干天，检查：

1. 词汇量是否真的在增长（而不是原地打转）
2. **读完删文章**是否影响进度
3. 纯阅读者与积极学习者的差距有多大

## 三种读者

| 读者 | 行为 |
|---|---|
| **极简读者** | 只读。不查词、不加生词本、不做测试。**且读完就删文章** |
| **普通读者** | 偶尔查词（10% 的词），不测试，不删文章 |
| **积极读者** | 常查词（30%），每 3 篇做一次测试，保留文章 |

用法：
    python3 tools/simulate_reading.py --days 30
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# 读者画像 —— 刻意把「极简」设成默认的最坏情况
PROFILES = {
    "极简读者（只读 + 读完删文章）": {"lookup_p": 0.0, "test_every": 0, "delete": True},
    "普通读者（偶尔查词）":          {"lookup_p": 0.10, "test_every": 0, "delete": False},
    "积极读者（常查词 + 做测试）":    {"lookup_p": 0.30, "test_every": 3, "delete": False},
}


def run_profile(name: str, cfg: dict, days: int, articles_per_day: int,
                base_vocab: int, seed: int) -> dict:
    """在独立临时库里完整跑一遍某个读者画像。"""
    tmp = tempfile.mkdtemp(prefix="readloops-sim-")
    os.environ["READLOOPS_DATA_DIR"] = tmp
    # 清掉可能残留的模块级缓存
    for mod in [m for m in list(sys.modules) if m.startswith("app.")]:
        del sys.modules[mod]

    sys.path.insert(0, str(ROOT))
    from app.database import get_db, init_db
    init_db()

    from app.services import encounter, known_set, mastery, selection

    rng = random.Random(seed)

    # 造一份最小词库：按词频排名铺开，让「已知集」有意义
    with get_db() as db:
        db.executemany(
            """INSERT OR IGNORE INTO words
               (id, lemma, text, meaning, level, frq, created_at, updated_at)
               VALUES (?,?,?,'释义','CET4',?,0,0)""",
            [(i, f"w{i:05d}", f"w{i:05d}", i) for i in range(1, base_vocab * 2 + 1)],
        )
        db.execute(
            "INSERT INTO placement_runs (vocab_estimate, is_lower_bound, false_alarm, "
            "answered, source, created_at) VALUES (?,0,0,0,'placement',0)", (base_vocab,))

    history = []
    article_id = 0
    for day in range(1, days + 1):
        for _ in range(articles_per_day):
            article_id += 1
            # ⚠️ 必须调用**真实的选词逻辑**。
            # 早先这里自己随机挑词，于是完全绕过了 selection 的分层与配额 ——
            # 模拟结果反映的是模拟器自己的行为，不是 App 的行为（教训：模拟器
            # 一定要调用被测代码，不能复刻一份「看起来一样」的逻辑）。
            exclude = encounter._recent_targets() if hasattr(encounter, "_recent_targets") else set()
            picked = selection.select_targets(word_count=8, exclude=exclude)
            targets = picked["words"]

            with get_db() as db:
                db.execute(
                    "INSERT INTO articles (id,title,content,source,word_count,"
                    "target_words,new_word_count,created_at) "
                    "VALUES (?,?,?,'ai',300,?,?,?)",
                    (article_id, f"Article {article_id}", " ".join(targets),
                     __import__("json").dumps(targets), len(targets), day))
            encounter.record_article(article_id, " ".join(targets), targets)

            # 查词行为
            if cfg["lookup_p"] > 0:
                for t in targets:
                    if rng.random() < cfg["lookup_p"]:
                        with get_db() as db:
                            db.execute(
                                "INSERT INTO word_encounters "
                                "(word_id, article_id, context, action, created_at) "
                                "SELECT id, ?, '', 'lookup', ? FROM words WHERE text=?",
                                (article_id, day, t))
                            db.execute(
                                "UPDATE words SET lookup_count = COALESCE(lookup_count,0)+1 "
                                "WHERE text=?", (t,))

            # 读完删文章（关键场景）
            if cfg["delete"]:
                with get_db() as db:
                    db.execute("DELETE FROM word_encounters WHERE article_id=?", (article_id,))
                    db.execute("DELETE FROM articles WHERE id=?", (article_id,))

            # 测试行为
            if cfg["test_every"] and article_id % cfg["test_every"] == 0 and targets:
                t = rng.choice(targets)
                with get_db() as db:
                    db.execute(
                        "UPDATE words SET correct_count = COALESCE(correct_count,0)+1 "
                        "WHERE text=?", (t,))

            mastery.refresh()

        vocab, src = known_set.vocab_size()
        levels = mastery.stats()["by_level"]
        history.append({"day": day, "vocab": vocab,
                        "seen": levels.get("seen", 0),
                        "recognized": levels.get("recognized", 0),
                        "recalled": levels.get("recalled", 0)})

    first, last = history[0], history[-1]
    return {"name": name, "history": history,
            "vocab_start": base_vocab, "vocab_end": last["vocab"],
            "recalled": last["recalled"], "recognized": last["recognized"],
            "seen": last["seen"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--per-day", type=int, default=3, help="每天读几篇")
    ap.add_argument("--base-vocab", type=int, default=2200)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    print(f"模拟 {args.days} 天 × 每天 {args.per_day} 篇 · 初始词汇量 {args.base_vocab}\n")
    print(f"{'读者画像':<30}{'起':>6}{'止':>7}{'增长':>7}"
          f"{'seen':>7}{'认得':>7}{'想起':>7}")
    print("-" * 76)
    results = []
    for i, (name, cfg) in enumerate(PROFILES.items()):
        r = run_profile(name, cfg, args.days, args.per_day, args.base_vocab,
                        args.seed + i)
        results.append(r)
        print(f"{name:<30}{r['vocab_start']:>6}{r['vocab_end']:>7}"
              f"{r['vocab_end']-r['vocab_start']:>+7}"
              f"{r['seen']:>7}{r['recognized']:>7}{r['recalled']:>7}")

    print("-" * 76)
    print("\n逐日词汇量曲线（每天结束时的估计值）：")
    for r in results:
        pts = [h["vocab"] for h in r["history"]]
        step = max(1, len(pts) // 10)
        print(f"  {r['name'][:22]:<24} {' → '.join(str(v) for v in pts[::step])}")

    print("\n判定：")
    for r in results:
        grew = r["vocab_end"] > r["vocab_start"]
        moved = (r["recognized"] + r["recalled"]) > 0
        ok = grew and moved
        print(f"  {r['name'][:24]:<26} {'✅ 有增长、有升级' if ok else '❌ 原地打转'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
