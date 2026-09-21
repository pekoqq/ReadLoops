#!/usr/bin/env python3
"""从真题语料**量化分析出题方向** —— 题材分布与语法点分布。

## 为什么需要这个

文章生成要「仿四级真题」，但「像」不能靠感觉。必须把真题的**题材比例**和
**句法特征**量化出来，生成时按这个分布抽样，才叫真的像。

原先 AI 的题材是 10 个写死的通用说法（"the impact of technology on daily life"），
既空泛又必然重复。本工具用聚类从 273 篇真题里**算出**真实题材。

## 方法

1. **TF-IDF + K-means 聚类**：把每篇变成一个 TF-IDF 向量，聚成 K 类
2. 每类的关键词 = 该类文档 TF-IDF 得分之和最高的词
3. 输出每类的**占比**（生成时按占比加权抽样）与关键词

用纯 Python 实现（语料只有几百篇，不需要 sklearn），保证可复现、无额外依赖。

## 输出

- `data/exam_profile/topics.json` —— 题材分布（生成时读取）
- 终端打印可读报告

用法：
    python3 tools/analyze_exam_topics.py
    python3 tools/analyze_exam_topics.py --k 10 --level CET4
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "语料库" / "真题阅读纯文本" / "exam_corpus.json"
# 同样放 app/resources：运行时要读，必须随包发布
OUT_DIR = ROOT / "app" / "resources"

STOP = set("""a an the and or but if while because so that this these those it its they them their we our
you your he she his her i me my of to in on at for with from by as is are was were be been being have
has had do does did will would can could should may might must not no nor there here what which who whom
whose when where why how all any both each few more most other some such than then too very s t just
don now also into over after before between under above out up down about only own same
one two three four five six seven eight nine ten first second third many much new old good bad
people time year years day days way ways thing things say says said make makes made get gets got
know knows knew take takes took see sees saw come comes came go goes went use uses used
find finds found give gives gave tell tells told work works worked call calls called try tries tried
ask asks asked need needs needed feel feels felt become becomes became leave leaves left put puts
mean means meant keep keeps kept let lets begin begins began seem seems seemed help helps helped
talk talks talked turn turns turned start starts started show shows showed hear hears heard
play plays played run runs ran move moves moved live lives lived believe believes believed
bring brings brought happen happens happened write writes wrote provide provides provided
sit sits sat stand stands stood lose loses lost pay pays paid meet meets met include includes included
continue continues continued set sets learn learns learned change changes changed lead leads led
understand understands understood watch watches watched follow follows followed stop stops stopped
create creates created speak speaks spoke read reads allow allows allowed add adds added
spend spends spent grow grows grew open opens opened walk walks walked win wins won
offer offers offered remember remembers remembered love loves loved consider considers considered
appear appears appeared buy buys bought wait waits waited serve serves served die dies died
send sends sent expect expects expected build builds built stay stays stayed fall falls fell
cut cuts cuts reach reaches reached kill kills killed remain remains remained""".split())


def content_words(text: str) -> list[str]:
    ws = [w.lower() for w in re.findall(r"[A-Za-z']+", text)]
    return [w for w in ws if w not in STOP and len(w) > 3]


def load_corpus(level: str | None) -> list[dict]:
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    if level:
        data = [d for d in data if level.upper() in str(d.get("level", "")).upper()]
    return data


def build_vectors(docs: list[list[str]]) -> tuple[list[list[float]], list[str]]:
    """TF-IDF 向量（L2 归一化）。"""
    n = len(docs)
    df = Counter()
    for d in docs:
        for w in set(d):
            df[w] += 1
    # 只保留有区分度的词：出现在 >=3 篇、且不在 >40% 的篇里
    vocab = sorted(w for w, c in df.items() if 3 <= c <= n * 0.4)
    idx = {w: i for i, w in enumerate(vocab)}
    vecs = []
    for d in docs:
        tf = Counter(d)
        v = [0.0] * len(vocab)
        for w, c in tf.items():
            if w in idx:
                v[idx[w]] = c * math.log(n / (1 + df[w]))
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        vecs.append([x / norm for x in v])
    return vecs, vocab


def kmeans(vecs: list[list[float]], k: int, iters: int = 40, seed: int = 42):
    """纯 Python K-means。语料小（几百篇），够用且无依赖。"""
    random.seed(seed)
    cent = [vecs[i] for i in random.sample(range(len(vecs)), min(k, len(vecs)))]
    groups: dict[int, list[int]] = {}
    for _ in range(iters):
        groups = defaultdict(list)
        for i, v in enumerate(vecs):
            best = max(range(len(cent)), key=lambda c: sum(a * b for a, b in zip(v, cent[c])))
            groups[best].append(i)
        for c in range(len(cent)):
            if not groups[c]:
                continue
            cent[c] = [sum(vecs[i][j] for i in groups[c]) / len(groups[c])
                       for j in range(len(vecs[0]))]
    return groups


def label_cluster(members: list[int], vecs, vocab, topn: int = 12) -> list[str]:
    score: dict[str, float] = defaultdict(float)
    for i in members:
        for j, x in enumerate(vecs[i]):
            if x:
                score[vocab[j]] += x
    return [w for w, _ in sorted(score.items(), key=lambda kv: -kv[1])[:topn]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=8, help="题材簇数量")
    ap.add_argument("--level", default=None, help="只看某个考试，如 CET4")
    args = ap.parse_args()

    arts = load_corpus(args.level)
    if len(arts) < args.k * 3:
        print(f"语料太少（{len(arts)} 篇），无法聚成 {args.k} 类")
        return 1

    docs = [content_words(a.get("text", "")) for a in arts]
    vecs, vocab = build_vectors(docs)
    groups = kmeans(vecs, args.k)

    print(f"=== 题材聚类（{len(arts)} 篇{('，' + args.level) if args.level else ''}，K={args.k}）===\n")
    topics = []
    for c in sorted(groups, key=lambda x: -len(groups[x])):
        members = groups[c]
        kws = label_cluster(members, vecs, vocab)
        share = len(members) / len(arts)
        topics.append({
            "id": f"topic_{c}",
            "share": round(share, 4),
            "size": len(members),
            "keywords": kws,
        })
        print(f"  {share*100:5.1f}%  ({len(members):>3} 篇)  {' '.join(kws)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "exam_topics.json"
    out.write_text(json.dumps({
        "level": args.level or "ALL",
        "passages": len(arts),
        "k": args.k,
        "topics": topics,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out.relative_to(ROOT)}")
    print("生成文章时按 share 加权抽样题材簇 —— 分布即忠实于真题。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
