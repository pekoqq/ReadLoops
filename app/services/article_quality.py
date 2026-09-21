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
LONG_SENT_WORDS = 30                        # 真题 p90=32
LONG_SENT_RATIO = (0.06, 0.16)              # 目标 8–12%，留一点容差
MAX_CONSECUTIVE_NEW = 2                     # 连续 3 个生词会直接卡住理解

SPECIFICITY_MIN = 5             # 数字 + 专名 + 引述 的总数下限
NUMBERS_MIN, NAMES_MIN = 3, 2

LEXICAL_DIVERSITY_MIN = 60.0    # MTLD；低于此值说明用词重复、有 AI 味
REPEAT_NGRAM_MAX = 0.05         # 重复 4-gram 占比上限

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

def cliche_hits(content: str) -> list[str]:
    low = content.lower()
    return [c for c in CLICHES if c in low]


# ---------------------------------------------------------------- 覆盖率

def coverage_report(content: str, known: set[str]) -> dict[str, Any]:
    """覆盖率 + 越界词 + 最长连续生词。

    `known` 是**已知词集合**（lemma 形式小写）。用 project 自己的 lexicon 口径
    还原词形，保证与统计页面同源，不出现两套标准。
    """
    from app.services import lexicon

    toks = lexicon.tokenize(content)
    if not toks:
        return {"coverage": 0.0, "out_of_level": [], "max_consecutive": 0, "unknown_count": 0}

    flags, unknown_counter = [], Counter()
    for t in toks:
        lemma = lexicon.normalize(t)
        hit = bool(lemma) and (lemma in known or t.lower() in known)
        flags.append(hit)
        if not hit and lemma:
            unknown_counter[lemma] += 1

    cov = sum(flags) / len(flags)

    # 最长连续生词（连续 3 个会直接卡住理解）
    longest = cur = 0
    for hit in flags:
        cur = 0 if hit else cur + 1
        longest = max(longest, cur)

    # 越界词按「词频无关的难度」排序：出现次数多 + 词形长的优先替换
    out = sorted(unknown_counter, key=lambda w: (-unknown_counter[w], -len(w)))[:25]
    return {
        "coverage": round(cov, 4),
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
        "target_words_missing": missing,
    }
    if known is not None:
        report.update(coverage_report(content, known))
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
