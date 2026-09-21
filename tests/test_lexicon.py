"""词形还原的测试。

这个模块的 bug 代价极大：ECDICT 把变形词**单独收录且没有词频数据**，
如果 `normalize` 不把它们还原成原形，它们会被当成生僻词 ——
实测**覆盖率被系统性低估约 9 个百分点**（2,500 词档：79.0% vs 真实 87.8%），
而覆盖率是整个产品所有判断（选词、缺口、i+1 判定）的地基。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.database import get_db
from app.services import lexicon

FORM_INDEX = Path(__file__).resolve().parent.parent / "data" / "form_index.json"

# 最高频的变形词：自身在库里有条目但 rank 极差（`is`=24648、`are`=0），
# 而原形 rank 极好（`be`=2）。必须还原成原形。
_REGRESSION = [
    ("is", "be"), ("are", "be"), ("was", "be"), ("were", "be"),
    ("been", "be"), ("being", "be"),
    ("has", "have"), ("had", "have"),
    ("said", "say"), ("made", "make"), ("went", "go"),
    ("thought", "think"), ("left", "leave"), ("gotten", "get"),
]


@pytest.fixture(autouse=True)
def _fresh():
    """播种最小词表。

    注意 ECDICT 只对**部分**变形单独建条目（is/are 有，students 没有）。
    有独立条目的走 form_index.json（静态文件，不依赖库）；
    没有的走规则兜底（-s/-ed/-ing），而规则兜底需要库里存在那个原形。
    """
    with get_db() as db:
        db.executemany(
            """INSERT OR REPLACE INTO words
               (id, lemma, text, type, meaning, level, frq, created_at, updated_at)
               VALUES (?,?,?,'word','x','CET4',?,0,0)""",
            [(900001, "student", "student", 500),
             (900002, "write", "write", 400),
             (900003, "run", "run", 300),
             (900004, "do", "do", 10)],
        )
    lexicon.invalidate()
    yield
    lexicon.invalidate()


def test_normalize_prefers_lemma_over_self_entry():
    """⚠️ 核心回归：反查表（变形→原形）必须优先于「直接命中库内词」。

    原来的实现是 `if token in words: return token`，于是 `is` 返回 `is` ——
    它自己的 rank 是 24648，被当成第 20 档的生僻词，覆盖率凭空少算约 9 个百分点。
    """
    bad = []
    for form, lemma in _REGRESSION:
        got = lexicon.normalize(form)
        if got != lemma:
            bad.append(f"{form} → {got!r}（应为 {lemma!r}）")
    assert not bad, "词形还原失败：\n  " + "\n  ".join(bad)


def test_expand_forms_covers_inflections():
    """已知集必须按「原形 + 全部变形」展开。

    认识 `be` 就等于认识 `is`/`are`/`was`/`were`/`been`/`being` ——
    否则「认识最高频 N 词」这个判据会漏掉英语里最常见的词形。
    """
    forms = lexicon.expand_forms(["be", "think", "leave"])
    for w in ("be", "is", "are", "was", "were", "been", "being",
              "think", "thought", "leave", "left"):
        assert w in forms, f"{w} 应当在 be/think/leave 的变形集合里"


def test_normalize_falls_back_to_rules():
    """规则兜底（-s/-ed/-ing）应对没有独立条目的变形生效。"""
    assert lexicon.normalize("students") == "student"
    assert lexicon.normalize("writes") == "write"
    assert lexicon.normalize("running") == "run"


def test_form_index_is_loaded():
    """预构建的 form_index.json 应当生效。

    它补上了 `exchange` 字段的遗漏 —— 例如 `be.exchange` 只有
    `{"past":"was","third":"is",...}`，**缺 are / were**，
    而 `are` 是英语第 2 高频词。
    """
    if not FORM_INDEX.exists():
        pytest.skip("form_index.json 不存在，先跑 tools/build_form_index.py")
    forms = lexicon.expand_forms(["be"])
    assert "are" in forms and "were" in forms, (
        "form_index.json 未生效或未包含 are/were")


def test_normalize_strips_contractions():
    """缩写要拆开：don't → do（拆开后能对上更高频的原形）。"""
    assert lexicon.normalize("don't") == "do"
    assert lexicon.normalize("Don't") == "do"


def test_tokenize_keeps_apostrophes_and_hyphens():
    toks = lexicon.tokenize("The students aren't well-known yet.")
    assert "students" in toks
    assert "aren't" in toks
    assert "well-known" in toks
