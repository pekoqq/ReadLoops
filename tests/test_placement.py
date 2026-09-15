"""定级测试的单元测试。

重点覆盖两件容易出错、且后果严重的事：
1. **伪词必须真的不是词** —— 否则虚报率校正失效，词汇量会被系统性低估
2. **题面不能泄露信息** —— 带词频档位或伪词标记会让用户翻网络面板就作弊成功
"""
import os
from pathlib import Path

import pytest

from app.services import placement as pl

# 覆盖 1–20000 共 20 档，每档塞 20 个假词，让 build_items 有样本可抽
_BAND_WORDS = 20


def _clear(db) -> None:
    """按外键依赖顺序清表（先删引用方）。

    teardown 也必须清：否则残留的 word_encounters 会让后续测试
    （conftest 的 conn fixture 会 DELETE FROM words）撞外键约束。
    """
    for t in ("word_encounters", "highlights", "reading_sessions",
              "test_questions", "articles", "words",
              "placement_bands", "placement_runs"):
        try:
            db.execute(f"DELETE FROM {t}")
        except Exception:
            pass


def _seed(db) -> None:
    """造一批带词频的测试词。

    注意必须带 meaning —— 抽题只取「有释义」的词（库里那 7 万个无释义的
    生僻词不能当题目）。少了这个字段，题库会是空的。
    """
    now = 0
    rows = []
    for i in range(20):
        lo = i * 1000 + 1
        for j in range(_BAND_WORDS):
            w = f"w{i}x{j}"
            rows.append((w, w, f"释义{i}-{j}", "CET4", 10, "new",
                         lo + j, 0, 0, 0, 0, 0, 0, now, now))
    db.executemany(
        """INSERT INTO words (lemma, text, meaning, level, frequency, status,
                              frq, bnc, collins, oxford, mastered,
                              encounter_count, lookup_count, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )


@pytest.fixture()
def seeded():
    """灌好测试数据并**提交**，再让被测服务自己开连接。

    不能沿用 conftest 的 conn fixture：它把连接开着不提交，
    而 build_items/score/save_run 都会自己开新连接 → sqlite database is locked。
    """
    from app.database import get_db

    with get_db() as db:
        _clear(db)
        _seed(db)
    yield
    with get_db() as db:
        _clear(db)


def _q(sql):
    from app.database import get_db
    with get_db() as db:
        return db.execute(sql).fetchone()[0]


# ---------------------------------------------------------------- PAVA

def test_pava_keeps_monotone_input():
    assert pl._pava([1.0, 0.8, 0.5, 0.2]) == [1.0, 0.8, 0.5, 0.2]


def test_pava_pools_violations():
    """违反单调的相邻档要合并取均值 —— 这是抑制抽样噪声的关键。"""
    out = pl._pava([1.0, 0.2, 0.9, 0.3])
    assert out[0] == 1.0
    assert out[1] == out[2] == 0.55      # 0.2 与 0.9 被合并
    assert out[3] == 0.3


def test_pava_output_is_never_increasing():
    out = pl._pava([0.1, 0.9, 0.2, 0.8, 0.3, 0.7])
    assert all(out[i] >= out[i + 1] for i in range(len(out) - 1))


# ---------------------------------------------------------------- 伪词

def test_pseudo_words_are_wellformed_and_unique():
    assert pl.PSEUDO_WORDS, "伪词表不能为空"
    assert len(set(pl.PSEUDO_WORDS)) == len(pl.PSEUDO_WORDS), "伪词不能重复"
    for w in pl.PSEUDO_WORDS:
        assert w.isalpha() and w.islower(), f"{w} 必须是纯小写字母"
        assert 5 <= len(w) <= 12, f"{w} 长度要在 5–12 之间（形似真词）"


def test_pseudo_words_are_not_real_words():
    """对照 ECDICT 校验伪词确实不存在。

    这是整个测试的基石：伪词一旦是真词，虚报率就会被高估，
    词汇量被系统性低估（我们踩过：24 个伪词里 5 个被"认识"，
    直接把估计拉低 30%）。没有 ECDICT 时退化为形状检查。
    """
    csv_path = Path(os.getenv("READLOOPS_ECDICT_CSV")
                    or Path(__file__).resolve().parent.parent / "data" / "ecdict" / "ecdict.csv")
    if not csv_path.exists():
        pytest.skip("本机没有 ECDICT 词库，跳过真词校验")

    import csv as _csv
    _csv.field_size_limit(10 ** 7)
    real = set()
    with open(csv_path, encoding="utf-8", errors="replace", newline="") as f:
        for row in _csv.DictReader(f):
            w = (row.get("word") or "").strip().lower()
            if w:
                real.add(w)
    clash = [w for w in pl.PSEUDO_WORDS if w in real]
    assert not clash, f"这些伪词是真实存在的单词，必须换掉：{clash}"


# ---------------------------------------------------------------- 出题

def test_build_items_shape_and_no_leak(seeded):
    test = pl.build_items(items_per_band=5, pseudo_count=6, seed=1)
    items = test["items"]
    assert len(items) == 20 * 5 + 6

    # 只允许下发单词本身 —— 带 band / is_pseudo 就等于把答案给了前端
    for it in items:
        assert set(it.keys()) == {"text"}, f"题目字段泄露：{it.keys()}"
    assert len({it["text"] for it in items}) == len(items), "同一份卷子里不该有重复题"

    assert len(test["bands"]) == 20
    assert test["bands"][0]["size"] == 1000


def test_build_items_is_reproducible_with_seed(seeded):
    a = [i["text"] for i in pl.build_items(items_per_band=5, pseudo_count=4, seed=42)["items"]]
    b = [i["text"] for i in pl.build_items(items_per_band=5, pseudo_count=4, seed=42)["items"]]
    assert a == b


# ---------------------------------------------------------------- 评分

def test_score_perfect_knowledge_hits_range_top(seeded):
    """全认识 + 无虚报 → 估计值应逼近 20,000 的上界。"""
    test = pl.build_items(items_per_band=5, pseudo_count=6, seed=2)
    answers = [{"text": i["text"], "known": True} for i in test["items"]
               if i["text"] not in set(pl.PSEUDO_WORDS)]
    r = pl.score(answers)
    assert r["vocab_estimate"] >= 19000
    assert r["is_lower_bound"] is True


def test_score_zero_knowledge_estimates_near_zero(seeded):
    """全不认识 + 从不误报 → 估计值应接近 0，绝不能凭空造出词汇量。"""
    test = pl.build_items(items_per_band=5, pseudo_count=6, seed=3)
    answers = [{"text": i["text"], "known": False} for i in test["items"]]
    r = pl.score(answers)
    assert r["vocab_estimate"] == 0
    # 虚报率带收缩先验，0/6 时是 0.5/14 而不是精确 0 —— 这是刻意的（抑制小样本
    # 极端值）。它只从「声称认识率」里扣，不会凭空制造词汇量。
    assert r["false_alarm"] < 0.1


def test_score_ignores_pseudo_flags_from_client(seeded):
    """客户端谎报 is_pseudo 也没用 —— 服务端按文本自行判定。"""
    test = pl.build_items(items_per_band=5, pseudo_count=6, seed=4)
    pseudo = [i["text"] for i in test["items"] if i["text"] in set(pl.PSEUDO_WORDS)]
    assert pseudo, "本份卷子应当含有伪词"
    # 全部伪词都答"认识"，同时伪造 is_pseudo=False
    answers = [{"text": w, "known": True, "is_pseudo": False} for w in pseudo]
    r = pl.score(answers)
    assert r["pseudo_sampled"] == len(pseudo)
    assert r["false_alarm"] > 0


def test_score_unknown_text_is_ignored(seeded):
    r = pl.score([{"text": "not-a-real-entry-xyz", "known": True}])
    assert r["bands"] == []
    assert r["vocab_estimate"] == 0


def test_score_is_monotone_in_knowledge(seeded):
    """认识得越多，估计的词汇量不能反而更小。"""
    test = pl.build_items(items_per_band=5, pseudo_count=6, seed=5)
    real = [i["text"] for i in test["items"] if i["text"] not in set(pl.PSEUDO_WORDS)]
    prev = -1
    for k in (0, 1, 2, 3, 5, 8, 12):
        answers = [{"text": t, "known": i < k} for i, t in enumerate(real)]
        est = pl.score(answers)["vocab_estimate"]
        assert est >= prev, f"认识 {k} 个时估计值反而变小了（{est} < {prev}）"
        prev = est


# ---------------------------------------------------------------- 落库

def test_save_run_persists_and_marks_mastery(seeded):
    test = pl.build_items(items_per_band=5, pseudo_count=6, seed=6)
    real = [i["text"] for i in test["items"] if i["text"] not in set(pl.PSEUDO_WORDS)]
    answers = [{"text": t, "known": i % 2 == 0} for i, t in enumerate(real)]

    result = pl.score(answers)
    run_id = pl.save_run(result, answers)
    assert run_id > 0

    bands = pl.latest_bands()
    assert len(bands) == len(result["bands"])
    assert bands[0]["band_lo"] == 1

    # 答"认识"的标 1，答"不认识"的标 2，都不再是 0
    known = _q("SELECT COUNT(*) FROM words WHERE mastered=1")
    unknown = _q("SELECT COUNT(*) FROM words WHERE mastered=2")
    assert known + unknown == len(real)
    assert known > 0 and unknown > 0


def test_rate_for_frq_uses_latest_run(seeded):
    test = pl.build_items(items_per_band=5, pseudo_count=6, seed=7)
    answers = [{"text": i["text"], "known": True} for i in test["items"]
               if i["text"] not in set(pl.PSEUDO_WORDS)]
    pl.save_run(pl.score(answers), answers)

    assert pl.rate_for_frq(500) is not None      # 落在某一档内
    assert pl.rate_for_frq(0) is None            # 无词频


def test_summary_before_any_run(seeded):
    assert pl.summary()["has_result"] is False
