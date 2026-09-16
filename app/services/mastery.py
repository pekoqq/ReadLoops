"""掌握度模型：把多路证据合成「识别」与「回忆」两个维度。

## 为什么必须是两个维度，不是一个概率

用户提出的直觉是对的：**没查过 + 在文章里见过几次，本身就是掌握的证据** ——
系统一直在采集这个信号（`word_encounters`），却从来没用过。

但文献给出了一个关键约束。Pellicer-Sánchez (2015, *SSLA* 38(1):97–130) 用眼动 +
多套测验测了语境偶发学习，**8 次遇见之后**：

| 测什么 | 正确率 |
|---|---|
| 认出词形 | **86%** |
| 认出词义 | **75%** |
| 回忆词义 | **55%** |

同一批词、同样的遇见次数，识别与回忆差了 20–30 个百分点。van den Broek et al.
(2022, *Cognitive Science* 46(4):e13135) 讲得更直接：

> 即使在语境中理解了词，也不一定能回忆出词形或词义。

所以把「没查过 + 见了几次」直接折算成一个「已掌握」的布尔值，会把**能认出来**
和**能想起来**混为一谈 —— 而这两件事在教学上的下一步动作是相反的：
前者应该继续输入，后者才该去做提取练习。

## 模型

两个 log-odds 累加器，各自独立：

    recognize_lo = 先验 + 0.42×干净遇见 − 0.9×查词次数
    recall_lo    = 先验×0.5 + 0.30×干净遇见 + 1.1×答对 − 1.3×答错

**权重怎么来的**（这是可复核的，不是拍脑袋）：

- **每次干净遇见 +0.42（识别层）**：把 Pellicer-Sánchez 的 75% 意义识别作为标定点。
  一个生词的先验取 P=0.1（log-odds −2.20），8 次遇见后要达到 P=0.75（log-odds +1.10），
  需要 +3.30，分到 8 次即每次 **+0.41**。词形识别（86%）对应 +0.50，取 0.42 偏保守。
- **每次干净遇见 +0.30（回忆层）**：同一研究的回忆率 55%（log-odds +0.20），
  从 −2.20 到 +0.20 需 +2.40，分 8 次即 **+0.30**。回忆增长明显慢于识别，与文献一致。
- **查词 −0.9**：查词是明确的「我不知道」自陈，比一次被动遇见强得多。
  取 0.9 ≈ 2 次干净遇见的抵消量（查一次意味着这个词对你至少难两次）。
- **答对 +1.1 / 答错 −1.3（只作用于回忆层）**：测试是**提取**，不是识别。
  van den Broek (2022) 与 Roediger & Karpicke 的 testing effect 都指出提取的作用
  远强于再认，故取值高于单次遇见；答错的惩罚再重一点（错误的记忆痕迹需要更多证据覆盖）。
- **先验**来自定级测试的分档掌握率（该词所在词频档位的掌握率取 logit）。
  已明确声明认识/不认识的词（`mastered`）用更强的先验。

## 等级

    recalled    回忆层 P ≥ 0.80   → 已经能想起来，不该再当生词埋进文章
    recognized  识别层 P ≥ 0.70   → 认得出来但想不起来 → 该做提取练习，而不是继续输入
    seen        至少遇见过一次
    unknown     没有证据
"""
from __future__ import annotations

import math
import time
from typing import Optional

from app.database import get_db
from app.services import placement as pl

# ---- 证据权重（全部有出处，见模块 docstring）----
W_ENCOUNTER_RECOGNIZE = 0.42
W_ENCOUNTER_RECALL = 0.30
W_LOOKUP = -0.9
W_TEST_CORRECT = 1.1
W_TEST_WRONG = -1.3

# 先验：定级测试里被明确声明过的词，权重加倍（显式自评比档位推断强）
W_DECLARED = 2.0

# 等级阈值
TH_RECALLED = 0.80
TH_RECOGNIZED = 0.70

# log-odds 收敛上下限，避免极端证据把值推到无穷
LO_CLAMP = 12.0

# 先验封顶。⚠️ 必须封 —— 默认假设给最高频两档 rate=1.0，logit 高达 9.2，
# 于是「这个词属于我认识的档位」直接压死一切证据：一个用户明确查过的词
# （nearly，lookup_count=1）也会被判成识别 100%。封在 ±2.0（P ∈ [0.12, 0.88]），
# 先验只表达「大概认不认识」，具体判定交给证据。
PRIOR_CLAMP = 2.0

# 等级不只看概率，还要看**证据类型** —— 见下面 level_of()
MIN_EXPOSURES_RECOGNIZED = 4   # Pellicer-Sánchez (2015)：3–4 次遇见后阅读速度显著变快
MIN_CORRECT_RECALLED = 2       # 四选一有 25% 蒙对率，一次答对不足以判定回忆

LEVELS = ("unknown", "seen", "recognized", "recalled")


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1 / (1 + z)
    z = math.exp(x)
    return z / (1 + z)


