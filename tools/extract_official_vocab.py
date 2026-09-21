#!/usr/bin/env python3
"""从**官方考纲 PDF** 提取四级/六级词汇表。

## 为什么必须用官方来源

此前项目用的是 ECDICT 的 `tags` 字段 —— 那是**二手来源**（第三方对考试归属的
标注）。它给出四级 3,846 词、六级 5,387 词，但**无法说明依据**。

而官方考纲是**教育部教育考试院**发布的权威文档：

> 《全国大学英语四、六级考试大纲（2016年修订版）》
> https://cet.neea.edu.cn/res/Home/1704/55b02330ac17274664f06d9d3db8249d.pdf
>
> 「本词表共收录**词目 5,418 个**。分四级和六级两个级别，**其中六级词用 ★ 标记**。」

## 词表排版

每行一个**词族**：

    a/an                      词条（斜杠是拼法变体）
    ★ abbreviation            ★ = 六级词
    able ability              词族：原形 + 派生
    age aged ag(e)ing         括号表示可有可无的字母

## 产出

`语料库/官方考纲/cet_official_vocab.json`，另导出 `app/resources/cet_official_vocab.json`
供运行期使用（**只含词形，不含释义** —— 官方词表本身就不给释义，无版权风险）。

用法：
    python3 tools/extract_official_vocab.py
    python3 tools/extract_official_vocab.py --check    # 只校验不写文件
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "语料库" / "官方考纲" / "CET考试大纲2016.pdf"
OUT = ROOT / "语料库" / "官方考纲" / "cet_official_vocab.json"
RESOURCE = ROOT / "app" / "resources" / "cet_official_vocab.json"

# 词表所在页范围（已实测：21–148，128 页无断档）
PAGE_LO, PAGE_HI = 21, 148

# 页眉页脚等噪声
NOISE = re.compile(
    r"^(?:[０-９\d]+|词\s*表|全\s*国\s*大\s*学\s*英\s*语|"
    r"全国大学英语四、六级考试大纲.*|说明.*|.*?词表\s*[０-９\d]+)$")
# 一个词族行：可选的 ★，然后是英文词（允许 () / - 空格 . 和 撇号）
ENTRY = re.compile(r"^[★\s]*([A-Za-z][A-Za-z()/\-'. ]{0,45})$")


def parse_pdf() -> list[dict]:
    """按行抽取词族。

    ⚠️ 两个 PDF 提取陷阱：

    1. **字体映射错位**：文档里有一个字符被映射成 U+FF27（全角 G），
       外观其实是**连字符**。渲染成图片核对过：
           `center/Ｇtre`  = `center/-tre`  → center / centre
           `centralize/Ｇise` = `centralize/-ise` → centralize / centralise
           `airＧconditioning` = `air-conditioning`
       不修的话，含英美拼写变体的 102 行会被整行丢弃。

    2. **三列排版**：同一行可能并列 2–3 个词族（如
       `center/-tre   central   centralize/-ise`）。按行抽会把它们并成一条，
       所以下面在空格切分后，再按「是否含 ★ / 是否像新词族」做拆分。
       实测按行抽得 5,322 条 vs 官方声明 5,418 条（差 1.77%），
       差异主要来自列合并 —— 对下游使用（词形集合）影响很小。
    """
    import pypdf

    reader = pypdf.PdfReader(str(PDF))
    entries: list[dict] = []
    BAD_HYPHEN = "\uff27"

    for pno in range(PAGE_LO - 1, min(PAGE_HI, len(reader.pages))):
        text = (reader.pages[pno].extract_text() or "")
        text = text.replace(BAD_HYPHEN, "-").replace("\u3000", " ")
        for raw in text.split("\n"):
            line = re.sub(r"\s+", " ", raw).strip()
            if not line or NOISE.match(line):
                continue
            # 一行里可能有多列：按 ★ 切分（★ 一定是某个词族的开头）
            chunks = re.split(r"\s+(?=★)|(?<=[a-z)\-])\s+(?=★)", line)
            for ch in chunks:
                ch = ch.strip()
                if not ch:
                    continue
                cet6 = ch.startswith("★")
                body = ch.lstrip("★ ").strip().rstrip(".")
                m = ENTRY.match(body)
                if not m:
                    continue
                forms = [w for w in m.group(1).split() if w]
                if not forms or len(re.sub(r"[^A-Za-z]", "", forms[0])) < 2:
                    continue
                entries.append({"head": forms[0], "family": forms, "cet6": cet6,
                                "page": pno + 1})
    return entries


def expand_variants(forms: list[str]) -> list[str]:
    """展开一个词族里的所有写法。

    处理三种情况：
      - 斜杠变体：`adviser/advisor` → adviser, advisor
      - 括号可选：`ag(e)ing` → ageing, aging
      - 派生词：`able ability` → 已经由空格切分
    """
    out = []
    for f in forms:
        f = f.strip().strip(".")
        if not f:
            continue
        # `center/-tre` 这种：连字符开头表示「替换前面的后缀」
        # → center 与 centre 两种拼法都要
        if "/-" in f:
            base, tail = f.split("/-", 1)
            out.append(re.sub(r"[^a-z]", "", base.lower()))
            if len(tail) <= len(base):
                out.append(re.sub(r"[^a-z]", "", (base[:len(base) - len(tail)] + tail).lower()))
            continue
        # 括号：生成含/不含两种
        if "(" in f and ")" in f:
            inner = re.search(r"\(([^)]*)\)", f)
            if inner:
                base = f[:inner.start()] + f[inner.end():]
                out.append(re.sub(r"[^a-z]", "", base.lower()))
                out.append(re.sub(r"[^a-z]", "", (f[:inner.start()] + inner.group(1) + f[inner.end():]).lower()))
                continue
        # 斜杠：两边都算
        for part in f.split("/"):
            part = re.sub(r"[^A-Za-z'\-]", "", part)
            if part:
                out.append(part.lower())
    return [w for w in dict.fromkeys(out) if w]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = ap.parse_args()

    if not PDF.exists():
        print(f"✗ 找不到 {PDF}\n  请先下载官方考纲 PDF")
        return 1

    rows = parse_pdf()
    cet4 = [r for r in rows if not r["cet6"]]
    cet6_only = [r for r in rows if r["cet6"]]

    forms4: set[str] = set()
    for r in cet4:
        forms4.update(expand_variants(r["family"]))
    forms6: set[str] = set()
    for r in cet6_only:
        forms6.update(expand_variants(r["family"]))
    all_forms = forms4 | forms6

    print(f"解析出词目: {len(rows)} 条")
    print(f"  四级（无 ★）: {len(cet4)}")
    print(f"  六级（★）  : {len(cet6_only)}")
    print("  官方声明    : 5,418 个词目")
    print(f"  偏差        : {len(rows) - 5418:+d}（{abs(len(rows)-5418)/5418*100:.2f}%）")
    print()
    print(f"展开出的词形: 四级 {len(forms4):,} / 六级 {len(forms6):,} / 合计 {len(all_forms):,}")
    print("\n样例:")
    for r in rows[:6]:
        tag = "★六级" if r["cet6"] else "  四级"
        print(f"    {tag}  {r['head']:<16} 词族={r['family']}")

    if args.check:
        return 0

    payload = {
        "source": "全国大学英语四、六级考试大纲（2016年修订版）",
        "source_url": "https://cet.neea.edu.cn/res/Home/1704/55b02330ac17274664f06d9d3db8249d.pdf",
        "publisher": "教育部教育考试院",
        "declared_count": 5418,
        "parsed_count": len(rows),
        "cet4_entries": len(cet4),
        "cet6_entries": len(cet6_only),
        "entries": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 运行期资源：只要词形集合，体积小
    RESOURCE.parent.mkdir(parents=True, exist_ok=True)
    RESOURCE.write_text(json.dumps({
        "source": payload["source"],
        "source_url": payload["source_url"],
        "cet4": sorted(forms4),
        "cet6": sorted(forms6),
    }, ensure_ascii=False), encoding="utf-8")

    print(f"\n已写入 {OUT.relative_to(ROOT)}")
    print(f"已写入 {RESOURCE.relative_to(ROOT)}"
          f"（{RESOURCE.stat().st_size/1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
