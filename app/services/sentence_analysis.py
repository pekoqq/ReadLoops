"""长难句拆解 —— 阅读器的「读不懂这一句」辅助。

## 为什么需要

阅读器现在对**生词**有即时释义（划词查词），但对**句子**没有任何帮助。
而四级的核心难点恰恰在长难句：p90 句长 32 词、多重嵌套从句，
是阅读理解的主要失分点。

## 为什么不用抓来的真题解析

`语料库/真题精读/` 里有源站做好的句子精讲，但那是**针对真题原文**的。
用户读的是**我们生成的文章**（内容各不相同），所以必须在任意文本上**实时分析**。

## 做法：依存句法拆解，不用 AI

`spaCy` 的依存树直接给出句子结构。拆解结果对读者是**可验证**的
（它能自己对照原文），而且：

- **即时**：不调 AI，无延迟、无费用
- **确定**：同一句话永远得到同样的拆解
- **可解释**：每个从句都标出类型与它在句中的位置

对一个正在练阅读的人来说，「这句的主干是 X，Y 是从句，由 which 引导」
比一段 AI 生成的泛泛讲解有用得多 —— 前者可以对着原文验证。
"""
from __future__ import annotations

from typing import Any

# 从句类型 → 中文标签 + 说明
CLAUSE_LABEL = {
    "relcl": ("定语从句", "修饰前面的名词"),
    "advcl": ("状语从句", "说明时间/原因/条件/让步等"),
    "ccomp": ("宾语从句", "作动词的宾语"),
    "xcomp": ("补语从句", "补充说明主语或宾语"),
    "csubj": ("主语从句", "整个从句作主语"),
    "acl": ("非谓语从句", "分词短语作定语"),
    "conj": ("并列分句", "与前面的成分并列"),
}

# 引导词 → 它引出的从句类型（spaCy 偶尔标不出 dep，用引导词兜底）
CONNECTIVES = {
    "which": "relcl", "that": "relcl", "who": "relcl", "whom": "relcl", "whose": "relcl",
    "because": "advcl", "although": "advcl", "though": "advcl", "while": "advcl",
    "whereas": "advcl", "unless": "advcl", "since": "advcl", "if": "advcl",
    "when": "advcl", "before": "advcl", "after": "advcl", "until": "advcl",
    "what": "ccomp", "whether": "ccomp",
}

# 判定为「长难句」的门槛（与 article_quality 保持一致）
LONG_WORDS = 28
NESTED_CLAUSES = 2


def _nlp():
    from app.services.article_quality import _nlp as _load
    return _load()


def analyze_sentence(sentence: str) -> dict[str, Any]:
    """拆解一个句子：主干 + 各从句（类型、文本、引导词、嵌套深度）。"""
    nlp = _nlp()
    words = len(sentence.split())
    out: dict[str, Any] = {
        "text": sentence,
        "words": words,
        "clauses": [],
        "main": "",
        "difficulty": "normal",
    }
    if nlp is None:
        out["difficulty"] = "long" if words >= LONG_WORDS else "normal"
        return out

    doc = nlp(sentence)
    # 主干：去掉所有从句后的核心（近似为 ROOT 及其直接论元）
    root = next((t for t in doc if t.dep_ == "ROOT"), None)
    if root is not None:
        out["main"] = root.text

    seen: set[tuple[int, int]] = set()
    for t in doc:
        # 只认 spaCy 的依存标注，不再用引导词兜底 ——
        # 兜底会和 dep 结果重复（同一从句被加两次）。
        if t.dep_ not in ("relcl", "advcl", "ccomp", "xcomp", "csubj", "acl"):
            continue
        subtree = sorted(t.subtree, key=lambda x: x.i)
        lo, hi = subtree[0].i, subtree[-1].i
        if (lo, hi) in seen:
            continue
        seen.add((lo, hi))

        # 引导词：从子树里找真正的连接词，而不是取 head 本身
        connective = ""
        for x in t.subtree:
            xl = x.text.lower()
            if x.dep_ == "mark" and xl in CONNECTIVES:
                connective = xl
                break
            if t.dep_ == "relcl" and x.dep_ in ("nsubj", "nsubjpass", "dobj", "pobj") \
                    and xl in CONNECTIVES:
                connective = xl
                break

        text = doc[lo:hi + 1].text
        label, why = CLAUSE_LABEL.get(t.dep_, (t.dep_, ""))
        inner = sum(1 for x in t.subtree
                    if x is not t and x.dep_ in ("relcl", "advcl", "ccomp", "xcomp", "csubj"))
        out["clauses"].append({
            "kind": t.dep_,
            "label": label,
            "why": why,
            "text": text,
            "connective": connective,
            "start": lo,
            "end": hi,
            "nested": inner,
        })

    # 难度：长 或 从句多 或 有嵌套
    nested = max((c["nested"] for c in out["clauses"]), default=0)
    if words >= LONG_WORDS and (len(out["clauses"]) >= NESTED_CLAUSES or nested >= 1):
        out["difficulty"] = "hard"
    elif words >= LONG_WORDS or len(out["clauses"]) >= 3:
        out["difficulty"] = "long"
    return out


def analyze_text(content: str, only_difficult: bool = True) -> list[dict[str, Any]]:
    """拆解整段文本。默认只返回值得讲解的句子（长 / 难）。"""
    from app.services.article_quality import sentences as split_sentences

    out = []
    for s in split_sentences(content):
        if len(s.split()) < 12:          # 太短的句子不需要讲解
            continue
        rec = analyze_sentence(s)
        if only_difficult and rec["difficulty"] == "normal":
            continue
        out.append(rec)
    return out
