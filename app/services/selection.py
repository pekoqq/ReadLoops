"""选词：把「词汇差距」接到「下一篇读什么」上。

## 为什么要重写

原来的 `_select_new_words()` 只按 FSRS 到期 + 词频挑词，不知道三件事：

1. **哪些词已经掌握了** —— 定级测试标为「已掌握」的词仍会被当生词埋进文章，
   白白吃掉 6–15 个生词额度里的一部分
2. **你的目标考试是什么** —— 想考六级，可能埋的是托福词，缺口缩不动
3. **重遇进度** —— 方法要求「在变化语境中重遇」，但选词完全不看某个词已经见过几篇

结果就是：统计页算得出「离六级还差 4,174 词」，但下一篇该读什么和这个数字毫无关系。

## 现在的分层

按**掌握度等级**分层，而不是一个平铺的 FSRS 队列：

    第 1 层  到期复习（srs_due ≤ now）且尚未 recalled
             —— 到了该复习的时候，别让它过期
    第 2 层  见过但还没建立识别（m_level='seen' 且干净遇见 < 4）
             —— 这正是「重遇缺口」：已经见过面的词再碰几次就能认出来，
                比从头教一个全新词便宜得多（Pellicer-Sánchez 2015：3–4 次就有质变）
    第 3 层  目标考试词表里的全新词，按词频从高到低
             —— 要补缺口，就先补最常用的那些（Nation：高频词的边际收益最大）
    第 4 层  兜底：其他未掌握的高频词

**明确排除 `recalled`**：已经能想起来的词不该再占生词额度。
`recognized` 也不进候选 —— 它该去做提取练习（单词测试），而不是继续被输入。
"""
from __future__ import annotations

import time

from app.database import get_db

# 目标考试（对应 words.tags 里的标签），存在 settings 表
DEFAULT_EXAM = "cet4"
EXAM_LABELS = {
    "cet4": "四级", "cet6": "六级", "ky": "考研",
    "ielts": "雅思", "toefl": "托福", "gre": "GRE",
}

# 分层查询。⚠️ 不要写成「一条 SQL 拉全表再在 Python 里分桶」——
# 库里有 18 万词，那样每次生成文章都要先把十万多行拉进内存（实测 0.19s 且随词库增长）。
# 按 m_level 分别查（该列有索引）并各自 LIMIT，只用取到够用的量。
_BASE_WHERE = """
    meaning IS NOT NULL AND meaning != ''
    AND text GLOB '[a-z]*' AND text NOT LIKE '% %'
    AND length(text) BETWEEN 3 AND 18
    AND status NOT IN ('known')
"""
_RANK = "COALESCE(NULLIF(frq, 0), bnc, 0)"

# 第 1 层：到期复习（按到期时间，最早的优先）
_TIER1_SQL = f"""
    SELECT id, text, {_RANK} AS rank, COALESCE(srs_due,0) AS due
    FROM words
    WHERE {_BASE_WHERE} AND m_level IN ('seen','recognized')
      AND srs_due IS NOT NULL AND srs_due <= ?
    ORDER BY srs_due ASC LIMIT ?
"""

# 第 2 层：重遇缺口 —— 见过但识别还没建立。
# 排序要按「干净遇见次数」降序（越接近 4 次越优先），这需要 join 遇见表。
_TIER2_SQL = f"""
    SELECT w.id, w.text, {_RANK.replace('frq', 'w.frq').replace('bnc', 'w.bnc')} AS rank,
           COALESCE(ev.clean, 0) AS clean
    FROM words w
    JOIN (
        SELECT we.word_id,
               COUNT(DISTINCT we.article_id) - COUNT(DISTINCT lu.article_id) AS clean
        FROM word_encounters we
        LEFT JOIN word_encounters lu
               ON lu.word_id = we.word_id AND lu.article_id = we.article_id
              AND lu.action = 'lookup'
        WHERE we.action = 'seen' AND we.article_id IS NOT NULL
        GROUP BY we.word_id
    ) ev ON ev.word_id = w.id
    WHERE w.meaning IS NOT NULL AND w.meaning != ''
      AND w.text GLOB '[a-z]*' AND w.text NOT LIKE '% %'
      AND length(w.text) BETWEEN 3 AND 18
      AND w.status NOT IN ('known')
      AND w.m_level = 'seen' AND ev.clean > 0 AND ev.clean < 4
    ORDER BY ev.clean DESC, rank ASC LIMIT ?
"""

