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

## 短语与单词同等对待

罗肖尼视频里讲得很明确：常用 3000 词其实不止 3000 个词条，很多是**多词组合语**。
所以短语走**完全相同**的分层逻辑，只是候选池换成 `type='phrase'`。

注意差异：
- 短语**一条释义都没有**（短语库只存了文本与真题频次）。这不影响选词 ——
  方法本来就是「在语境中习得」，释义是复习时的锦上添花，不是前提。
- 短语没有考试标签，所以第 3 层（目标考试新词）对它们不适用；
  改用**真题语料频次**（`frequency` 列）排序：越常出现的短语越值得先学。
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

# 功能词（封闭类）：冠词、介词、连词、代词、助动词、限定词。
# ⚠️ 必须排除。实测：文章里出现过的功能词会被标成 m_level='seen'，
# 然后被第 2 层「重遇」选中当目标生词 —— `the` 的 rank 是 1，还排在最前面。
# 结果文章在「教」用户 the / and / have / for / you，质量自然上不去。
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "while", "because", "so", "that",
    "this", "these", "those", "it", "its", "they", "them", "their", "theirs",
    "we", "our", "ours", "you", "your", "yours", "he", "him", "his", "she", "her",
    "hers", "i", "me", "my", "mine", "who", "whom", "whose", "which", "what",
    "when", "where", "why", "how", "all", "any", "both", "each", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same",
    "than", "then", "too", "very", "can", "will", "just", "should", "now", "would",
    "could", "may", "might", "must", "shall", "do", "does", "did", "done", "doing",
    "have", "has", "had", "having", "be", "am", "is", "are", "was", "were", "been",
    "being", "of", "to", "in", "on", "at", "for", "with", "from", "by", "as",
    "into", "onto", "over", "under", "above", "below", "between", "among", "about",
    "after", "before", "during", "through", "against", "without", "within", "upon",
    "there", "here", "up", "down", "out", "off", "again", "further", "once",
    "every", "either", "neither", "another", "much", "many", "one", "two", "three",
    "also", "however", "therefore", "thus", "yet", "still", "even", "ever", "never",
    "always", "often", "sometimes", "usually", "get", "got", "make", "made", "go",
    "goes", "went", "gone", "come", "came", "take", "took", "taken", "give", "gave",
}


def _stopword_clause(col: str = "text") -> str:
    """生成排除功能词的 SQL 片段（用 text 与 lemma 双查，兼容变形）。"""
    words = ",".join(f"'{w}'" for w in sorted(STOPWORDS))
    return f"AND lower({col}) NOT IN ({words})"


# 候选池过滤：单词与短语的唯一区别就是「含不含空格 / type 值」。
# 短语**不要求有释义**（它们本来就只有文本与频次），单词仍然要求有释义。
_KIND_WHERE = {
    "word": ("type = 'word' AND meaning IS NOT NULL AND meaning != '' "
             "AND text GLOB '[a-z]*' AND text NOT LIKE '% %' "
             "AND length(text) BETWEEN 3 AND 18 "
             + _stopword_clause("text")),
    # 短语额外要求「有词典来源的释义」。查不到来源说明它不是词汇单位
    # （只是碰巧连在一起的高频词，如 `young people` / `new study`），
    # 就不该硬造释义去教 —— 见 tools/enrich_phrases.py 的说明。
    "phrase": ("type = 'phrase' AND text LIKE '% %' "
               "AND length(text) BETWEEN 5 AND 40 "
               "AND meaning IS NOT NULL AND meaning != ''"),
}
# 排序用的「有效频次」——**两者语义相反，必须归一化**：
#   单词 frq/bnc 是 COCA 排名：**越小越高频**
#   短语 frequency 是真题语料出现**次数**：**越大越高频**
# 用同一个 ORDER BY rank ASC，短语就会优先选到最低频的垃圾（实测挑出 `s and s`）。
# 所以短语取负号，让「越小越高频」成为统一口径。
_KIND_RANK = {
    "word": "COALESCE(NULLIF(frq, 0), bnc, 0)",
    "phrase": "-frequency",
}
# 配合上面的负号，正值判据也要分开写
_KIND_RANK_POSITIVE = {
    "word": "COALESCE(NULLIF(frq, 0), bnc, 0) > 0",
    "phrase": "frequency >= 4",      # 低于 4 次的多是高频词序列而非固定搭配
}
_BASE_WHERE = ""    # 兼容旧引用，实际用 _kind_where()
_RANK = ""

