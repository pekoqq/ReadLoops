"""生成 prompt 的风格约束回归测试。

守三件事，都是踩过坑的：
1. 个人画像**不能压过**真题基准（曾经排在前面且写着 "Follow this profile"）
2. 画像提示**不能出现「低频词占比」** —— 那等于指示模型多写生僻词，
   与 i+1（95–98% 覆盖率）直接矛盾
3. 基准参数必须来自全量真题实测，不是拍脑袋的旧数字
"""
import json

import pytest

from app.database import get_db
from app.services import ai, distill


@pytest.fixture()
def seeded_style():
    with get_db() as db:
        db.execute("DELETE FROM style_profiles")
        # 高 avg_len 是基准、高 compound 是基准；刻意给一组与基准**相反**的画像数据
        db.execute(
            """INSERT INTO style_profiles (name, material_ids, params, sample_count,
                                           is_active, created_at)
               VALUES (?,?,?,?,?,?)""",
            ("测试画像", "[1]",
             json.dumps({"sentence_count": 100, "avg_len": 9.0, "stdev_len": 3.0,
                         "long_ratio": 0.02, "compound_ratio": 0.10, "ttr": 0.20,
                         "low_freq_ratio": 1.0}),
             100, 1, 0),
        )
    yield
    with get_db() as db:
        db.execute("DELETE FROM style_profiles")


def _capture_prompt(monkeypatch) -> str:
    captured = {}

    def fake_chat(messages, max_tokens=1500, temperature=0.7, retries=2):
        captured["prompt"] = messages[0]["content"]
        return json.dumps({"title": "T", "content": "x " * 300, "new_words": []})

    monkeypatch.setattr(ai, "_chat", fake_chat)
    ai._generate_once("a trend", "start with a fact", "end with a fact", ["abandon"])
    return captured["prompt"]


def test_prompt_uses_measured_cet4_baseline(monkeypatch, seeded_style):
    p = _capture_prompt(monkeypatch)
    assert "measured from 273 real CET-4 passages" in p
    # 旧硬编码里复合句 50%、状语从句 30%，实测是 65% / 17%
    assert "~65% of sentences" in p
    assert "~17% of sentences" in p
    # 旧表述必须消失
    assert "205 real CET-4 passages" not in p


def test_personal_profile_cannot_override_baseline(monkeypatch, seeded_style):
    """画像块必须排在基准之后，且明确声明基准优先。"""
    p = _capture_prompt(monkeypatch)
    assert "OPTIONAL REGISTER NOTE" in p
    assert "light touch ONLY" in p
    assert "CET-4 baseline above always wins" in p
    assert "ACTIVE PERSONAL STYLE PROFILE" not in p
    assert "Follow this profile" not in p
    assert p.index("STYLE REQUIREMENTS") < p.index("OPTIONAL REGISTER NOTE")


def test_profile_hint_never_mentions_low_frequency_ratio():
    """低频词占比绝不能进提示 —— 它和 i+1 的覆盖率约束直接对立。"""
    with get_db() as db:
        db.execute(
            """INSERT INTO style_profiles (name, material_ids, params, sample_count,
                                           is_active, created_at)
               VALUES (?,?,?,?,?,?)""",
            ("低频陷阱", "[]",
             json.dumps({"avg_len": 17.0, "long_ratio": 0.23, "compound_ratio": 0.57,
                         "ttr": 0.055, "low_freq_ratio": 1.0, "high_freq_ratio": 0.0}),
             100, 1, 0),
        )
    try:
        hint = distill.profile_prompt_hint()
        assert hint, "画像生效时应当产出提示"
        assert "低频" not in hint
        assert "low" not in hint.lower()
        # 保留的维度仍然在
        assert "平均句长" in hint
    finally:
        with get_db() as db:
            db.execute("DELETE FROM style_profiles")


def test_no_profile_means_no_register_block(monkeypatch):
    with get_db() as db:
        db.execute("DELETE FROM style_profiles")
    p = _capture_prompt(monkeypatch)
    assert "OPTIONAL REGISTER NOTE" not in p
    assert "TARGET WORDS" in p          # 其余部分照常


def test_generate_article_returns_target_words(monkeypatch, seeded_style):
    """回归：Article 必须带上 target_words。

    它默认是 "[]"，一旦忘了传，调用方（API 响应、重遇记录）都会拿到空列表 ——
    重遇机制就统计不到「文章里刻意埋的目标生词」了。
    """
    from app.services import ai as ai_mod

    # 播种候选词，让 select_targets 真能选出东西来
    with get_db() as db:
        db.execute("DELETE FROM words")
        db.executemany(
            """INSERT INTO words (id, lemma, text, type, meaning, level, status, frq,
                                  created_at, updated_at)
               VALUES (?,?,?,'word','释义','CET4','new',?,0,0)""",
            # ⚠️ frq 必须 > 已知集上限（默认 2500），否则会被选词排除
            [(700001, "alpha", "alpha", 3000), (700002, "beta", "beta", 3200),
             (700003, "gamma", "gamma", 3400), (700004, "delta", "delta", 3600)],
        )

    def fake_chat(messages, max_tokens=1500, temperature=0.7, retries=2):
        # 注意：模型**自称**的 new_words 与调用方选的目标词不同 ——
        # 这正是要验证的：落库的必须是**调用方选的**，不是模型自称的。
        return json.dumps({"title": "T", "content": "word " * 320,
                           "new_words": ["not", "selected"]})

    monkeypatch.setattr(ai_mod, "_chat", fake_chat)
    monkeypatch.setattr(ai_mod, "_select_new_words", lambda n: ["abandon", "consequence"])
    from app.services import similarity
    monkeypatch.setattr(similarity, "check_duplicate", lambda *a, **k: (False, 0.1, ""))

    art = ai_mod.generate_article(target_new_words=2)
    assert art is not None
    assert art.target_words, "target_words 不能是空的默认值"

    # ⚠️ 语义：落库的必须是**调用方 select_targets 选出的词**，
    # 而不是模型在 JSON 里自称的 new_words —— 模型经常漏用或改写目标词，
    # 按它自称的存会让重遇记录与掌握度追踪到错误的词，
    # 而「12 次遇见」的累积正是靠这张表。
    saved = json.loads(art.target_words)
    assert "not" not in saved and "selected" not in saved, (
        f"落库的是模型自称的词而不是我们选的：{saved}")
    assert saved, "应当落库我们选出的目标词"
