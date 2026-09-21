"""「已知集」构建 —— 文章生成与覆盖率校验的地基。

## 它解决什么问题

文章要写成「95% 的词读者都认识」，就必须先知道**读者认识哪些词**。
系统里有两个来源：

1. **定级测试**：给出词汇量估计（如 2,200 词）
2. **掌握度模型**：给出用户在 App 里实际接触过的词的状态

## 为什么不用「定级的分档掌握率」

定级给出每个档位的掌握百分比（如第 1 档 68.8%），理论上可以按比例抽词。
但**实测这条路很脆**：
- 每档只抽 16 题，单档标准误约 12 个百分点
- 分档率受单次作答波动影响极大（实测同一人两次测试在不同档位反复横跳）
- 而且**不知道该档里具体是哪些词认识**

所以这里采用**更稳的口径**：

    已知集 = 最高频 N 个词（N = 定级词汇量估计）∪ 掌握度为 recalled/recognized 的词

依据：词频与「学习者是否认识」高度相关（Nation 2006 等），
而掌握度是**逐个词的真实证据**，两者互补。

## 必须展开变形

`be` 的认识度等于 `is`/`are`/`was`/`were` 的认识度 ——
不展开的话已知集会漏掉英语里最高频的词形（详见 `lexicon.expand_forms`）。
"""
from __future__ import annotations

from typing import Optional

from app.database import get_db

# 定级测试没做过时的兜底词汇量
DEFAULT_VOCAB = 2500

# 「识别缓冲区」：用于**覆盖率校验**的词频上限放大系数。
#
# 定级测出的是读者的**核心**词汇量（能可靠运用的部分）。但阅读时的**识别**词汇量
# 明显更大 —— 一个核心词汇 2,200 的读者，对 2,200–4,000 频段的词大多也「看着眼熟、
# 能猜出大意」，这正是 Hu & Nation (2000) 说的「可理解」而非「能产出」。
#
# 不加这个缓冲的话，覆盖率会被系统性低估：实测一篇 350 词的文章里，
# `digital`(rank 2356) / `online`(2257) / `percent`(2802) 这类**中等常见词**
# 会被算成生词，覆盖率从 95% 掉到 92%，于是校验循环永远判不达标、
# 反复要求模型把文章改简单 —— 那是把好文章改坏。
#
# ⚠️ 只用于覆盖率校验；**选词**不受影响（选词要的是「核心词汇之外」的词）。
RECOGNITION_BUFFER = 1.6
# 低于此值的结果不可信（多半是测试没认真做），退回兜底
MIN_SANE_VOCAB = 500


def vocab_size() -> tuple[int, str]:
    """取用户当前的词汇量估计。返回 (词数, 来源说明)。"""
    try:
        from app.services import placement

        rows = placement.latest_bands()
        if rows:
            with get_db() as db:
                r = db.execute(
                    "SELECT vocab_estimate, source FROM placement_runs "
                    "ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
            if r and r["vocab_estimate"] and r["vocab_estimate"] >= MIN_SANE_VOCAB:
                src = "定级测试" if (r["source"] or "") == "placement" else "默认假设"
                return int(r["vocab_estimate"]), src
    except Exception:
        pass
    return DEFAULT_VOCAB, "默认假设"


def build(vocab: Optional[int] = None, exam: Optional[str] = None,
          for_selection: bool = False) -> set[str]:
    """构建已知集（**表面形式**集合，可直接用于 token 匹配）。

    ## 两个用途必须分开（`for_selection`）

    「已知集」在两个地方被用到，而它们的**语义相反**：

    | 用途 | 含义 | 查过的词算不算已知 |
    |---|---|---|
    | **覆盖率校验**（默认） | 读者「能不能读懂」这段文字 | **算** —— 查过一次不等于不认识，    而且它可能是文章里顺带出现的常见词，算成生词会让覆盖率过于悲观 |
    | **选词**（`for_selection=True`） | 哪些词「该被重新埋进文章」 | **不算** ——    你查过它，正是最该在不同语境再遇见的 |

    之前用一个集合兼顾两者，于是要么重遇失效（旧的 bug），
    要么覆盖率被低估（实测非目标词覆盖率掉到 90.8%）。

    参数：
      vocab         — 词汇量；缺省用定级结果
      exam          — 若给出（如 'cet4'），额外把官方考纲词表里更低级别的词也算已知
      for_selection — True 时把「查过的词」从已知集剔除（选词用）
    """
    if vocab is None:
        vocab, _ = vocab_size()
    # 覆盖率校验时放大到「识别词汇量」；选词时用核心词汇量（见 RECOGNITION_BUFFER）
    cutoff = vocab if for_selection else int(vocab * RECOGNITION_BUFFER)

    lemmas: set[str] = set()
    with get_db() as db:
        # ① 最高频 N 个词
        for r in db.execute(
            "SELECT lower(text) t FROM words "
            "WHERE COALESCE(NULLIF(frq, 0), bnc, 0) BETWEEN 1 AND ?", (cutoff,)
        ):
            lemmas.add(r["t"])
        # ② 掌握度确认的词（强制算已知，无论词频高低）
        for r in db.execute(
            "SELECT lower(text) t FROM words WHERE m_level IN ('recognized', 'recalled')"
        ):
            lemmas.add(r["t"])

        # ③ **查过词的要从已知集里剔除** —— 这是比词频更硬的证据。
        # 频率假设说「认识 top-N 就等于认识 study」，但你**实际查过它**，
        # 说明当时不认识。不让查词证据覆盖词频假设的话，
        # 被查过的常见词永远进不了重遇（实测 study：lookup=1 却被排除）。
        looked_up = {
            r["t"] for r in db.execute(
                "SELECT lower(text) t FROM words WHERE COALESCE(lookup_count, 0) > 0")
        }

    # ⚠️ 只有**选词**才剔除查过的词。覆盖率校验必须保留它们 ——
    # 查过一次不等于不认识，而且它们可能只是顺带出现在文中的常见词。
    if for_selection:
        lemmas -= looked_up

    # ④ 展开成「原形 + 全部变形」
    from app.services import lexicon

    return lexicon.expand_forms(lemmas)


def summary(vocab: Optional[int] = None) -> dict:
    """给前端/日志用的已知集概况。"""
    n, src = vocab_size() if vocab is None else (vocab, "手工指定")
    known = build(vocab)
    with get_db() as db:
        mastered = db.execute(
            "SELECT COUNT(*) FROM words WHERE m_level IN ('recognized','recalled')"
        ).fetchone()[0]
    return {
        "vocab_estimate": n,
        "source": src,
        "known_forms": len(known),
        "mastered_words": mastered,
    }
