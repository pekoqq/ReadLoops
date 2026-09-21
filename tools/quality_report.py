#!/usr/bin/env python3
"""文章生成质量报告 —— 批量生成并测量全部维度。

## 为什么需要它

文章生成是整个产品的本体，但它此前**没有任何可验证的质量度量** ——
prompt 里写一堆要求，生成后不做检查，实测覆盖率只有 68.8% 却无人发现。

本工具批量生成，逐篇测量 11 个维度，输出可对比的报告。
每次改动生成逻辑后都应当跑它，看指标是升还是降。

用法：
    python3 tools/quality_report.py --count 5
    python3 tools/quality_report.py --count 5 --article-id 31   # 分析已有文章，不生成
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 目标值（与 article_quality 保持一致）
TARGETS = {
    "coverage": ("覆盖率", "95%±2", lambda v: 0.93 <= v <= 0.97),
    "coverage_all": ("总体覆盖率", "参考", lambda v: True),
    "target_missing": ("目标词缺失", "0", lambda v: v == 0),
    "sent_avg": ("平均句长", "16–21", lambda v: 16 <= v <= 21),
    "sent_std": ("句长标准差", "≥5", lambda v: v >= 5),
    "long_ratio": ("长难句占比", "8–12%", lambda v: 0.06 <= v <= 0.16),
    "spec": ("具体性", "≥5", lambda v: v >= 5),
    "cliche": ("陈词滥调", "0", lambda v: v == 0),
    "relcl": ("定语从句", "≥2", lambda v: v >= 2),
    "advcl": ("状语从句", "≥2", lambda v: v >= 2),
    "passive": ("被动语态", "≥2", lambda v: v >= 2),
    "nfin": ("非谓语", "≥2", lambda v: v >= 2),
    "consec": ("最长连续生词", "≤2", lambda v: v <= 2),
}


def measure(content: str, target_words: list[str], known: set) -> dict:
    from app.services import article_quality as aq

    rep = aq.analyze(content, known=known, target_words=target_words)
    g = rep["grammar"]
    return {
        "coverage": rep["coverage"],
        "coverage_all": rep.get("coverage_all", 0),
        "target_missing": len(rep["target_words_missing"]),
        "sent_avg": rep["sentences"]["avg_len"],
        "sent_std": rep["sentences"]["std_len"],
        "long_ratio": rep["sentences"]["long_ratio"],
        "spec": rep["specificity"]["total"],
        "cliche": len(rep["style"]["cliche_hits"]),
        "relcl": g["relative_clause"],
        "advcl": g["adverbial_clause"],
        "passive": g["passive"],
        "nfin": g["non_finite"],
        "consec": rep["max_consecutive"],
        "words": rep["words"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--article-id", type=int, default=0, help="分析已有文章而非生成")
    args = ap.parse_args()

    from app.services import known_set

    known = known_set.build()
    vocab, src = known_set.vocab_size()
    rows = []

    if args.article_id:
        from app.database import get_db
        with get_db() as db:
            arts = db.execute(
                "SELECT id, title, content, target_words FROM articles WHERE id >= ? "
                "ORDER BY id LIMIT ?", (args.article_id, args.count)).fetchall()
        for a in arts:
            tw = json.loads(a["target_words"] or "[]")
            m = measure(a["content"], tw, known)
            m["title"] = a["title"]
            rows.append(m)
    else:
        import httpx
        for i in range(args.count):
            print(f"  生成第 {i+1}/{args.count} 篇…", flush=True)
            try:
                r = httpx.post("http://127.0.0.1:8000/api/articles/generate", timeout=300)
                r.raise_for_status()
                d = r.json()
            except Exception as e:
                print(f"    ✗ 生成失败: {e}")
                continue
            m = measure(d["content"], d["target_words"], known)
            m["title"] = d["title"]
            rows.append(m)

    if not rows:
        print("没有可分析的文章")
        return 1

    print(f"\n已知集：词汇量 {vocab}（{src}）· {len(known):,} 个词形\n")
    print(f"{'指标':<14}{'目标':<10}{'实测中位':<12}{'范围':<20}达标")
    print("-" * 68)
    passed = 0
    for key, (label, target, okf) in TARGETS.items():
        vals = [r[key] for r in rows if key in r]
        if not vals:
            continue
        med = statistics.median(vals)
        lo, hi = min(vals), max(vals)
        fmt = (lambda v: f"{v*100:.1f}%") if key in ("coverage", "coverage_all", "long_ratio") \
            else (lambda v: f"{v:g}")
        n_ok = sum(1 for v in vals if okf(v))
        mark = "✅" if n_ok == len(vals) else ("⚠️" if n_ok else "❌")
        if n_ok == len(vals):
            passed += 1
        print(f"{label:<14}{target:<10}{fmt(med):<12}"
              f"{fmt(lo)+' – '+fmt(hi):<20}{mark} {n_ok}/{len(vals)}")

    print("-" * 68)
    print(f"完全达标的指标: {passed}/{len(TARGETS)}")

    # 单篇视角：一篇文章到底有几项不达标 ——
    # 比「某维度是否 12/12 全过」更能反映真实阅读体验（读者一次只读一篇）
    per = [len([k for k, (_, _, okf) in TARGETS.items() if k in r and not okf(r[k])])
           for r in rows]
    print("\n单篇合规情况：")
    print("-" * 40)
    for n in sorted(set(per)):
        c = per.count(n)
        label = "全部达标" if n == 0 else f"不达标 {n} 项"
        print(f"  {label:<14}{c:>3} 篇   {c/len(rows)*100:.0f}%")
    print(f"  平均不达标 {statistics.mean(per):.1f} 项/篇")

    print("\n逐篇明细：")
    for r in rows:
        print(f"  {r['title'][:38]:<40} {r['words']:>4} 词  "
              f"覆盖 {r['coverage']*100:5.1f}%  缺词 {r['target_missing']}  "
              f"被动 {r['passive']}  长难句 {r['long_ratio']*100:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
