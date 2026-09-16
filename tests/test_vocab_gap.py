"""词汇差距计算测试。

守两条要害：
1. **覆盖率按词频档位加权**，不是「认识多少个词表里的词」——
   实测 CET4 大纲词表只覆盖四级真题语料 15.8% 的 token，用词表百分比当
   「能读懂多少」会严重失真。
2. **缺口取期望值**，不设「掌握率 < 0.5 就算不认识」的硬阈值。
"""
import pytest

from app.database import get_db
from app.services import placement, vocab_gap


@pytest.fixture()
def bands():
    """直接构造一组分档掌握率，不依赖数据库状态。"""
    out, remaining = [], 2500.0
    for lo, hi in placement.BANDS:
        size = hi - lo + 1
        rate = max(0.0, min(1.0, remaining / size))
        remaining -= rate * size
        out.append({"band_lo": lo, "band_hi": hi, "band_size": size, "rate": rate})
    return out


def test_rate_of_frq_maps_bands(bands):
    assert vocab_gap.rate_of_frq(500, bands) == 1.0        # 前 1000 全掌握
    assert vocab_gap.rate_of_frq(1500, bands) == 1.0       # 1001-2000 全掌握
    assert vocab_gap.rate_of_frq(2500, bands) == 0.5       # 2001-3000 掌握一半
    assert vocab_gap.rate_of_frq(5000, bands) == 0.0
    assert vocab_gap.rate_of_frq(0, bands) == 0.0          # 无词频按不认识
    assert vocab_gap.rate_of_frq(None, bands) == 0.0


def test_band_rates_sum_to_vocab(bands):
    """分档掌握率乘档宽应当等于目标词汇量。"""
    total = sum(b["rate"] * b["band_size"] for b in bands)
    assert abs(total - 2500) < 1


def test_coverage_is_monotone_in_vocabulary(bands):
    """认识得越多，覆盖率不能反而更低。"""
    prev = -1.0
    for n in (1000, 2000, 3000, 5000, 8000, 12000):
        b = []
        remaining = float(n)
        for lo, hi in placement.BANDS:
            size = hi - lo + 1
            r = max(0.0, min(1.0, remaining / size))
            remaining -= r * size
            b.append({"band_lo": lo, "band_hi": hi, "band_size": size, "rate": r})
        cov = vocab_gap.coverage("CET4", b)
        if not cov.get("available"):
            pytest.skip("本机没有真题语料")
        assert cov["coverage"] >= prev, f"{n} 词时覆盖率反而下降"
        prev = cov["coverage"]


def test_coverage_in_unit_range_and_i1_flag(bands):
    cov = vocab_gap.coverage("CET4", bands)
    if not cov.get("available"):
        pytest.skip("本机没有真题语料")
    assert 0.0 <= cov["coverage"] <= 1.0
    assert cov["unknown_per_300"] == pytest.approx((1 - cov["coverage"]) * 300, abs=0.2)
    assert cov["in_i1"] == (cov["i1_low"] <= cov["coverage"] <= cov["i1_high"])


def test_more_vocabulary_means_smaller_gap():
    """词汇量越大，每场考试的缺口都必须越小。"""
    def gap_for(n):
        b, remaining = [], float(n)
        for lo, hi in placement.BANDS:
            size = hi - lo + 1
            r = max(0.0, min(1.0, remaining / size))
            remaining -= r * size
            b.append({"band_lo": lo, "band_hi": hi, "band_size": size, "rate": r})
        # 清掉已有记录，用构造的分档直接算
        return {e["key"]: e["gap"] for e in vocab_gap.gap_by_exam(b)}

    small, large = gap_for(2000), gap_for(8000)
    if not small:
        pytest.skip("库里没有带标签的词")
    for k in small:
        assert large[k] <= small[k], f"{k} 缺口没有随词汇量下降"


def test_eta_none_when_no_rate():
    assert vocab_gap.eta(1000, 0) is None
    assert vocab_gap.eta(0, 5) is None
    assert vocab_gap.eta(1000, 5) is not None


def test_default_run_is_marked_and_never_overwrites(seeded_placement_clear):
    """默认假设必须能被识别出来；已有真实结果时不能被覆盖。"""
    from app.services import placement as pl

    rid = pl.ensure_default_run(2500)
    assert rid > 0
    assert pl.summary()["source"] == "default"
    # 再调一次不应新建
    assert pl.ensure_default_run(2500) == rid
    total = sum(b["rate"] * b["band_size"] for b in pl.latest_bands())
    assert abs(total - 2500) < 1


@pytest.fixture()
def seeded_placement_clear():
    with get_db() as db:
        db.execute("DELETE FROM placement_bands")
        db.execute("DELETE FROM placement_runs")
    yield
    with get_db() as db:
        db.execute("DELETE FROM placement_bands")
        db.execute("DELETE FROM placement_runs")


