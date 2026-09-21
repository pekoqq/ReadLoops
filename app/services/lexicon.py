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
from pathlib import Path
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
_REV: Optional[dict[str, str]] = None          # 变形 → 原形
_FORMS: Optional[dict[str, set[str]]] = None   # 原形 → 全部变形


def invalidate() -> None:
    """词库变更后调用（导入词典、新增单词等）—— 清空全部缓存。"""
    global _WORDS, _REV, _FORMS
    _WORDS = None
    _REV = None
    _FORMS = None


def word_set() -> set[str]:
    """库内全部小写词元。"""
    global _WORDS
    if _WORDS is None:
        with get_db() as db:
            _WORDS = {r[0] for r in db.execute("SELECT lower(text) FROM words")}
    return _WORDS


def _rev_index() -> dict[str, str]:
    """屈折形式 → 词元。

    两个来源，**以预构建的 form_index 为准**：
    1. `data/form_index.json`（tools/build_form_index.py 生成）——
       在 exchange 之外用 spaCy 补齐了 `are`/`were`/`gotten` 这类遗漏
    2. `words.exchange` 字段（兜底，索引不存在时用）

    ⚠️ 只用 exchange 是不够的：它的 `be` 条目只有 `{"past":"was","third":"is",...}`，
    **缺 `are` 与 `were`** —— 而 `are` 是英语第 2 高频词。
    """
    global _REV
    if _REV is not None:
        return _REV

    # 词频表：用于判断「反查方向」是否合理
    with get_db() as db:
        rfreq = {r["t"]: (r["r"] or 10 ** 9) for r in db.execute(
            "SELECT lower(text) t, COALESCE(NULLIF(frq, 0), bnc, 0) AS r FROM words")}

    def better_lemma(form: str, lemma: str) -> bool:
        """只在**原形比变形更常见**时才反向。

        ⚠️ 否则会把常见词还原成罕见词，让覆盖率被低估。实测两例：
          `data`   (rank ~300)   → `datum` (28458)   ✗
          `number` (rank ~150)   → `numb`  (9317)    ✗
        这些都是形态学上「正确」的拉丁/规则变化，但对「读者认不认识这个词」
        这个判断毫无帮助 —— 读者认的是 data，不是 datum。
        """
        return rfreq.get(lemma, 10 ** 9) <= rfreq.get(form, 10 ** 9)

    rev: dict[str, str] = {}
    # 优先：预构建索引的逆向
    for lemma, forms in _forms_index().items():
        for f in forms:
            if f and f != lemma and better_lemma(f, lemma):
                rev.setdefault(f, lemma)
    if rev:
        _REV = rev
        return _REV

    # 兜底：直接用 exchange
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
            if key == "lemma" or not isinstance(val, str):
                continue
            for f in val.split("/"):
                f = f.strip().lower()
                if f and f != text:
                    rev.setdefault(f, text)
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

    # ⚠️ 反查表（变形 → 原形）必须**优先于**直接命中。
    # ECDICT 把变形词单独收录且没有词频数据：`is` 是独立条目、rank 24648，
    # 而它的原形 `be` 的 rank 是 2。原来的 `if token in words: return token`
    # 让 is/are/thought/being 全返回自身 → 被当成生僻词，
    # **覆盖率被系统性低估约 9 个百分点**（实测 2,500 词档：79.0% → 87.8%）。
    rev = _rev_index()
    if token in rev:
        return rev[token]

    if token in words:
        return token

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


def _forms_index() -> dict[str, set[str]]:
    """原形 → 全部变形（含原形自身）。反向索引的逆。

    ⚠️ 为什么必须有这个：ECDICT 把变形词**单独收录**且**没有词频数据** ——
    `be` 的 rank 是 2，但它自己的变形 `is`(24648) / `are`(0) / `was` / `been`
    各自成条且 rank 极差。于是「认识最高频 N 个词」这种判据会
    **漏掉英语中最常见的词形**，把覆盖率系统性低估。

    实测：19,205 个变形词的自身 rank 比原形差 5 倍以上
    （thought 760 vs think 56；being 1676 vs be 2；left 771 vs leave 150）。

    修法不是去改 rank（evening / willing / left 本身也是独立词，改了就错），
    而是**已知集按「原形 + 全部变形」展开** —— 认识 think 就等于认识 thought。
    """
    global _FORMS
    if _FORMS is not None:
        return _FORMS

    # 优先用预构建的权威索引（tools/build_form_index.py 生成，
    # 在 exchange 之外用 spaCy 补齐了 are/were/gotten 这类遗漏）
    # 随包发布的资源目录（app/resources/），不是 .gitignore 的 data/
    fi = Path(__file__).resolve().parent.parent / "resources" / "form_index.json"
    if fi.exists():
        try:
            raw = json.loads(fi.read_text(encoding="utf-8"))
            _FORMS = {k: set(v) for k, v in raw.items()}
            return _FORMS
        except (ValueError, OSError):
            pass

    out: dict[str, set[str]] = {}
    with get_db() as db:
        for r in db.execute(
            "SELECT text, exchange FROM words "
            "WHERE exchange IS NOT NULL AND exchange != '' AND exchange != 'null'"
        ):
            base = (r["text"] or "").strip().lower()
            if not base:
                continue
            try:
                ex = json.loads(r["exchange"])
            except (ValueError, TypeError):
                continue
            forms = {base}
            for k, v in ex.items():
                if k in ("past", "third", "done", "ing", "s", "ed", "er", "est", "pl") \
                        and isinstance(v, str):
                    for f in v.split("/"):
                        f = f.strip().lower()
                        if f:
                            forms.add(f)
            out.setdefault(base, set()).update(forms)
    _FORMS = out
    return out


def expand_forms(lemmas) -> set[str]:
    """把一组原形展开成「原形 + 全部变形」的表面形式集合。

    构造「已知集」时必须用它，否则会更漏掉 is/are/was 这类高频变形。
    """
    idx = _forms_index()
    out: set[str] = set()
    for w in lemmas:
        w = (w or "").strip().lower()
        if not w:
            continue
        out.add(w)
        out.update(idx.get(w, ()))
    return out


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
