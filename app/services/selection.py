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
        # ⚠️ 必须给「查过的词」开例外。
        # 频率假设说「rank 240 的 study 你认识」，但你**实际查过它** ——
        # 查词记录是更硬的证据。不给例外的话，被查过的常见词
        # 会同时被频率下推和已知集剔除双重排除，永远进不了重遇。
        where += (f" AND (COALESCE(NULLIF(frq, 0), bnc, 0) > {int(vocab_floor)}"
                  f" OR COALESCE(lookup_count, 0) > 0)")
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
    # ---- 第 2 层：重遇 ----
    # ⚠️ 这一层原来的判据是 `clean > 0 AND clean < 4`（clean = 见过但**没查过**的次数），
    # 结果**逻辑是反的**：一个词你查得越多、越记不住，clean 越低，越会被排除出重遇。
    # 实测 `study`（lookup_count=1、遇见 0 次）永远进不了这一层。
    #
    # 新判据：查词次数是**最重要的正信号** —— 你反复查的词正是最该在不同语境再遇见的。
    # 依据：Pellicer-Sánchez (2015) / graded readers 研究都指向「≥12 次不同语境遇见」
    # 才可能习得；而查词行为直接暴露了哪些词还没习得。
    tier2 = f"""
        SELECT w.id, w.text,
               {rank.replace('frequency', 'w.frequency').replace('frq', 'w.frq').replace('bnc', 'w.bnc')} AS rank,
               COALESCE(w.lookup_count, 0) AS lookups,
               COALESCE(ev.seen_n, 0) AS seen_n,
               COALESCE(ev.clean, 0) AS clean,
               COALESCE(ev.last_ts, 0) AS last_ts,
               (
                   3.0 * MIN(COALESCE(w.lookup_count, 0), 5)
                 + 2.0 * (12 - MIN(COALESCE(ev.seen_n, 0), 12))
                 + 1.0 * MIN((strftime('%s','now') - COALESCE(ev.last_ts, 0)) / 864000.0, 3.0)
                 - 2.0 * MIN(COALESCE(ev.clean, 0), 6)
               ) AS score
        FROM words w
        LEFT JOIN (
            SELECT word_id,
                   COUNT(DISTINCT article_id) AS seen_n,
                   SUM(CASE WHEN action = 'seen' THEN 1 ELSE 0 END) AS seen_rows,
                   COUNT(DISTINCT CASE WHEN action = 'seen' THEN article_id END)
                     - COUNT(DISTINCT CASE WHEN action = 'lookup' THEN article_id END) AS clean,
                   MAX(created_at) AS last_ts
            FROM word_encounters
            WHERE article_id IS NOT NULL
            GROUP BY word_id
        ) ev ON ev.word_id = w.id
        WHERE {where.replace('type =', 'w.type =').replace('meaning IS', 'w.meaning IS')
                      .replace('meaning !=', 'w.meaning !=').replace('text GLOB', 'w.text GLOB')
                      .replace('text NOT LIKE', 'w.text NOT LIKE').replace('text LIKE', 'w.text LIKE')
                      .replace('length(text)', 'length(w.text)')}
          AND w.status NOT IN ('known')
          -- 见过面或认得出来，但**还没掌握**（recalled 会被下面的 m_level 条件排除）
          AND w.m_level IN ('seen', 'recognized')
          -- 有证据才值得重遇：查过 或 见过
          AND (COALESCE(w.lookup_count, 0) > 0 OR COALESCE(ev.seen_n, 0) > 0)
        ORDER BY score DESC, rank ASC LIMIT ?
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
    _known = _ks.build(_vocab, for_selection=True) if kind == "word" else set()
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


# **回锅配额**：一篇的目标词里，多大比例留给「需要再遇见」的旧词。
#
# ## 为什么必须有这个配额
#
# 原设计完全依赖分层：到期复习 → 重遇缺口 → 新词。看起来很合理，但实际会退化 ——
# 早期没有到期词、也没有「见过 4 次以内」的词，于是**每一层都空**，
# 全部额度被「新词」层吃掉。结果：每篇都在埋全新的词，从不等旧词回锅。
#
# 模拟三种读者各读 30 天 × 每天 3 篇（共 720 个目标词名额）：
#   撒在 2,200 词的候选池里 → 平均每个词只被遇见 **0.33 次**
#   而 recognized 需要 ≥4 次、recalled 需要 ≥12 次
#   → **没有任何词能升级，词汇量 30 天纹丝不动**
#
# 这正是「一直在同一水平线上打转」的根因。方法本身要求「在不同语境中重遇 ≥12 次」，
# 而重遇**必须有配额保障**，不能指望分层碰巧选中旧词。
REENTRY_QUOTA = 0.75


def select_targets(word_count: int = 10, phrase_count: int = TARGET_PHRASES,
                   exam: str | None = None,
                   exclude: set[str] | None = None) -> dict:
    """一次挑出这一篇要埋的**单词 + 短语**。

    单词按 `REENTRY_QUOTA` 分成两部分：
      - **回锅**：已有接触、但遇见次数还不够的词 —— 优先「离下一级门槛最远的」
      - **新词**：完全没接触过的词

    两者刻意分开挑：它们不共享候选池，也不该互相挤占额度 ——
    视频里「短语和单词同等重要」的意思正是要各自都有位置，
    而不是让短语去抢单词的名额。
    """
    exclude = {w.lower() for w in (exclude or set())}
    n_reentry = int(round(word_count * REENTRY_QUOTA))
    n_new = max(0, word_count - n_reentry)

    # ① 回锅：只从「有接触、未掌握」的池子里选
    reentry = _select_reentry(n_reentry, exam=exam, exclude=exclude)
    # ② 新词：从没见过面的池子里选
    fresh = select_new_words(n_new, exam=exam,
                             exclude=exclude | {w.lower() for w in reentry},
                             kind="word")
    # 回锅不够就用新词补足（新用户前几篇没有旧词可回锅）
    if len(reentry) < n_reentry:
        extra = select_new_words(n_reentry - len(reentry) + n_new, exam=exam,
                                 exclude=exclude | {w.lower() for w in reentry + fresh},
                                 kind="word")
        fresh = fresh + extra
    words = (reentry + fresh)[:word_count]

    phrases = select_new_words(phrase_count, exam=exam,
                               exclude=exclude | {w.lower() for w in words},
                               kind="phrase")
    return {"words": words, "phrases": phrases,
            "reentry": len(reentry), "fresh": max(0, len(words) - len(reentry))}


def _select_reentry(target_count: int, exam: str | None = None,
                    exclude: set[str] | None = None) -> list[str]:
    """挑「需要再遇见」的词：已有接触、还没掌握。

    ## 排序：优先**最接近突破**的词（这里踩过一个反直觉的坑）

    我最初按「遇见次数少的优先」排，想法是「离门槛还远的最需要补」。
    实际效果完全相反：遇见被撒得越来越薄，**没有任何词能累积到门槛**。
    实测只读 30 天的读者，所有词的遇见次数**卡在 3 就再也上不去** ——
    因为每次都有大量「只见过 1 次」的新词排在前面。

    正确目标是**集中火力把词推过门槛**：3 次的词只差 1 次就能成为 recognized，
    而 1 次的词还差 3 次。所以按**遇见次数从多到少**排，
    先把一批词推到 recognized，再从 recognized 推到 recalled，然后它们退出池子。

    这也符合方法本身：习得一个词靠的是**在有限时间内足够密集的重遇**，
    而不是把注意力摊到无限多的词上。
    """
    if target_count <= 0:
        return []
    exclude = {w.lower() for w in (exclude or set())}

    # ⚠️ 这一层**刻意不套用「已知集排除」**。
    #
    # 已知集是「读者能读懂什么」的定义，用于覆盖率校验；但回锅层的目标是
    # 「把词推到下一级」，而正是**升到 recognized 的词才会被加进已知集** ——
    # 一旦套用排除，词刚有点起色就退出重遇池，**永远到不了 12 次、升不到 recalled**。
    # 实测：只读型读者的增长曲线两天后就平掉，正是这个原因。
    #
    # 这一层的准入条件本身已经足够收紧：
    #   m_level ∈ (seen, recognized)  有证据
    #   encounter_count ∈ (0, 12)     还需要更多遇见
    #   status != known、非功能词、有释义、长度合理
    # 池子里不会有「用户早就认识的词」——那些没有遇见记录。

    # 与其它层同样的准入条件，外加「必须有接触记录」。
    # ⚠️ 功能词过滤不能漏：否则回锅层会把 the / and / every 当成「需要再遇见」的词
    # （它们确实在文章里高频出现，m_level 也都是 seen）。
    q = (
        "SELECT lower(text) t, COALESCE(encounter_count,0) n, "
        "       COALESCE(lookup_count,0) lk "
        "FROM words "
        "WHERE type = 'word' AND m_level IN ('seen','recognized') "
        "  AND status NOT IN ('known') "
        "  AND COALESCE(encounter_count,0) > 0 "
        # 已经到顶（≥12 次）的交给掌握度判 recalled，不再占重遇名额
        "  AND COALESCE(encounter_count,0) < 12 "
        "  AND meaning IS NOT NULL AND meaning != '' "
        "  AND length(text) BETWEEN 3 AND 18 "
        "  AND text GLOB '[a-z]*' AND text NOT LIKE '% %' "
        f"  {_stopword_clause('text')} "
        # 排序：先推最接近突破的（遇见次数多），同档内查词多的优先（说明真没记住）
        "ORDER BY COALESCE(encounter_count,0) DESC, "
        "         COALESCE(lookup_count,0) DESC, "
        "         COALESCE(NULLIF(frq,0), bnc, 0) ASC LIMIT ?"
    )
    with get_db() as db:
        rows = db.execute(q, (target_count * 6,)).fetchall()

    out: list[str] = []
    for r in rows:
        t = r["t"]
        if t in exclude:
            continue
        out.append(t)
        if len(out) >= target_count:
            break
    return out


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