# 第 1 层：到期复习（按到期时间，最早的优先）
def _tier_sql(kind: str, vocab_floor: int = 0) -> tuple[str, str, str]:
    """按候选池类型生成三层查询。

    `vocab_floor`：已知集对应的词频上限。
    ⚠️ 必须把它**下推到 SQL**。否则 tier3/4 会先取回「按词频排序的前 N 个未知词」，
    而它们全落在已知集里（rank 1~2200），取回后又被 Python 侧过滤掉 ——
    结果**一个目标词都选不出来**（实测生成的文章 target_words 为空）。
    """
    where = _KIND_WHERE[kind]
    if vocab_floor > 0 and kind == "word":
        where += f" AND COALESCE(NULLIF(frq, 0), bnc, 0) > {int(vocab_floor)}"
    rank = _KIND_RANK[kind]
    rank_ok = _KIND_RANK_POSITIVE[kind]
    tier1 = f"""
        SELECT id, text, {rank} AS rank, COALESCE(srs_due,0) AS due
        FROM words
        WHERE {where} AND status NOT IN ('known')
          AND m_level IN ('seen','recognized')
          AND srs_due IS NOT NULL AND srs_due <= ?
        ORDER BY srs_due ASC LIMIT ?
    """
    tier2 = f"""
        SELECT w.id, w.text, {rank.replace('frequency', 'w.frequency').replace('frq', 'w.frq').replace('bnc', 'w.bnc')} AS rank,
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
        WHERE {where.replace('type =', 'w.type =').replace('meaning IS', 'w.meaning IS')
                      .replace('meaning !=', 'w.meaning !=').replace('text GLOB', 'w.text GLOB')
                      .replace('text NOT LIKE', 'w.text NOT LIKE').replace('text LIKE', 'w.text LIKE')
                      .replace('length(text)', 'length(w.text)')}
          AND w.status NOT IN ('known')
          AND w.m_level = 'seen' AND ev.clean > 0 AND ev.clean < 4
        ORDER BY ev.clean DESC, rank ASC LIMIT ?
    """
    tier34 = f"""
        SELECT id, text, {rank} AS rank
        FROM words
        WHERE {where} AND status NOT IN ('known')
          AND m_level = 'unknown' AND {rank_ok}
        ORDER BY rank ASC LIMIT ?
    """
    return tier1, tier2, tier34


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
                     exclude: set[str] | None = None, kind: str = "word") -> list[str]:
    """挑出这一篇要埋的目标词条。

    kind='word' 挑单词，kind='phrase' 挑短语 —— 分层逻辑完全相同。
    分层见模块 docstring。
    """
    exam = exam or target_exam()
    exclude = {w.lower() for w in (exclude or set())}
    now = int(time.time())
    # 每层多取一些，抵消 exclude（最近 30 篇用过的词）造成的损耗
    ask = max(target_count * 3, 30)
    # 已知集：认识 top-2200 的人不该再被"教" the / every。
    # 这比功能词表更根本 —— 已知集就是「你认识什么」的定义。
    from app.services import known_set as _ks
    _vocab, _ = _ks.vocab_size()
    _known = _ks.build(_vocab) if kind == "word" else set()
    q1, q2, q34 = _tier_sql(kind, _vocab)

    with get_db() as db:
        t1 = db.execute(q1, (now, ask)).fetchall()
        t2 = db.execute(q2, (ask,)).fetchall()
        t34 = db.execute(q34, (ask * 4,)).fetchall()
        # 目标考试词表：短语没有考试标签，这一层对它们恒为空
        exam_ids = set()
        if kind == "word":
            exam_ids = {r[0] for r in db.execute(
                "SELECT id FROM words WHERE m_level = 'unknown' AND type = 'word' "
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
            # 已知集里的词不该当目标 —— 你认识 top-2200，就不该再被"教" the / every。
            # 这比功能词表更根本：已知集就是「你认识什么」的定义。
            if _known and key in _known:
                continue
            seen.add(key)
            picks.append(w)
            if len(picks) >= target_count:
                return picks
    return picks


# 一篇文章里短语的目标条数。少于单词 —— 短语更长、更难自然融入，
# 而且方法里「重遇 12 次」对短语同样成立，一次埋太多反而都记不住。
TARGET_PHRASES = 3


def select_targets(word_count: int = 10, phrase_count: int = TARGET_PHRASES,
                   exam: str | None = None,
                   exclude: set[str] | None = None) -> dict:
    """一次挑出这一篇要埋的**单词 + 短语**。

    两者刻意分开挑：它们不共享候选池，也不该互相挤占额度 ——
    视频里「短语和单词同等重要」的意思正是要各自都有位置，
    而不是让短语去抢单词的名额。
    """
    exclude = {w.lower() for w in (exclude or set())}
    words = select_new_words(word_count, exam=exam, exclude=exclude, kind="word")
    phrases = select_new_words(phrase_count, exam=exam,
                               exclude=exclude | {w.lower() for w in words},
                               kind="phrase")
    return {"words": words, "phrases": phrases}


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
