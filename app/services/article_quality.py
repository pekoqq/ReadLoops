"""文章质量评分器 —— **确定性、纯代码、可单测**。

## 为什么需要它

生成流程原来只写 prompt 要求（"用常见词"、"平均句长 19"），
**生成后不做任何检查** —— 实测生成文章覆盖率只有 68.8~76.4%，
远低于可理解输入要求的 95%，而且没有任何机制发现这件事。

本模块把「文章好不好」拆成 11 个**可测量**的维度，供生成流程做
「校验 → 定向修复 → 再校验」的闭环。所有指标都是纯计算，不调 AI。

## 文献依据

| 维度 | 依据 |
|---|---|
| 覆盖率 95% | Laufer & Ravenhorst-Kalovski (2010)：95% **配支持**时足以支撑理解 |
| 生词密度均匀 | Hu & Nation (2000)：未知词密度直接决定理解程度 |
| 长难句 8–12% | 四级真题句长 p90 = 32 词（本项目实测 273 篇） |
| 具体性 ≥5 处 | AI 文本的通病是缺乏具体信息（Pangram 的风格分析） |
| 不做 AI 检测 | Pangram：困惑度/突发性检测对非母语写作者误判严重 |

## 依赖

`spaCy` + `en_core_web_sm` 用于依存句法（语法点识别）与命名实体（具体性）。
**缺失时自动降级为正则**，精度下降但不崩 —— 用户可以不装。
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Optional

# ---------------------------------------------------------------- 目标常量
# 每一项都对应一个明确依据，不要随手改。

TARGET_COVERAGE = 0.95          # Laufer & Ravenhorst-Kalovski (2010)
COVERAGE_TOLERANCE = 0.02       # 93%~97% 都算通过

SENT_AVG_MIN, SENT_AVG_MAX = 16.0, 21.0     # 真题 p50=18
SENT_STD_MIN = 5.0                          # 句长标准差：人写作有波动，AI 均匀
LONG_SENT_WORDS = 28                        # 真题 p90=32；取 28 让模型更易稳定命中
LONG_SENT_RATIO = (0.06, 0.16)              # 目标 8–12%，留一点容差
MAX_CONSECUTIVE_NEW = 2                     # 连续 3 个生词会直接卡住理解

SPECIFICITY_MIN = 5             # 数字 + 专名 + 引述 的总数下限
NUMBERS_MIN, NAMES_MIN = 3, 2

LEXICAL_DIVERSITY_MIN = 60.0    # MTLD 下限；低于此值说明用词重复、有 AI 味
REPEAT_NGRAM_MAX = 0.05         # 重复 4-gram 占比上限

# ---------------------------------------------------------------- 去 AI 味：实测校准的真题画像
#
# 以下阈值来自**把我们的生成文章与 273 篇真实四级真题逐维对比**，
# 而非拍脑袋。对比暴露的差距（真题中位 / 我们中位）：
#
#   MTLD（词汇丰富度）      105.7 / 179.6   我们**过高 1.7 倍**
#   标点种类                  8   /   4      只有一半
#   破折号 ‰                  2.72 /  0      完全不用
#   括号 ‰                    4.27 /  0      完全不用
#   段落长度标准差           26.35 / 12.89   段落过于均匀
#   段落数                     7   /   5
#
# 与文献一致：AI 文本被证实「压缩话语与结构熵」「把复杂标点压到基线的 3.2–23.2%」
# （arXiv:2605.28826），而**词汇丰富度**是跨模型跨领域最稳健的判别特征
# （arXiv:2606.04177）。
LEXICAL_DIVERSITY_MAX = 150.0   # 上界：超过真题太多说明缺乏话题连贯、为变化而变化
PUNCT_KINDS_MIN = 6             # 至少用 6 种标点（真题中位 8）
# 每 1000 词的目标出现次数（真题中位）
DASH_PER_1K_MIN = 1.5           # 破折号 —— AI 完全不用，而真题常见
PAREN_PER_1K_MIN = 2.0          # 括号
PARA_COUNT = (6, 9)             # 段落数（真题中位 7）
PARA_LEN_STD_MIN = 18.0         # 段落长度标准差（真题中位 26）
# 最长段 / 最短段 的比值。真题中位 **8.67 倍**、p25 是 4.12；
# 我们实测只有 **1.88 倍** —— 各段长度几乎一致，是最明显的模板感来源。
# 用比值而不是标准差：它对段落数不敏感，也更容易翻译成给模型的指令。
PARA_LEN_RATIO_MIN = 4.0
PAREN_PER_ARTICLE_MIN = 1       # 每篇括号数（真题中位 2、p25 是 1）
# 话题聚焦度：最高频 5 个实词的平均出现次数。
# 真题中位 5.40、p25 是 4.40；我们实测 3.70。
# 这是「话题是否连贯」的直接度量，比 MTLD 更容易翻译成指令。
TOPIC_WORD_REPEAT_MIN = 4.0

# 目标词中「有上下文线索」的比例下限。
# 实测真实四级文章是 **100%**（200 篇中位）—— 这不是巧合：真题的选材标准之一
# 就是难点词可由上下文推出。我们生成的是 95%，差的 5% 那几个词等于没被教。
CONTEXT_CLUE_MIN = 1.0

# ---------------------------------------------------------------- 论证手法
#
# 真实四级文章的论证手法覆盖面（实测 273 篇真题正文）：
#   对比 95.2% · 数据 79.1% · 因果 68.9% · 引用 64.5% · 举例 51.6% · 反问 50.5%
#
# 对比我们的生成结果（15 篇）：对比 100% · 数据 100% · 因果 86.7% ·
# **引用 100%（过度）** · **举例 20%（不足）** · **反问 13.3%（严重不足）**。
#
# 「引用」超标是因为 prompt 要求「cite studies, experts」却没给上限；
# 「反问」不足是因为 prompt 只把「以问句开头」列为五个开篇选项之一 ——
# 而真实文章里一半都有设问句（不只是开头）。
RHETORIC_PATTERNS = {
    "对比": r"\b(?:but|however|by contrast|in contrast|unlike|whereas|"
            r"on the other hand|although|while|rather than|instead)\b",
    "引用": r"\b(?:researchers?|scientists?|according to|a (?:new )?study|"
            r"surveys?|experts?|reports?|found that|showed that)\b",
    "举例": r"\b(?:such as|for example|for instance|including|e\.g\.)\b",
    "数据": r"\b\d+(?:\.\d+)?\s*(?:%|percent|million|billion|thousand)?\b",
    "反问": r"\?",
    "因果": r"\b(?:because|since|therefore|thus|hence|as a result|"
            r"leads? to|results? in|due to)\b",
}
# 目标覆盖面（真题实测值）。用区间而非单点，避免矫枉过正。
RHETORIC_TARGETS = {
    "反问": (0.25, 1.0),      # 真题 50.5%，先要求至少 1 处设问
    "举例": (0.35, 1.0),      # 真题 51.6%
}

# 实词高频表用的停用词（计算话题聚焦度时排除）
_TOPIC_STOP = set("""a an the and or but if while because so that this these those it its they them
their we our you your he she his her of to in on at for with from by as is are was were be been have
has had not no nor there here what which who when where why how all any both each few more most other
some such than then too very can could may might must will would should about into over under between
out up down also just even still only own same very""".split())

# 陈词滥调黑名单：AI 写作最典型的模板化表达
CLICHES = [
    "in today's fast-paced world", "in today's world", "in the modern era",
    "it is important to note", "it is worth noting", "it is crucial to",
    "plays a crucial role", "plays a vital role", "plays a significant role",
    "a double-edged sword", "cannot be overstated", "in the realm of",
    "serves as a testament", "delve into", "navigate the complexities",
    "in conclusion", "as we all know", "nowadays", "with the development of society",
    "it is undeniable that", "there is no denying that", "when it comes to",
    "a growing number of", "in this day and age", "the fact of the matter is",
    "it goes without saying", "last but not least", "needless to say",
    "at the end of the day", "a wide range of", "an array of",
    "shed light on", "pave the way for", "a myriad of", "in essence",
    "it is evident that", "first and foremost",
]

# 语法点 → spaCy 依存标注的判据
GRAMMAR_POINTS = {
    "relative_clause": "定语从句",
    "adverbial_clause": "状语从句",
    "nominal_clause": "名词性从句",
    "non_finite": "非谓语动词",
    "passive": "被动语态",
    "perfect_tense": "完成时",
    "subjunctive": "虚拟语气",
}

_NLP = None
_NLP_TRIED = False


def _nlp():
    """懒加载 spaCy；不可用时返回 None（降级为正则）。"""
    global _NLP, _NLP_TRIED
    if _NLP_TRIED:
        return _NLP
    _NLP_TRIED = True
    try:
        import spacy
        _NLP = spacy.load("en_core_web_sm")
    except Exception:
        _NLP = None
    return _NLP


# ---------------------------------------------------------------- 基础切分

def sentences(content: str) -> list[str]:
    """按句切分。优先用 spaCy（更准），否则退化为正则。"""
    nlp = _nlp()
    if nlp is not None:
        return [s.text.strip() for s in nlp(content).sents if s.text.strip()]
    parts = re.split(r"(?<=[.!?])\s+", content.strip())
    return [p.strip() for p in parts if p.strip()]


def words_of(text: str) -> list[str]:
    return re.findall(r"[A-Za-z']+", text)


# ---------------------------------------------------------------- 语法检测

def grammar_counts(content: str) -> dict[str, int]:
    """统计各语法点的出现次数 —— 用依存句法，不用正则。

    ⚠️ 正则做不了这件事：我上一轮用 `which|that|who` 统计「定语从句」，
    把 `that` 当关系代词，得出 73% 的虚高结果。spaCy 的 `relcl` 才是真判据。
    """
    counts = {k: 0 for k in GRAMMAR_POINTS}
    nlp = _nlp()
    if nlp is None:
        return _fallback_grammar(content, counts)

    doc = nlp(content)
    for t in doc:
        if t.dep_ == "relcl":
            counts["relative_clause"] += 1
        elif t.dep_ == "advcl":
            counts["adverbial_clause"] += 1
        elif t.dep_ in ("ccomp", "xcomp", "csubj"):
            counts["nominal_clause"] += 1
        elif t.dep_ in ("acl", "advcl") and t.tag_ in ("VBG", "VBN"):
            counts["non_finite"] += 1
        elif t.dep_ == "auxpass":
            counts["passive"] += 1
        elif t.dep_ == "aux" and t.lemma_ in ("have", "has", "had") and t.head.tag_ == "VBN":
            counts["perfect_tense"] += 1
        # 非谓语还包括作定语/状语的 to-V 与动名词
        if t.tag_ == "VBG" and t.dep_ in ("npadvmod", "prep", "dobj", "pobj", "ROOT", "conj"):
            counts["non_finite"] += 1
    # 虚拟语气：would/could/might + have + VBN，或非真实条件里的 were
    if re.search(r"\b(would|could|might|should)\s+have\s+\w+ed\b", content, re.I):
        counts["subjunctive"] += 1
    if re.search(r"\bif\s+\w+\s+were\b", content, re.I):
        counts["subjunctive"] += 1
    # 非谓语：不定式（粗略补充，spaCy 的 xcomp 已覆盖大部分）
    counts["non_finite"] += len(re.findall(r"\bto\s+[a-z]+", content, re.I)) // 4
    return counts


def _fallback_grammar(content: str, counts: dict[str, int]) -> dict[str, int]:
    """没有 spaCy 时的降级判据（精度低，但比没有强）。"""
    counts["relative_clause"] = len(re.findall(r"\b(which|who|whom|whose)\b", content, re.I))
    counts["adverbial_clause"] = len(re.findall(
        r"\b(because|although|though|while|whereas|unless|since|if|when)\b", content, re.I))
    counts["passive"] = len(re.findall(r"\b(is|are|was|were|been|being)\s+\w+(ed|en)\b", content, re.I))
    counts["perfect_tense"] = len(re.findall(r"\b(have|has|had)\s+\w+(ed|en)\b", content, re.I))
    counts["non_finite"] = len(re.findall(r"\b(to\s+[a-z]+|\w+ing)\b", content, re.I))
    counts["nominal_clause"] = len(re.findall(r"\b(that|what|whether)\b", content, re.I))
    return counts


# ---------------------------------------------------------------- 具体性

def specificity(content: str) -> dict[str, int]:
    """具体性：数字、专名、引述。

    AI 文章的通病不是用词，是**没有具体信息** —— 通篇道理但没有事实。
    这条指标比封禁陈词滥调有效得多。
    """
    numbers = len(re.findall(r"\b\d+(?:\.\d+)?\s*(?:%|percent|million|billion|thousand)?\b",
                             content, re.I))
    quotes = len(re.findall(
        r"\b(researchers?|scientists?|studies|study|survey|report|findings?|"
        r"according to|found that|showed that|suggests? that)\b", content, re.I))
    nlp = _nlp()
    if nlp is not None:
        doc = nlp(content)
        names = len({e.text for e in doc.ents if e.label_ in
                     ("ORG", "GPE", "PERSON", "NORP", "FAC", "LOC")})
    else:
        # 降级：句首之外的大写词粗略当专名
        names = 0
        for s in sentences(content):
            for i, w in enumerate(s.split()):
                core = re.sub(r"[^A-Za-z]", "", w)
                if i > 0 and len(core) > 2 and core[0].isupper() and core[1:].islower():
                    names += 1
        names = min(names, 40) // 2
    return {"numbers": numbers, "proper_nouns": names, "quotes": quotes,
            "total": min(numbers, 10) + min(names, 6) + min(quotes, 6)}


# ---------------------------------------------------------------- 词汇丰富度

def mtld(text: str, threshold: float = 0.72) -> float:
    """MTLD（Measure of Textual Lexical Diversity）—— 对长度不敏感的词丰富度。

    做法：顺序扫描，累加 TTR；一旦 TTR 跌破阈值就算一个「因子」，
    最后用 总词数 / 因子数 作为得分。低于 ~60 说明用词重复、有 AI 味。
    """
    toks = [w.lower() for w in words_of(text)]
    if len(toks) < 20:
        return 0.0

    def count_factors(seq: list[str]) -> float:
        factors, types, start = 0.0, set(), 0
        for i, w in enumerate(seq):
            types.add(w)
            ttr = len(types) / (i - start + 1)
            if ttr <= threshold:
                factors += 1
                types, start = set(), i + 1
        # 余项按比例折算
        if start < len(seq):
            types = set(seq[start:])
            ttr = len(types) / (len(seq) - start)
            factors += (1 - ttr) / (1 - threshold) if ttr < 1 else 0
        return max(factors, 1.0)

    fwd = count_factors(toks)
    bwd = count_factors(list(reversed(toks)))
    return round((len(toks) / fwd + len(toks) / bwd) / 2, 1)


def repeat_ngram_ratio(text: str, n: int = 4) -> float:
    """重复 n-gram 占比。AI 文本偏爱反复使用同样的短语骨架。"""
    toks = [w.lower() for w in words_of(text)]
    if len(toks) < n * 2:
        return 0.0
    grams = [tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)]
    c = Counter(grams)
    repeated = sum(v - 1 for v in c.values() if v > 1)
    return round(repeated / len(grams), 4)


# ---------------------------------------------------------------- 陈词滥调

def structure_stats(content: str) -> dict[str, Any]:
    """标点与篇章结构统计 —— 去 AI 味的核心维度。

    这些维度来自实测对比（见文件顶部注释）：AI 文本的破绽不只是「用词像 AI」，
    更明显的是**结构过于规整** —— 标点种类少、段落长度均匀、不敢用破折号和括号。
    真实文章在这些地方是「不整齐」的。
    """
    words = max(1, len(words_of(content)))
    kinds = {c for c in content if c in ';,:—–-()"' + "'" + '?!'}
    def per1k(ch: str) -> float:
        return round(content.count(ch) * 1000 / words, 2)

    paras = [p for p in content.split("\n") if p.strip()]
    plens = [len(words_of(p)) for p in paras]
    para_std = 0.0
    if len(plens) > 1:
        mean = sum(plens) / len(plens)
        para_std = (sum((x - mean) ** 2 for x in plens) / len(plens)) ** 0.5

    def _ratio() -> float:
        """最长段 / 最短段。真题中位 8.67 倍，是「段落是否有起伏」的直接度量。"""
        if len(plens) < 3:
            return 0.0
        shortest = max(1, min(plens))
        return round(max(plens) / shortest, 2)

    return {
        "paren_count": content.count("("),
        "para_len_ratio": _ratio(),
        "punct_kinds": len(kinds),
        "dash_per_1k": round(per1k("—") + per1k("–"), 2),
        "paren_per_1k": per1k("("),
        "colon_per_1k": per1k(":"),
        "semicolon_per_1k": per1k(";"),
        "paras": len(paras),
        "para_len_std": round(para_std, 1),
    }


# 上下文线索的六种类型 —— 分类依据是真题精读数据的实测分布
# （2,518 条生词推断示范：定义 28%、因果 22%、对比 21%、并列 18%、构词 18%）。
# 这些线索是「不查词典也能推出词义」的前提；**没有线索的生词等于没被教**。
CLUE_PATTERNS = [
    ("定义", r"\b(?:means|refers to|is called|known as|that is|in other words|"
             r"is defined as|namely)\b|,\s+(?:a|an|the)\s+\w+\s+(?:that|which|who)"),
    ("对比", r"\b(?:but|however|unlike|whereas|in contrast|on the other hand|"
             r"although|though|instead|rather than)\b"),
    ("因果", r"\b(?:because|since|therefore|thus|hence|as a result|leads? to|"
             r"results? in|causes?|due to|owing to)\b"),
    ("并列", r"\b(?:and|or|as well as|along with|both)\b"),
    ("举例", r"\b(?:such as|for example|for instance|including|like|e\.g\.)\b"),
    ("同位", r"[—–]\s*\w|\w+\s*\([^)]{3,60}\)|,\s*\w+\s*,\s*\w+"),
]


def context_clues(content: str, target_words: Optional[list[str]] = None) -> dict[str, Any]:
    """目标词附近是否提供了可推断词义的上下文线索。

    ## 为什么这是核心维度

    产品的方法前提是「在语境中习得」—— 读者遇到生词时**能从上下文推出来**。
    如果文章把目标词埋进去却不给任何线索，读者只能去查词典，
    那就退化成了背单词，方法失效。

    所以这个指标直接度量**文章是否可教**，而不只是「是否可读」。

    检测范围：目标词所在句 + 前后各一句（线索常跨句出现）。
    """

    targets = {w.lower().strip() for w in (target_words or []) if w and w.strip()}
    if not targets:
        return {"checked": 0, "with_clue": 0, "ratio": 0.0, "clue_types": {}, "naked": []}

    sents = sentences(content)
    hit_types: Counter = Counter()
    naked: list[str] = []
    checked = 0

    for i, sent in enumerate(sents):
        low = sent.lower()
        for w in list(targets):
            if not re.search(r"\b" + re.escape(w) + r"\w{0,4}\b", low):
                continue
            checked += 1
            window = " ".join(sents[max(0, i - 1): i + 2])
            found = [name for name, pat in CLUE_PATTERNS
                     if re.search(pat, window, re.I)]
            if found:
                for f in found:
                    hit_types[f] += 1
            else:
                naked.append(w)

    uniq_checked = len({w for w in targets
                        if re.search(r"\b" + re.escape(w) + r"\w{0,4}\b",
                                     content.lower())})
    return {
        "checked": uniq_checked,
        "with_clue": uniq_checked - len(set(naked)),
        "ratio": round((uniq_checked - len(set(naked))) / uniq_checked, 3) if uniq_checked else 0.0,
        "clue_types": dict(hit_types),
        "naked": sorted(set(naked))[:10],
    }


def rhetoric_stats(content: str) -> dict[str, Any]:
    """论证手法的使用情况。

    四级文章是**说明文/议论文**，它的「修辞」主要是**怎么把道理说清楚**：
    对比、举例、引用、因果、数据、设问。真实文章的用法有稳定的配比，
    而我们生成的结果在「举例」和「反问」上明显不足、在「引用」上过度。
    """
    used = {}
    for name, pat in RHETORIC_PATTERNS.items():
        used[name] = len(re.findall(pat, content, re.I))
    return {"counts": used, "kinds": sum(1 for v in used.values() if v)}


def topic_repetition(content: str) -> dict[str, Any]:
    """话题聚焦度：最高频 5 个实词各出现多少次。

    真题靠**反复使用主题词**维持话题连贯（中位 5.40 次），
    而生成结果倾向于不断换同义词（实测 3.70 次）—— 后者读起来像同义词词典，
    而且对学习者不利：**主题词复现才带来强化**。
    """
    ws = [w.lower() for w in words_of(content)]
    if not ws:
        return {"top5": [], "avg": 0.0}
    c = Counter(w for w in ws if w not in _TOPIC_STOP and len(w) > 3)
    top5 = c.most_common(5)
    return {"top5": [w for w, _ in top5],
            "avg": round(sum(n for _, n in top5) / len(top5), 2) if top5 else 0.0}


def cliche_hits(content: str) -> list[str]:
    low = content.lower()
    return [c for c in CLICHES if c in low]


# ---------------------------------------------------------------- 覆盖率

def coverage_report(content: str, known: set[str],
                    target_words: Optional[list[str]] = None) -> dict[str, Any]:
    """覆盖率 + 越界词 + 最长连续生词。

    `known` 是**已知词集合**（lemma 形式小写）。用 project 自己的 lexicon 口径
    还原词形，保证与统计页面同源，不出现两套标准。
    """
    from app.services import lexicon

    toks = lexicon.tokenize(content)
    if not toks:
        return {"coverage": 0.0, "out_of_level": [], "max_consecutive": 0, "unknown_count": 0}

    # 目标词是**有意**埋进去的学习对象，不该算进「读不懂」。
    # i+1 说的是「95% 的词你认识，剩下 5% 正是要学的」——
    # 把目标词算作未知会双重计数：既要求埋词，又因为埋了词而判覆盖率不达标。
    targets = set()
    for w in (target_words or []):
        wl = w.lower().strip()
        if not wl:
            continue
        targets.add(wl)
        n = lexicon.normalize(wl)
        if n:
            targets.add(n)

    flags, unknown_counter, non_target = [], Counter(), []
    for t in toks:
        lemma = lexicon.normalize(t)
        hit = bool(lemma) and (lemma in known or t.lower() in known)
        is_target = t.lower() in targets or (bool(lemma) and lemma in targets)
        flags.append(hit)
        if not is_target:
            non_target.append(hit)
        if not hit and lemma and not is_target:
            unknown_counter[lemma] += 1

    cov = sum(flags) / len(flags)
    # 主指标：只看**非目标词**的覆盖率（这才是 i+1 的判据）
    cov_nt = (sum(non_target) / len(non_target)) if non_target else cov

    # 最长连续生词（连续 3 个会直接卡住理解）
    longest = cur = 0
    for hit in flags:
        cur = 0 if hit else cur + 1
        longest = max(longest, cur)

    # 越界词按「词频无关的难度」排序：出现次数多 + 词形长的优先替换
    out = sorted(unknown_counter, key=lambda w: (-unknown_counter[w], -len(w)))[:25]
    return {
        "coverage": round(cov_nt, 4),          # 主指标：非目标词覆盖率
        "coverage_all": round(cov, 4),         # 含目标词的总体覆盖率（参考）
        "target_tokens": len(flags) - len(non_target),
        "unknown_count": sum(1 for f in flags if not f),
        "total_tokens": len(flags),
        "out_of_level": out,
        "max_consecutive": longest,
    }


# ---------------------------------------------------------------- 主入口

def analyze(
    content: str,
    *,
    known: Optional[set[str]] = None,
    target_words: Optional[list[str]] = None,
    target_grammar: Optional[list[str]] = None,
) -> dict[str, Any]:
    """对一个生成结果做完整的 11 维体检。纯计算，无副作用。"""
    sents = sentences(content)
    lens = [len(words_of(s)) for s in sents] or [0]
    avg = sum(lens) / len(lens)
    var = sum((x - avg) ** 2 for x in lens) / len(lens)
    long_ratio = sum(1 for x in lens if x > LONG_SENT_WORDS) / len(lens)

    low = content.lower()
    missing = [w for w in (target_words or []) if not re.search(
        r"\b" + re.escape(w.lower()).replace(r"\ ", r"\s+") + r"\w{0,4}\b", low)]

    gram = grammar_counts(content)
    gram_missing = [g for g in (target_grammar or []) if gram.get(g, 0) < 2]

    report: dict[str, Any] = {
        "words": len(words_of(content)),
        "sentences": {
            "n": len(sents),
            "avg_len": round(avg, 1),
            "std_len": round(math.sqrt(var), 1),
            "max_len": max(lens),
            "long_ratio": round(long_ratio, 3),
        },
        "grammar": gram,
        "grammar_missing": gram_missing,
        "specificity": specificity(content),
        "style": {
            "mtld": mtld(content),
            "repeat_4gram": repeat_ngram_ratio(content),
            "cliche_hits": cliche_hits(content),
        },
        "structure": structure_stats(content),
        "topic": topic_repetition(content),
        "clues": context_clues(content, target_words),
        "rhetoric": rhetoric_stats(content),
        "target_words_missing": missing,
    }
    if known is not None:
        report.update(coverage_report(content, known, target_words))
    return report


def verdict(report: dict, *, project_coverage: float = TARGET_COVERAGE) -> tuple[bool, list[str]]:
    """判断是否通过，并给出**具体、可执行**的修复清单。

    返回的 issue 会被直接拼进修复 prompt —— 所以必须具体到「哪个词、哪个数」，
    不能只说「覆盖率不足」。
    """
    issues: list[str] = []

    if "coverage" in report:
        cov = report["coverage"]
        if abs(cov - project_coverage) > COVERAGE_TOLERANCE:
            direction = "太简单" if cov > project_coverage + COVERAGE_TOLERANCE else "太难"
            words = ", ".join(report.get("out_of_level", [])[:12])
            issues.append(
                f"覆盖率 {cov*100:.1f}%（目标 {project_coverage*100:.0f}%±2，当前{direction}）。"
                f"请把这些词换成更常见的说法：{words}")
        if report.get("max_consecutive", 0) > MAX_CONSECUTIVE_NEW:
            issues.append(
                f"有 {report['max_consecutive']} 个生词连续出现 —— 会让读者卡住。"
                f"请把它们分散到不同段落。")

    if report.get("target_words_missing"):
        issues.append(
            f"这些目标词没有出现：{', '.join(report['target_words_missing'])}。请自然地用进文中。")

    if report.get("grammar_missing"):
        from_names = [GRAMMAR_POINTS.get(g, g) for g in report["grammar_missing"]]
        issues.append(f"缺少这些语法结构：{', '.join(from_names)}。每种至少出现 2 次。")

    s = report["sentences"]
    if not (SENT_AVG_MIN <= s["avg_len"] <= SENT_AVG_MAX):
        issues.append(f"平均句长 {s['avg_len']} 词，目标 {SENT_AVG_MIN:.0f}–{SENT_AVG_MAX:.0f}。")
    if s["std_len"] < SENT_STD_MIN:
        issues.append(
            f"句子长度太均匀（标准差 {s['std_len']}）—— 人写作会有长短起伏。"
            f"请刻意插入短句和长句。")
    if not (LONG_SENT_RATIO[0] <= s["long_ratio"] <= LONG_SENT_RATIO[1]):
        issues.append(
            f"长难句占比 {s['long_ratio']*100:.0f}%（目标 8–12%）。"
            f"请把 1–2 句写成含嵌套从句的长句。")

    spec = report["specificity"]
    if spec["total"] < SPECIFICITY_MIN:
        issues.append(
            f"内容太空洞（具体信息 {spec['total']} 处，需 ≥{SPECIFICITY_MIN}）："
            f"请加入具体数字、机构/地名、以及研究引述。")

    if report["style"]["cliche_hits"]:
        issues.append(f"出现了模板化表达：{', '.join(report['style']['cliche_hits'])}。请改写。")
    if report["style"]["mtld"] and report["style"]["mtld"] < LEXICAL_DIVERSITY_MIN:
        issues.append(f"用词重复度高（MTLD {report['style']['mtld']}），请换用更多样的表达。")
    if report["style"]["repeat_4gram"] > REPEAT_NGRAM_MAX:
        issues.append(f"存在重复短语（{report['style']['repeat_4gram']*100:.1f}%），请改写。")

    # ---- 结构层面的 AI 味（实测校准，见文件顶部注释）----
    st = report.get("structure") or {}
    if st:
        mt = report["style"]["mtld"]
        if mt and mt > LEXICAL_DIVERSITY_MAX:
            issues.append(
                f"用词过于求变（MTLD {mt}，真实四级文章约 106）—— 这说明文章缺少话题焦点。"
                f"请围绕一个主题词反复展开，让核心词自然复现，而不是每句都换新词。")
        if st["punct_kinds"] < PUNCT_KINDS_MIN:
            issues.append(
                f"标点过于单一（只用了 {st['punct_kinds']} 种，真实文章约 8 种）。"
                f"请自然使用破折号、括号、分号、冒号来组织信息，而不是全靠逗号和句号。")
        elif st["dash_per_1k"] < DASH_PER_1K_MIN:
            issues.append("完全没有使用破折号 —— 真实文章用它插入解释或转折，请自然用上 1–2 处。")
        if st["paren_per_1k"] < PAREN_PER_1K_MIN:
            issues.append("没有使用括号 —— 真实文章用它补充限定信息，请自然用上 1–2 处。")
        if not (PARA_COUNT[0] <= st["paras"] <= PARA_COUNT[1]):
            issues.append(
                f"段落数 {st['paras']}，真实文章通常 {PARA_COUNT[0]}–{PARA_COUNT[1]} 段。")
        if st["para_len_std"] < PARA_LEN_STD_MIN or st["para_len_ratio"] < PARA_LEN_RATIO_MIN:
            issues.append(
                f"各段长度太接近（最长段/最短段仅 {st['para_len_ratio']} 倍，"
                f"真实文章约 8.7 倍）—— 读起来像模板。"
                f"请让段落长短明显交替：至少有一段只用两三句就把一个点说清，"
                f"另有一段展开到三四倍长度。")
        if st["paren_count"] < PAREN_PER_ARTICLE_MIN:
            issues.append(
                "全篇没有使用括号。真实四级文章每篇平均用 2 处括号来补充限定信息，"
                "请自然加入至少 1 处，例如界定一个词的范围或给出一个具体数值。")

    cl = report.get("clues") or {}
    if cl.get("checked") and cl.get("ratio", 1.0) < CONTEXT_CLUE_MIN:
        naked = cl.get("naked") or []
        issues.append(
            f"这些目标词在文中**没有任何可推断的上下文线索**：{', '.join(naked)}。"
            f"读者只能去查词典 —— 那就退化成了背单词，而不是在语境中习得。"
            f"请给每个词补上线索：用同位语、对比、因果、举例或括号补充说明，"
            f"让读者能从上下文推出它的大意。")

    rh = report.get("rhetoric") or {}
    rc = rh.get("counts") or {}
    if rc:
        if rc.get("反问", 1) < 1:
            issues.append(
                "全篇没有一处设问句。真实四级文章里**一半都有** —— "
                "设问是引出论点、引导读者思路的常用手段。"
                "请在开篇或段首加一处问句（不必多，一处即可）。")
        if rc.get("举例", 1) < 2:
            issues.append(
                "几乎没有举例。真实四级文章约一半会用具体例子支撑论点。"
                "请用 such as / for example 引入一到两个具体事例。")
        if rc.get("引用", 0) > 4:
            issues.append(
                f"引用套话过多（{rc['引用']} 处）。真实文章约 65% 会引用，"
                f"但通常集中在一两处；现在的密度读起来像堆砌。请精简。")

    tp = report.get("topic") or {}
    if tp.get("avg", 99) < TOPIC_WORD_REPEAT_MIN:
        issues.append(
            f"话题不够聚焦：最高频的 5 个实词平均只出现 {tp['avg']} 次"
            f"（真实文章约 5.4 次）。请选定 2–3 个核心主题词并各自复现 4–6 次 —— "
            f"这种重复是话题连贯的来源，不是缺陷。")

    return (not issues, issues)


def repair_instruction(issues: list[str]) -> str:
    """把修复清单拼成给模型的定向重写指令。"""
    if not issues:
        return ""
    body = "\n".join(f"{i}. {x}" for i, x in enumerate(issues, 1))
    return (
        "The passage below was rejected by an automated quality check. "
        "Rewrite it fixing EXACTLY these problems, keeping the topic, structure and length:\n\n"
        f"{body}\n\n"
        "Keep every sentence that already passes. Return the same JSON shape."
    )
