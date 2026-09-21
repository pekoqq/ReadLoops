#!/usr/bin/env python3
"""从抓到的真题精读数据里，提取文章生成要仿的**形式层特征**。

## 为什么

「仿四级真题」不能靠感觉。四级真题的「难」分两层：

1. **词汇层** —— 低频词多。这层要按读者的词汇量**降下来**（否则 87% 覆盖率读不懂）
2. **形式层** —— 体裁、作者态度、篇章结构、句法。这层要**完整保留**，才叫「像真题」

本工具从 `语料库/真题精读/exam_analysis.json`（由 `crawl_exam_analysis.py` 抓取）
统计出形式层的**真实分布**，写进 `app/resources/exam_style.json`，
供生成时按分布抽样。

## 产出

- `app/resources/exam_style.json`：体裁/态度/CEFR 分布 + 意群结构模板

用法：
    python3 tools/analyze_exam_style.py
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "语料库" / "真题精读" / "exam_analysis.json"
OUT = ROOT / "app" / "resources" / "exam_style.json"

# 意群功能 → 归一化的结构角色（把「结尾」「总结建议」「总结升华」归成「收束」）
# 意群功能 → 归一化角色。模式来自实测的标签语料（见 tools/analyze_exam_style.py
# 的「其他」诊断）；纯内容性的标签（如「性别差异」「男校的情感教育优势」）
# 本来就没有功能含义，会合理地留在「其他」。
ROLE_PATTERNS = [
    ("引入", r"引出|引入|开篇|导语|引子|引言|问题|现象|背景|起源"),
    ("数据", r"数据|研究|发现|证据|调查|统计|实验"),
    ("观点", r"专家观点|学者观点|观点"),
    ("分析", r"分析|原因|机制|解释|剖析|分论点|论证|界定"),
    ("对比", r"对比|比较|正反|质疑|反驳|对立"),
    ("例证", r"举例|案例|实例|例证|例子"),
    ("影响", r"影响|后果|意义|作用|启示"),
    ("建议", r"建议|对策|措施|呼吁|方案|解决"),
    ("收束", r"总结|结尾|结束|升华|展望|收束"),
]


def role_of(text: str) -> str:
    for role, pat in ROLE_PATTERNS:
        if re.search(pat, text):
            return role
    return "其他"


def main() -> int:
    if not SRC.exists():
        print(f"✗ 找不到 {SRC}\n  请先跑 tools/crawl_exam_analysis.py")
        return 1

    data = json.loads(SRC.read_text(encoding="utf-8"))
    n = len(data)
    print(f"分析 {n} 篇真题精读\n")

    def from_faq(rec, pat):
        for f in rec.get("faq", []):
            m = re.search(pat, f.get("a", ""))
            if m:
                return m.group(1)
        return None

    genre = Counter(from_faq(x, r"体裁为([^，,。；]+)") for x in data)
    genre.pop(None, None)
    cefr = Counter(from_faq(x, r"CEFR 难度\s*([A-C][12])") for x in data)
    cefr.pop(None, None)

    attitude = Counter()
    for x in data:
        m = re.search(r"作者态度为\s*([^（(]+)", x.get("attitude", ""))
        if m:
            attitude[m.group(1).strip()] += 1

    # 意群结构：每篇的角色序列
    shapes: Counter = Counter()
    roles_all: Counter = Counter()
    for x in data:
        roles = []
        for item in x.get("structure", []):
            # 条目形如 `P1 引出问题：作物产量增长放缓 这个 opening paragraph …`
            # ⚠️ 结构角色写在**冒号之前**的标签里，不是在冒号后的解释里。
            m = re.search(r"^P?\d*\s*([^：:]{2,20})[：:]", item.strip())
            label = m.group(1) if m else item
            r = role_of(label)
            roles.append(r)
            roles_all[r] += 1
        if roles:
            shapes[" → ".join(roles)] += 1

    para_counts = [len(x["paragraphs"]) for x in data if x.get("paragraphs")]
    sent_counts = [len(x["sentences"]) for x in data if x.get("sentences")]

    def dist(counter, total):
        return {k: round(v / total, 4) for k, v in counter.most_common() if v}

    payload = {
        "source": "真题精读数据统计（crawl_exam_analysis.py 抓取）",
        "passages": n,
        "genre": dist(genre, sum(genre.values())),
        "attitude": dist(attitude, sum(attitude.values())),
        "cefr": dist(cefr, sum(cefr.values())),
        "roles": dist(roles_all, sum(roles_all.values())),
        "structure_shapes": [{"shape": s, "count": c}
                             for s, c in shapes.most_common(15)],
        "paragraphs": {
            "avg": round(statistics.mean(para_counts), 1) if para_counts else 0,
            "median": statistics.median(para_counts) if para_counts else 0,
            "min": min(para_counts) if para_counts else 0,
            "max": max(para_counts) if para_counts else 0,
        },
        "sentences": {
            "avg": round(statistics.mean(sent_counts), 1) if sent_counts else 0,
            "median": statistics.median(sent_counts) if sent_counts else 0,
        },
    }

    print("=== 体裁 ===")
    for k, v in payload["genre"].items():
        print(f"  {k:<8} {v*100:5.1f}%")
    print("\n=== 作者态度 ===")
    for k, v in payload["attitude"].items():
        print(f"  {k:<8} {v*100:5.1f}%")
    print("\n=== CEFR ===")
    for k, v in sorted(payload["cefr"].items()):
        print(f"  {k:<6} {v*100:5.1f}%")
    print("\n=== 意群角色 ===")
    for k, v in payload["roles"].items():
        print(f"  {k:<6} {v*100:5.1f}%")
    print("\n=== 段落/句子 ===")
    print(f"  段落 平均 {payload['paragraphs']['avg']} 中位 {payload['paragraphs']['median']}")
    print(f"  句子 平均 {payload['sentences']['avg']} 中位 {payload['sentences']['median']}")
    print("\n=== 最常见的篇章结构骨架 ===")
    for s in payload["structure_shapes"][:5]:
        print(f"  {s['count']:>3}× {s['shape']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