def _clamp(x: float) -> float:
    return max(-LO_CLAMP, min(LO_CLAMP, x))


def _clamp_prior(x: float) -> float:
    return max(-PRIOR_CLAMP, min(PRIOR_CLAMP, x))


def level_of(*, clean: int, lookups: int, declared: int,
             correct: int, wrong: int, p_recall: float) -> str:
    """按**证据类型**判等级，而不是按概率。

    依据（Pellicer-Sánchez 2015）：
      3–4 次遇见 → 阅读速度显著变快（词形识别开始形成）
      8 次遇见   → 词形识别 86% / 意义识别 75% / **回忆仅 55%**
    也就是说反复遇见能建立「认得出来」，但撑不起「想得起来」——
    后者需要真实的提取证据（答题）。所以：

      recalled   需要答题证据，纯遇见再多也不算
      recognized 需要 ≥4 次干净遇见，或用户在定级测试里声明认识
      seen       有过任何接触
    """
    if correct >= MIN_CORRECT_RECALLED or (correct >= 1 and p_recall >= 0.75):
        return "recalled"
    if declared == 1 or clean >= MIN_EXPOSURES_RECOGNIZED:
        return "recognized"
    if clean > 0 or lookups > 0:
        return "seen"
    return "unknown"


# ---------------------------------------------------------------- 证据采集

_EVIDENCE_SQL = """
SELECT w.id,
       w.lookup_count,
       w.correct_count,
       w.wrong_count,
       w.mastered,
       w.frq, w.bnc,
       COALESCE(ev.exposures, 0) AS exposures,
       COALESCE(ev.looked, 0)    AS looked
FROM words w
LEFT JOIN (
    -- 「机会」= 该词被埋进某篇文章（action='seen'）。
    -- 若同 (word, article) 还有 lookup 记录，说明这次机会里用户查了它。
    SELECT we.word_id,
           COUNT(DISTINCT we.article_id) AS exposures,
           COUNT(DISTINCT lu.article_id) AS looked
    FROM word_encounters we
    LEFT JOIN word_encounters lu
           ON lu.word_id = we.word_id
          AND lu.article_id = we.article_id
          AND lu.action = 'lookup'
    WHERE we.action = 'seen' AND we.article_id IS NOT NULL
    GROUP BY we.word_id
) ev ON ev.word_id = w.id
WHERE ev.word_id IS NOT NULL
   OR w.lookup_count > 0
   OR w.correct_count > 0
   OR w.wrong_count > 0
   OR w.mastered != 0
"""


def _band_rate_for(frq: int, bands: list[dict]) -> float:
    """该词所在词频档位的掌握率；没有词频时给一个保守的默认。"""
    rank = frq or 0
    for b in bands:
        if rank and b["band_lo"] <= rank <= b["band_hi"]:
            return b["rate"]
    # 没有词频（生僻词/专名）：默认按 0.05，即基本不认识
    return 0.05


def compute_one(row, bands: list[dict]) -> dict:
    """算一个词的掌握度。抽出来是为了可单测。"""
    frq = row["frq"] or row["bnc"] or 0
    prior_p = _band_rate_for(frq, bands)
    prior_lo = _logit(prior_p)

    # 定级测试里被明确声明过的词：用强先验覆盖档位推断
    if row["mastered"] == 1:
        prior_lo = W_DECLARED
    elif row["mastered"] == 2:
        prior_lo = -W_DECLARED
    else:
        prior_lo = _clamp_prior(prior_lo)

    exposures = row["exposures"] or 0
    looked = row["looked"] or 0
    # 「干净遇见」= 遇见了但没查。这才是用户说的那个信号。
    clean = max(0, exposures - looked)
    # lookup_count 里可能包含没有 seen 记录的查词，只补差额，避免重复计数
    lookups = max(0, (row["lookup_count"] or 0) - looked)

    # 查词总数 = 带 seen 记录的（looked）+ 没有 seen 记录的零散查词（lookups）
    total_lookups = lookups + looked
    recognize = _clamp(prior_lo
                       + W_ENCOUNTER_RECOGNIZE * clean
                       + W_LOOKUP * total_lookups)

    recall = _clamp(prior_lo * 0.5
                    + W_ENCOUNTER_RECALL * clean
                    + W_TEST_CORRECT * (row["correct_count"] or 0)
                    + W_TEST_WRONG * (row["wrong_count"] or 0))

    p_rec = _sigmoid(recognize)
    p_rcl = _sigmoid(recall)

    level = level_of(clean=clean, lookups=total_lookups,
                     declared=row["mastered"] or 0,
                     correct=row["correct_count"] or 0,
                     wrong=row["wrong_count"] or 0,
                     p_recall=p_rcl)

    return {
        "recognize": round(recognize, 3),
        "recall": round(recall, 3),
        "p_recognize": round(p_rec, 4),
        "p_recall": round(p_rcl, 4),
        "level": level,
        "clean_exposures": clean,
        "lookups": lookups + looked,
    }


