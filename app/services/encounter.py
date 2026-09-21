"""重遇：把「在语境里反复遇见」变成可计算的量。

视频方法（罗肖尼，基于 Krashen）的第二条：
**学会一个单词至少要在「变化的语境」中重遇它 ≥ 12 次。**

项目此前完全没有实现这条，而且实现方式还有个根本缺陷：

- `words.encounter_count` 只在**用户查词**时 +1 —— **看到了但没查就不算遇见**。
  可方法的要害恰恰是「反复遇见」，查词只是其中一种反应。
- 它是个纯计数器，区分不了「同一篇文章里见了 12 次」和「12 篇文章各见一次」——
  而方法强调的重点正是**语境要变化**。

所以这里的口径是：

    重遇次数(w) = COUNT(DISTINCT article_id)     ← 跨了多少篇，即「变化语境」的度量

每篇文章生成后按 (word_id, article_id) **幂等**记一条 `action='seen'`，
这样反复重看同一篇不会灌水，换一篇文章才 +1。
"""
from __future__ import annotations

import time
from typing import Optional

from app.database import get_db
from app.services import lexicon

# 方法给出的阈值：在变化语境中重遇 12 次
TARGET_ENCOUNTERS = 12

# 记录遇见时最多处理多少个词（一篇文章 300 词量级，留足余量即可）
_MAX_PER_ARTICLE = 400


def record_article(article_id: int, content: str = "",
                   target_words: Optional[list[str]] = None) -> dict:
    """文章生成后记录「遇见」。幂等：同一篇重复调用不会重复计数。

    记两类词：
    1. **本篇的目标生词** —— 它们是被刻意埋进语境的
    2. **本篇正文里出现的、用户已有的学习词** —— 这是一次真正的「在新语境中重遇」

    第 2 类常被忽略但其实才是方法的关键：光埋新词不叫重遇，
    在新文章里再次碰到旧词才叫重遇。
    """
    if not article_id:
        return {"recorded": 0}

    wanted: set[str] = set()

    for t in (target_words or []):
        n = lexicon.normalize(str(t))
        if n:
            wanted.add(n)

    if content:
        with get_db() as db:
            active = {
                r[0] for r in db.execute(
                    "SELECT lower(text) FROM words WHERE status IN ('learning','target') "
                    "OR mastered = 1"
                )
            }
        if active:
            for token in lexicon.tokenize(content):
                n = lexicon.normalize(token)
                if n and n in active:
                    wanted.add(n)
                    if len(wanted) >= _MAX_PER_ARTICLE:
                        break

    if not wanted:
        return {"recorded": 0}

    id_map = lexicon.resolve_ids(list(wanted))
    if not id_map:
        return {"recorded": 0}

    now = int(time.time())
    fresh = 0
    with get_db() as db:
        # action='seen' 上有 (word_id, article_id) 的部分唯一索引，天然幂等。
        #
        # ⚠️ 同时累加 `words.encounter_count` —— 这是**耐久计数器**，
        # 与逐篇的 word_encounters 记录并存，但用途不同：
        #
        #   word_encounters   逐篇明细 → 分析「在哪些篇目、哪些语境里遇见过」
        #   encounter_count   耐久总数 → **掌握度判定的依据**
        #
        # 为什么必须分开：删文章会（也应当）连带删掉它的 word_encounters 明细，
        # 但**「我见过这个词 5 次」是用户的学习历史，不该因为删掉内容而消失**。
        # 此前掌握度直接数 word_encounters 行数，于是用户读完删文章 = 学习证据归零，
        # 系统重新教一遍，陷入死循环。
        for wid in id_map.values():
            cur = db.execute(
                "INSERT OR IGNORE INTO word_encounters "
                "(word_id, article_id, context, action, created_at) VALUES (?,?,'','seen',?)",
                (wid, article_id, now))
            if cur.rowcount:
                # 只有**真的新增了一次遇见**才累加，保证幂等
                db.execute(
                    "UPDATE words SET encounter_count = COALESCE(encounter_count, 0) + 1 "
                    "WHERE id = ?", (wid,))
                fresh += 1
    return {"recorded": fresh, "words": len(id_map)}


# ---------------------------------------------------------------- 查询

# 分段拼接，**过滤条件必须进 WHERE**。
# 踩过的坑：把条件直接接在整条 _STATS_SQL 后面，会落到 GROUP BY 之后变成
# `GROUP BY w.id AND (w.status IN (...))` —— SQLite 把它当作分组表达式求值，
# 于是所有词被并成 2 组，列表只剩 2 行、计数还大得离谱，而且不报任何错。
_STATS_SELECT = """
    SELECT w.id, w.text, w.meaning, w.status, w.mastered,
           COUNT(DISTINCT we.article_id) AS articles,
           COUNT(*)                      AS encounters
    FROM words w
    JOIN word_encounters we ON we.word_id = w.id
"""
_STATS_WHERE = " WHERE we.article_id IS NOT NULL AND we.action IN ('seen', 'lookup')"
_STATS_GROUP = " GROUP BY w.id "
_ACTIVE_FILTER = " AND (w.status IN ('learning','target') OR w.mastered = 1)"


def reentry_stats(limit: int = 200, only_active: bool = True) -> list[dict]:
    """每个词的重遇进度，按「离达标还差多少」排序。

    only_active=True 时只看用户正在学的词（learning / target / 已掌握），
    因为「遇见了几次」对没在学的词没有意义。
    """
    sql = (_STATS_SELECT + _STATS_WHERE
           + (_ACTIVE_FILTER if only_active else "")
           + _STATS_GROUP + " ORDER BY articles DESC, encounters DESC LIMIT ?")
    with get_db() as db:
        rows = db.execute(sql, (max(1, min(limit, 1000)),)).fetchall()

    out = []
    for r in rows:
        articles = r["articles"] or 0
        out.append({
            "word_id": r["id"],
            "text": r["text"],
            "meaning": r["meaning"] or "",
            "status": r["status"],
            "mastered": r["mastered"] or 0,
            "articles": articles,
            "encounters": r["encounters"] or 0,
            "target": TARGET_ENCOUNTERS,
            "remaining": max(0, TARGET_ENCOUNTERS - articles),
            "done": articles >= TARGET_ENCOUNTERS,
            "progress": round(min(1.0, articles / TARGET_ENCOUNTERS), 4),
        })
    return out


def summary() -> dict:
    """重遇总览：有多少词已达标、还在路上的平均进度。"""
    with get_db() as db:
        row = db.execute(
            f"""SELECT COUNT(*) AS tracked,
                       SUM(CASE WHEN articles >= {TARGET_ENCOUNTERS} THEN 1 ELSE 0 END) AS done,
                       AVG(articles) AS avg_articles
                FROM (
                    SELECT COUNT(DISTINCT we.article_id) AS articles
                    FROM words w
                    JOIN word_encounters we ON we.word_id = w.id
                    WHERE we.article_id IS NOT NULL AND we.action IN ('seen','lookup')
                      AND (w.status IN ('learning','target') OR w.mastered = 1)
                    GROUP BY w.id
                )"""
        ).fetchone()
    tracked = row["tracked"] or 0
    done = row["done"] or 0
    return {
        "target": TARGET_ENCOUNTERS,
        "tracked": tracked,
        "done": done,
        "avg_articles": round(row["avg_articles"] or 0, 2),
        "message": ("还没有可统计的重遇数据" if not tracked else
                    f"{done} / {tracked} 个学习词已跨 {TARGET_ENCOUNTERS} 篇重遇达标"),
    }
