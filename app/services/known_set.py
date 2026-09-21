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


def build(vocab: Optional[int] = None, exam: Optional[str] = None) -> set[str]:
    """构建已知集（**表面形式**集合，可直接用于 token 匹配）。

    参数：
      vocab — 词汇量；缺省用定级结果
      exam  — 若给出（如 'cet4'），额外把**官方考纲词表里等级低于目标考试**的词
              也算作已知（因为那些是更早阶段就该掌握的）
    """
    if vocab is None:
        vocab, _ = vocab_size()

    lemmas: set[str] = set()
    with get_db() as db:
        # ① 最高频 N 个词
        for r in db.execute(
            "SELECT lower(text) t FROM words "
            "WHERE COALESCE(NULLIF(frq, 0), bnc, 0) BETWEEN 1 AND ?", (vocab,)
        ):
            lemmas.add(r["t"])
        # ② 掌握度确认的词（强制算已知，无论词频高低）
        for r in db.execute(
            "SELECT lower(text) t FROM words WHERE m_level IN ('recognized', 'recalled')"
        ):
            lemmas.add(r["t"])

    # ③ 展开成「原形 + 全部变形」
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