def refresh(word_ids: Optional[list[int]] = None) -> dict:
    """重算掌握度并写回 words 表。

    只处理**有证据的词**（遇见过 / 查过 / 测过 / 定级声明过）——
    剩下 18 万词没有任何证据，等级天然是 unknown，不需要逐个算。
    """
    bands = pl.latest_bands()
    now = int(time.time())

    with get_db() as db:
        sql = _EVIDENCE_SQL
        params: tuple = ()
        if word_ids:
            ph = ",".join("?" * len(word_ids))
            sql = (sql.replace("WHERE ev.word_id IS NOT NULL",
                               f"WHERE w.id IN ({ph}) AND (ev.word_id IS NOT NULL")
                   + ")")
            params = tuple(word_ids)
        rows = db.execute(sql, params).fetchall()

        updates = []
        counts = {lv: 0 for lv in LEVELS}
        for r in rows:
            m = compute_one(r, bands)
            counts[m["level"]] += 1
            updates.append((m["recognize"], m["recall"], m["level"], now, r["id"]))

        if updates:
            db.executemany(
                "UPDATE words SET m_recognize=?, m_recall=?, m_level=?, m_updated=? WHERE id=?",
                updates,
            )

        # ⚠️ 证据会**消失**，此时等级必须跟着回退。
        # 典型场景：删除文章会连带删除它产生的遇见记录（v2.5.2 的修复）——
        # 如果那是某个词唯一的证据，它的 m_level 就该回到 unknown。
        # 而上面的查询只挑「有证据的词」，失去全部证据的词根本进不来，
        # 于是永久卡在旧等级上（实测 abandon 就是被这么卡住的）。
        ph = ",".join("?" * len(updates)) if updates else None
        stale_sql = ("UPDATE words SET m_recognize=0, m_recall=0, m_level='unknown', m_updated=? "
                     "WHERE m_level != 'unknown'")
        params: tuple = (now,)
        if ph:
            stale_sql += f" AND id NOT IN ({ph})"
            params = (now, *[u[4] for u in updates])
        cleared = db.execute(stale_sql, params).rowcount
    return {"processed": len(updates), "cleared": max(0, cleared), "by_level": counts}


def refresh_all() -> dict:
    """全量重算（导入词库、定级测试后调用）。"""
    return refresh()


# ---------------------------------------------------------------- 查询

def explain(word_id: int) -> Optional[dict]:
    """给单个词展示「为什么判成这样」—— 判定必须可解释，否则用户没法信任它。"""
    bands = pl.latest_bands()
    with get_db() as db:
        row = db.execute(_EVIDENCE_SQL.replace("WHERE ev.word_id IS NOT NULL",
                                               "WHERE w.id = ? AND (ev.word_id IS NOT NULL")
                          + " OR w.id = ?)",
                         (word_id, word_id)).fetchone()
        if not row:
            return None
        w = db.execute("SELECT text, meaning FROM words WHERE id=?", (word_id,)).fetchone()
    m = compute_one(row, bands)
    return {
        "word_id": word_id,
        "text": w["text"] if w else "",
        "meaning": (w["meaning"] or "") if w else "",
        **m,
        "level_label": {
            "unknown": "没有证据",
            "seen": "遇见过，但还没有掌握的证据",
            "recognized": "能认出来，但多半想不起来",
            "recalled": "能想起来",
        }[m["level"]],
        "evidence": {
            "干净遇见（见过但没查）": m["clean_exposures"],
            "查词次数": m["lookups"],
            "测试答对": row["correct_count"] or 0,
            "测试答错": row["wrong_count"] or 0,
            "定级声明": {0: "未测", 1: "认识", 2: "不认识"}[row["mastered"] or 0],
        },
    }


def stats() -> dict:
    """掌握度分布。单词与短语分开统计 —— 它们同等重要，所以各自都要能看到进度。"""
    with get_db() as db:
        rows = db.execute(
            """SELECT COALESCE(type,'word') AS t, m_level, COUNT(*) c
               FROM words WHERE m_level != 'unknown' GROUP BY t, m_level"""
        ).fetchall()
        tracked = db.execute(
            """SELECT COALESCE(type,'word') AS t, COUNT(*) c FROM words
               WHERE type='phrase' AND (status IN ('learning','target')
                    OR lookup_count>0 OR encounter_count>0) GROUP BY t"""
        ).fetchall()
    out = {"word": {lv: 0 for lv in LEVELS}, "phrase": {lv: 0 for lv in LEVELS}}
    for r in rows:
        out.setdefault(r["t"], {lv: 0 for lv in LEVELS})[r["m_level"]] = r["c"]
    return {
        "by_level": out["word"],
        "phrases": out.get("phrase", {}),
        "phrase_tracked": sum(r["c"] for r in tracked),
        "targets": {"recalled": TH_RECALLED, "recognized": TH_RECOGNIZED},
    }
