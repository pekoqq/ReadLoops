#!/usr/bin/env python3
"""构建权威的「原形 → 全部变形」索引，供 lexicon 使用。

## 为什么需要单独建索引

ECDICT 把变形词**单独收录且没有词频数据**：

| 词 | 自身 rank | 原形 | 原形 rank |
|---|---|---|---|
| `is` | 24,648 | be | **2** |
| `are` | 0 | be | **2** |
| `thought` | 760 | think | 56 |
| `being` | 1,676 | be | 2 |

实测 **19,205 个变形词**的自身 rank 比原形差 5 倍以上。

后果：「认识最高频 N 个词」这个判据会**漏掉英语中最常见的词形**，
把覆盖率系统性低估（`is`/`are`/`was` 全被算成生词）。

## 两个来源

1. **ECDICT 的 `exchange` 字段**（主）—— 覆盖大部分规则与不规则变形
2. **spaCy 词形还原器**（补）—— 补 ECDICT 遗漏的，例如
   `be.exchange` 只有 `{"past":"was","third":"is",...}`，
   **缺 `are` / `were`**，而 `are` 是英语第 2 高频词

不做手工补表 —— 那属于「瞎编」。用 NLP 工具补，可复现、可审计。

输出：`data/form_index.json`（`{原形: [变形...]}`）。
`lexicon` 存在时优先加载它，否则退回只用 exchange。

用法：
    python3 tools/build_form_index.py
    python3 tools/build_form_index.py --top 30000   # 只处理最高频的 N 个词（默认全部）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ⚠️ 放在 app/resources 而不是 data/ —— data/ 是 .gitignore 的，
# 而这个索引是**运行必需品**（lexicon 启动时加载），必须随包发布。
OUT = ROOT / "app" / "resources" / "form_index.json"
LEXICAL = ("past", "third", "done", "ing", "s", "ed", "er", "est", "pl")


def from_exchange(db) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in db.execute(
        "SELECT text, exchange FROM words WHERE exchange IS NOT NULL "
        "AND exchange != '' AND exchange != 'null'"
    ):
        base = (r["text"] or "").strip().lower()
        if not base:
            continue
        try:
            ex = json.loads(r["exchange"])
        except (ValueError, TypeError):
            continue
        forms = out.setdefault(base, {base})
        for k, v in ex.items():
            if k in LEXICAL and isinstance(v, str):
                forms.update(f.strip().lower() for f in v.split("/") if f.strip())
    return out


def supplement_with_spacy(db, index: dict[str, set[str]], top: int) -> int:
    """用 spaCy 找出 exchange 遗漏的变形。

    做法：对高频词做词形还原，若 `lemma(w) != w` 且 w 也是库内词，
    说明 w 是某个原形的变形 → 把它并进该原形的变形集合。
    """
    try:
        import spacy
    except ImportError:
        print("  ⚠️ 未安装 spaCy，跳过补全（只保留 exchange 的结果）")
        return 0

    try:
        nlp = spacy.load("en_core_web_sm", disable=["ner", "parser"])
    except Exception as e:
        print(f"  ⚠️ spaCy 模型不可用（{e}），跳过补全")
        return 0

    # ⚠️ 不要按 rank 过滤！`are` 的 frq/bnc 都是 0（rank=0），
    # 用 `BETWEEN 1 AND N` 会把英语第 2 高频词直接滤掉 —— 正是要补的那个。
    q = "SELECT lower(text) t FROM words"
    if top:
        q += " WHERE COALESCE(NULLIF(frq,0),bnc,0) = 0 OR COALESCE(NULLIF(frq,0),bnc,0) <= ?"
    words = [r["t"] for r in db.execute(q, (int(top),)) if r["t"]]
    all_words = {r["t"] for r in db.execute("SELECT lower(text) t FROM words")}
    print(f"  对 {len(words):,} 个词做词形还原…")

    added = 0
    BATCH = 2000
    for i in range(0, len(words), BATCH):
        chunk = words[i:i + BATCH]
        for doc, w in zip(nlp.pipe(chunk, batch_size=256), chunk):
            if len(doc) != 1:
                continue
            lemma = doc[0].lemma_.strip().lower()
            if not lemma or lemma == w or lemma.startswith("-"):
                continue
            # ⚠️ 只接受「库内确实存在的原形」。否则 spaCy 对生僻词/专名的
            # 还原会凭空造出原形条目，污染索引。
            if lemma not in all_words:
                continue
            index.setdefault(lemma, {lemma}).add(w)
            added += 1
        if (i // BATCH) % 5 == 0:
            print(f"    {i + len(chunk):,}/{len(words):,}  已补 {added:,}")
    return added


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30000,
                    help="只对最高频的 N 个词跑 spaCy 补全（0 = 全部）")
    args = ap.parse_args()

    from app.database import get_db

    with get_db() as db:
        index = from_exchange(db)
        base_forms = sum(len(v) for v in index.values())
        print(f"① exchange 字段: {len(index):,} 个原形 / {base_forms:,} 个形式")

        added = supplement_with_spacy(db, index, args.top)
        print(f"② spaCy 补全: 新增 {added:,} 条变形关联")

        # 关键检查：那些自身 rank 很差的常见变形是否已被覆盖
        check = {"are": "be", "were": "be", "gotten": "get", "is": "be", "was": "be",
                 "thought": "think", "being": "be", "left": "leave", "went": "go"}
        print("③ 关键变形核对:")
        ok = 0
        for form, lemma in check.items():
            hit = form in index.get(lemma, ())
            ok += hit
            print(f"     {form:<10} → {lemma:<8} {'✓' if hit else '✗'}")
        print(f"   {ok}/{len(check)} 通过")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    serializable = {k: sorted(v) for k, v in index.items()}
    OUT.write_text(json.dumps(serializable, ensure_ascii=False), encoding="utf-8")
    total = sum(len(v) for v in index.values())
    print(f"\n已写入 {OUT.relative_to(ROOT)}（{len(index):,} 个原形 / {total:,} 个形式，"
          f"{OUT.stat().st_size/1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
