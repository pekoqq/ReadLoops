"""官方考试词汇表（来自教育部教育考试院的考纲 PDF）。

## 为什么必须换掉 ECDICT 的 tags

此前项目用 `words.tags` 判定一个词属于哪场考试。但 ECDICT 的 `tag` 是
**「在哪个级别新学」的增量标注**（`zk` 中考 < `gk` 高考 < `cet4` < `cet6` < `ky`…）——
`a` / `about` / `above` / `able` 这些词早被标成 `zk`，就**不再标 `cet4`**。

而**官方词表是累积的**：四级考生必须掌握词表里的全部词汇。

实测两者的差距：

| | 数量 |
|---|---|
| 官方四级词表（归一化后） | **5,930** 词元 |
| ECDICT `cet4` 标签 | 3,845 |
| 交集 | 仅 3,277 |
| **官方有、ECDICT 无** | **2,653** |
| ECDICT 有、官方无 | 568 |

后果：「四级还差多少词」被**低估约 1,300 词**（2,500 词水平时：2,524 → **3,857**）。

## 来源

《全国大学英语四、六级考试大纲（2016年修订版）》，教育部教育考试院
https://cet.neea.edu.cn/res/Home/1704/55b02330ac17274664f06d9d3db8249d.pdf

「本词表共收录词目 5,418 个。分四级和六级两个级别，其中六级词用 ★ 标记。」

由 `tools/extract_official_vocab.py` 从 PDF 提取（解析出 5,401 条，覆盖 99.69%）。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

RESOURCE = Path(__file__).resolve().parent.parent / "resources" / "cet_official_vocab.json"

# 官方词表**只覆盖四级/六级**（考纲就是四六级考纲）。
# 考研/雅思/托福/GRE 没有官方词表，继续用 ECDICT 的 tags。
OFFICIAL_EXAMS = ("cet4", "cet6")


@lru_cache(maxsize=1)
def _load() -> Optional[dict]:
    if not RESOURCE.exists():
        return None
    try:
        return json.loads(RESOURCE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


@lru_cache(maxsize=8)
def forms(exam: str) -> frozenset[str]:
    """某场考试的**累积**词形集合（小写）。

    六级是**累积**的：六级考生要掌握四级 + 六级附加的全部词汇。
    """
    raw = _load()
    if not raw or exam not in OFFICIAL_EXAMS:
        return frozenset()
    cet4 = {w.lower() for w in raw.get("cet4", [])}
    if exam == "cet4":
        return frozenset(cet4)
    # ⚠️ 六级是**累积**的：考纲里 ★ 只标「六级附加词」，
    # 但六级考生要掌握四级 + 附加的全部词汇。
    return frozenset(cet4 | {w.lower() for w in raw.get("cet6", [])})


@lru_cache(maxsize=8)
def lemmas(exam: str) -> frozenset[str]:
    """把词形归一到词元（`centres` → `centre`），用于和 `words` 表比对。"""
    from app.services import lexicon

    out = set()
    for w in forms(exam):
        n = lexicon.normalize(w)
        if n:
            out.add(n)
    return frozenset(out)


@lru_cache(maxsize=1)
def available() -> bool:
    """官方词表是否可用（缺失时调用方应退回 tags）。"""
    return bool(_load())


def has_official(exam: str) -> bool:
    """某场考试是否有官方词表。没有的（考研/雅思/托福）继续用 tags。"""
    return available() and exam in OFFICIAL_EXAMS


def source_info() -> dict:
    raw = _load() or {}
    return {
        "available": available(),
        "source": raw.get("source", ""),
        "source_url": raw.get("source_url", ""),
        "publisher": raw.get("publisher", ""),
        "declared_count": raw.get("declared_count"),
        "parsed_count": raw.get("parsed_count"),
    }
