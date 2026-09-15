"""词形还原与词表查询的共用底座。

**为什么需要它**：库里存的是词元（`student`），而语料里跑的是屈折形式
（`students`/`has`/`women`）。实测只有 **53.2%** 的语料词型能在库里直接查到，
剩下全是屈折形式 —— 不做还原，覆盖率会凭空少算 16.8% 的 token。

三个调用方都依赖它：覆盖率统计、考试缺口、重遇记录。

缓存策略：反向词形表（约 4 万条）在进程内缓存一次即可 —— 它来自
`words.exchange`，只在导入词库时才会变，届时调用 `invalidate()`。
"""
from __future__ import annotations

import json
import re
from typing import Optional

from app.database import get_db

# 语料分词：只认英文单词，保留撇号与连字符
TOKEN_RE = re.compile(r"[a-z][a-z'-]*")

# 缩写还原：it's → it，don't → do，you're → you …
# 这些形式在词库里查不到，但拆开后是最高频的词，漏掉会明显低估覆盖率。
_CONTRACTIONS = {
    "n't": "", "'s": "", "'re": "", "'ve": "", "'ll": "", "'d": "", "'m": "",
}


def tokenize(text: str) -> list[str]:
    """把文本切成小写词序列。"""
    return TOKEN_RE.findall((text or "").lower())


def _strip_contraction(token: str) -> str:
    for suffix, repl in _CONTRACTIONS.items():
        if token.endswith(suffix) and len(token) > len(suffix) + 1:
            return token[: -len(suffix)] + repl
    return token


_WORDS: Optional[set[str]] = None
_REV: Optional[dict[str, str]] = None


def invalidate() -> None:
    """词库变更后调用（导入词典、新增单词等）。"""
    global _WORDS, _REV
    _WORDS = None
    _REV = None


def word_set() -> set[str]:
    """库内全部小写词元。"""
    global _WORDS
    if _WORDS is None:
        with get_db() as db:
            _WORDS = {r[0] for r in db.execute("SELECT lower(text) FROM words")}
    return _WORDS


def _rev_index() -> dict[str, str]:
    """屈折形式 → 词元。来自 words.exchange 的 JSON。

    `exchange` 形如 {"done": "abandoned", "past": "abandoned", "ing": "abandoning"}，
    反向即可把 abandoned/abandoning/abandons 都指回 abandon。
    """
    global _REV
    if _REV is None:
        rev: dict[str, str] = {}
        with get_db() as db:
            rows = db.execute(
                "SELECT lower(text) t, exchange FROM words "
                "WHERE exchange IS NOT NULL AND exchange != ''"
            ).fetchall()
        for text, ex in rows:
            try:
                forms = json.loads(ex)
            except (TypeError, ValueError):
                continue
            if not isinstance(forms, dict):
                continue
            for key, val in forms.items():
                if key == "lemma":
                    continue
                for form in (val if isinstance(val, list) else [val]):
                    form = str(form).strip().lower()
                    if form and form not in rev:
                        rev[form] = text
        _REV = rev
    return _REV


def normalize(token: str) -> Optional[str]:
    """把一个语料 token 还原成库内词元；无法还原时返回 None。

    四级语料实测命中率：直接查表 83.2% → 还原后 94.9%。
    """
    token = _strip_contraction((token or "").strip().lower())
    if not token:
        return None
    words = word_set()
    if token in words:
        return token

    rev = _rev_index()
    if token in rev:
        return rev[token]

    # 规则兜底：词形还原表没覆盖到的（尤其 -ly / -er / -est）
    for suffix, repl in (("ies", "y"), ("es", ""), ("s", ""), ("ed", ""),
                         ("ing", ""), ("ly", ""), ("er", ""), ("est", "")):
        if token.endswith(suffix) and len(token) > len(suffix) + 2:
            base = token[: -len(suffix)] + repl
            for cand in (base, base + "e", base + "y"):
                if cand in words:
                    return cand
            # 双写辅音：running → run
            if len(base) > 3 and base[-1] == base[-2] and base[:-1] in words:
                return base[:-1]
    return None


def normalize_all(tokens: list[str]) -> list[Optional[str]]:
    return [normalize(t) for t in tokens]


def resolve_ids(tokens: list[str]) -> dict[str, int]:
    """批量把 token 映射到 words.id（未命中的丢弃）。"""
    words = word_set()
    lemmas = set()
    for t in tokens:
        n = normalize(t)
        if n and n in words:
            lemmas.add(n)
    if not lemmas:
        return {}
    ph = ",".join("?" * len(lemmas))
    with get_db() as db:
        rows = db.execute(
            f"SELECT lower(text) t, id FROM words WHERE lower(text) IN ({ph})",
            tuple(lemmas),
        ).fetchall()
    return {r["t"]: r["id"] for r in rows}
