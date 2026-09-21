#!/usr/bin/env python3
"""从真题精读数据里提取**真实四级句式模板**，供生成时模仿。

## 为什么

长难句一直是生成最不稳定的一项：prompt 里写「至少 2 句 28 词以上、含嵌套从句」，
模型照样写全短句 —— 抽象指令对长句的约束力很弱。

而我们已经抓到了 **5,363 句真实四级真题句子**（`crawl_exam_analysis.py` 的产物），
每句还带语法标注。把它们按句式分类，抽出代表性例句，直接作为**范例**写进 prompt，
比任何抽象描述都具体。

## 做法

1. 从 `语料库/真题精读/exam_analysis.json` 取出英文句子部分（剥掉后半的语法说明）
2. 用 spaCy 解析每句，提取结构特征：词数、从句类型组合、嵌套层数、语态
3. 按「从句类型组合 + 长度档」聚类成句式模式
4. 每个模式保留 2–3 条真实例句
5. 产出 `app/resources/sentence_patterns.json`

用法：
    python3 tools/build_sentence_patterns.py
    python3 tools/build_sentence_patterns.py --min-words 25
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SRC = ROOT / "语料库" / "真题精读" / "exam_analysis.json"
OUT = ROOT / "app" / "resources" / "sentence_patterns.json"

# 从句类型的排列顺序（生成 pattern key 时固定，避免 "relcl+advcl" 与 "advcl+relcl" 分裂）
ORDER = ["relcl", "advcl", "ccomp", "xcomp", "csubj", "acl", "auxpass"]


def english_part(raw: str) -> str:
    """精读条目形如 `<英文句子> 第3段，第4句 "主句 + if 条件状语从句" 是四六级…`。

    只取英文句子部分 —— 后面是站方的语法讲解，混进例句就全毁了。

    ⚠️ 不能用 `第\\d+段` 这种具体格式匹配：实测还有 `第53题`、`第2句` 等变体，
    漏掉一种就会把整段中文讲解带进例句。**在第一个中文字符处直接切断**才稳。
    """
    s = re.sub(r"\s+", " ", raw.strip())
    cut = re.search(r"[\u4e00-\u9fff]", s)
    if cut:
        s = s[:cut.start()]
    return s.strip().rstrip("。，,、").strip()


def features(nlp, sent: str) -> dict | None:
    doc = nlp(sent)
    words = [t for t in doc if t.is_alpha]
    if len(words) < 8:
        return None
    kinds = []
    for t in doc:
        if t.dep_ == "auxpass":
            kinds.append("auxpass")
        elif t.dep_ in ("relcl", "advcl", "ccomp", "xcomp", "csubj", "acl"):
            kinds.append(t.dep_)
    uniq = sorted(set(kinds), key=lambda k: ORDER.index(k) if k in ORDER else 99)
    # 嵌套：某个从句内部还有别的从句
    heads = [t for t in doc if t.dep_ in ("relcl", "advcl", "ccomp", "xcomp", "csubj")]
    nested = 0
    for h in heads:
        if any(x is not h and x.dep_ in ("relcl", "advcl", "ccomp", "xcomp", "csubj")
               for x in h.subtree):
            nested += 1
    return {"words": len(words), "kinds": kinds, "uniq": uniq, "nested": nested,
            "has_passive": "auxpass" in kinds}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-words", type=int, default=22,
                    help="只统计这个长度以上的句子（长难句才需要模板）")
    ap.add_argument("--per-pattern", type=int, default=3)
    args = ap.parse_args()

    if not SRC.exists():
        print(f"✗ 找不到 {SRC}\n  请先跑 tools/crawl_exam_analysis.py")
        return 1

    from app.services.article_quality import _nlp
    nlp = _nlp()
    if nlp is None:
        print("✗ spaCy 不可用，无法解析句法")
        return 1

    data = json.loads(SRC.read_text(encoding="utf-8"))
    sents: list[str] = []
    for rec in data:
        for raw in rec.get("sentences", []):
            s = english_part(raw)
            if s and len(s.split()) >= args.min_words:
                sents.append(s)
    sents = list(dict.fromkeys(sents))
    print(f"从 {len(data)} 篇精读里取到 {len(sents):,} 句 ≥{args.min_words} 词的真实句子")

    by_pattern: dict[str, list[dict]] = defaultdict(list)
    all_feats = []
    for s in sents:
        f = features(nlp, s)
        if not f:
            continue
        all_feats.append(f)
        key = "+".join(f["uniq"]) if f["uniq"] else "plain"
        if f["nested"]:
            key += "+nested"
        by_pattern[key].append({"text": s, **f})

    if not all_feats:
        print("✗ 没有可用句子")
        return 1

    lens = [f["words"] for f in all_feats]
    print(f"解析成功 {len(all_feats):,} 句")
    print(f"  长度：中位 {statistics.median(lens):.0f} 词，p90 {sorted(lens)[int(len(lens)*0.9)]} 词")
    print(f"  含嵌套从句: {sum(1 for f in all_feats if f['nested']):,} 句")
    print(f"  含被动语态: {sum(1 for f in all_feats if f['has_passive']):,} 句")
    print("\n最常见的句式（前 12）:")
    patterns = []
    for key, items in sorted(by_pattern.items(), key=lambda kv: -len(kv[1]))[:12]:
        share = len(items) / len(all_feats)
        print(f"  {share*100:5.1f}%  {key:<34} {len(items):>4} 句")
        patterns.append({
            "pattern": key,
            "share": round(share, 4),
            "count": len(items),
            # 每个模式挑长度接近该模式中位的例句（最有代表性）
            "examples": [x["text"] for x in sorted(
                items, key=lambda x: abs(x["words"] - statistics.median(
                    [i["words"] for i in items])))[:args.per_pattern]],
        })

    payload = {
        "source": "真题精读数据的句子（crawl_exam_analysis.py 抓取）",
        "sentences_analyzed": len(all_feats),
        "min_words": args.min_words,
        "length": {"median": round(statistics.median(lens), 1),
                   "p90": sorted(lens)[int(len(lens) * 0.9)]},
        "patterns": patterns,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT.relative_to(ROOT)}（{OUT.stat().st_size/1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