# 第 3 / 4 层：全新词，按有效词频从高到低
_TIER34_SQL = f"""
    SELECT id, text, {_RANK} AS rank
    FROM words
    WHERE {_BASE_WHERE} AND m_level = 'unknown' AND rank > 0
    ORDER BY rank ASC LIMIT ?
"""

def target_exam() -> str:
    """当前目标考试。"""
    with get_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key='target_exam'").fetchone()
    val = (row["value"] if row else "") or DEFAULT_EXAM
    return val if val in EXAM_LABELS else DEFAULT_EXAM


def set_target_exam(exam: str) -> bool:
    if exam not in EXAM_LABELS:
        return False
    with get_db() as db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('target_exam', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (exam,),
        )
    return True


def _clean_exposure_map() -> dict[int, int]:
    """每个词「见过但没查」的次数（跨文章去重）。"""
    with get_db() as db:
        rows = db.execute(
            """SELECT we.word_id,
                      COUNT(DISTINCT we.article_id) AS exposures,
                      COUNT(DISTINCT lu.article_id) AS looked
               FROM word_encounters we
               LEFT JOIN word_encounters lu
                      ON lu.word_id = we.word_id
                     AND lu.article_id = we.article_id
                     AND lu.action = 'lookup'
               WHERE we.action = 'seen' AND we.article_id IS NOT NULL
               GROUP BY we.word_id"""
        ).fetchall()
    return {r["word_id"]: max(0, (r["exposures"] or 0) - (r["looked"] or 0)) for r in rows}


def select_new_words(target_count: int = 10, exam: str | None = None,
                     exclude: set[str] | None = None) -> list[str]:
    """挑出这一篇要埋的目标生词。分层见模块 docstring。"""
    exam = exam or target_exam()
    exclude = {w.lower() for w in (exclude or set())}
    now = int(time.time())
    # 每层多取一些，抵消 exclude（最近 30 篇用过的词）造成的损耗
    ask = max(target_count * 3, 30)

    with get_db() as db:
        t1 = db.execute(_TIER1_SQL, (now, ask)).fetchall()
        t2 = db.execute(_TIER2_SQL, (ask,)).fetchall()
        t34 = db.execute(_TIER34_SQL, (ask * 4,)).fetchall()
        # 目标考试词表（只在需要时查，且只查 level='unknown' 的词）
        exam_ids = {r[0] for r in db.execute(
            "SELECT id FROM words WHERE m_level = 'unknown' "
            "AND ' ' || COALESCE(tags,'') || ' ' LIKE ?", (f"% {exam} %",))}

    tier1 = [(r["due"], r["text"]) for r in t1]
    tier2 = [(-(r["clean"]), r["rank"] or 10 ** 9, r["text"]) for r in t2]
    tier3 = [(r["rank"], r["text"]) for r in t34 if r["id"] in exam_ids]
    tier4 = [(r["rank"], r["text"]) for r in t34 if r["id"] not in exam_ids]

    picks: list[str] = []
    seen: set[str] = set()
    for bucket in (tier1, tier2, tier3, tier4):
        for item in bucket:
            w = item[-1]
            key = w.lower()          # 库里有 every / Every 这类大小写重复条目，
            if key in seen or key in exclude:
                continue             # 不去重的话同一篇会埋进两个"同一个词"
            seen.add(key)
            picks.append(w)
            if len(picks) >= target_count:
                return picks
    return picks


def explain_selection(limit: int = 10) -> dict:
    """给「本篇补的是哪一层的词」提供可解释的输出（前端展示用）。"""
    exam = target_exam()
    words = select_new_words(target_count=limit, exam=exam)
    return {
        "exam": exam,
        "exam_label": EXAM_LABELS.get(exam, exam),
        "count": len(words),
        "words": words,
    }