@pytest.fixture()
def synth_corpus(tmp_path, monkeypatch):
    """造一份合成语料，让覆盖率的核心算法在测试环境也能真跑。

    语料里放 3 个词：一个落在「全掌握」档、一个落在「半掌握」档、一个生僻。
    期望覆盖率 = (全掌握 token 数 × 1.0 + 半掌握 × 0.5 + 生僻 × 0.0) / 总数。
    """
    from app.database import get_db

    with get_db() as db:
        db.execute("DELETE FROM words")
        now = 0
        db.executemany(
            """INSERT INTO words (lemma, text, meaning, level, frequency, status,
                                  frq, bnc, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [("commonword", "commonword", "常用", "CET4", 0, "new", 500, 0, now, now),
             ("midword", "midword", "中频", "CET4", 0, "new", 2500, 0, now, now),
             ("rareword", "rareword", "生僻", "CET4", 0, "new", 0, 0, now, now)],
        )
    import json
    path = tmp_path / "exam_corpus.json"
    # commonword ×4、midword ×4、rareword ×2 → 覆盖率 = (4 + 4*0.5) / 10 = 0.6
    path.write_text(json.dumps([
        {"level": "CET4", "text": "commonword " * 4 + "midword " * 4 + "rareword " * 2}
    ]), encoding="utf-8")
    monkeypatch.setattr(vocab_gap, "CORPUS_FILE", path)
    vocab_gap.invalidate()
    yield
    vocab_gap.invalidate()


def test_coverage_weights_tokens_by_band_rate(bands, synth_corpus):
    """覆盖率必须按词频档位加权，而不是数「认识了几个词」。"""
    cov = vocab_gap.coverage("CET4", bands)
    assert cov["available"]
    # 4 个 commonword(1.0) + 4 个 midword(0.5) + 2 个 rareword(0.0) = 6/10
    assert cov["coverage"] == pytest.approx(0.6, abs=0.001)
    assert cov["unknown_per_300"] == pytest.approx(120, abs=1)


def test_coverage_uses_bnc_when_coca_missing(bands, tmp_path, monkeypatch):
    """ECDICT 里有一批常见词 COCA 排名是 0 但 BNC 有排名（如 percent）。

    只看 frq 会把它们当生僻词，覆盖率被系统性低估。
    """
    import json

    from app.database import get_db

    with get_db() as db:
        db.execute("DELETE FROM words")
        now = 0
        db.execute(
            """INSERT INTO words (lemma, text, meaning, level, frequency, status,
                                  frq, bnc, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("percent", "percent", "百分比", "CET4", 0, "new", 0, 800, now, now),
        )
    path = tmp_path / "c.json"
    path.write_text(json.dumps([{"level": "CET4", "text": "percent " * 10}]), encoding="utf-8")
    monkeypatch.setattr(vocab_gap, "CORPUS_FILE", path)
    vocab_gap.invalidate()

    cov = vocab_gap.coverage("CET4", bands)
    # bnc=800 落在 1-1000 档（掌握率 1.0）→ 覆盖率应为 100%，而不是 0%
    assert cov["coverage"] == pytest.approx(1.0, abs=0.001)
    vocab_gap.invalidate()


# ---------------------------------------------------------------- 生词本删除语义

def test_vocab_delete_keeps_dictionary_entry(seeded_kept):
    """⚠️ 回归：从生词本删除**不能**把词从词典里抠掉。

    曾经就是这个 bug：用户在生词本点一下删除，`DELETE FROM words` 直接把词
    从 18.6 万词库里移除了（排查数据时发现常见词 percent 就这么没了）。
    """
    from app.api.words import _remove_word

    with get_db() as db:
        action = _remove_word(db, 1, 0)
    assert action == "removed"
    with get_db() as db:
        row = db.execute("SELECT status, lookup_count, srs_stability, m_level FROM words WHERE id=1").fetchone()
        assert row is not None, "词典条目必须保留"
        assert row["status"] == "new"
        assert row["lookup_count"] == 0
        assert row["srs_stability"] == 0
        assert row["m_level"] == "unknown"
        assert db.execute("SELECT COUNT(*) FROM word_encounters WHERE word_id=1").fetchone()[0] == 0


def test_user_added_word_is_really_deleted(seeded_kept):
    """用户在界面里自己加的词（source='user'）才允许真删。"""
    from app.api.words import _remove_word

    with get_db() as db:
        db.execute(
            """INSERT INTO words (id, lemma, text, meaning, level, frequency, status, source,
                                  created_at, updated_at)
               VALUES (900,'myownword','myownword','自建','CET4',0,'learning','user',0,0)""")
        action = _remove_word(db, 900, 0)
    assert action == "deleted"
    with get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM words WHERE id=900").fetchone()[0] == 0


def test_remove_missing_word_is_safe(seeded_kept):
    from app.api.words import _remove_word

    with get_db() as db:
        assert _remove_word(db, 999999, 0) == "missing"


@pytest.fixture()
def seeded_kept():
    """一个带完整学习痕迹的 ecdict 词，用于验证「删除 = 复位」而非「移除」。"""
    from app.database import get_db

    with get_db() as db:
        for t in ("word_encounters", "articles", "words", "placement_bands", "placement_runs"):
            db.execute(f"DELETE FROM {t}")
        db.execute(
            """INSERT INTO words (id, lemma, text, meaning, level, frequency, status, source,
                                  lookup_count, srs_stability, m_level, created_at, updated_at)
               VALUES (1,'abandon','abandon','放弃','CET4',0,'learning','ecdict',
                       3, 5.0, 'seen', 0, 0)""")
        db.execute("INSERT INTO word_encounters (word_id, article_id, context, action, created_at) "
                   "VALUES (1, 1, '', 'seen', 0)")
    yield
    with get_db() as db:
        for t in ("word_encounters", "articles", "words", "placement_bands", "placement_runs"):
            db.execute(f"DELETE FROM {t}")
